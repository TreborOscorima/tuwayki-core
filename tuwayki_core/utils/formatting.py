"""Currency and number formatting utilities."""
from decimal import Decimal, ROUND_HALF_UP


def round_currency(value: float) -> float:
    return float(
        Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def format_currency(value: float, symbol: str) -> str:
    return f"{symbol}{round_currency(value):.2f}"
