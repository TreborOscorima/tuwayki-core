"""Lectura de comprobantes de compra con IA de visión (Gemini), compartida por SHOP y LIFE.

Lee una foto o un PDF de una factura, boleta, ticket o presupuesto del proveedor
y devuelve datos estructurados (encabezado + ítems) para precargar la compra y
el inventario. La persona SIEMPRE revisa y corrige antes de confirmar.

Diseño:
- **Servicio puro**, sin base de datos ni Reflex: cada sistema busca el
  proveedor y los productos y arma su borrador (SHOP en `ingreso_state`, LIFE en
  `compras_ocr`). Los dos importan de acá, así un arreglo llega a ambos con
  solo subir el SHA del core (antes eran dos copias y el del 2026-11-20 hubo
  que hacerlo dos veces).
- **Proveedor intercambiable**: interfaz `InvoiceOCRProvider` + `GeminiProvider`.
- **Multi-país**: el identificador fiscal se extrae tal como figura (RUC, CUIT,
  RUT, NIT, RFC…); la etiqueta del país va solo como pista.
- La clave va en un encabezado, nunca en la dirección ni en el log.

Config (entorno):
- `GEMINI_API_KEY`: clave de Google Generative Language API. Sin clave, la
  lectura avisa y la carga a mano sigue igual.
- `GEMINI_OCR_MODEL` (opcional): modelo primario, por defecto
  "gemini-flash-lite-latest".
- `GEMINI_OCR_FALLBACKS` (opcional): modelos de respaldo en orden, separados
  por comas. Vacía o sin definir = `_FALLBACK_MODELS`. Cuando Google retira o
  satura un modelo, se cambia la variable sin tocar código.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

# Tamaño máximo del lado mayor de la imagen antes de enviar (baja costo/latencia).
_MAX_IMAGE_DIM = 1600
# Tiempo por modelo: una lectura normal tarda 10–20 s. Un modelo que no contesta
# en 35 s se da por saturado y se prueba el siguiente (2026-11-20, en LIFE: el
# respaldo se quedó 60 s sin responder y la lectura falló sin probar los demás).
# Tope total para que la persona no espere de más.
_HTTP_TIMEOUT_S = 35.0
_TOTAL_MAX_S = 110.0
# Modelo primario: el "lite" (alias vigente) responde rápido y tiene más
# capacidad disponible; los flash pesados suelen dar 503 (alta demanda) y hacen
# lento el escaneo. El alias evita el 404 "no longer available to new users".
_DEFAULT_MODEL = "gemini-flash-lite-latest"
# Reintentos: solo ante 429 (demasiados pedidos seguidos), con backoff corto.
# Un 503 («modelo saturado») suele durar minutos y cada respuesta tardaba ~14 s:
# reintentar el mismo modelo solo demoraba el escaneo, así que se pasa al
# siguiente enseguida (2026-11-20: los dos «-latest» devolvieron 503 a la vez y
# la lectura falló; minutos después anduvo).
_RETRY_STATUSES = {429, 503}
_MAX_RETRIES = 2
_RETRY_BACKOFF_S = 1.0
# Modelos de respaldo, en orden (se deduplica el primario), si no hay
# `GEMINI_OCR_FALLBACKS`. Los más nuevos (los que apunta «gemini-flash-latest»)
# son los más pedidos y se saturan o tardan; las versiones fijas un poco
# anteriores tienen capacidad aparte. Probado el 2026-11-20 con las claves de
# SHOP y LIFE: 3.5 y 3.1 contestaban en 2–4 s mientras el «-latest» completo no
# respondía; las 2.5 ya dan 404 («no disponible para cuentas nuevas»). Uno
# retirado (404) se saltea solo.
_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
]
_FALLBACKS_ENV = "GEMINI_OCR_FALLBACKS"
_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
# El nombre del modelo va dentro de la dirección: solo letras, números, punto,
# guion y guion bajo (nada de "/", "?" ni "..").
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

# Tipos MIME aceptados (imagen + PDF).
_ACCEPTED_IMAGE_PREFIX = "image/"
_ACCEPTED_PDF = "application/pdf"


@dataclass
class InvoiceItem:
    """Un renglón de producto extraído del comprobante."""

    description: str = ""
    quantity: float = 0.0
    unit: str = ""
    unit_cost: float = 0.0
    sale_price: Optional[float] = None
    barcode: str = ""
    line_total: Optional[float] = None  # total impreso del renglón (para cruzar)


@dataclass
class InvoiceExtraction:
    """Resultado de la extracción. `error` no vacío indica fallo."""

    supplier_name: str = ""
    supplier_tax_id: str = ""
    supplier_phone: str = ""
    supplier_address: str = ""
    doc_type: str = ""  # "boleta" | "factura" (o "" si no se pudo inferir)
    series: str = ""
    number: str = ""
    issue_date: str = ""  # "YYYY-MM-DD" o ""
    currency: str = ""
    items: List[InvoiceItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _build_prompt(*, currency_hint: str, tax_id_label: str, categories: List[str]) -> str:
    """Construye el prompt que pide JSON estricto y multi-país."""
    cats = ", ".join(categories[:40]) if categories else ""
    cats_line = (
        f"\nCategorías disponibles del sistema (elige la más cercana por ítem, o deja vacío): {cats}."
        if cats
        else ""
    )
    return (
        "Eres un asistente que extrae datos de comprobantes de compra de proveedores "
        "(facturas, boletas, tickets o presupuestos, impresos o manuscritos) para un "
        "sistema de inventario que opera en varios países.\n\n"
        "Devuelve EXCLUSIVAMENTE un objeto JSON válido con esta forma exacta:\n"
        "{\n"
        '  "supplier_name": string,        // razón social / nombre del proveedor emisor\n'
        '  "supplier_tax_id": string,      // identificador fiscal del proveedor TAL CUAL aparece\n'
        '  "supplier_phone": string,\n'
        '  "supplier_address": string,\n'
        '  "doc_type": string,             // "factura" o "boleta" (minúsculas); la factura con su letra si la tiene ("factura a", "factura b", "factura c"); "" si no se sabe\n'
        '  "series": string,               // serie del documento si existe\n'
        '  "number": string,               // número del documento\n'
        '  "issue_date": string,           // fecha de emisión en formato YYYY-MM-DD; "" si no se lee\n'
        '  "currency": string,             // código ISO de moneda si es visible (PEN, ARS, USD, ...)\n'
        '  "items": [\n'
        "    {\n"
        '      "description": string,      // nombre del producto\n'
        '      "quantity": number,         // cantidad (usa punto decimal)\n'
        '      "unit": string,             // unidad EN QUE SE CUENTA la cantidad (unidad, caja, docena, kg si se vende a granel); "" si el documento no la indica\n'
        '      "unit_cost": number,        // costo unitario (precio de COMPRA); usa punto decimal\n'
        '      "sale_price": number|null,  // precio de venta SOLO si el documento lo indica; si no, null\n'
        '      "barcode": string,          // código de barras del ítem si aparece; "" si no\n'
        '      "line_total": number|null   // TOTAL impreso de ese renglón (columna importe/total); null si no aparece\n'
        "    }\n"
        "  ],\n"
        '  "warnings": [string]            // notas para el cajero, en español claro (ver reglas)\n'
        "}\n\n"
        "Reglas IMPORTANTES:\n"
        "- 'supplier_name' = el nombre comercial MÁS PROMINENTE del encabezado/logo del "
        "documento. Si hay varios nombres (redes sociales, marcas, pie de página), usa el "
        "principal de arriba; no mezcles ni uses el de las redes si hay uno principal.\n"
        "- NO confundas teléfonos, emails o números de las redes con el identificador fiscal.\n"
        f"- El identificador fiscal NO es siempre CUIT/RUC: en este país suele llamarse "
        f'"{tax_id_label}". Extrae el valor tal como figura, sin reformatear ni inventar. Si no '
        "hay un identificador fiscal claro, deja supplier_tax_id vacío.\n"
        "- 'doc_type' SOLO puede ser 'factura' (con su letra A, B o C si la tiene) o 'boleta'. Si el documento es un presupuesto, "
        "remito, cotización, orden o cualquier otro tipo, deja doc_type vacío (\"\").\n"
        "- 'unit' es la unidad de la columna de cantidad, NO el contenido del envase: en "
        "'Yerba 1kg' x 24 o 'Aceite 900ml' x 12 la cantidad son paquetes/botellas, así que "
        "unit = \"unidad\" (o \"\" si no hay columna de unidad). Usa kg/g/l/ml solo si el "
        "producto se vende suelto por peso o volumen.\n"
        "- Es un documento de COMPRA: 'unit_cost' es el costo unitario. NO inventes precio "
        "de venta; si no está explícito, pon sale_price = null.\n"
        "- 'line_total' es el importe TOTAL de ese renglón tal como está impreso/escrito en la "
        "columna de total (NO lo calcules tú): cópialo tal cual del documento; null si no aparece.\n"
        "- Números sin separador de miles y con punto decimal. No incluyas símbolos de moneda.\n"
        "- Si un valor no se puede leer con confianza, déjalo vacío/null y agrega una nota en 'warnings'.\n"
        "- Las 'warnings' son para una persona (cajero): escríbelas en español claro y natural, "
        "SIN nombres técnicos ni de campos internos (nunca menciones 'doc_type', 'supplier_tax_id', "
        "'issue_date', 'sale_price', etc.). Ej.: 'La cantidad de un producto no se lee bien, revísala.'\n"
        "- NO agregues warnings sobre el tipo de documento faltante, la fecha de emisión faltante ni "
        "el identificador fiscal faltante: de esos avisos se encarga el sistema. Usa 'warnings' SOLO "
        "para datos de baja legibilidad o dudosos (sobre todo en manuscritos).\n"
        f"{'- Moneda esperada por defecto: ' + currency_hint + '.' if currency_hint else ''}"
        f"{cats_line}\n"
        "- NO agregues texto fuera del JSON."
    )


def _prepare_image(data: bytes) -> tuple[bytes, str]:
    """Redimensiona/normaliza una imagen para reducir costo/latencia.

    Retorna (bytes, mime). Si pillow falla, devuelve los bytes originales como JPEG
    genérico (el modelo suele tolerarlo). Nunca lanza.
    """
    try:
        from PIL import Image  # pillow (llega con reportlab)

        img = Image.open(io.BytesIO(data))
        # Corregir orientación EXIF (fotos de celular) si es posible.
        try:
            from PIL import ImageOps

            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > _MAX_IMAGE_DIM:
            scale = _MAX_IMAGE_DIM / float(longest)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        logger.warning("No se pudo preprocesar la imagen; se envía original", exc_info=True)
        return data, "image/jpeg"


def _valid_model_name(name: str) -> bool:
    return bool(_MODEL_NAME_RE.fullmatch(name or ""))


def _fallback_models() -> List[str]:
    """Respaldos de `GEMINI_OCR_FALLBACKS` (separados por comas) o la lista fija.

    Vacía, sin definir o sin ningún nombre válido = `_FALLBACK_MODELS` (un
    `${VAR:-}` de docker-compose llega como texto vacío y no debe dejar la
    lectura sin respaldos). El valor no se escribe en el log: si alguien pegó
    ahí la clave por error, no queda a la vista.
    """
    models: List[str] = []
    descartados = 0
    for name in (part.strip() for part in (os.getenv(_FALLBACKS_ENV) or "").split(",")):
        if not name or name in models:
            continue
        if not _valid_model_name(name):
            descartados += 1
            continue
        models.append(name)
    if descartados:
        logger.warning(
            "%s tiene %d nombre(s) de modelo no válido(s); se ignoran", _FALLBACKS_ENV, descartados
        )
    return models or list(_FALLBACK_MODELS)


class InvoiceOCRProvider:
    """Interfaz de proveedor de OCR de comprobantes."""

    async def extract(
        self,
        data: bytes,
        mime: str,
        *,
        prompt: str,
    ) -> dict:  # pragma: no cover - interfaz
        raise NotImplementedError


class GeminiProvider(InvoiceOCRProvider):
    """Extracción vía Google Generative Language API (Gemini).

    `fallbacks=None` toma los respaldos de `GEMINI_OCR_FALLBACKS` (o la lista fija).
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        fallbacks: Optional[Sequence[str]] = None,
    ):
        self.api_key = api_key
        self.model = model or _DEFAULT_MODEL
        self.fallbacks = list(fallbacks) if fallbacks is not None else _fallback_models()

    def models(self) -> List[str]:
        """Cadena de modelos: primario + respaldos (distintos, sin duplicar)."""
        return [self.model] + [m for m in dict.fromkeys(self.fallbacks) if m != self.model]

    async def extract(self, data: bytes, mime: str, *, prompt: str) -> dict:
        b64 = base64.b64encode(data).decode("ascii")
        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": mime, "data": b64}},
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0,
            },
        }
        models = self.models()
        resp = None
        limite = time.monotonic() + _TOTAL_MAX_S
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            for model_idx, model in enumerate(models):
                url = _GEMINI_ENDPOINT.format(model=model)
                if model_idx and time.monotonic() > limite - 15:
                    break  # sin tiempo para otro intento útil
                for attempt in range(_MAX_RETRIES):
                    # La clave va en un encabezado y no en la dirección: el texto
                    # de un error de httpx trae la dirección completa y terminaba
                    # en el log (auditoría de SHOP 2026-11-05, Tanda 4).
                    try:
                        resp = await client.post(
                            url,
                            json=payload,
                            headers={
                                "Content-Type": "application/json",
                                "x-goog-api-key": self.api_key,
                            },
                        )
                    except httpx.TimeoutException:
                        # Sin respuesta a tiempo: como un 503, al siguiente modelo;
                        # si era el último, se avisa como timeout.
                        if model_idx == len(models) - 1:
                            raise
                        logger.warning(
                            "Modelo OCR '%s' no respondió a tiempo; probando respaldo",
                            model,
                        )
                        resp = None
                        break
                    if resp.status_code == 200:
                        break
                    # Demasiados pedidos (429): backoff y reintento del mismo
                    # modelo. Saturado (503): directo al siguiente.
                    if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                        continue
                    break
                if resp is not None and resp.status_code == 200:
                    break
                if resp is None:
                    continue  # no respondió a tiempo: siguiente modelo
                # Modelo saturado/retirado: probar el siguiente respaldo si queda.
                if (
                    resp.status_code in (_RETRY_STATUSES | {404})
                    and model_idx < len(models) - 1
                ):
                    logger.warning(
                        "Modelo OCR '%s' devolvió %s; probando respaldo",
                        model, resp.status_code,
                    )
                    continue
                break
        if resp is None:
            raise httpx.TimeoutException("Ningún modelo respondió a tiempo")
        resp.raise_for_status()
        body = resp.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise ValueError("La IA no devolvió candidatos (posible bloqueo de contenido).")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise ValueError("La IA devolvió una respuesta vacía.")
        return _parse_json_text(text)


def _parse_json_text(text: str) -> dict:
    """Parsea el JSON devuelto, tolerando fences ```json ... ``` o texto alrededor."""
    text = text.strip()
    if text.startswith("```"):
        # Quitar fences de markdown si el modelo los agregó.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Rescatar el primer objeto {...} balanceado.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _s(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# Tokens de campos internos que jamás deben aparecer en un aviso al cajero.
_INTERNAL_FIELD_TOKENS = (
    "doc_type",
    "supplier_tax_id",
    "supplier_name",
    "supplier_phone",
    "supplier_address",
    "issue_date",
    "sale_price",
    "unit_cost",
    "barcode",
)


def _mentions_internal_field(text: str) -> bool:
    low = (text or "").lower()
    return any(tok in low for tok in _INTERNAL_FIELD_TOKENS)


def _fmt_amount(n: float) -> str:
    """Importe legible: sin decimales si es entero, con 2 si no."""
    return f"{n:.0f}" if abs(n - round(n)) < 0.005 else f"{n:.2f}"


def _line_mismatch_warnings(items: List[InvoiceItem]) -> List[str]:
    """Avisa cuando cantidad x costo no cuadra con el total impreso del renglón.

    Caza errores de lectura de la IA en manuscritos (ej. leer 247 en vez de 24):
    el total impreso del renglón deja de coincidir con cantidad x costo unitario.
    """
    out: List[str] = []
    for it in items:
        lt = it.line_total
        if lt is None or lt <= 0 or it.quantity <= 0 or it.unit_cost <= 0:
            continue
        computed = round(it.quantity * it.unit_cost, 2)
        # Tolerancia: 1% del total del renglón o $1 (lo que sea mayor) para no
        # marcar por redondeos triviales.
        if abs(computed - lt) > max(1.0, lt * 0.01):
            name = it.description or "producto sin nombre"
            out.append(
                f'Revisa "{name}": {_fmt_amount(it.quantity)} x '
                f"{_fmt_amount(it.unit_cost)} = {_fmt_amount(computed)}, pero el "
                f"comprobante muestra {_fmt_amount(lt)} en esa línea. "
                "Verifica la cantidad o el precio."
            )
    return out


def _normalize_extraction(raw: dict, *, tax_id_label: str = "ID Fiscal") -> InvoiceExtraction:
    """Convierte el dict de la IA en InvoiceExtraction, saneando tipos."""
    items_raw = raw.get("items") or []
    items: List[InvoiceItem] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        desc = _s(it.get("description"))
        if not desc and not it.get("barcode"):
            continue
        sp_raw = it.get("sale_price", None)
        sale_price = None if sp_raw in (None, "", "null") else _f(sp_raw, 0.0)
        lt_raw = it.get("line_total", None)
        line_total = None if lt_raw in (None, "", "null") else _f(lt_raw, 0.0)
        items.append(
            InvoiceItem(
                description=desc,
                quantity=_f(it.get("quantity"), 0.0),
                unit=_s(it.get("unit")),
                unit_cost=_f(it.get("unit_cost"), 0.0),
                sale_price=sale_price,
                barcode=_s(it.get("barcode")),
                line_total=line_total,
            )
        )

    # Tolerante a variantes ("Factura B", "Boleta de venta", "FACTURA A", etc.).
    # La letra de la factura argentina se guarda (factura_b); cada sistema la
    # lleva al tipo que ofrece el país.
    doc_type_raw = _s(raw.get("doc_type")).lower()
    if "factura" in doc_type_raw:
        letra = re.search(r"factura\W*(?:tipo\W*)?([abc])\b", doc_type_raw)
        doc_type = f"factura_{letra.group(1)}" if letra else "factura"
    elif "boleta" in doc_type_raw:
        doc_type = "boleta"
    else:
        doc_type = ""

    supplier_tax_id = _s(raw.get("supplier_tax_id"))
    issue_date = _s(raw.get("issue_date"))

    # Warnings de la IA: se descartan las que filtren nombres de campos internos
    # (defensa por si el modelo ignora el prompt) para que el cajero nunca vea jerga.
    warnings_raw = raw.get("warnings") or []
    ai_warnings = (
        [_s(w) for w in warnings_raw if _s(w)] if isinstance(warnings_raw, list) else []
    )
    ai_warnings = [w for w in ai_warnings if not _mentions_internal_field(w)]

    # Avisos estándar redactados en español claro (no dependemos del texto de la IA).
    std_warnings: List[str] = []
    if not doc_type:
        std_warnings.append(
            "No se reconoció el tipo de comprobante (puede ser un presupuesto o un "
            "remito). Revisa el Tipo de Documento y ajústalo si corresponde."
        )
    if not issue_date:
        std_warnings.append(
            "No se pudo leer la fecha de emisión. Complétala si la necesitas."
        )
    if not supplier_tax_id:
        std_warnings.append(
            f"No se detectó el {tax_id_label} del proveedor. Puedes dejarlo vacío o "
            "completarlo al crear/seleccionar el proveedor."
        )

    return InvoiceExtraction(
        supplier_name=_s(raw.get("supplier_name")),
        supplier_tax_id=supplier_tax_id,
        supplier_phone=_s(raw.get("supplier_phone")),
        supplier_address=_s(raw.get("supplier_address")),
        doc_type=doc_type,
        series=_s(raw.get("series")),
        number=_s(raw.get("number")),
        issue_date=issue_date,
        currency=_s(raw.get("currency")).upper(),
        items=items,
        # El descuadre por línea va primero: es el aviso más accionable.
        warnings=_line_mismatch_warnings(items) + std_warnings + ai_warnings,
    )


def get_provider(api_key: Optional[str] = None) -> Optional[InvoiceOCRProvider]:
    """Factory: retorna el proveedor configurado, o None si no hay credencial."""
    key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        return None
    model = (os.getenv("GEMINI_OCR_MODEL") or "").strip() or _DEFAULT_MODEL
    if not _valid_model_name(model):
        logger.warning("GEMINI_OCR_MODEL no es un nombre de modelo válido; se usa %s", _DEFAULT_MODEL)
        model = _DEFAULT_MODEL
    return GeminiProvider(api_key=key, model=model)


def is_supported_mime(mime: str) -> bool:
    mime = (mime or "").lower()
    return mime.startswith(_ACCEPTED_IMAGE_PREFIX) or mime == _ACCEPTED_PDF


async def extract_invoice(
    data: bytes,
    mime: str,
    *,
    currency_hint: str = "",
    tax_id_label: str = "ID Fiscal",
    categories: Optional[List[str]] = None,
    api_key: Optional[str] = None,
) -> InvoiceExtraction:
    """Extrae datos estructurados de un comprobante (imagen o PDF).

    Nunca lanza: cualquier fallo se devuelve en `InvoiceExtraction.error`.
    """
    if not data:
        return InvoiceExtraction(error="Archivo vacío.")

    mime = (mime or "").lower().split(";")[0].strip()
    if not is_supported_mime(mime):
        return InvoiceExtraction(
            error="Formato no soportado. Sube una imagen (foto) o un PDF."
        )

    provider = get_provider(api_key)
    if provider is None:
        return InvoiceExtraction(
            error="La lectura por IA no está configurada (falta GEMINI_API_KEY). "
            "Puedes cargar los productos manualmente."
        )

    # Preprocesar sólo imágenes; el PDF se envía tal cual.
    if mime.startswith(_ACCEPTED_IMAGE_PREFIX):
        send_data, send_mime = _prepare_image(data)
    else:
        send_data, send_mime = data, _ACCEPTED_PDF

    prompt = _build_prompt(
        currency_hint=currency_hint,
        tax_id_label=tax_id_label or "ID Fiscal",
        categories=categories or [],
    )

    try:
        raw = await provider.extract(send_data, send_mime, prompt=prompt)
    except httpx.TimeoutException:
        logger.warning("Timeout llamando al proveedor de OCR")
        return InvoiceExtraction(
            error="La lectura tardó demasiado. Reintenta o carga los productos manualmente."
        )
    except httpx.HTTPStatusError as exc:
        # Solo el código y la ruta sin la consulta: nada de claves en el log.
        logger.warning(
            "HTTP %s del proveedor de OCR (%s)",
            exc.response.status_code, exc.request.url.copy_with(query=None),
        )
        return InvoiceExtraction(
            error=f"El servicio de IA respondió con error ({exc.response.status_code}). "
            "Reintenta más tarde o carga los productos manualmente."
        )
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Respuesta de OCR ilegible: %s", exc)
        return InvoiceExtraction(
            error="No se pudo interpretar la lectura del comprobante. "
            "Prueba con una foto más nítida o carga los productos manualmente."
        )
    except Exception:
        logger.exception("Fallo inesperado en extract_invoice")
        return InvoiceExtraction(
            error="Error inesperado leyendo el comprobante. Carga los productos manualmente."
        )

    if not isinstance(raw, dict):
        return InvoiceExtraction(error="La IA devolvió un formato inesperado.")

    result = _normalize_extraction(raw, tax_id_label=tax_id_label or "ID Fiscal")
    if not result.items:
        result.warnings.append(
            "No se detectaron productos legibles; revisa el comprobante o cárgalos manualmente."
        )
    return result
