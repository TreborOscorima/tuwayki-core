"""Currency and number formatting utilities."""
from decimal import Decimal, ROUND_HALF_UP


def round_currency(value: float) -> float:
    return float(
        Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def format_currency(value: float, symbol: str) -> str:
    return f"{symbol}{round_currency(value):.2f}"


def fmt_price(v) -> str:
    """Format a monetary value for form inputs: always 2 decimal places.
    3.2→'3.20', 10.0→'10.00', 21.3→'21.30', 9.33→'9.33'
    """
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "0.00"


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
