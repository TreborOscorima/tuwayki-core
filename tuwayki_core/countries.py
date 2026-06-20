"""Country configuration: timezones, currencies, tax IDs, denominations, payment methods.

Extracted from db_seeds so it can be used by timezone and sanitization utils
without importing models or DB-related code.
"""
from __future__ import annotations

import re
import unicodedata

from tuwayki_core.enums import PaymentMethodType

# ============================================================================
# SUPPORTED COUNTRIES
# ============================================================================

SUPPORTED_COUNTRIES: dict[str, dict] = {
    "PE": {
        "name": "Perú",
        "currency": "PEN",
        "currency_name": "Sol peruano",
        "currency_symbol": "S/",
        "timezone": "America/Lima",
        "tax_id_label": "RUC",
        "personal_id_label": "DNI",
        "tax_id_placeholder": "20123456789",
        "personal_id_placeholder": "12345678",
        "phone_digits": [9, 11],
        "personal_id_length": (8, 8),
        "tax_id_length": (11, 11),
        "denominations": [
            {"value": 200,  "label": "S/200",  "type": "bill"},
            {"value": 100,  "label": "S/100",  "type": "bill"},
            {"value": 50,   "label": "S/50",   "type": "bill"},
            {"value": 20,   "label": "S/20",   "type": "bill"},
            {"value": 10,   "label": "S/10",   "type": "bill"},
            {"value": 5,    "label": "S/5",    "type": "coin"},
            {"value": 2,    "label": "S/2",    "type": "coin"},
            {"value": 1,    "label": "S/1",    "type": "coin"},
            {"value": 0.50, "label": "S/0.50", "type": "coin"},
            {"value": 0.20, "label": "S/0.20", "type": "coin"},
            {"value": 0.10, "label": "S/0.10", "type": "coin"},
        ],
    },
    "AR": {
        "name": "Argentina",
        "currency": "ARS",
        "currency_name": "Peso argentino",
        "currency_symbol": "$",
        "timezone": "America/Argentina/Buenos_Aires",
        "tax_id_label": "CUIT",
        "personal_id_label": "DNI",
        "tax_id_placeholder": "20-12345678-9",
        "personal_id_placeholder": "12345678",
        "phone_digits": [10, 13],
        "personal_id_length": (7, 8),
        "tax_id_length": (11, 11),
        "denominations": [
            {"value": 20000, "label": "$20.000", "type": "bill"},
            {"value": 10000, "label": "$10.000", "type": "bill"},
            {"value": 2000,  "label": "$2.000",  "type": "bill"},
            {"value": 1000,  "label": "$1.000",  "type": "bill"},
            {"value": 500,   "label": "$500",    "type": "bill"},
            {"value": 200,   "label": "$200",    "type": "bill"},
            {"value": 100,   "label": "$100",    "type": "coin"},
            {"value": 50,    "label": "$50",     "type": "coin"},
            {"value": 20,    "label": "$20",     "type": "coin"},
            {"value": 10,    "label": "$10",     "type": "coin"},
            {"value": 5,     "label": "$5",      "type": "coin"},
            {"value": 2,     "label": "$2",      "type": "coin"},
            {"value": 1,     "label": "$1",      "type": "coin"},
        ],
    },
    "EC": {
        "name": "Ecuador",
        "currency": "USD",
        "currency_name": "Dólar estadounidense",
        "currency_symbol": "$",
        "timezone": "America/Guayaquil",
        "tax_id_label": "RUC",
        "personal_id_label": "Cédula",
        "tax_id_placeholder": "1234567890001",
        "personal_id_placeholder": "1234567890",
        "phone_digits": [9, 12],
        "personal_id_length": (10, 10),
        "tax_id_length": (13, 13),
        "denominations": [
            {"value": 100,  "label": "$100",  "type": "bill"},
            {"value": 50,   "label": "$50",   "type": "bill"},
            {"value": 20,   "label": "$20",   "type": "bill"},
            {"value": 10,   "label": "$10",   "type": "bill"},
            {"value": 5,    "label": "$5",    "type": "bill"},
            {"value": 1,    "label": "$1",    "type": "bill"},
            {"value": 0.50, "label": "$0.50", "type": "coin"},
            {"value": 0.25, "label": "$0.25", "type": "coin"},
            {"value": 0.10, "label": "$0.10", "type": "coin"},
            {"value": 0.05, "label": "$0.05", "type": "coin"},
            {"value": 0.01, "label": "$0.01", "type": "coin"},
        ],
    },
    "CO": {
        "name": "Colombia",
        "currency": "COP",
        "currency_name": "Peso colombiano",
        "currency_symbol": "$",
        "timezone": "America/Bogota",
        "tax_id_label": "NIT",
        "personal_id_label": "C.C.",
        "tax_id_placeholder": "900123456-7",
        "personal_id_placeholder": "1234567890",
        "phone_digits": [10, 12],
        "personal_id_length": (6, 10),
        "tax_id_length": (9, 10),
        "denominations": [
            {"value": 100000, "label": "$100.000", "type": "bill"},
            {"value": 50000,  "label": "$50.000",  "type": "bill"},
            {"value": 20000,  "label": "$20.000",  "type": "bill"},
            {"value": 10000,  "label": "$10.000",  "type": "bill"},
            {"value": 5000,   "label": "$5.000",   "type": "bill"},
            {"value": 2000,   "label": "$2.000",   "type": "bill"},
            {"value": 1000,   "label": "$1.000",   "type": "coin"},
            {"value": 500,    "label": "$500",     "type": "coin"},
            {"value": 200,    "label": "$200",     "type": "coin"},
            {"value": 100,    "label": "$100",     "type": "coin"},
            {"value": 50,     "label": "$50",      "type": "coin"},
        ],
    },
    "CL": {
        "name": "Chile",
        "currency": "CLP",
        "currency_name": "Peso chileno",
        "currency_symbol": "$",
        "timezone": "America/Santiago",
        "tax_id_label": "RUT",
        "personal_id_label": "RUN",
        "tax_id_placeholder": "12.345.678-9",
        "personal_id_placeholder": "12.345.678-9",
        "phone_digits": [9, 11],
        "personal_id_length": (8, 9),
        "tax_id_length": (8, 9),
        "denominations": [
            {"value": 20000, "label": "$20.000", "type": "bill"},
            {"value": 10000, "label": "$10.000", "type": "bill"},
            {"value": 5000,  "label": "$5.000",  "type": "bill"},
            {"value": 2000,  "label": "$2.000",  "type": "bill"},
            {"value": 1000,  "label": "$1.000",  "type": "bill"},
            {"value": 500,   "label": "$500",    "type": "coin"},
            {"value": 100,   "label": "$100",    "type": "coin"},
            {"value": 50,    "label": "$50",     "type": "coin"},
            {"value": 10,    "label": "$10",     "type": "coin"},
        ],
    },
    "MX": {
        "name": "México",
        "currency": "MXN",
        "currency_name": "Peso mexicano",
        "currency_symbol": "$",
        "timezone": "America/Mexico_City",
        "tax_id_label": "RFC",
        "personal_id_label": "CURP",
        "tax_id_placeholder": "XAXX010101000",
        "personal_id_placeholder": "XEXX010101HNEXXXA4",
        "phone_digits": [10, 12],
        "personal_id_length": (18, 18),
        "tax_id_length": (12, 13),
        "denominations": [
            {"value": 1000, "label": "$1,000", "type": "bill"},
            {"value": 500,  "label": "$500",   "type": "bill"},
            {"value": 200,  "label": "$200",   "type": "bill"},
            {"value": 100,  "label": "$100",   "type": "bill"},
            {"value": 50,   "label": "$50",    "type": "bill"},
            {"value": 20,   "label": "$20",    "type": "bill"},
            {"value": 10,   "label": "$10",    "type": "coin"},
            {"value": 5,    "label": "$5",     "type": "coin"},
            {"value": 2,    "label": "$2",     "type": "coin"},
            {"value": 1,    "label": "$1",     "type": "coin"},
            {"value": 0.50, "label": "$0.50",  "type": "coin"},
        ],
    },
}


def get_country_config(country_code: str) -> dict:
    """Returns configuration for a country code (default: PE)."""
    country_code = (country_code or "PE").upper()
    return SUPPORTED_COUNTRIES.get(country_code, SUPPORTED_COUNTRIES["PE"])


def get_country_config_by_currency(currency_code: str) -> dict:
    """Returns country config whose currency matches currency_code (default: PE/PEN)."""
    currency_code = (currency_code or "PEN").upper()
    for config in SUPPORTED_COUNTRIES.values():
        if config.get("currency", "").upper() == currency_code:
            return config
    return SUPPORTED_COUNTRIES["PE"]


# ============================================================================
# PAYMENT METHODS BY COUNTRY
# ============================================================================

UNIVERSAL_PAYMENT_METHODS: list[dict] = [
    {"name": "Efectivo",         "code": "cash",       "method_id": "cash",       "description": "Billetes, Monedas",        "kind": PaymentMethodType.cash,     "allows_change": True},
    {"name": "Transferencia",    "code": "transfer",   "method_id": "transfer",   "description": "Transferencia bancaria",   "kind": PaymentMethodType.transfer, "allows_change": False},
    {"name": "Tarjeta de Crédito", "code": "credit_card", "method_id": "credit_card", "description": "Pago con tarjeta crédito", "kind": PaymentMethodType.credit, "allows_change": False},
    {"name": "Tarjeta de Débito",  "code": "debit_card",  "method_id": "debit_card",  "description": "Pago con tarjeta débito",  "kind": PaymentMethodType.debit,  "allows_change": False},
]

COUNTRY_PAYMENT_METHODS: dict[str, list[dict]] = {
    "PE": [
        {"name": "Yape", "code": "yape", "method_id": "yape", "description": "Pago con Yape (BCP)", "kind": PaymentMethodType.yape, "allows_change": False},
        {"name": "Plin", "code": "plin", "method_id": "plin", "description": "Pago con Plin",       "kind": PaymentMethodType.plin, "allows_change": False},
    ],
    "AR": [
        {"name": "Mercado Pago", "code": "mercadopago", "method_id": "mercadopago", "description": "Pago con Mercado Pago",                     "kind": PaymentMethodType.wallet, "allows_change": False},
        {"name": "Cuenta DNI",   "code": "cuenta_dni",  "method_id": "cuenta_dni",  "description": "Pago con Cuenta DNI (Banco Provincia)",      "kind": PaymentMethodType.wallet, "allows_change": False},
        {"name": "MODO",         "code": "modo",        "method_id": "modo",        "description": "Pago con MODO",                             "kind": PaymentMethodType.wallet, "allows_change": False},
    ],
    "EC": [
        {"name": "Payphone", "code": "payphone", "method_id": "payphone", "description": "Pago con Payphone",                     "kind": PaymentMethodType.wallet, "allows_change": False},
        {"name": "De Una",   "code": "deuna",    "method_id": "deuna",    "description": "Pago con De Una (Banco Pichincha)",     "kind": PaymentMethodType.wallet, "allows_change": False},
    ],
    "CO": [
        {"name": "Nequi",     "code": "nequi",     "method_id": "nequi",     "description": "Pago con Nequi",     "kind": PaymentMethodType.wallet, "allows_change": False},
        {"name": "Daviplata", "code": "daviplata", "method_id": "daviplata", "description": "Pago con Daviplata", "kind": PaymentMethodType.wallet, "allows_change": False},
    ],
    "CL": [
        {"name": "Mercado Pago", "code": "mercadopago", "method_id": "mercadopago", "description": "Pago con Mercado Pago", "kind": PaymentMethodType.wallet, "allows_change": False},
        {"name": "MACH",         "code": "mach",        "method_id": "mach",        "description": "Pago con MACH",         "kind": PaymentMethodType.wallet, "allows_change": False},
    ],
    "MX": [
        {"name": "Mercado Pago", "code": "mercadopago", "method_id": "mercadopago", "description": "Pago con Mercado Pago",    "kind": PaymentMethodType.wallet, "allows_change": False},
        {"name": "CoDi",         "code": "codi",        "method_id": "codi",        "description": "Cobro Digital (Banxico)", "kind": PaymentMethodType.wallet, "allows_change": False},
    ],
}

LEGACY_PAYMENT_METHOD_IDS: set[str] = {"credit_sale"}
RESERVED_PAYMENT_METHOD_NAME_KEYS: set[str] = {
    "credito fiado",
    "venta a credito",
    "venta al credito",
}

CURRENCY_CATALOG: list[dict] = [
    {"code": "PEN", "name": "Sol peruano",           "symbol": "S/"},
    {"code": "ARS", "name": "Peso argentino",         "symbol": "$"},
    {"code": "COP", "name": "Peso colombiano",        "symbol": "$"},
    {"code": "CLP", "name": "Peso chileno",           "symbol": "$"},
    {"code": "USD", "name": "Dólar estadounidense",   "symbol": "$"},
    {"code": "BOB", "name": "Boliviano",              "symbol": "Bs."},
    {"code": "UYU", "name": "Peso uruguayo",          "symbol": "$U"},
    {"code": "PYG", "name": "Guaraní",                "symbol": "₲"},
    {"code": "MXN", "name": "Peso mexicano",          "symbol": "$"},
    {"code": "VES", "name": "Bolívar venezolano",     "symbol": "Bs.S"},
]


def _payment_method_name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def is_reserved_payment_method(
    *,
    method_id: str | None = None,
    code: str | None = None,
    name: str | None = None,
) -> bool:
    if (method_id or "").strip().lower() in LEGACY_PAYMENT_METHOD_IDS:
        return True
    if (code or "").strip().lower() in LEGACY_PAYMENT_METHOD_IDS:
        return True
    return _payment_method_name_key(name or "") in RESERVED_PAYMENT_METHOD_NAME_KEYS


def get_payment_methods_for_country(country_code: str) -> list[dict]:
    """Returns universal + country-specific payment methods."""
    country_code = (country_code or "PE").upper()
    methods = list(UNIVERSAL_PAYMENT_METHODS)
    if country_code in COUNTRY_PAYMENT_METHODS:
        methods.extend(COUNTRY_PAYMENT_METHODS[country_code])
    return [
        dict(m)
        for m in methods
        if not is_reserved_payment_method(
            method_id=m.get("method_id"),
            code=m.get("code"),
            name=m.get("name"),
        )
    ]
