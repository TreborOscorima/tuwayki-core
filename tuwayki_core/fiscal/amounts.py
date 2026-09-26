"""Separar base e impuesto de precios con impuesto incluido.

Regla: se redondea POR LÍNEA y los totales son la suma de las líneas, así la
cabecera siempre coincide con el detalle (Nubefact y ARCA lo validan).

La tasa llega de la configuración de la empresa: en Perú no siempre es 18%
(las MYPE de restaurantes y hoteles tienen tasa reducida por la Ley 31556 y
sus prórrogas).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from tuwayki_core.fiscal.models import Line, LineAmounts, TaxCategory, Totals

CENT = Decimal("0.01")
UNIT_VALUE_QUANTUM = Decimal("0.0000000001")  # 10 decimales (máximo de Nubefact)


def round2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _rate(tax_rate_percent: Decimal) -> Decimal:
    rate = Decimal(str(tax_rate_percent))
    if rate < 0 or rate >= 100:
        raise ValueError(f"Tasa de impuesto inválida: {tax_rate_percent}%.")
    return rate / Decimal("100")


def split_tax(total: Decimal, tax_rate_percent: Decimal) -> tuple[Decimal, Decimal]:
    """(base, impuesto) de un monto con impuesto incluido."""
    total = round2(Decimal(str(total)))
    base = round2(total / (1 + _rate(tax_rate_percent)))
    return base, total - base


def line_amounts(line: Line, tax_rate_percent: Decimal) -> LineAmounts:
    qty = Decimal(str(line.quantity))
    price = Decimal(str(line.unit_price))
    if qty <= 0:
        raise ValueError(f"Cantidad inválida en '{line.description}': {qty}.")
    if price < 0:
        raise ValueError(f"Precio negativo en '{line.description}': {price}.")
    total = round2(price * qty)
    if line.category == TaxCategory.TAXED:
        base, tax = split_tax(total, tax_rate_percent)
    else:
        base, tax = total, Decimal("0.00")
    unit_value = (base / qty).quantize(UNIT_VALUE_QUANTUM, rounding=ROUND_HALF_UP)
    return LineAmounts(unit_value=unit_value, base=base, tax=tax, total=total)


def compute_totals(
    lines: Iterable[Line],
    tax_rate_percent: Decimal,
    global_discount: Decimal = Decimal("0"),
) -> Totals:
    """Totales del comprobante.

    ``global_discount`` va con impuesto incluido y se aplica sobre lo gravado
    (el caso de los descuentos de caja). Se reparte en base + impuesto con la
    misma tasa, así ``taxed + tax == total`` siempre.
    """
    lines = list(lines)
    if not lines:
        raise ValueError("El comprobante no tiene líneas.")
    amounts = [line_amounts(ln, tax_rate_percent) for ln in lines]

    taxed = exempt = unaffected = tax = Decimal("0.00")
    taxed_total = Decimal("0.00")
    for ln, am in zip(lines, amounts):
        if ln.category == TaxCategory.TAXED:
            taxed += am.base
            tax += am.tax
            taxed_total += am.total
        elif ln.category == TaxCategory.EXEMPT:
            exempt += am.base
        else:
            unaffected += am.base

    discount_total = round2(Decimal(str(global_discount or 0)))
    if discount_total < 0:
        raise ValueError("El descuento no puede ser negativo.")
    if discount_total > taxed_total:
        raise ValueError(
            f"El descuento ({discount_total}) supera lo gravado ({taxed_total})."
        )
    discount_base, discount_tax = (
        split_tax(discount_total, tax_rate_percent) if discount_total else (Decimal("0.00"), Decimal("0.00"))
    )
    taxed -= discount_base
    tax -= discount_tax
    total = taxed + tax + exempt + unaffected
    return Totals(
        lines=amounts,
        taxed=taxed,
        exempt=exempt,
        unaffected=unaffected,
        tax=tax,
        discount_base=discount_base,
        discount_total=discount_total,
        total=total,
    )
