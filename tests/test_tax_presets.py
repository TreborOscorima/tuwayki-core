"""Tasas de impuesto de fábrica por país (tuwayki_core.utils.tax_presets)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tuwayki_core.countries import SUPPORTED_COUNTRIES
from tuwayki_core.utils.tax_presets import (
    COUNTRY_TAX_PRESETS,
    get_presets_for_country,
    preset_summary,
)


def _default_rate(code: str) -> Decimal:
    return next(p["rate"] for p in get_presets_for_country(code) if p["is_default"])


def test_ecuador_usa_el_iva_vigente_de_15():
    # Subió de 12% a 15% en abril de 2024.
    assert _default_rate("EC") == Decimal("15.00")
    assert [p["rate"] for p in get_presets_for_country("EC")] == [
        Decimal("15.00"),
        Decimal("5.00"),
    ]


@pytest.mark.parametrize(
    "code, rate",
    [("PE", "18"), ("AR", "21"), ("CO", "19"), ("CL", "19"), ("EC", "15"),
     ("BO", "13"), ("UY", "22"), ("PY", "10"), ("MX", "16"), ("VE", "16")],
)
def test_tasa_general_por_pais(code, rate):
    assert _default_rate(code) == Decimal(rate)


def test_todos_los_paises_tienen_tasas_y_una_sola_predeterminada():
    assert set(COUNTRY_TAX_PRESETS) == set(SUPPORTED_COUNTRIES)
    for code, presets in COUNTRY_TAX_PRESETS.items():
        assert sum(p["is_default"] for p in presets) == 1, code
        assert presets[0]["is_default"], code


@pytest.mark.parametrize(
    "code, summary",
    [
        ("PE", "IGV 18%"),
        ("AR", "IVA 21%/10,5%/27%"),  # decimales con coma, como en Argentina
        ("EC", "IVA 15%/5%"),
        ("UY", "IVA 22%/10%"),
        ("MX", "IVA 16%/0%"),
        ("ec", "IVA 15%/5%"),
    ],
)
def test_resumen_de_tasas_para_mostrar(code, summary):
    assert preset_summary(code) == summary
