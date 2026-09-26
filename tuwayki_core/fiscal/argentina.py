"""Argentina: emitir un ``Document`` ante ARCA (ex AFIP) con WSAA + WSFEv1.

Cada empresa emite con SU certificado y clave (el sistema los descifra y los
pasa en claro). Soporta factura A/B/C (la "boleta" se emite como B o C);
las notas de crédito/débito de Argentina quedan para más adelante.

Diferencias con la versión de TUWAYKISHOP (corregidas acá, según el manual
del desarrollador WSFEv1 de ARCA):
  - Factura C: el subtotal va en ``ImpNeto`` e ``ImpTotConc`` = 0.
  - Se informa ``CondicionIVAReceptorId`` (RG 5616, obligatorio).
  - Sin respuesta de ARCA no es "rechazado": se consulta el número con
    FECompConsultar y, si no aparece, queda en error para reintentar.
  - La alícuota sale de la configuración (21 o 10,5), no fija.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx

from tuwayki_core.fiscal import argentina_wsaa as wsaa
from tuwayki_core.fiscal import argentina_wsfe as wsfe
from tuwayki_core.fiscal.amounts import compute_totals
from tuwayki_core.fiscal.models import (
    Document,
    DocumentType,
    FiscalStatus,
    IssueResult,
    Totals,
)
from tuwayki_core.utils.fiscal_validators import VALID_ENVIRONMENTS, validate_cuit

logger = logging.getLogger(__name__)

# Letra según condición frente al IVA de emisor y receptor.
_LETTER_MATRIX: dict[tuple[str, str], str] = {
    ("RI", "RI"): "A",
    ("RI", "monotributo"): "B",
    ("RI", "exento"): "B",
    ("RI", "CF"): "B",
}
# CbteTipo de ARCA por letra (factura; la "boleta" usa el mismo código).
CBTE_TIPO_FACTURA = {"A": 1, "B": 6, "C": 11}
# Condición frente al IVA del receptor (tabla de ARCA, RG 5616).
CONDICION_IVA_RECEPTOR = {"RI": 1, "exento": 4, "CF": 5, "monotributo": 6}
# Id de alícuota de IVA de ARCA.
ALICUOTA_IVA_ID = {
    Decimal("0"): 3, Decimal("10.5"): 4, Decimal("21"): 5,
    Decimal("27"): 6, Decimal("5"): 8, Decimal("2.5"): 9,
}
MONEDA_ARCA = {"ARS": "PES", "USD": "DOL"}


@dataclass(frozen=True)
class ArcaIssuer:
    """Datos de la empresa emisora (credenciales YA descifradas)."""
    cuit: str
    point_of_sale: int
    vat_condition: str                  # "RI", "monotributo", "exento"
    certificate_pem: bytes | str
    private_key_pem: bytes | str
    environment: str = "sandbox"        # "sandbox" (homologación) o "production"
    concept: int = 1                    # 1 productos, 2 servicios, 3 ambos


def invoice_letter(issuer_vat: str, buyer_vat: str) -> str:
    issuer_vat = (issuer_vat or "RI").strip()
    if issuer_vat != "RI":
        return "C"                      # monotributo / exento: siempre C
    return _LETTER_MATRIX.get((issuer_vat, (buyer_vat or "CF").strip()), "B")


def iva_items(totals: Totals, tax_rate: Decimal) -> list[dict]:
    rate = Decimal(str(tax_rate)).normalize()
    alic = ALICUOTA_IVA_ID.get(rate)
    if alic is None:
        raise ValueError(f"Alícuota de IVA no admitida por ARCA: {tax_rate}%.")
    return [{"Id": alic, "BaseImp": totals.taxed, "Importe": totals.tax}]


def build_request(doc: Document, issuer: ArcaIssuer, totals: Totals) -> wsfe.FECAERequest:
    buyer = doc.buyer
    buyer_vat = buyer.vat_condition if buyer else "CF"
    letter = invoice_letter(issuer.vat_condition, buyer_vat)
    fecha = doc.issue_date.strftime("%Y%m%d")
    req = wsfe.FECAERequest(
        cbte_tipo=CBTE_TIPO_FACTURA[letter],
        punto_vta=int(issuer.point_of_sale),
        concepto=issuer.concept if issuer.concept in (1, 2, 3) else 1,
        tipo_doc=int(buyer.doc_type) if buyer and buyer.doc_type.isdigit() else 99,
        nro_doc=int(buyer.doc_number) if buyer and (buyer.doc_number or "").isdigit() else 0,
        cbte_desde=int(doc.number),
        cbte_hasta=int(doc.number),
        fecha_cbte=fecha,
        imp_total=totals.total,
        mon_id=MONEDA_ARCA.get(doc.currency, "PES"),
        mon_cotiz=doc.exchange_rate or Decimal("1"),
        condicion_iva_receptor=CONDICION_IVA_RECEPTOR.get(buyer_vat or "CF", 5),
    )
    if req.concepto in (2, 3):
        req.fecha_serv_desde = req.fecha_serv_hasta = req.fecha_vto_pago = fecha
    if letter == "C":
        # Factura C: no discrimina IVA. El subtotal va en ImpNeto; ImpTotConc,
        # ImpOpEx e ImpIVA van en 0 (manual WSFEv1). ImpTotal = ImpNeto.
        req.imp_neto = totals.total
        req.imp_tot_conc = Decimal("0")
        req.imp_op_ex = Decimal("0")
        req.imp_iva = Decimal("0")
    else:
        req.imp_neto = totals.taxed
        req.imp_tot_conc = totals.unaffected
        req.imp_op_ex = totals.exempt
        req.imp_iva = totals.tax
        req.iva_items = iva_items(totals, doc.tax_rate) if totals.taxed else []
    return req


def arca_qr(issuer: ArcaIssuer, doc: Document, req: wsfe.FECAERequest, cae: str) -> str:
    """QR según RG 4291/2018: JSON en Base64 dentro de la URL de ARCA."""
    payload = {
        "ver": 1,
        "fecha": doc.issue_date.strftime("%Y-%m-%d"),
        "cuit": int(issuer.cuit),
        "ptoVta": int(issuer.point_of_sale),
        "tipoCmp": req.cbte_tipo,
        "nroCmp": int(doc.number),
        "importe": float(req.imp_total),
        "moneda": req.mon_id,
        "ctz": float(req.mon_cotiz),
        "tipoDocRec": req.tipo_doc,
        "nroDocRec": req.nro_doc,
        "tipoCodAut": "E",
        "codAut": int(cae) if cae.isdigit() else 0,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "https://www.afip.gob.ar/fe/qr/?p=" + base64.b64encode(raw).decode()


def validate_issuer(issuer: ArcaIssuer) -> list[str]:
    errors = []
    ok, msg = validate_cuit(issuer.cuit)
    if not ok:
        errors.append(f"CUIT del emisor: {msg}")
    if issuer.environment not in VALID_ENVIRONMENTS:
        errors.append(f"Ambiente no válido: {issuer.environment}.")
    if not 1 <= int(issuer.point_of_sale or 0) <= 99998:
        errors.append("El punto de venta debe estar entre 1 y 99998.")
    if not issuer.certificate_pem or not issuer.private_key_pem:
        errors.append("Falta el certificado o la clave de ARCA de la empresa.")
    return errors


async def last_authorized(
    issuer: ArcaIssuer, cbte_tipo: int, *, transport: httpx.AsyncBaseTransport | None = None
) -> wsfe.UltimoAutorizadoResult:
    creds = await wsaa.authenticate(
        issuer.certificate_pem, issuer.private_key_pem, issuer.environment, transport=transport
    )
    return await wsfe.fe_comp_ultimo_autorizado(
        creds.token, creds.sign, int(issuer.cuit), int(issuer.point_of_sale), cbte_tipo,
        issuer.environment, transport=transport,
    )


async def issue(
    doc: Document, issuer: ArcaIssuer, *, transport: httpx.AsyncBaseTransport | None = None
) -> IssueResult:
    """Emite el comprobante y devuelve el CAE (o el motivo del rechazo)."""
    if doc.doc_type not in (DocumentType.INVOICE, DocumentType.RECEIPT):
        return IssueResult(
            status=FiscalStatus.REJECTED, error_code="validacion",
            message="Las notas de crédito/débito de Argentina todavía no están disponibles.",
        )
    errors = validate_issuer(issuer)
    if doc.country != "AR":
        errors.append("Este conector es para comprobantes de Argentina.")
    try:
        totals = compute_totals(doc.lines, doc.tax_rate, doc.global_discount)
        req = build_request(doc, issuer, totals)
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        return IssueResult(status=FiscalStatus.REJECTED, error_code="validacion", message=" ".join(errors))

    request_log = {
        "cbte_tipo": req.cbte_tipo, "punto_vta": req.punto_vta, "cbte_nro": req.cbte_desde,
        "imp_total": str(req.imp_total), "imp_neto": str(req.imp_neto), "imp_iva": str(req.imp_iva),
        "tipo_doc": req.tipo_doc, "nro_doc": req.nro_doc, "fecha": req.fecha_cbte,
        "condicion_iva_receptor": req.condicion_iva_receptor, "ambiente": issuer.environment,
    }
    try:
        creds = await wsaa.authenticate(
            issuer.certificate_pem, issuer.private_key_pem, issuer.environment, transport=transport
        )
    except ConnectionError as exc:
        return IssueResult(status=FiscalStatus.ERROR, error_code="wsaa_conexion",
                           message=str(exc), request=request_log)
    except ValueError as exc:
        return IssueResult(status=FiscalStatus.ERROR, error_code="wsaa",
                           message=f"No se pudo autenticar con ARCA: {exc}", request=request_log)

    cuit = int(issuer.cuit)
    result = await wsfe.fe_cae_solicitar(
        creds.token, creds.sign, cuit, req, issuer.environment, transport=transport
    )
    if result.communication_error:
        # Pudo quedar autorizado aunque no llegó la respuesta: se consulta.
        found = await wsfe.fe_comp_consultar(
            creds.token, creds.sign, cuit, req.punto_vta, req.cbte_tipo, req.cbte_desde,
            issuer.environment, transport=transport,
        )
        if found.found and found.cae:
            return IssueResult(
                status=FiscalStatus.AUTHORIZED, message="Autorizado por ARCA (recuperado tras un corte).",
                authorization_code=found.cae, authorization_expiry=found.cae_fch_vto,
                qr_data=arca_qr(issuer, doc, req, found.cae), request=request_log,
                response={"consulta": found.__dict__},
            )
        return IssueResult(
            status=FiscalStatus.ERROR, error_code="wsfe_conexion",
            message="; ".join(result.errors) or "Sin respuesta de ARCA.",
            request=request_log, response={"errors": result.errors},
        )
    response_log = {
        "resultado": result.resultado, "cae": result.cae, "cae_fch_vto": result.cae_fch_vto,
        "errors": result.errors, "observations": result.observations,
    }
    if result.success:
        return IssueResult(
            status=FiscalStatus.AUTHORIZED,
            message="; ".join(result.observations) or "Autorizado por ARCA.",
            authorization_code=result.cae, authorization_expiry=result.cae_fch_vto,
            qr_data=arca_qr(issuer, doc, req, result.cae),
            request=request_log, response=response_log,
        )
    messages = result.errors + result.observations
    return IssueResult(
        status=FiscalStatus.REJECTED,
        error_code=(result.errors[0].split("]")[0].lstrip("[") if result.errors else "rechazo"),
        message="; ".join(messages) or "ARCA rechazó el comprobante.",
        request=request_log, response=response_log,
    )
