"""Arithmetic utilities for sales and inventory."""
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any

MONEY_QUANT = Decimal("0.01")


def _to_decimal(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value or 0))


def calculate_subtotal(quantity: Decimal, price: Decimal) -> Decimal:
    return (_to_decimal(quantity) * _to_decimal(price)).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )


def calculate_total(items: List[Dict[str, Any]], key: str = "subtotal") -> Decimal:
    total = Decimal("0.00")
    for item in items:
        total += _to_decimal(item.get(key, 0))
    return total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
