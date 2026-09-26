"""Base + impuesto desde precios con impuesto incluido (tuwayki_core.fiscal.amounts)."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from tuwayki_core.fiscal.amounts import compute_totals, round2, split_tax
from tuwayki_core.fiscal.models import Line, TaxCategory

D = Decimal


def test_ejemplo_del_manual_de_nubefact():
    # "EJEMPLO GENERAR CPE BOLETA 1 GRAVADA": 1 x 590 + 5 x 23.60 al 18 %.
    t = compute_totals(
        [Line("DETALLE DEL PRODUCTO", D("1"), D("590")),
         Line("DETALLE DEL SERVICIO", D("5"), D("23.60"), unit="ZZ")],
        D("18"),
    )
    assert (t.taxed, t.tax, t.total) == (D("600.00"), D("108.00"), D("708.00"))
    assert [a.unit_value for a in t.lines] == [D("500"), D("20")]
    assert [a.base for a in t.lines] == [D("500.00"), D("100.00")]


def test_descuento_global_como_el_ejemplo_de_nubefact():
    # "BOLETA 7 DESCUENTO GLOBAL": descuento de 300 sin IGV (354 con IGV).
    t = compute_totals(
        [Line("P", D("1"), D("590")), Line("S", D("5"), D("23.60"))], D("18"), D("354")
    )
    assert (t.discount_base, t.taxed, t.tax, t.total) == (
        D("300.00"), D("300.00"), D("54.00"), D("354.00"),
    )


def test_tasa_reducida_de_restaurantes_mype():
    t = compute_totals([Line("Lomo saltado", D("2"), D("32.00"))], D("10.5"))
    assert t.total == D("64.00")
    assert t.taxed == D("57.92") and t.tax == D("6.08")


def test_exonerado_e_inafecto_no_llevan_impuesto():
    t = compute_totals(
        [Line("Gravado", D("1"), D("11.80")),
         Line("Exonerado", D("1"), D("5.00"), category=TaxCategory.EXEMPT),
         Line("Inafecto", D("1"), D("3.00"), category=TaxCategory.UNAFFECTED)],
        D("18"),
    )
    assert (t.taxed, t.exempt, t.unaffected, t.tax, t.total) == (
        D("10.00"), D("5.00"), D("3.00"), D("1.80"), D("19.80"),
    )


@pytest.mark.parametrize("rate", [D("18"), D("10.5"), D("21"), D("10.50"), D("15")])
def test_cabecera_siempre_cuadra_con_el_detalle(rate):
    rnd = random.Random(20260926)
    for _ in range(400):
        lines = [
            Line(f"x{i}", D(rnd.choice(["1", "2", "3", "0.5", "1.25", "7"])),
                 D(rnd.randint(1, 50000)) / 100)
            for i in range(rnd.randint(1, 8))
        ]
        gross = sum(round2(ln.unit_price * ln.quantity) for ln in lines)
        discount = round2(gross * D(rnd.choice(["0", "0.05", "0.1", "0.333"])))
        t = compute_totals(lines, rate, discount)
        assert t.taxed + t.tax + t.exempt + t.unaffected == t.total
        assert t.total == gross - discount
        # Nubefact: subtotal = valor_unitario x cantidad (valor con 10 decimales).
        for ln, am in zip(lines, t.lines):
            assert round2(am.unit_value * ln.quantity) == am.base
            assert am.base + am.tax == am.total
        # El IGV de cabecera no se aleja de base x tasa más que el redondeo por línea.
        assert abs(t.tax - round2(t.taxed * rate / 100)) <= D("0.01") * (len(lines) + 1)


def test_split_tax():
    assert split_tax(D("118"), D("18")) == (D("100.00"), D("18.00"))


@pytest.mark.parametrize("bad", [
    {"lines": []},
    {"lines": [Line("x", D("0"), D("1"))]},
    {"lines": [Line("x", D("1"), D("-1"))]},
    {"lines": [Line("x", D("1"), D("10"))], "discount": D("10.01")},
    {"lines": [Line("x", D("1"), D("10"))], "discount": D("-1")},
    {"lines": [Line("x", D("1"), D("10"))], "rate": D("100")},
])
def test_datos_invalidos(bad):
    with pytest.raises(ValueError):
        compute_totals(bad["lines"], bad.get("rate", D("18")), bad.get("discount", D("0")))
