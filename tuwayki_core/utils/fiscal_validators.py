"""Validadores fiscales para RUC (Perú), CUIT (Argentina) y configuración.

Cada función retorna (is_valid, error_message).
Si is_valid es True, error_message es cadena vacía.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

VALID_ENVIRONMENTS = {"sandbox", "production"}

_WEIGHTS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]

_RUC_PREFIXES = {"10", "15", "17", "20"}

_CUIT_PREFIXES = {"20", "23", "24", "25", "26", "27", "30", "33", "34"}


def validate_ruc(ruc: str) -> tuple[bool, str]:
    if not ruc:
        return False, "El RUC no puede estar vacío."

    ruc = ruc.strip().replace("-", "")

    if not ruc.isdigit():
        return False, "El RUC debe contener solo dígitos."

    if len(ruc) != 11:
        return False, "El RUC debe tener exactamente 11 dígitos."

    prefix = ruc[:2]
    if prefix not in _RUC_PREFIXES:
        return (
            False,
            f"El prefijo '{prefix}' no es válido. "
            "Prefijos permitidos: 10 (persona natural), 15 (no domiciliado), "
            "17 (no domiciliado sin RUC), 20 (persona jurídica).",
        )

    digits = [int(d) for d in ruc]
    weighted_sum = sum(d * w for d, w in zip(digits[:10], _WEIGHTS))
    remainder = weighted_sum % 11
    check_digit = 11 - remainder
    if check_digit == 10:
        check_digit = 0
    elif check_digit == 11:
        check_digit = 1

    if digits[10] != check_digit:
        return False, "El dígito verificador del RUC no es válido."

    return True, ""


def validate_cuit(cuit: str) -> tuple[bool, str]:
    if not cuit:
        return False, "El CUIT no puede estar vacío."

    cuit = cuit.strip().replace("-", "")

    if not cuit.isdigit():
        return False, "El CUIT debe contener solo dígitos."

    if len(cuit) != 11:
        return False, "El CUIT debe tener exactamente 11 dígitos."

    prefix = cuit[:2]
    if prefix not in _CUIT_PREFIXES:
        return (
            False,
            f"El prefijo '{prefix}' no es válido. "
            "Prefijos permitidos: 20, 23, 24, 25, 26, 27 (persona física), "
            "30, 33, 34 (persona jurídica).",
        )

    digits = [int(d) for d in cuit]
    weighted_sum = sum(d * w for d, w in zip(digits[:10], _WEIGHTS))
    remainder = weighted_sum % 11

    if remainder == 0:
        check_digit = 0
    elif remainder == 1:
        if prefix == "23":
            check_digit = 9
        else:
            return False, "El CUIT es inválido (resto 1 no permitido para este prefijo)."
    else:
        check_digit = 11 - remainder

    if digits[10] != check_digit:
        return False, "El dígito verificador del CUIT no es válido."

    return True, ""


def validate_nubefact_url(url: str) -> tuple[bool, str]:
    if not url or not url.strip():
        return False, "La URL no puede estar vacía."

    url = url.strip()

    if url.lower().startswith("http://"):
        return False, "La URL debe usar HTTPS. No se permite HTTP por seguridad."

    if not url.lower().startswith("https://"):
        return False, "La URL debe comenzar con https://."

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "La URL tiene un formato inválido."

    if not parsed.hostname:
        return False, "La URL no contiene un dominio válido."

    host = parsed.hostname.lower()
    if "nubefact" not in host:
        return (
            True,
            "Advertencia: la URL no contiene 'nubefact' en el dominio. "
            "Verifique que sea un endpoint de API válido.",
        )

    return True, ""


def validate_environment(env: str) -> tuple[bool, str]:
    if not env or not env.strip():
        return False, "El entorno no puede estar vacío."

    env = env.strip().lower()
    if env not in VALID_ENVIRONMENTS:
        return (
            False,
            f"Entorno '{env}' no válido. Los valores permitidos son: "
            f"{', '.join(sorted(VALID_ENVIRONMENTS))}.",
        )

    return True, ""


def validate_business_name(name: str) -> tuple[bool, str]:
    if not name or not name.strip():
        return False, "La razón social no puede estar vacía."

    name = name.strip()
    if len(name) < 3:
        return False, "La razón social debe tener al menos 3 caracteres."

    return True, ""


def validate_tax_id(tax_id: str, country: str) -> tuple[bool, str]:
    if not country or not country.strip():
        return False, "El país no puede estar vacío."

    country_key = country.strip().upper()

    if country_key in {"PE", "PER", "PERU", "PERÚ"}:
        return validate_ruc(tax_id)

    if country_key in {"AR", "ARG", "ARGENTINA"}:
        return validate_cuit(tax_id)

    return False, f"País '{country}' no soportado. Países válidos: PE, AR."


def validate_private_key_pem(key_pem: str) -> tuple[bool, str]:
    if not key_pem or not key_pem.strip():
        return False, "La clave privada no puede estar vacía."

    pem = key_pem.strip()

    if "-----BEGIN" not in pem or "PRIVATE KEY" not in pem:
        return False, "Formato inválido. Debe ser un archivo PEM (-----BEGIN ... PRIVATE KEY-----)."

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        key = load_pem_private_key(pem.encode("utf-8"), password=None)
        if isinstance(key, RSAPrivateKey):
            key_size = key.key_size
            if key_size < 2048:
                return False, f"La clave RSA debe ser de al menos 2048 bits (actual: {key_size} bits)."
        return True, ""
    except Exception as exc:
        return False, f"Clave privada inválida: {exc}"
