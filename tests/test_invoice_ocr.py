"""Lector de comprobantes con IA (Gemini): las pruebas de SHOP y LIFE juntas.

Sin red: el cliente HTTP es falso (saturado, demasiados pedidos, sin respuesta)
o un `httpx.MockTransport`. Traídas el 2026-11-20 de
`Sistema-de-Ventas/tests/test_invoice_ocr_service.py`,
`Sistema-de-Ventas/tests/test_tanda4_subidas_y_logs.py` y
`Sistema-Gestion-Clinica/tests/test_compras_ocr.py`.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from tuwayki_core import invoice_ocr as ocr

_PRIMARIO = "gemini-flash-lite-latest"
_LEIDO = {"supplier_name": "Distribuidora JM"}
CLAVE = "AIzaSyFALSA_de_prueba_0123456789abcdefg"


def _correr(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    """Que una variable del equipo no cambie la cadena de modelos esperada."""
    for var in ("GEMINI_API_KEY", "GEMINI_OCR_MODEL", "GEMINI_OCR_FALLBACKS"):
        monkeypatch.delenv(var, raising=False)


# ── JSON de la respuesta ──────────────────────────────────────────────────────

def test_parse_json_plain():
    assert ocr._parse_json_text('{"a": 1}') == {"a": 1}


def test_parse_json_with_markdown_fence():
    text = '```json\n{"a": 1, "b": "x"}\n```'
    assert ocr._parse_json_text(text) == {"a": 1, "b": "x"}


def test_parse_json_with_surrounding_text():
    text = 'Claro, aquí está: {"a": 1} listo.'
    assert ocr._parse_json_text(text) == {"a": 1}


# ── Normalización y avisos ────────────────────────────────────────────────────

def test_normalize_extraction_maps_fields_and_filters_empty_items():
    raw = {
        "supplier_name": "  SHANTALA S.A. ",
        "supplier_tax_id": "30-71127985-3",
        "doc_type": "REMITO",  # no válido → ""
        "number": "0001-7280",
        "issue_date": "2025-03-26",
        "currency": "ars",
        "items": [
            {"description": "Masala Rosa", "quantity": "24", "unit_cost": "1500.5",
             "unit": "caja", "sale_price": None, "barcode": ""},
            {"description": "", "barcode": ""},  # se descarta
            {"description": "Sahumerio", "quantity": 12, "unit_cost": 800,
             "sale_price": "1200"},
        ],
        "warnings": ["Cantidad de la fila 1 poco legible", ""],
    }
    result = ocr._normalize_extraction(raw)
    assert result.supplier_name == "SHANTALA S.A."
    assert result.supplier_tax_id == "30-71127985-3"
    assert result.doc_type == ""  # REMITO no es boleta/factura
    assert result.currency == "ARS"
    assert len(result.items) == 2
    assert result.items[0].description == "Masala Rosa"
    assert result.items[0].quantity == 24.0
    assert result.items[0].unit == "caja"
    assert result.items[0].sale_price is None
    assert result.items[1].sale_price == 1200.0
    # La warning vacía se descarta y la de la IA se conserva.
    assert "Cantidad de la fila 1 poco legible" in result.warnings
    assert "" not in result.warnings
    # doc_type quedó vacío (REMITO) → aviso estándar en español claro, sin jerga.
    assert any("tipo de comprobante" in w for w in result.warnings)
    assert not any(ocr._mentions_internal_field(w) for w in result.warnings)


@pytest.mark.parametrize("leido,tipo", [
    ("FACTURA ELECTRÓNICA", "factura"),
    ("Factura B", "factura_b"),
    ("factura tipo a", "factura_a"),
    ("Boleta de venta", "boleta"),
    ("presupuesto", ""),
])
def test_tipo_de_comprobante(leido, tipo):
    assert ocr._normalize_extraction({"doc_type": leido, "items": []}).doc_type == tipo


def test_warnings_are_plain_language_no_internal_fields():
    """Sin tipo/fecha/ID → avisos en español claro; se filtra jerga de la IA."""
    raw = {
        "supplier_name": "Regalería Sheyla",
        "supplier_tax_id": "",   # sin ID → aviso estándar con la etiqueta del país
        "doc_type": "",           # sin tipo → aviso estándar
        "issue_date": "",         # sin fecha → aviso estándar
        "items": [{"description": "Satya", "quantity": 12, "unit_cost": 680}],
        "warnings": [
            "doc_type se dejó vacío",              # jerga → se descarta
            "La cantidad de Satya es poco clara",  # válida → se conserva
        ],
    }
    result = ocr._normalize_extraction(raw, tax_id_label="CUIT")
    assert not any(ocr._mentions_internal_field(w) for w in result.warnings)
    assert "doc_type se dejó vacío" not in result.warnings
    assert "La cantidad de Satya es poco clara" in result.warnings
    assert any("tipo de comprobante" in w for w in result.warnings)
    assert any("fecha de emisión" in w.lower() for w in result.warnings)
    assert any("CUIT" in w for w in result.warnings)


def test_line_total_mismatch_generates_warning():
    """Cantidad x costo != total impreso del renglón → aviso (caza el 247 vs 24)."""
    raw = {
        "supplier_name": "Regalería Sheyla",
        "doc_type": "boleta",
        "issue_date": "2023-05-03",
        "supplier_tax_id": "x",  # no dispara el aviso de ID
        "items": [
            # La IA leyó 247 pero el total impreso es 18720 (=> 24 x 780).
            {"description": "Chackra", "quantity": 247, "unit_cost": 780,
             "line_total": 18720},
        ],
        "warnings": [],
    }
    result = ocr._normalize_extraction(raw)
    assert result.items[0].line_total == 18720.0
    assert any("Chackra" in w and "18720" in w for w in result.warnings)
    assert not any(ocr._mentions_internal_field(w) for w in result.warnings)


def test_descuadre_con_la_etiqueta_del_pais():
    raw = {
        "supplier_name": "Dental Sur", "doc_type": "FACTURA ELECTRÓNICA", "number": "123",
        "items": [{"description": "Resina", "quantity": 24, "unit_cost": 10, "line_total": 2470}],
    }
    r = ocr._normalize_extraction(raw, tax_id_label="RUC")
    assert r.doc_type == "factura"
    assert any("Resina" in w and "2470" in w for w in r.warnings)
    assert any("RUC" in w for w in r.warnings)


def test_line_total_match_no_warning():
    """Cuando cuadra (24 x 780 = 18720) no se agrega aviso de descuadre."""
    raw = {
        "supplier_name": "Regalería Sheyla",
        "doc_type": "boleta",
        "issue_date": "2023-05-03",
        "supplier_tax_id": "x",
        "items": [
            {"description": "Chackra", "quantity": 24, "unit_cost": 780,
             "line_total": 18720},
        ],
        "warnings": [],
    }
    assert ocr._normalize_extraction(raw).warnings == []


def test_line_total_absent_no_warning():
    """Sin total impreso (line_total null) no se puede cruzar → sin aviso de descuadre."""
    raw = {
        "supplier_name": "X", "doc_type": "boleta", "issue_date": "2023-05-03",
        "supplier_tax_id": "x",
        "items": [{"description": "Y", "quantity": 247, "unit_cost": 780,
                   "line_total": None}],
        "warnings": [],
    }
    assert ocr._normalize_extraction(raw).warnings == []


def test_is_supported_mime():
    assert ocr.is_supported_mime("image/jpeg")
    assert ocr.is_supported_mime("application/pdf")
    assert not ocr.is_supported_mime("text/csv")
    assert not ocr.is_supported_mime("")


# ── extract_invoice: nunca lanza ──────────────────────────────────────────────

def test_extract_invoice_unsupported_mime():
    res = _correr(ocr.extract_invoice(b"x", "text/csv"))
    assert not res.ok
    assert "Formato no soportado" in res.error


def test_extract_invoice_archivo_vacio():
    res = _correr(ocr.extract_invoice(b"", "application/pdf"))
    assert not res.ok and "vacío" in res.error


def test_sin_clave_avisa_y_no_rompe():
    assert ocr.get_provider() is None
    res = _correr(ocr.extract_invoice(b"%PDF-1.4 algo", "application/pdf"))
    assert not res.ok and "GEMINI_API_KEY" in res.error


def test_extract_invoice_happy_path_pdf(monkeypatch):
    class FakeProvider:
        async def extract(self, data, mime, *, prompt):
            assert mime == "application/pdf"
            assert '"CUIT"' in prompt
            return {
                "supplier_name": "Distribuidora JM",
                "supplier_tax_id": "20304050607",
                "doc_type": "factura",
                "number": "000123",
                "issue_date": "2025-08-16",
                "currency": "ARS",
                "items": [
                    {"description": "Almohadas viajeras", "quantity": 6,
                     "unit_cost": 4500, "sale_price": None},
                ],
                "warnings": [],
            }

    monkeypatch.setattr(ocr, "get_provider", lambda api_key=None: FakeProvider())
    res = _correr(ocr.extract_invoice(b"%PDF-1.4 fake", "application/pdf", tax_id_label="CUIT"))
    assert res.ok
    assert res.supplier_name == "Distribuidora JM"
    assert res.doc_type == "factura"
    assert len(res.items) == 1
    assert res.items[0].unit_cost == 4500.0


def test_extract_invoice_sin_productos_avisa(monkeypatch):
    class Vacio:
        async def extract(self, *_a, **_k):
            return {"supplier_name": "X", "items": []}

    monkeypatch.setattr(ocr, "get_provider", lambda api_key=None: Vacio())
    res = _correr(ocr.extract_invoice(b"%PDF-1.4", "application/pdf"))
    assert res.ok
    assert any("No se detectaron productos" in w for w in res.warnings)


def test_extract_invoice_provider_exception_is_caught(monkeypatch):
    class BoomProvider:
        async def extract(self, data, mime, *, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr(ocr, "get_provider", lambda api_key=None: BoomProvider())
    res = _correr(ocr.extract_invoice(b"fake", "application/pdf"))
    assert not res.ok
    assert res.error  # mensaje amigable, no excepción cruda


# ── La clave fuera de la dirección y del log (auditoría de SHOP, Tanda 4) ─────

def test_la_clave_de_gemini_va_en_un_encabezado_y_no_en_la_direccion(monkeypatch):
    vistos: list[httpx.Request] = []

    def manejar(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        cuerpo = {"candidates": [{"content": {"parts": [{"text": '{"items": []}'}]}}]}
        return httpx.Response(200, json=cuerpo)

    real = httpx.AsyncClient
    monkeypatch.setattr(ocr.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(manejar), **kw))
    _correr(ocr.GeminiProvider(api_key=CLAVE).extract(b"%PDF-1.4", "application/pdf", prompt="p"))
    assert vistos, "no se llamó a Gemini"
    assert CLAVE not in str(vistos[0].url) and "key=" not in str(vistos[0].url)
    assert vistos[0].headers["x-goog-api-key"] == CLAVE


def test_un_error_de_gemini_no_deja_la_clave_en_el_log(monkeypatch, caplog):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={CLAVE}"

    class _Proveedor:
        async def extract(self, *_a, **_k):
            request = httpx.Request("POST", url)
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError(f"Client error '400' for url '{url}'", request=request, response=response)

    monkeypatch.setattr(ocr, "get_provider", lambda api_key=None: _Proveedor())
    with caplog.at_level(logging.DEBUG):
        resultado = _correr(ocr.extract_invoice(b"%PDF-1.4", "application/pdf"))
    assert "(400)" in resultado.error
    assert CLAVE not in caplog.text and "key=" not in caplog.text
    assert "HTTP 400 del proveedor de OCR" in caplog.text


# ── Gemini saturado o sin respuesta (2026-11-20) ──────────────────────────────
# Un modelo saturado (503) o que no contesta pasa enseguida al siguiente; solo
# un 429 se reintenta en el mismo modelo; uno retirado (404) se saltea.

class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def _cliente_falso(respuestas: dict, pedidos: list):
    """httpx.AsyncClient falso: responde por modelo según la lista de estados."""
    ok = {"candidates": [{"content": {"parts": [{"text": '{"supplier_name": "Distribuidora JM"}'}]}}]}

    class Cliente:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            modelo = url.split("/models/")[1].split(":")[0]
            pedidos.append(modelo)
            estados = respuestas.get(modelo) or [200]
            estado = estados.pop(0) if len(estados) > 1 else estados[0]
            if estado == "timeout":
                raise httpx.ReadTimeout("sin respuesta")
            return _Resp(estado, ok if estado == 200 else {})

    return Cliente


def _leer(provider=None):
    return _correr((provider or ocr.GeminiProvider("clave")).extract(b"x", "image/png", prompt="p"))


def test_si_el_primario_esta_saturado_lee_con_un_respaldo(monkeypatch):
    pedidos: list = []
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso(
        {_PRIMARIO: [503], "gemini-3.5-flash-lite": [404]}, pedidos))
    assert _leer() == _LEIDO
    # Un 503 no se reintenta en el mismo modelo y uno retirado (404) se saltea.
    assert pedidos == [_PRIMARIO, "gemini-3.5-flash-lite", "gemini-3.5-flash"]


def test_un_429_se_reintenta_en_el_mismo_modelo(monkeypatch):
    pedidos: list = []
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso({_PRIMARIO: [429, 200]}, pedidos))
    monkeypatch.setattr(ocr, "_RETRY_BACKOFF_S", 0)
    assert _leer() == _LEIDO
    assert pedidos == [_PRIMARIO, _PRIMARIO]


def test_todos_saturados_da_error_sin_colgarse(monkeypatch):
    pedidos: list = []
    modelos = [_PRIMARIO, *ocr._FALLBACK_MODELS]
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso({m: [503] for m in modelos}, pedidos))
    with pytest.raises(RuntimeError):
        _leer()
    assert pedidos == modelos


def test_un_modelo_que_no_responde_a_tiempo_pasa_al_siguiente(monkeypatch):
    pedidos: list = []
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso(
        {_PRIMARIO: ["timeout"], "gemini-3.5-flash-lite": [503]}, pedidos))
    assert _leer() == _LEIDO
    assert pedidos == [_PRIMARIO, "gemini-3.5-flash-lite", "gemini-3.5-flash"]


def test_si_ninguno_responde_avisa_timeout(monkeypatch):
    pedidos: list = []
    modelos = [_PRIMARIO, *ocr._FALLBACK_MODELS]
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso({m: ["timeout"] for m in modelos}, pedidos))
    monkeypatch.setattr(ocr, "_TOTAL_MAX_S", 10_000)
    with pytest.raises(httpx.TimeoutException):
        _leer()
    assert pedidos == modelos


def test_con_el_tope_total_vencido_no_prueba_mas_modelos(monkeypatch):
    pedidos: list = []
    modelos = [_PRIMARIO, *ocr._FALLBACK_MODELS]
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso({m: ["timeout"] for m in modelos}, pedidos))
    monkeypatch.setattr(ocr, "_TOTAL_MAX_S", 0)
    with pytest.raises(httpx.TimeoutException):
        _leer()
    assert pedidos == [_PRIMARIO]


def test_extract_invoice_si_nadie_responde_avisa_en_lenguaje_simple(monkeypatch):
    modelos = [_PRIMARIO, *ocr._FALLBACK_MODELS]
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso({m: ["timeout"] for m in modelos}, []))
    monkeypatch.setattr(ocr, "get_provider", lambda api_key=None: ocr.GeminiProvider("clave"))
    res = _correr(ocr.extract_invoice(b"%PDF-1.4 fake", "application/pdf"))
    assert not res.ok
    assert "tardó demasiado" in res.error


# ── Modelos de respaldo configurables (GEMINI_OCR_FALLBACKS) ──────────────────

def test_sin_variable_usa_la_lista_fija():
    assert ocr.GeminiProvider("clave").models() == [_PRIMARIO, *ocr._FALLBACK_MODELS]


@pytest.mark.parametrize("valor", ["", "  ", " , ,"])
def test_variable_vacia_no_deja_sin_respaldos(monkeypatch, valor):
    # docker-compose pasa `${VAR:-}` como texto vacío.
    monkeypatch.setenv("GEMINI_OCR_FALLBACKS", valor)
    assert ocr.GeminiProvider("clave").models() == [_PRIMARIO, *ocr._FALLBACK_MODELS]


def test_la_variable_cambia_los_respaldos_y_su_orden(monkeypatch):
    monkeypatch.setenv("GEMINI_OCR_FALLBACKS", " gemini-4-flash , gemini-3.5-flash,gemini-4-flash,")
    pedidos: list = []
    monkeypatch.setattr(ocr.httpx, "AsyncClient", _cliente_falso(
        {_PRIMARIO: [503], "gemini-4-flash": [404]}, pedidos))
    assert _leer() == _LEIDO
    # En el orden de la variable, sin repetidos ni la lista fija.
    assert pedidos == [_PRIMARIO, "gemini-4-flash", "gemini-3.5-flash"]


def test_el_primario_no_se_repite_en_los_respaldos(monkeypatch):
    monkeypatch.setenv("GEMINI_OCR_FALLBACKS", f"{_PRIMARIO},gemini-3.5-flash")
    assert ocr.GeminiProvider("clave").models() == [_PRIMARIO, "gemini-3.5-flash"]


def test_nombres_raros_se_ignoran_sin_mostrarlos_en_el_log(monkeypatch, caplog):
    # Un nombre va dentro de la dirección: nada de "/", "?" ni "..". Y si alguien
    # pegó la clave en la variable, no aparece en el log.
    monkeypatch.setenv("GEMINI_OCR_FALLBACKS", f"../x,gemini-a?key=1,{CLAVE}/y,gemini-3.5-flash")
    with caplog.at_level(logging.WARNING):
        assert ocr.GeminiProvider("clave").models() == [_PRIMARIO, "gemini-3.5-flash"]
    assert "3 nombre(s)" in caplog.text
    assert CLAVE not in caplog.text and "../x" not in caplog.text


def test_respaldos_explicitos_ganan_a_la_variable(monkeypatch):
    monkeypatch.setenv("GEMINI_OCR_FALLBACKS", "gemini-4-flash")
    assert ocr.GeminiProvider("clave", fallbacks=[]).models() == [_PRIMARIO]


def test_get_provider_lee_modelo_y_respaldos_del_entorno(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", " clave ")
    monkeypatch.setenv("GEMINI_OCR_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_OCR_FALLBACKS", "gemini-4-flash")
    p = ocr.get_provider()
    assert isinstance(p, ocr.GeminiProvider)
    assert p.api_key == "clave"
    assert p.models() == ["gemini-3.5-flash", "gemini-4-flash"]


@pytest.mark.parametrize("modelo", ["", "   ", "../models/x", "m?key=1"])
def test_modelo_primario_raro_usa_el_de_siempre(monkeypatch, modelo):
    monkeypatch.setenv("GEMINI_OCR_MODEL", modelo)
    p = ocr.get_provider(api_key="clave")
    assert p.model == _PRIMARIO
