"""Currency and number formatting utilities.

Formato de dinero multi país / multi moneda. Cada parte del monto la decide
quien corresponde:

  - **Separadores de miles y decimales → el PAÍS de la empresa** (la convención
    de quien lee la pantalla). Latinoamérica se divide en dos grupos:

      - Coma-miles / punto-decimal → PE, MX, EC                  (1,234.56)
      - Punto-miles / coma-decimal → AR, CO, CL, BO, UY, PY, VE  (1.234,56)

  - **Cantidad de decimales → la MONEDA**: guaraní (PYG), peso chileno (CLP) y
    peso colombiano (COP) se muestran sin centavos (₲15.000, $15.000); el resto
    con dos.

  - **Símbolo → la MONEDA**, desambiguado cuando no es la moneda local del país:
    un dólar en Argentina se muestra "US$", no "$" (que ahí es el peso).

Sin país (``country=None``) los separadores salen de la moneda, como antes de
existir el parámetro: el código que todavía no pasa país conserva su salida.
"""
from decimal import Decimal, ROUND_HALF_UP

from tuwayki_core import countries as _countries

_DEFAULT_SPEC: dict = {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."}

_COMMA_THOUSANDS: dict = {"thousands_sep": ",", "decimal_sep": "."}  # 1,234.56
_POINT_THOUSANDS: dict = {"thousands_sep": ".", "decimal_sep": ","}  # 1.234,56

# Separadores por país (ISO-2): la convención numérica local.
COUNTRY_NUMBER_FORMAT: dict[str, dict] = {
    "PE": _COMMA_THOUSANDS,
    "MX": _COMMA_THOUSANDS,
    "EC": _COMMA_THOUSANDS,
    "AR": _POINT_THOUSANDS,
    "CO": _POINT_THOUSANDS,
    "CL": _POINT_THOUSANDS,
    "BO": _POINT_THOUSANDS,
    "UY": _POINT_THOUSANDS,
    "PY": _POINT_THOUSANDS,
    "VE": _POINT_THOUSANDS,
}

# Spec por moneda ISO. Los decimales son propios de la moneda; los separadores
# de acá solo se usan cuando no se conoce el país (compatibilidad).
CURRENCY_FORMAT: dict[str, dict] = {
    # Coma-miles / punto-decimal (estilo EE.UU.)
    "PEN": {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."},
    "USD": {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."},
    "MXN": {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."},
    # Punto-miles / coma-decimal
    "ARS": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "BOB": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "UYU": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "VES": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    # Cero decimales (en la práctica no circulan centavos)
    "CLP": {"decimals": 0, "thousands_sep": ".", "decimal_sep": ","},
    "PYG": {"decimals": 0, "thousands_sep": ".", "decimal_sep": ","},
    "COP": {"decimals": 0, "thousands_sep": ".", "decimal_sep": ","},
}

# Símbolo de una moneda EXTRANJERA cuyo símbolo local es "$" (ambiguo fuera de
# su país). El resto de los símbolos (S/, Bs., ₲, …) ya son inequívocos.
FOREIGN_CURRENCY_SYMBOL: dict[str, str] = {
    "USD": "US$",
    "ARS": "AR$",
    "CLP": "CLP$",
    "COP": "COL$",
    "MXN": "MX$",
    "UYU": "$U",
}


def _norm(code: str | None) -> str:
    return (code or "").strip().upper()


def currency_spec(code: str | None, country: str | None = None) -> dict:
    """Spec de formato (decimals/thousands_sep/decimal_sep) de un monto.

    ``decimals`` sale de la moneda; los separadores, del país si se conoce (si
    no, de la moneda). Devuelve un dict nuevo: se puede modificar sin riesgo.
    """
    spec = dict(CURRENCY_FORMAT.get(_norm(code), _DEFAULT_SPEC))
    country_format = COUNTRY_NUMBER_FORMAT.get(_norm(country))
    if country_format:
        spec.update(country_format)
    return spec


def currency_decimals(code: str | None) -> int:
    """Decimales que corresponden a la moneda (0 para PYG/CLP/COP, 2 por defecto)."""
    return currency_spec(code)["decimals"]


def round_currency(value, decimals: int = 2) -> float:
    """Redondea un monto a los decimales indicados (HALF_UP).

    `decimals=0` redondea a entero (guaraní / peso chileno). Retrocompatible:
    llamado sin `decimals` usa 2, el comportamiento previo.
    """
    quantum = Decimal(1).scaleb(-int(decimals))  # 10^-decimals: 0.01, 1, 0.001...
    return float(Decimal(str(value or 0)).quantize(quantum, rounding=ROUND_HALF_UP))


def format_number(value, code: str | None = None, country: str | None = None) -> str:
    """Formatea un monto sin símbolo: decimales de la moneda, separadores del país.

    Ej.: format_number(1234.5, "ARS", "AR") → '1.234,50';
    format_number(1234.5, "USD", "AR") → '1.234,50';
    format_number(1234.5, "USD", "EC") → '1,234.50';
    format_number(15000, "COP", "CO") → '15.000'.
    Sin país usa los separadores de la moneda (comportamiento previo).
    """
    spec = currency_spec(code, country)
    decimals = spec["decimals"]
    rounded = round_currency(value, decimals)
    # Base con coma-miles y punto-decimal (estilo Python), luego se traducen los
    # separadores al convenio local usando un placeholder intermedio para evitar
    # colisiones cuando ambos separadores se intercambian.
    base = f"{rounded:,.{decimals}f}"
    return (
        base.replace(",", "\x00")
        .replace(".", spec["decimal_sep"])
        .replace("\x00", spec["thousands_sep"])
    )


def format_currency(
    value,
    symbol: str,
    code: str | None = None,
    country: str | None = None,
) -> str:
    """Formatea un monto con símbolo + decimales/separadores (ver format_number).

    El símbolo se antepone tal cual (el llamador decide el espacio). Para el
    símbolo correcto de una moneda extranjera usar ``display_currency_symbol``.
    """
    return f"{symbol}{format_number(value, code, country)}"


def parse_amount(value, code: str | None = None, country: str | None = None) -> float:
    """Inverso de ``format_number``: convierte a float un monto ya formateado.

    Acepta números tal cual. Un texto se interpreta con los MISMOS separadores
    con los que se formatea (moneda + país), así "3.042,45" en AR vuelve a
    3042.45. No sirve para texto de máquina ("5200.00") en países de coma
    decimal: ahí el punto es separador de miles.
    """
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    spec = currency_spec(code, country)
    thousands = spec.get("thousands_sep", ",")
    decimal = spec.get("decimal_sep", ".")
    keep = set("0123456789-")
    if thousands:
        keep.add(thousands)
    if decimal:
        keep.add(decimal)
    cleaned = "".join(c for c in text if c in keep)
    if thousands:
        cleaned = cleaned.replace(thousands, "")
    if decimal and decimal != ".":
        cleaned = cleaned.replace(decimal, ".")
    try:
        return float(cleaned or 0)
    except (TypeError, ValueError):
        return 0.0


def local_currency(country: str | None) -> str | None:
    """Moneda local (ISO) de un país soportado, o None si no se conoce."""
    config = _countries.SUPPORTED_COUNTRIES.get(_norm(country))
    if not config:
        return None
    return _norm(config.get("currency")) or None


def catalog_currency_symbol(code: str | None) -> str:
    """Símbolo de catálogo de una moneda ('' si no está en el catálogo)."""
    code_n = _norm(code)
    for entry in _countries.CURRENCY_CATALOG:
        if entry.get("code") == code_n:
            return entry.get("symbol", "")
    return ""


def display_currency_symbol(
    code: str | None,
    country: str | None = None,
    symbol: str | None = None,
) -> str:
    """Símbolo a mostrar para ``code`` en una empresa de ``country``.

    - Moneda local del país (o país desconocido): el símbolo configurado
      (``symbol``) o el de catálogo. Ej.: ARS en AR → "$"; USD en EC → "$".
    - Moneda extranjera con símbolo "$" (ambiguo): el símbolo desambiguado.
      Ej.: USD en AR/PE/VE → "US$"; ARS en UY → "AR$".
    - Moneda extranjera con símbolo propio (S/, €, Bs., …): se respeta.
    """
    code_n = _norm(code)
    base = (symbol or "").strip() or catalog_currency_symbol(code_n) or code_n or "$"
    local = local_currency(country)
    if not code_n or local is None or code_n == local or base != "$":
        return base
    return FOREIGN_CURRENCY_SYMBOL.get(code_n, code_n)


def fmt_price(v, code: str | None = None) -> str:
    """Format a monetary value for form inputs.

    Sin `code`: 2 decimales fijos (comportamiento previo). Con `code`: usa los
    decimales de la moneda (0 para guaraní/peso chileno/peso colombiano). Sin
    separador de miles (es un input editable).
    """
    try:
        decimals = currency_decimals(code) if code is not None else 2
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return "0.00" if (code is None or currency_decimals(code)) else "0"


def fmt_input_num(v) -> str:
    """Format a numeric value for form inputs.

    Removes trailing zeros: 10.0→'10', 9.5→'9.5', 9.33→'9.33', 0.0→'0'.
    Safe for quantities, prices and percentages in POS inputs.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    if f == int(f):
        return str(int(f))
    return f"{f:.10f}".rstrip("0").rstrip(".")
