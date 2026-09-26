"""Argentina: WSFEv1 (factura electrónica de ARCA, ex AFIP).

Portado de TUWAYKISHOP (``app/services/afip_wsfe.py``). Envelopes SOAP
armados a mano (sin zeep), todo texto escapado y montos con HALF_UP.

Cambios respecto del original:
  - ``CAEResult.communication_error``: distingue "ARCA lo rechazó" de "no se
    pudo hablar con ARCA" (timeout, caída). Lo segundo NO es un rechazo: el
    comprobante pudo quedar autorizado y hay que consultarlo antes de reintentar.
  - ``fe_comp_consultar`` (FECompConsultar) para recuperar un CAE tras un corte.
  - ``transport`` inyectable para tests.

Endpoints:
    - Homologación: https://wswhomo.afip.gov.ar/wsfev1/service.asmx
    - Producción:   https://servicios1.afip.gov.ar/wsfev1/service.asmx
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape as _xml_escape_raw

import httpx
from defusedxml import ElementTree as ET

logger = logging.getLogger(__name__)

WSFE_URLS = {
    "sandbox": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    "production": "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
}
_WSFE_NAMESPACE = "http://ar.gov.afip.dif.FEV1/"
_WSFE_TIMEOUT_SECONDS = 30
_MONEY_QUANTUM = Decimal("0.01")


@dataclass
class CAEResult:
    success: bool
    cae: str = ""
    cae_fch_vto: str = ""            # AAAAMMDD
    cbte_nro: int = 0
    resultado: str = ""              # "A" aprobado, "R" rechazado
    errors: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    communication_error: bool = False


@dataclass
class UltimoAutorizadoResult:
    success: bool
    cbte_nro: int = 0
    errors: list[str] = field(default_factory=list)
    communication_error: bool = False


@dataclass
class ComprobanteConsultado:
    found: bool
    cae: str = ""
    cae_fch_vto: str = ""
    resultado: str = ""
    imp_total: str = ""
    errors: list[str] = field(default_factory=list)
    communication_error: bool = False


@dataclass
class FECAERequest:
    cbte_tipo: int
    punto_vta: int
    concepto: int = 1                # 1 productos, 2 servicios, 3 ambos
    tipo_doc: int = 99               # 80 CUIT, 96 DNI, 99 consumidor final
    nro_doc: int = 0
    cbte_desde: int = 0
    cbte_hasta: int = 0
    fecha_cbte: str = ""             # AAAAMMDD
    imp_total: Decimal | float = 0
    imp_tot_conc: Decimal | float = 0   # neto no gravado (en tipo C debe ser 0)
    imp_neto: Decimal | float = 0       # neto gravado (en tipo C: el subtotal)
    imp_iva: Decimal | float = 0
    imp_trib: Decimal | float = 0
    imp_op_ex: Decimal | float = 0
    mon_id: str = "PES"
    mon_cotiz: Decimal | float = 1
    fecha_serv_desde: str = ""
    fecha_serv_hasta: str = ""
    fecha_vto_pago: str = ""
    iva_items: list[dict] = field(default_factory=list)   # [{"Id": 5, "BaseImp": X, "Importe": Y}]
    # Condición frente al IVA del receptor (RG 5616, obligatoria desde 2025).
    condicion_iva_receptor: int | None = None


def _xe(value: object) -> str:
    if value is None:
        return ""
    return _xml_escape_raw(str(value), entities={'"': "&quot;", "'": "&apos;"})


def _money(value: object) -> str:
    try:
        d = Decimal(str(value))
    except Exception:
        d = Decimal("0")
    return str(d.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _soap_envelope(method: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:wsfe="{_WSFE_NAMESPACE}">'
        f"<soap:Body><wsfe:{method}>{body}</wsfe:{method}></soap:Body>"
        "</soap:Envelope>"
    )


def _auth_xml(token: str, sign: str, cuit: int) -> str:
    return (
        "<wsfe:Auth>"
        f"<wsfe:Token>{_xe(token)}</wsfe:Token>"
        f"<wsfe:Sign>{_xe(sign)}</wsfe:Sign>"
        f"<wsfe:Cuit>{int(cuit)}</wsfe:Cuit>"
        "</wsfe:Auth>"
    )


def _find(root, path: list[str]):
    current = root
    for part in path:
        current = next((c for c in current if _local(c.tag) == part), None)
        if current is None:
            return None
    return current


def _text(root, path: list[str]) -> str:
    elem = _find(root, path)
    return (elem.text or "") if elem is not None else ""


def _first(root, name: str):
    return next((e for e in root.iter() if _local(e.tag) == name), None)


def _messages(block, item_tag: str) -> list[str]:
    out = []
    if block is None:
        return out
    for item in block:
        if _local(item.tag) == item_tag:
            code, msg = _text(item, ["Code"]), _text(item, ["Msg"])
            out.append(f"[{code}] {msg}" if code else msg)
    return out


def _errors(elem) -> list[str]:
    return _messages(_find(elem, ["Errors"]), "Err")


def _observations(elem) -> list[str]:
    return _messages(_find(elem, ["Observaciones"]), "Obs")


def _url(environment: str) -> str:
    if environment not in WSFE_URLS:
        raise ValueError(
            f"Ambiente WSFEv1 inválido: {environment!r}. Válidos: {sorted(WSFE_URLS)}."
        )
    return WSFE_URLS[environment]


async def _soap_call(url: str, action: str, envelope: str, transport=None):
    """Raises ConnectionError (sin respuesta) o ValueError (respuesta inválida)."""
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{_WSFE_NAMESPACE}{action}"',
    }
    try:
        async with httpx.AsyncClient(timeout=_WSFE_TIMEOUT_SECONDS, transport=transport) as client:
            response = await client.post(url, content=envelope.encode("utf-8"), headers=headers)
    except httpx.TimeoutException as exc:
        raise ConnectionError(f"WSFEv1 no respondió a tiempo ({url}).") from exc
    except httpx.HTTPError as exc:
        raise ConnectionError(f"No se pudo conectar a WSFEv1 ({url}): {exc}") from exc
    if response.status_code >= 500:
        raise ConnectionError(f"WSFEv1 respondió HTTP {response.status_code}.")
    if response.status_code != 200:
        logger.debug("WSFEv1 HTTP %s body=%r", response.status_code, response.text)
        raise ValueError(
            f"WSFEv1 respondió HTTP {response.status_code}: {response.text[:100].strip()}"
        )
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise ValueError(f"Respuesta SOAP inválida: {exc}") from exc
    fault = _first(root, "Fault")
    if fault is not None:
        raise ValueError(f"SOAP Fault de ARCA: {_text(fault, ['faultstring'])}")
    return root


async def fe_comp_ultimo_autorizado(
    token: str, sign: str, cuit: int, punto_venta: int, cbte_tipo: int,
    environment: str = "sandbox", *, transport=None,
) -> UltimoAutorizadoResult:
    """Último número autorizado (ARCA no acepta saltos de numeración)."""
    body = (
        f"{_auth_xml(token, sign, cuit)}"
        f"<wsfe:PtoVta>{int(punto_venta)}</wsfe:PtoVta>"
        f"<wsfe:CbteTipo>{int(cbte_tipo)}</wsfe:CbteTipo>"
    )
    try:
        root = await _soap_call(
            _url(environment), "FECompUltimoAutorizado",
            _soap_envelope("FECompUltimoAutorizado", body), transport,
        )
    except ConnectionError as exc:
        return UltimoAutorizadoResult(success=False, errors=[str(exc)], communication_error=True)
    except ValueError as exc:
        return UltimoAutorizadoResult(success=False, errors=[str(exc)])
    result = _first(root, "FECompUltimoAutorizadoResult")
    if result is None:
        return UltimoAutorizadoResult(
            success=False, errors=["Falta FECompUltimoAutorizadoResult en la respuesta."]
        )
    errors = _errors(result)
    try:
        nro = int(_text(result, ["CbteNro"]) or 0)
    except ValueError:
        nro = 0
    return UltimoAutorizadoResult(success=not errors, cbte_nro=nro, errors=errors)


def build_fecae_request_xml(token: str, sign: str, cuit: int, req: FECAERequest) -> str:
    if int(req.cbte_desde) != int(req.cbte_hasta):
        raise ValueError(
            "FECAESolicitar solo admite 1 comprobante por pedido (CantReg=1); "
            f"recibido desde={req.cbte_desde} hasta={req.cbte_hasta}."
        )
    mon_cotiz = Decimal(str(req.mon_cotiz)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    iva_xml = ""
    if req.iva_items:
        iva_xml = "<wsfe:Iva>" + "".join(
            "<wsfe:AlicIva>"
            f"<wsfe:Id>{int(it['Id'])}</wsfe:Id>"
            f"<wsfe:BaseImp>{_money(it['BaseImp'])}</wsfe:BaseImp>"
            f"<wsfe:Importe>{_money(it['Importe'])}</wsfe:Importe>"
            "</wsfe:AlicIva>"
            for it in req.iva_items
        ) + "</wsfe:Iva>"
    servicio = (
        f"<wsfe:FchServDesde>{_xe(req.fecha_serv_desde)}</wsfe:FchServDesde>"
        f"<wsfe:FchServHasta>{_xe(req.fecha_serv_hasta)}</wsfe:FchServHasta>"
        f"<wsfe:FchVtoPago>{_xe(req.fecha_vto_pago)}</wsfe:FchVtoPago>"
        if req.concepto in (2, 3) and req.fecha_serv_desde else ""
    )
    condicion = (
        f"<wsfe:CondicionIVAReceptorId>{int(req.condicion_iva_receptor)}</wsfe:CondicionIVAReceptorId>"
        if req.condicion_iva_receptor else ""
    )
    body = (
        f"{_auth_xml(token, sign, cuit)}"
        "<wsfe:FeCAEReq>"
        "<wsfe:FeCabReq>"
        "<wsfe:CantReg>1</wsfe:CantReg>"
        f"<wsfe:PtoVta>{int(req.punto_vta)}</wsfe:PtoVta>"
        f"<wsfe:CbteTipo>{int(req.cbte_tipo)}</wsfe:CbteTipo>"
        "</wsfe:FeCabReq>"
        "<wsfe:FeDetReq><wsfe:FECAEDetRequest>"
        f"<wsfe:Concepto>{int(req.concepto)}</wsfe:Concepto>"
        f"<wsfe:DocTipo>{int(req.tipo_doc)}</wsfe:DocTipo>"
        f"<wsfe:DocNro>{int(req.nro_doc)}</wsfe:DocNro>"
        f"<wsfe:CbteDesde>{int(req.cbte_desde)}</wsfe:CbteDesde>"
        f"<wsfe:CbteHasta>{int(req.cbte_hasta)}</wsfe:CbteHasta>"
        f"<wsfe:CbteFch>{_xe(req.fecha_cbte)}</wsfe:CbteFch>"
        f"<wsfe:ImpTotal>{_money(req.imp_total)}</wsfe:ImpTotal>"
        f"<wsfe:ImpTotConc>{_money(req.imp_tot_conc)}</wsfe:ImpTotConc>"
        f"<wsfe:ImpNeto>{_money(req.imp_neto)}</wsfe:ImpNeto>"
        f"<wsfe:ImpOpEx>{_money(req.imp_op_ex)}</wsfe:ImpOpEx>"
        f"<wsfe:ImpTrib>{_money(req.imp_trib)}</wsfe:ImpTrib>"
        f"<wsfe:ImpIVA>{_money(req.imp_iva)}</wsfe:ImpIVA>"
        f"{servicio}"
        f"<wsfe:MonId>{_xe(req.mon_id)}</wsfe:MonId>"
        f"<wsfe:MonCotiz>{mon_cotiz}</wsfe:MonCotiz>"
        f"{condicion}"
        f"{iva_xml}"
        "</wsfe:FECAEDetRequest></wsfe:FeDetReq>"
        "</wsfe:FeCAEReq>"
    )
    return _soap_envelope("FECAESolicitar", body)


async def fe_cae_solicitar(
    token: str, sign: str, cuit: int, request: FECAERequest,
    environment: str = "sandbox", *, transport=None,
) -> CAEResult:
    """Pide el CAE de UN comprobante."""
    envelope = build_fecae_request_xml(token, sign, cuit, request)
    try:
        root = await _soap_call(_url(environment), "FECAESolicitar", envelope, transport)
    except ConnectionError as exc:
        return CAEResult(success=False, errors=[str(exc)], communication_error=True)
    except ValueError as exc:
        return CAEResult(success=False, errors=[str(exc)])
    result = _first(root, "FECAESolicitarResult")
    if result is None:
        return CAEResult(success=False, errors=["Falta FECAESolicitarResult en la respuesta."])
    general_errors = _errors(result)
    det = _first(result, "FECAEDetResponse")
    if det is None:
        return CAEResult(
            success=False, errors=general_errors or ["Falta FECAEDetResponse en la respuesta."]
        )
    resultado = _text(det, ["Resultado"])
    cae = _text(det, ["CAE"])
    try:
        nro = int(_text(det, ["CbteDesde"]) or request.cbte_desde)
    except ValueError:
        nro = int(request.cbte_desde)
    return CAEResult(
        success=resultado == "A" and bool(cae),
        cae=cae,
        cae_fch_vto=_text(det, ["CAEFchVto"]),
        cbte_nro=nro,
        resultado=resultado,
        errors=general_errors + _errors(det),
        observations=_observations(det),
    )


async def fe_comp_consultar(
    token: str, sign: str, cuit: int, punto_venta: int, cbte_tipo: int, cbte_nro: int,
    environment: str = "sandbox", *, transport=None,
) -> ComprobanteConsultado:
    """Datos de un comprobante ya emitido (para recuperar el CAE tras un corte)."""
    body = (
        f"{_auth_xml(token, sign, cuit)}"
        "<wsfe:FeCompConsReq>"
        f"<wsfe:CbteTipo>{int(cbte_tipo)}</wsfe:CbteTipo>"
        f"<wsfe:CbteNro>{int(cbte_nro)}</wsfe:CbteNro>"
        f"<wsfe:PtoVta>{int(punto_venta)}</wsfe:PtoVta>"
        "</wsfe:FeCompConsReq>"
    )
    try:
        root = await _soap_call(
            _url(environment), "FECompConsultar", _soap_envelope("FECompConsultar", body), transport
        )
    except ConnectionError as exc:
        return ComprobanteConsultado(found=False, errors=[str(exc)], communication_error=True)
    except ValueError as exc:
        return ComprobanteConsultado(found=False, errors=[str(exc)])
    result = _first(root, "FECompConsultarResult")
    if result is None:
        return ComprobanteConsultado(found=False, errors=["Falta FECompConsultarResult."])
    get = _find(result, ["ResultGet"])
    errors = _errors(result)
    if get is None:
        return ComprobanteConsultado(found=False, errors=errors)
    return ComprobanteConsultado(
        found=True,
        cae=_text(get, ["CodAutorizacion"]),
        cae_fch_vto=_text(get, ["FchVto"]),
        resultado=_text(get, ["Resultado"]),
        imp_total=_text(get, ["ImpTotal"]),
        errors=errors,
    )
