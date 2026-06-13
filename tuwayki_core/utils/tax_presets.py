"""Configuraciones de impuesto por país para auto-poblar CompanyTaxRate.

Cada entrada define las tasas que se crearán al inicializar o reiniciar
la configuración fiscal de una empresa. El campo ``is_default`` marca
la tasa que se usa como fallback en documentos fiscales y recibos.
"""

from decimal import Decimal
from typing import TypedDict


class TaxRatePreset(TypedDict):
    tax_name: str
    label: str
    rate: Decimal
    is_default: bool
    display_order: int


COUNTRY_TAX_PRESETS: dict[str, list[TaxRatePreset]] = {
    "PE": [
        {
            "tax_name": "IGV",
            "label": "Estándar",
            "rate": Decimal("18.00"),
            "is_default": True,
            "display_order": 1,
        },
    ],
    "AR": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("21.00"),
            "is_default": True,
            "display_order": 1,
        },
        {
            "tax_name": "IVA",
            "label": "Reducida",
            "rate": Decimal("10.50"),
            "is_default": False,
            "display_order": 2,
        },
        {
            "tax_name": "IVA",
            "label": "Incrementada",
            "rate": Decimal("27.00"),
            "is_default": False,
            "display_order": 3,
        },
    ],
    "CO": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("19.00"),
            "is_default": True,
            "display_order": 1,
        },
        {
            "tax_name": "IVA",
            "label": "Reducida",
            "rate": Decimal("5.00"),
            "is_default": False,
            "display_order": 2,
        },
    ],
    "CL": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("19.00"),
            "is_default": True,
            "display_order": 1,
        },
    ],
    "EC": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("12.00"),
            "is_default": True,
            "display_order": 1,
        },
        {
            "tax_name": "IVA",
            "label": "Reducida",
            "rate": Decimal("5.00"),
            "is_default": False,
            "display_order": 2,
        },
    ],
    "BO": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("13.00"),
            "is_default": True,
            "display_order": 1,
        },
    ],
    "UY": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("22.00"),
            "is_default": True,
            "display_order": 1,
        },
        {
            "tax_name": "IVA",
            "label": "Mínima",
            "rate": Decimal("10.00"),
            "is_default": False,
            "display_order": 2,
        },
    ],
    "PY": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("10.00"),
            "is_default": True,
            "display_order": 1,
        },
        {
            "tax_name": "IVA",
            "label": "Reducida",
            "rate": Decimal("5.00"),
            "is_default": False,
            "display_order": 2,
        },
    ],
    "MX": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("16.00"),
            "is_default": True,
            "display_order": 1,
        },
        {
            "tax_name": "IVA",
            "label": "Tasa 0%",
            "rate": Decimal("0.00"),
            "is_default": False,
            "display_order": 2,
        },
    ],
    "VE": [
        {
            "tax_name": "IVA",
            "label": "Estándar",
            "rate": Decimal("16.00"),
            "is_default": True,
            "display_order": 1,
        },
    ],
}

_DEFAULT_PRESET: list[TaxRatePreset] = [
    {
        "tax_name": "IVA",
        "label": "Estándar",
        "rate": Decimal("18.00"),
        "is_default": True,
        "display_order": 1,
    },
]


def get_presets_for_country(country_code: str) -> list[TaxRatePreset]:
    return COUNTRY_TAX_PRESETS.get(country_code.upper(), _DEFAULT_PRESET)
