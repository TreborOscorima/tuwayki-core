"""Billetes y monedas del arqueo: solo los que circulan en cada país.

2026-10-15: el arqueo de Argentina ofrecía billetes de $100.000, $50.000 y
$5.000 y una moneda de $50, que no existen; Uruguay tenía una moneda de $U20
(es billete) y a Venezuela le faltaban los billetes de Bs.200 y Bs.500.
"""
from __future__ import annotations

import pytest

from tuwayki_core.countries import SUPPORTED_COUNTRIES


def _valores(code: str, tipo: str) -> list[float]:
    return [d["value"] for d in SUPPORTED_COUNTRIES[code]["denominations"] if d["type"] == tipo]


@pytest.mark.parametrize("code", sorted(SUPPORTED_COUNTRIES))
def test_cada_billete_y_moneda_aparece_una_sola_vez(code):
    filas = [(d["type"], d["value"]) for d in SUPPORTED_COUNTRIES[code]["denominations"]]
    assert len(filas) == len(set(filas))
    assert {t for t, _v in filas} <= {"bill", "coin"}
    # De mayor a menor dentro de cada tipo, y los billetes antes que las monedas.
    for tipo in ("bill", "coin"):
        valores = _valores(code, tipo)
        assert valores == sorted(valores, reverse=True)
    tipos = [t for t, _v in filas]
    assert tipos == sorted(tipos)  # "bill" < "coin"


def test_argentina_sin_billetes_ni_monedas_que_no_existen():
    assert _valores("AR", "bill") == [20000, 10000, 2000, 1000, 500, 200, 100, 50, 20, 10]
    assert _valores("AR", "coin") == [10, 5, 2, 1]


def test_venezuela_con_el_cono_vigente():
    assert _valores("VE", "bill") == [500, 200, 100, 50, 20, 10, 5]
    assert _valores("VE", "coin") == [1, 0.50, 0.25]


def test_uruguay_billete_de_20_y_sin_moneda_de_20():
    assert 20 in _valores("UY", "bill")
    assert _valores("UY", "coin") == [50, 10, 5, 2, 1]


def test_monedas_que_faltaban():
    assert 20 in _valores("MX", "coin")  # convive con el billete de $20
    assert 1 in _valores("EC", "coin")   # la moneda de 1 dólar es muy usada en Ecuador
