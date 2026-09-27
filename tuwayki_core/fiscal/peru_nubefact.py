"""Perú: comprobantes electrónicos con Nubefact (OSE autorizado por SUNAT).

Basado en el "Manual de integración — archivo JSON" de Nubefact (v3.0) y sus
ejemplos oficiales. Cada empresa emite con su propia RUTA + TOKEN (cuenta de
Nubefact asociada a SU RUC); este módulo no guarda nada: recibe el
comprobante y las credenciales, y devuelve un ``IssueResult``.

Operaciones: ``issue`` (generar_comprobante), ``query`` (consultar_comprobante),
``void`` (generar_anulacion) y ``query_void`` (consultar_anulacion).
``verify_credentials`` prueba la ruta y el token de una empresa sin emitir.

Reintentos seguros: se envía ``codigo_unico``; si Nubefact responde que el
documento ya existe (código 23, p. ej. tras un timeout), se consulta y se
devuelve su estado real en vez de fallar o duplicar.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any

import httpx

from tuwayki_core.fiscal.amounts import compute_totals
from tuwayki_core.fiscal.models import (
    Document,
    DocumentType,
    FiscalStatus,
    IssueResult,
    TaxCategory,
    Totals,
)
from tuwayki_core.utils.fiscal_validators import validate_nubefact_url, validate_ruc

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30

DOC_TYPE_CODE: dict[DocumentType, int] = {
    DocumentType.INVOICE: 1,
    DocumentType.RECEIPT: 2,
    DocumentType.CREDIT_NOTE: 3,
    DocumentType.DEBIT_NOTE: 4,
}
# Código SUNAT del tipo de comprobante (el que va en el QR).
SUNAT_DOC_CODE: dict[DocumentType, str] = {
    DocumentType.INVOICE: "01",
    DocumentType.RECEIPT: "03",
    DocumentType.CREDIT_NOTE: "07",
    DocumentType.DEBIT_NOTE: "08",
}
CURRENCY_CODE = {"PEN": 1, "USD": 2, "EUR": 3, "GBP": 4}
IGV_TYPE: dict[TaxCategory, int] = {
    TaxCategory.TAXED: 1,        # Gravado - Operación Onerosa
    TaxCategory.EXEMPT: 8,       # Exonerado - Operación Onerosa
    TaxCategory.UNAFFECTED: 9,   # Inafecto - Operación Onerosa
}
BUYER_DOC_TYPES: dict[str, str] = {
    "6": "RUC",
    "1": "DNI",
    "-": "Varios (sin documento)",
    "4": "Carné de extranjería",
    "7": "Pasaporte",
    "A": "Cédula diplomática",
    "B": "Documento del país de residencia (no domiciliado)",
    "0": "No domiciliado, sin RUC (exportación)",
    "G": "Salvoconducto",
}
# Desde este monto la boleta tiene que identificar al cliente.
RECEIPT_ID_THRESHOLD = Decimal("700.00")
CREDIT_NOTE_REASONS = range(1, 14)   # tipo_de_nota_de_credito 1..13
DEBIT_NOTE_REASONS = range(1, 6)     # tipo_de_nota_de_debito 1..5

# Códigos de error de Nubefact (manual, "Manejo de errores").
ERR_ALREADY_EXISTS = 23
ERR_NOT_FOUND = 24
_ACCOUNT_ERRORS = {
    10: "Nubefact rechazó el token: revísalo en la configuración de facturación.",
    11: "La ruta de Nubefact no es correcta: revísala en la configuración de facturación.",
    12: "Solicitud mal formada (Content-Type).",
    50: "La cuenta de Nubefact está suspendida.",
    51: "La cuenta de Nubefact está suspendida por falta de pago.",
}
_DATA_ERRORS = {20, 21, 22}

_SERIES_RE = re.compile(r"^[A-Z0-9]{4}$")
_TOKEN_RE = re.compile(r'token\s*=\s*"[^"]*"', re.IGNORECASE)


def _num(value: Decimal) -> str:
    """Número como texto sin notación científica ni ceros de más."""
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _money(value: Decimal) -> str:
    return format(Decimal(value).quantize(Decimal("0.01")), "f")


def _clip(text: str, limit: int) -> str:
    return (text or "").replace('"', "'").strip()[:limit]


def _sanitize(text: str) -> str:
    return _TOKEN_RE.sub('token="***"', text or "")


def _series_prefix(doc_type: DocumentType) -> str:
    return "F" if doc_type == DocumentType.INVOICE else "B"


# ── Validación previa (errores claros antes de gastar una llamada) ──────────

def validate_document(doc: Document, totals: Totals | None = None) -> list[str]:
    """Errores del comprobante, en español, listos para mostrar. Vacío = OK."""
    errors: list[str] = []
    if doc.country != "PE":
        errors.append("Este conector es para comprobantes de Perú.")
    if doc.currency not in CURRENCY_CODE:
        errors.append(f"Moneda no soportada por Nubefact: {doc.currency}.")
    series = (doc.series or "").upper()
    if not _SERIES_RE.match(series):
        errors.append("La serie debe tener 4 caracteres (ej. B001 o F001).")
    if not 1 <= int(doc.number or 0) <= 99_999_999:
        errors.append("El número del comprobante debe estar entre 1 y 99999999.")

    # Factura/boleta y sus notas: la serie empieza con F o con B.
    base_type = doc.doc_type
    if doc.doc_type in (DocumentType.CREDIT_NOTE, DocumentType.DEBIT_NOTE):
        if doc.reference is None:
            errors.append("La nota debe indicar el comprobante que modifica.")
        else:
            base_type = doc.reference.doc_type
            reasons = (
                CREDIT_NOTE_REASONS if doc.doc_type == DocumentType.CREDIT_NOTE
                else DEBIT_NOTE_REASONS
            )
            if doc.reference.reason_code not in reasons:
                errors.append("El motivo de la nota no es válido.")
    if _SERIES_RE.match(series) and not series.startswith(_series_prefix(base_type)):
        errors.append(
            f"La serie de una {'factura' if base_type == DocumentType.INVOICE else 'boleta'} "
            f"(y sus notas) empieza con {_series_prefix(base_type)}."
        )

    buyer = doc.buyer
    if base_type == DocumentType.INVOICE:
        if buyer is None or buyer.doc_type != "6":
            errors.append("La factura necesita el RUC del cliente.")
        else:
            ok, msg = validate_ruc(buyer.doc_number)
            if not ok:
                errors.append(f"RUC del cliente: {msg}")
            if not (buyer.name or "").strip():
                errors.append("La factura necesita la razón social del cliente.")
            if not (buyer.address or "").strip():
                errors.append("La factura necesita la dirección del cliente.")
    elif buyer is not None:
        if buyer.doc_type not in BUYER_DOC_TYPES:
            errors.append(f"Tipo de documento del cliente no válido: {buyer.doc_type}.")
        elif buyer.doc_type == "1" and not re.fullmatch(r"\d{8}", buyer.doc_number or ""):
            errors.append("El DNI debe tener 8 dígitos.")
        elif buyer.doc_type == "6":
            ok, msg = validate_ruc(buyer.doc_number)
            if not ok:
                errors.append(f"RUC del cliente: {msg}")

    if not doc.lines:
        errors.append("El comprobante no tiene productos.")
    if not errors:
        try:
            totals = totals or compute_totals(doc.lines, doc.tax_rate, doc.global_discount)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            sin_documento = buyer is None or buyer.doc_type == "-"
            if (
                base_type == DocumentType.RECEIPT
                and doc.currency == "PEN"
                and sin_documento
                and totals.total >= RECEIPT_ID_THRESHOLD
            ):
                errors.append(
                    f"Desde S/ {RECEIPT_ID_THRESHOLD} la boleta necesita el DNI "
                    "(u otro documento) del cliente."
                )
    return errors


# ── Armado del JSON ───────────────────────────────────────────────────────

def unique_code(doc: Document) -> str:
    """Código único del comprobante para que Nubefact no lo duplique."""
    return f"{DOC_TYPE_CODE[doc.doc_type]}-{doc.series.upper()}-{doc.number}"[:20]


def build_payload(doc: Document, totals: Totals) -> dict[str, Any]:
    buyer = doc.buyer
    doc_type = buyer.doc_type if buyer else "-"
    doc_number = (buyer.doc_number if buyer else "") or "-"
    name = (buyer.name if buyer else "") or "CLIENTE VARIOS"

    items = []
    for line, am in zip(doc.lines, totals.lines):
        items.append({
            "unidad_de_medida": line.unit or "NIU",
            "codigo": _clip(line.code, 250),
            "descripcion": _clip(line.description, 250) or "Producto",
            "cantidad": _num(line.quantity),
            "valor_unitario": _num(am.unit_value),
            "precio_unitario": _num(line.unit_price),
            "descuento": "",
            "subtotal": _money(am.base),
            "tipo_de_igv": IGV_TYPE[line.category],
            "igv": _money(am.tax),
            "total": _money(am.total),
            "anticipo_regularizacion": False,
        })

    payload: dict[str, Any] = {
        "operacion": "generar_comprobante",
        "tipo_de_comprobante": DOC_TYPE_CODE[doc.doc_type],
        "serie": doc.series.upper(),
        "numero": int(doc.number),
        "sunat_transaction": 1,                       # venta interna
        "cliente_tipo_de_documento": doc_type,
        "cliente_numero_de_documento": _clip(doc_number, 15),
        "cliente_denominacion": _clip(name, 100),
        "cliente_direccion": _clip(buyer.address if buyer else "", 100),
        "cliente_email": _clip(buyer.email if buyer else "", 250),
        "fecha_de_emision": doc.issue_date.strftime("%d-%m-%Y"),
        "moneda": CURRENCY_CODE[doc.currency],
        "tipo_de_cambio": _num(doc.exchange_rate) if doc.exchange_rate else "",
        "porcentaje_de_igv": _money(Decimal(str(doc.tax_rate))),
        "descuento_global": _money(totals.discount_base) if totals.discount_base else "",
        "total_descuento": _money(totals.discount_base) if totals.discount_base else "",
        "total_gravada": _money(totals.taxed) if totals.taxed else "",
        "total_inafecta": _money(totals.unaffected) if totals.unaffected else "",
        "total_exonerada": _money(totals.exempt) if totals.exempt else "",
        "total_igv": _money(totals.tax),
        "total": _money(totals.total),
        "observaciones": _clip(doc.notes, 1000),
        "enviar_automaticamente_a_la_sunat": True,
        "enviar_automaticamente_al_cliente": False,
        "codigo_unico": unique_code(doc),
        "formato_de_pdf": "TICKET",
        "items": items,
    }
    if doc.reference is not None:
        ref = doc.reference
        payload["documento_que_se_modifica_tipo"] = DOC_TYPE_CODE[ref.doc_type]
        payload["documento_que_se_modifica_serie"] = ref.series.upper()
        payload["documento_que_se_modifica_numero"] = int(ref.number)
        key = (
            "tipo_de_nota_de_credito" if doc.doc_type == DocumentType.CREDIT_NOTE
            else "tipo_de_nota_de_debito"
        )
        payload[key] = int(ref.reason_code)
    return payload


def sunat_qr(
    issuer_ruc: str, doc: Document, totals: Totals, hash_code: str = ""
) -> str:
    """Cadena del QR según SUNAT, por si Nubefact no la devuelve.

    RUC | TIPO | SERIE | NÚMERO | IGV | TOTAL | FECHA | TIPO DOC. | NÚM. DOC. | HASH |
    """
    buyer = doc.buyer
    parts = [
        issuer_ruc,
        SUNAT_DOC_CODE[doc.doc_type],
        doc.series.upper(),
        str(doc.number),
        _money(totals.tax),
        _money(totals.total),
        doc.issue_date.strftime("%Y-%m-%d"),     # mismo formato que el XML (UBL)
        buyer.doc_type if buyer else "-",
        (buyer.doc_number if buyer else "") or "-",
        hash_code,
    ]
    return "|".join(parts) + "|"


# ── Respuestas ────────────────────────────────────────────────────────────

def parse_document_response(data: dict[str, Any]) -> IssueResult:
    """Respuesta de generar/consultar comprobante → IssueResult."""
    common = dict(
        authorization_code=data.get("codigo_hash") or "",
        qr_data=data.get("cadena_para_codigo_qr") or "",
        pdf_url=data.get("enlace_del_pdf") or "",
        xml_url=data.get("enlace_del_xml") or "",
        cdr_url=data.get("enlace_del_cdr") or "",
        voided=bool(data.get("anulado")),
        response=data,
    )
    if data.get("aceptada_por_sunat"):
        return IssueResult(
            status=FiscalStatus.AUTHORIZED,
            message=data.get("sunat_description") or "Aceptado por SUNAT.",
            **common,
        )
    code = str(data.get("sunat_responsecode") or "").strip()
    if code and code != "0":
        # Códigos de rechazo de SUNAT (2000-3999): hay que corregir el comprobante.
        return IssueResult(
            status=FiscalStatus.REJECTED,
            error_code=code,
            message=data.get("sunat_description") or data.get("sunat_note")
            or f"SUNAT rechazó el comprobante (código {code}).",
            **common,
        )
    # Nubefact lo generó pero SUNAT todavía no respondió (o no se pudo enviar):
    # queda para consultar más tarde.
    detail = data.get("sunat_soap_error") or data.get("sunat_description") or ""
    return IssueResult(
        status=FiscalStatus.PENDING,
        message=detail or "Enviado. Falta la respuesta de SUNAT: se consulta más tarde.",
        **common,
    )


def parse_error_response(status_code: int, data: Any, text: str) -> IssueResult:
    """Respuesta con error de Nubefact ({"errors": ..., "codigo": ...})."""
    code = None
    message = ""
    if isinstance(data, dict):
        message = _sanitize(str(data.get("errors") or ""))[:500]
        try:
            code = int(data.get("codigo")) if data.get("codigo") is not None else None
        except (TypeError, ValueError):
            code = None
        data = {**data, "errors": message}
    if code in _ACCOUNT_ERRORS:
        return IssueResult(
            status=FiscalStatus.ERROR, error_code=str(code),
            message=_ACCOUNT_ERRORS[code], response=data if isinstance(data, dict) else {},
        )
    if code in _DATA_ERRORS or (code is None and 400 <= status_code < 500):
        return IssueResult(
            status=FiscalStatus.REJECTED, error_code=str(code or status_code),
            message=message or f"Nubefact rechazó la solicitud (HTTP {status_code}).",
            response=data if isinstance(data, dict) else _sanitize(text)[:500],
        )
    return IssueResult(
        status=FiscalStatus.ERROR, error_code=str(code or status_code),
        message=message or f"Nubefact no pudo completar la operación (HTTP {status_code}).",
        response=data if isinstance(data, dict) else _sanitize(text)[:500],
    )


# ── Cliente ───────────────────────────────────────────────────────────────

class NubefactClient:
    """Cliente de la API JSON de Nubefact para UNA empresa (su RUTA y su TOKEN)."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = (url or "").strip()
        self._token = (token or "").strip()
        self.timeout = timeout
        self._transport = transport

    async def _post(self, payload: dict[str, Any]) -> tuple[int, Any, str]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            response = await client.post(
                self.url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f'Token token="{self._token}"',
                },
            )
        try:
            data = response.json()
        except ValueError:
            data = None
        return response.status_code, data, response.text

    async def _call(self, payload: dict[str, Any]) -> tuple[IssueResult | None, dict[str, Any] | None]:
        """(error, data): error si no hubo una respuesta 200 con JSON."""
        if not self.url or not self._token:
            return IssueResult(
                status=FiscalStatus.ERROR, error_code="config",
                message="Falta configurar la ruta y el token de Nubefact.",
            ), None
        try:
            status_code, data, text = await self._post(payload)
        except httpx.TimeoutException:
            return IssueResult(
                status=FiscalStatus.ERROR, error_code="timeout",
                message=f"Nubefact no respondió en {self.timeout:g} s.",
            ), None
        except httpx.HTTPError as exc:
            return IssueResult(
                status=FiscalStatus.ERROR, error_code="conexion",
                message=f"No se pudo conectar con Nubefact: {_sanitize(str(exc))[:200]}",
            ), None
        if status_code == 200 and isinstance(data, dict) and "errors" not in data:
            return None, data
        return parse_error_response(status_code, data, text), None

    async def issue(self, doc: Document) -> IssueResult:
        """Emite el comprobante. Valida antes de llamar."""
        try:
            totals = compute_totals(doc.lines, doc.tax_rate, doc.global_discount)
        except ValueError as exc:
            return IssueResult(status=FiscalStatus.REJECTED, error_code="validacion", message=str(exc))
        errors = validate_document(doc, totals)
        if errors:
            return IssueResult(
                status=FiscalStatus.REJECTED, error_code="validacion",
                message=" ".join(errors),
            )
        payload = build_payload(doc, totals)
        error, data = await self._call(payload)
        if error is not None:
            if error.error_code == str(ERR_ALREADY_EXISTS):
                # Ya se había emitido (reintento tras un corte): su estado real.
                logger.info("Nubefact: %s ya existía, se consulta.", unique_code(doc))
                found = await self.query(doc.doc_type, doc.series, doc.number)
                found.request = payload
                return found
            error.request = payload
            return error
        result = parse_document_response(data)
        result.request = payload
        return result

    async def query(self, doc_type: DocumentType, series: str, number: int) -> IssueResult:
        """Estado actual en SUNAT (y si fue anulado)."""
        payload = {
            "operacion": "consultar_comprobante",
            "tipo_de_comprobante": DOC_TYPE_CODE[doc_type],
            "serie": series.upper(),
            "numero": int(number),
        }
        error, data = await self._call(payload)
        if error is not None:
            if error.error_code == str(ERR_NOT_FOUND):
                error.status = FiscalStatus.ERROR
                error.message = "Nubefact no tiene ese comprobante."
            error.request = payload
            return error
        result = parse_document_response(data)
        result.request = payload
        return result

    async def void(
        self, doc_type: DocumentType, series: str, number: int, reason: str
    ) -> IssueResult:
        """Comunicación de baja. SUNAT la resuelve después: suele volver PENDIENTE."""
        payload = {
            "operacion": "generar_anulacion",
            "tipo_de_comprobante": DOC_TYPE_CODE[doc_type],
            "serie": series.upper(),
            "numero": int(number),
            "motivo": _clip(reason, 100) or "ANULACION DE LA OPERACION",
            "codigo_unico": f"BAJA-{DOC_TYPE_CODE[doc_type]}-{series.upper()}-{number}",
        }
        error, data = await self._call(payload)
        if error is not None:
            error.request = payload
            return error
        result = parse_document_response(data)
        result.request = payload
        if data.get("sunat_ticket_numero"):
            result.authorization_code = str(data["sunat_ticket_numero"])
        return result

    async def query_void(self, doc_type: DocumentType, series: str, number: int) -> IssueResult:
        payload = {
            "operacion": "consultar_anulacion",
            "tipo_de_comprobante": DOC_TYPE_CODE[doc_type],
            "serie": series.upper(),
            "numero": int(number),
        }
        error, data = await self._call(payload)
        if error is not None:
            error.request = payload
            return error
        result = parse_document_response(data)
        result.request = payload
        return result


# ── Verificación de credenciales ──────────────────────────────────────────

# Respuestas que indican que la ruta o el token no sirven (o que no hubo
# conexión). Cualquier otra respuesta de Nubefact prueba que los aceptó.
_CREDENTIAL_ERRORS = {"10", "11", "12", "50", "51", "timeout", "conexion", "config"}


async def verify_credentials(
    url: str,
    token: str,
    series: str = "B001",
    *,
    timeout: float = TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bool, str]:
    """Prueba la ruta y el token de una empresa sin emitir nada.

    Consulta un comprobante que no existe: si Nubefact contesta "no existe"
    (código 24), la ruta y el token son válidos. Devuelve (ok, mensaje para
    mostrar).
    """
    ok, error = validate_nubefact_url(url)
    if not ok:
        return False, error
    if not (token or "").strip():
        return False, "Falta el token de Nubefact."
    client = NubefactClient(url, token, timeout=timeout, transport=transport)
    try:
        result = await client.query(
            DocumentType.RECEIPT, (series or "B001").strip().upper(), 99_999_999
        )
    except Exception as exc:  # p. ej. una ruta que httpx no puede usar
        detail = _sanitize(str(exc))[:200]
        logger.warning("Verificación de Nubefact falló: %s", detail)
        return False, f"No se pudo conectar con Nubefact: {detail}"
    code = str(result.error_code or "")
    # Los códigos de Nubefact tienen 2 dígitos; 3 dígitos es un error HTTP
    # (p. ej. 404 si la ruta apunta a otro servidor).
    if code in _CREDENTIAL_ERRORS or (code.isdigit() and int(code) >= 100):
        return False, result.message or "Nubefact rechazó la ruta o el token."
    return True, "Nubefact aceptó la ruta y el token de la empresa."
