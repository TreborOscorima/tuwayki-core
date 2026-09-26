"""Perú: conector de Nubefact con respuestas simuladas según su manual JSON."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from tuwayki_core.fiscal import peru_nubefact as nf
from tuwayki_core.fiscal.amounts import compute_totals
from tuwayki_core.fiscal.models import (
    Buyer,
    Document,
    DocumentType,
    FiscalStatus,
    Line,
    Reference,
)

D = Decimal
URL = "https://api.nubefact.com/api/v1/ruta-de-prueba"
TOKEN = "token-super-secreto-123"
RUC_CLIENTE = "20600695771"   # RUC del ejemplo del manual (válido)


def _boleta(**kw) -> Document:
    base = dict(
        country="PE", doc_type=DocumentType.RECEIPT, series="B001", number=15,
        issue_date=date(2026, 9, 26), currency="PEN", tax_rate=D("10.5"),
        lines=[Line("Lomo saltado", D("2"), D("32.00"), code="LS1"),
               Line("Chicha morada", D("1"), D("8.50"))],
    )
    base.update(kw)
    return Document(**base)


def _factura(**kw) -> Document:
    kw.setdefault("buyer", Buyer("6", RUC_CLIENTE, "NUBEFACT SA", "CALLE LIBERTAD 116 MIRAFLORES"))
    return _boleta(doc_type=DocumentType.INVOICE, series="F001", tax_rate=D("18"), **kw)


def _run(coro):
    return asyncio.run(coro)


class _Nubefact:
    """Servidor falso: responde en orden y guarda lo que recibió."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    def client(self) -> nf.NubefactClient:
        return nf.NubefactClient(URL, TOKEN, transport=httpx.MockTransport(self))

    def payload(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


ACEPTADA = {
    "tipo_de_comprobante": 2, "serie": "B001", "numero": 15,
    "enlace": "https://www.nubefact.com/cpe/abc", "enlace_del_pdf": "https://x/abc.pdf",
    "enlace_del_xml": "https://x/abc.xml", "enlace_del_cdr": "https://x/abc.cdr",
    "aceptada_por_sunat": True, "sunat_description": "La Boleta numero B001-15, ha sido aceptada",
    "sunat_note": None, "sunat_responsecode": "0", "sunat_soap_error": "",
    "cadena_para_codigo_qr": "20111111111 | 03 | B001 | 000015 | ...",
    "codigo_hash": "xMLFMnbgp1/bHEy572RKRTE9hPY=",
}


# ── Validación ──────────────────────────────────────────────────────────

def test_boleta_valida():
    assert nf.validate_document(_boleta()) == []


def test_factura_exige_ruc_razon_social_y_direccion():
    errs = nf.validate_document(_boleta(doc_type=DocumentType.INVOICE, series="F001"))
    assert any("RUC" in e for e in errs)
    errs = nf.validate_document(_factura(buyer=Buyer("6", "20600695771", "", "")))
    assert any("razón social" in e for e in errs) and any("dirección" in e for e in errs)
    errs = nf.validate_document(_factura(buyer=Buyer("6", "20600695772", "X", "Y")))
    assert any("RUC del cliente" in e for e in errs)


def test_boleta_desde_700_soles_necesita_documento():
    grande = [Line("Banquete", D("1"), D("700.00"))]
    assert any("DNI" in e for e in nf.validate_document(_boleta(lines=grande)))
    assert nf.validate_document(_boleta(lines=[Line("x", D("1"), D("699.99"))])) == []
    con_dni = _boleta(lines=grande, buyer=Buyer("1", "41920371", "JORGE LOPEZ"))
    assert nf.validate_document(con_dni) == []
    assert any("8 dígitos" in e for e in nf.validate_document(
        _boleta(buyer=Buyer("1", "4192037", "X"))))


@pytest.mark.parametrize("series", ["F001", "B01", "b0011"])
def test_serie_de_boleta(series):
    assert nf.validate_document(_boleta(series=series))


def test_nota_de_credito_necesita_referencia_y_serie_del_original():
    nota = _boleta(doc_type=DocumentType.CREDIT_NOTE)
    assert any("modifica" in e for e in nf.validate_document(nota))
    ok = _boleta(doc_type=DocumentType.CREDIT_NOTE,
                 reference=Reference(DocumentType.RECEIPT, "B001", 15, 1, "Anulación"))
    assert nf.validate_document(ok) == []
    mala = _boleta(doc_type=DocumentType.CREDIT_NOTE, series="F001",
                   reference=Reference(DocumentType.RECEIPT, "B001", 15, 1))
    assert nf.validate_document(mala)


# ── JSON enviado ────────────────────────────────────────────────────────

def test_payload_de_boleta_sin_cliente():
    doc = _boleta(global_discount=D("7.00"))
    p = nf.build_payload(doc, compute_totals(doc.lines, doc.tax_rate, doc.global_discount))
    assert p["operacion"] == "generar_comprobante"
    assert (p["tipo_de_comprobante"], p["serie"], p["numero"]) == (2, "B001", 15)
    assert p["fecha_de_emision"] == "26-09-2026"           # DD-MM-AAAA (manual)
    assert p["cliente_tipo_de_documento"] == "-"           # varios
    assert p["cliente_denominacion"] == "CLIENTE VARIOS"
    assert p["porcentaje_de_igv"] == "10.50"
    assert p["moneda"] == 1
    assert p["codigo_unico"] == "2-B001-15"
    assert p["descuento_global"] == p["total_descuento"] == "6.33"   # 7 / 1.105
    total_items = sum(D(it["total"]) for it in p["items"])
    assert D(p["total"]) == total_items - D("7.00")
    assert D(p["total_gravada"]) + D(p["total_igv"]) == D(p["total"])
    it = p["items"][0]
    assert (it["cantidad"], it["precio_unitario"], it["tipo_de_igv"]) == ("2", "32", 1)
    assert D(it["subtotal"]) + D(it["igv"]) == D(it["total"]) == D("64.00")


def test_payload_de_nota_de_credito():
    doc = _boleta(doc_type=DocumentType.CREDIT_NOTE,
                  reference=Reference(DocumentType.RECEIPT, "B001", 9, 6, "Devolución"))
    p = nf.build_payload(doc, compute_totals(doc.lines, doc.tax_rate))
    assert p["tipo_de_comprobante"] == 3
    assert (p["documento_que_se_modifica_tipo"], p["documento_que_se_modifica_serie"],
            p["documento_que_se_modifica_numero"], p["tipo_de_nota_de_credito"]) == (2, "B001", 9, 6)


def test_qr_de_respaldo():
    doc = _factura()
    t = compute_totals(doc.lines, doc.tax_rate)
    qr = nf.sunat_qr("20111111111", doc, t, "HASH")
    assert qr == f"20111111111|01|F001|15|{t.tax}|{t.total}|2026-09-26|6|{RUC_CLIENTE}|HASH|"


# ── Respuestas ──────────────────────────────────────────────────────────

def test_aceptada():
    srv = _Nubefact((200, ACEPTADA))
    r = _run(srv.client().issue(_boleta()))
    assert r.status == FiscalStatus.AUTHORIZED and r.ok
    assert r.authorization_code == ACEPTADA["codigo_hash"]
    assert r.qr_data.startswith("20111111111") and r.pdf_url.endswith(".pdf")
    req = srv.requests[0]
    assert req.headers["Authorization"] == f'Token token="{TOKEN}"'
    assert r.request["serie"] == "B001"


def test_rechazada_por_sunat():
    body = dict(ACEPTADA, aceptada_por_sunat=False, sunat_responsecode="2800",
                sunat_description="El dato ingresado en el tipo de documento no es válido")
    r = _run(_Nubefact((200, body)).client().issue(_boleta()))
    assert r.status == FiscalStatus.REJECTED and not r.retryable
    assert r.error_code == "2800" and "tipo de documento" in r.message


def test_sin_respuesta_de_sunat_queda_pendiente():
    body = dict(ACEPTADA, aceptada_por_sunat=False, sunat_responsecode=None,
                sunat_description=None, sunat_soap_error="")
    r = _run(_Nubefact((200, body)).client().issue(_boleta()))
    assert r.status == FiscalStatus.PENDING and r.retryable


def test_ya_existe_se_consulta_en_vez_de_duplicar():
    srv = _Nubefact(
        (400, {"errors": "Este documento ya existe en NubeFacT", "codigo": 23}),
        (200, dict(ACEPTADA, anulado=False)),
    )
    r = _run(srv.client().issue(_boleta()))
    assert r.status == FiscalStatus.AUTHORIZED
    assert srv.payload(1) == {"operacion": "consultar_comprobante",
                              "tipo_de_comprobante": 2, "serie": "B001", "numero": 15}
    assert r.request["operacion"] == "generar_comprobante"


def test_formato_invalido_es_rechazo():
    r = _run(_Nubefact((400, {"errors": "El archivo enviado no cumple con el formato establecido",
                              "codigo": 20})).client().issue(_boleta()))
    assert r.status == FiscalStatus.REJECTED and r.error_code == "20"


@pytest.mark.parametrize("code, texto", [(10, "token"), (11, "ruta"), (51, "falta de pago")])
def test_problemas_de_cuenta_se_reintentan(code, texto):
    r = _run(_Nubefact((401, {"errors": "x", "codigo": code})).client().issue(_boleta()))
    assert r.status == FiscalStatus.ERROR and r.retryable and texto in r.message


def test_error_del_servidor_y_timeout():
    r = _run(_Nubefact((500, {"errors": "Error interno desconocido", "codigo": 40}))
             .client().issue(_boleta()))
    assert r.status == FiscalStatus.ERROR and r.retryable
    r = _run(_Nubefact(httpx.ReadTimeout("lento")).client().issue(_boleta()))
    assert (r.status, r.error_code) == (FiscalStatus.ERROR, "timeout")


def test_validacion_no_llama_a_nubefact():
    srv = _Nubefact()
    r = _run(srv.client().issue(_boleta(series="F001")))
    assert r.status == FiscalStatus.REJECTED and r.error_code == "validacion"
    assert srv.requests == []


def test_sin_credenciales():
    r = _run(nf.NubefactClient("", "").issue(_boleta()))
    assert r.status == FiscalStatus.ERROR and "ruta y el token" in r.message


def test_el_token_nunca_aparece_en_el_resultado():
    for resp in [(200, ACEPTADA), (500, {"errors": f'token="{TOKEN}"', "codigo": 40}),
                 httpx.ConnectError(f'fallo token="{TOKEN}"')]:
        r = _run(_Nubefact(resp).client().issue(_boleta()))
        assert TOKEN not in json.dumps(
            [r.message, r.request, r.response if isinstance(r.response, dict) else str(r.response)],
            default=str,
        )


def test_anulacion_queda_pendiente_con_ticket():
    srv = _Nubefact((200, {
        "numero": 1, "enlace": "https://www.nubefact.com/anulacion/b7fc",
        "sunat_ticket_numero": "1494358661332", "aceptada_por_sunat": False,
        "sunat_description": None, "sunat_note": None, "sunat_responsecode": None,
        "sunat_soap_error": "",
    }))
    r = _run(srv.client().void(DocumentType.RECEIPT, "B001", 15, "Error del sistema"))
    assert r.status == FiscalStatus.PENDING and r.authorization_code == "1494358661332"
    assert srv.payload()["operacion"] == "generar_anulacion"
    assert srv.payload()["motivo"] == "Error del sistema"
