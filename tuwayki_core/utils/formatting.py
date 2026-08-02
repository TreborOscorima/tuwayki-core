"""Currency and number formatting utilities.

Formato de dinero *locale-aware*: cada moneda define sus decimales y sus
separadores de miles/decimal según la convención local. Latinoamérica se divide
en dos grupos:

  - Punto-miles / coma-decimal  → ARS, COP, CLP, BOB, UYU, PYG, VES  (1.234,56)
  - Coma-miles / punto-decimal  → PEN, USD/EC, MXN                   (1,234.56)

Además el guaraní (PYG) y el peso chileno (CLP) son de CERO decimales
(₲15.000, $15.000), no de dos.
"""
from decimal import Decimal, ROUND_HALF_UP

_DEFAULT_SPEC: dict = {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."}

# Spec de formato por código de moneda ISO.
CURRENCY_FORMAT: dict[str, dict] = {
    # Coma-miles / punto-decimal (estilo EE.UU.)
    "PEN": {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."},
    "USD": {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."},
    "MXN": {"decimals": 2, "thousands_sep": ",", "decimal_sep": "."},
    # Punto-miles / coma-decimal
    "ARS": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "COP": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "BOB": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "UYU": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    "VES": {"decimals": 2, "thousands_sep": ".", "decimal_sep": ","},
    # Cero decimales
    "CLP": {"decimals": 0, "thousands_sep": ".", "decimal_sep": ","},
    "PYG": {"decimals": 0, "thousands_sep": ".", "decimal_sep": ","},
}


def currency_spec(code: str | None) -> dict:
    """Spec de formato (decimals/thousands_sep/decimal_sep) de una moneda."""
    return CURRENCY_FORMAT.get((code or "").strip().upper(), _DEFAULT_SPEC)


def currency_decimals(code: str | None) -> int:
    """Decimales que corresponden a la moneda (0 para PYG/CLP, 2 por defecto)."""
    return currency_spec(code)["decimals"]


def round_currency(value, decimals: int = 2) -> float:
    """Redondea un monto a los decimales indicados (HALF_UP).

    `decimals=0` redondea a entero (guaraní / peso chileno). Retrocompatible:
    llamado sin `decimals` usa 2, el comportamiento previo.
    """
    quantum = Decimal(1).scaleb(-int(decimals))  # 10^-decimals: 0.01, 1, 0.001...
    return float(Decimal(str(value or 0)).quantize(quantum, rounding=ROUND_HALF_UP))


def format_number(value, code: str | None = None) -> str:
    """Formatea un número con decimales y separadores de la moneda (sin símbolo).

    Ej.: format_number(15000, "PYG") → '15.000'; format_number(1234.5, "ARS")
    → '1.234,50'; format_number(1234.5, "PEN") → '1,234.50'.
    """
    spec = currency_spec(code)
    decimals = spec["decimals"]
    rounded = round_currency(value, decimals)
    # Base con coma-miles y punto-decimal (estilo Python), luego se traducen los
    # separadores al convenio de la moneda usando un placeholder intermedio para
    # evitar colisiones cuando ambos separadores se intercambian.
    base = f"{rounded:,.{decimals}f}"
    return (
        base.replace(",", "\x00")
        .replace(".", spec["decimal_sep"])
        .replace("\x00", spec["thousands_sep"])
    )


def format_currency(value, symbol: str, code: str | None = None) -> str:
    """Formatea un monto con símbolo + decimales/separadores de la moneda.

    Retrocompatible: `format_currency(value, symbol)` sin `code` usa el spec por
    defecto (2 decimales, agrupación de miles). Pasá `code` (ISO) para respetar
    la convención local exacta.
    """
    return f"{symbol}{format_number(value, code)}"


def fmt_price(v, code: str | None = None) -> str:
    """Format a monetary value for form inputs.

    Sin `code`: 2 decimales fijos (comportamiento previo). Con `code`: usa los
    decimales de la moneda (0 para guaraní/peso chileno). Sin separador de miles
    (es un input editable).
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
