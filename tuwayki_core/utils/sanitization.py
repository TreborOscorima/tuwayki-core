"""Sanitización para prevenir XSS y validar entrada de datos."""
from __future__ import annotations

import re
from typing import Any

from tuwayki_core.constants import NAME_MAX_LENGTH, NOTES_MAX_LENGTH, REASON_MAX_LENGTH


def sanitize_text(value: Any, max_length: int = 500) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip()
    if "<" in cleaned and ">" in cleaned:
        cleaned = re.sub(r"<[^>]*>", "", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def sanitize_text_preserve_spaces(value: Any, max_length: int = 500) -> str:
    if value is None:
        return ""
    cleaned = str(value)
    if "<" in cleaned and ">" in cleaned:
        cleaned = re.sub(r"<[^>]*>", "", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def sanitize_notes(value: Any) -> str:
    return sanitize_text(value, max_length=NOTES_MAX_LENGTH)


def sanitize_notes_preserve_spaces(value: Any) -> str:
    return sanitize_text_preserve_spaces(value, max_length=NOTES_MAX_LENGTH)


def sanitize_name(value: Any) -> str:
    return sanitize_text(value, max_length=NAME_MAX_LENGTH)


def sanitize_phone(value: Any) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip()
    cleaned = re.sub(r"[^\d\s\-+]", "", cleaned)
    return cleaned[:20]


def sanitize_dni(value: Any) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip().upper()
    cleaned = re.sub(r"[^A-Z0-9\-]", "", cleaned)
    return cleaned[:20]


def sanitize_barcode(value: Any) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip()
    cleaned = re.sub(r"[^A-Za-z0-9]", "", cleaned)
    return cleaned[:50]


def sanitize_reason(value: Any) -> str:
    return sanitize_text(value, max_length=REASON_MAX_LENGTH)


def sanitize_reason_preserve_spaces(value: Any) -> str:
    return sanitize_text_preserve_spaces(value, max_length=REASON_MAX_LENGTH)


def is_valid_phone(phone: str, country_code: str = "PE") -> bool:
    from tuwayki_core.countries import get_country_config

    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return False
    config = get_country_config(country_code)
    valid_lengths = config.get("phone_digits", [9, 11])
    return len(digits) in valid_lengths


def is_valid_personal_id(id_value: str, country_code: str = "PE") -> bool:
    from tuwayki_core.countries import get_country_config

    cleaned = re.sub(r"[^A-Za-z0-9]", "", id_value or "")
    if not cleaned:
        return False
    config = get_country_config(country_code)
    min_len, max_len = config.get("personal_id_length", (6, 12))
    return min_len <= len(cleaned) <= max_len


def is_valid_dni(dni: str, country_code: str = "PE") -> bool:
    return is_valid_personal_id(dni, country_code)


def escape_like(pattern: str) -> str:
    return (
        pattern
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
