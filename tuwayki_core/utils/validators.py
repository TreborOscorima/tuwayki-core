"""Utilidades de validación para inputs."""
import re

from tuwayki_core.constants import (
    PASSWORD_MIN_LENGTH,
    PASSWORD_REQUIRE_UPPERCASE,
    PASSWORD_REQUIRE_DIGIT,
    PASSWORD_REQUIRE_SPECIAL,
)


def validate_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_password_strength(
    password: str,
    min_length: int = 6,
    require_uppercase: bool = False,
    require_digit: bool = False,
    require_special: bool = False,
) -> tuple[bool, str]:
    if not password:
        return False, "La contraseña no puede estar vacía."

    if len(password) < min_length:
        return False, f"La contraseña debe tener al menos {min_length} caracteres."

    if require_uppercase and not re.search(r"[A-Z]", password):
        return False, "La contraseña debe contener al menos una mayúscula."

    if require_digit and not re.search(r"\d", password):
        return False, "La contraseña debe contener al menos un número."

    if require_special and not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "La contraseña debe contener al menos un carácter especial."

    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    return validate_password_strength(
        password,
        min_length=PASSWORD_MIN_LENGTH,
        require_uppercase=PASSWORD_REQUIRE_UPPERCASE,
        require_digit=PASSWORD_REQUIRE_DIGIT,
        require_special=PASSWORD_REQUIRE_SPECIAL,
    )
