"""Utilidades criptográficas para encriptar/desencriptar credenciales fiscales.

Diseño de seguridad:
    - Usa Fernet (AES-128-CBC + HMAC-SHA256) del paquete ``cryptography``.
    - La clave de encriptación se **deriva** de AUTH_SECRET_KEY (env var que
      ya existe en el sistema) usando PBKDF2-HMAC-SHA256 con 600 000
      iteraciones (recomendación OWASP 2024+).
    - Cada valor encriptado lleva un **salt aleatorio de 16 bytes**
      prepended al ciphertext, de modo que dos encriptaciones del mismo
      plaintext producen resultados distintos (semántica de seguridad).
    - Los certificados (.crt, .key, .pfx) se almacenan encriptados en la
      columna ``Text`` de ``CompanyBillingConfig`` como base64-safe ASCII.

Flujo:
    encrypt_credential(plaintext_bytes) → str (para guardar en DB)
    decrypt_credential(ciphertext_str)  → bytes (para usar en runtime)

Nunca se almacenan credenciales en texto plano.  Si AUTH_SECRET_KEY cambia,
los valores previamente encriptados serán irrecuperables (fail-secure).
"""
from __future__ import annotations

import base64
import logging
import os

from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.x509 import load_pem_x509_certificate

logger = logging.getLogger(__name__)

_SALT_LENGTH = 16
_KDF_ITERATIONS = 600_000
_KDF_KEY_LENGTH = 32
_ENV_KEY_NAME = "AUTH_SECRET_KEY"


def _get_passphrase() -> bytes:
    value = os.environ.get(_ENV_KEY_NAME)
    if not value:
        raise RuntimeError(
            f"Variable de entorno '{_ENV_KEY_NAME}' no definida. "
            "Es obligatoria para la encriptación de credenciales fiscales."
        )
    return value.encode("utf-8")


def _derive_fernet(salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KDF_KEY_LENGTH,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    derived = kdf.derive(_get_passphrase())
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_credential(plaintext: bytes) -> str:
    salt = os.urandom(_SALT_LENGTH)
    fernet = _derive_fernet(salt)
    encrypted = fernet.encrypt(plaintext)
    combined = salt + encrypted
    return base64.urlsafe_b64encode(combined).decode("ascii")


def decrypt_credential(ciphertext: str) -> bytes:
    try:
        combined = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except Exception as exc:
        raise ValueError("Ciphertext base64 inválido") from exc

    if len(combined) < _SALT_LENGTH + 1:
        raise ValueError("Ciphertext demasiado corto — posible corrupción")

    salt = combined[:_SALT_LENGTH]
    encrypted = combined[_SALT_LENGTH:]

    try:
        fernet = _derive_fernet(salt)
        return fernet.decrypt(encrypted)
    except InvalidToken as exc:
        raise ValueError(
            "No se pudo desencriptar la credencial. "
            "Posible causa: AUTH_SECRET_KEY cambió o el dato fue manipulado."
        ) from exc


def encrypt_text(plaintext: str) -> str:
    return encrypt_credential(plaintext.encode("utf-8"))


def decrypt_text(ciphertext: str) -> str:
    return decrypt_credential(ciphertext).decode("utf-8")


@dataclass(frozen=True, slots=True)
class CertMetadata:
    """Metadatos extraídos de un certificado X.509 PEM."""

    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    serial_number: str

    @property
    def days_remaining(self) -> int:
        delta = self.not_after - datetime.now(timezone.utc)
        return delta.days

    @property
    def is_expired(self) -> bool:
        return self.days_remaining < 0


def parse_certificate_pem(pem_text: str) -> CertMetadata | None:
    try:
        cert = load_pem_x509_certificate(pem_text.encode("utf-8"))
        subject = cert.subject.rfc4514_string()
        issuer = cert.issuer.rfc4514_string()
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        serial = format(cert.serial_number, "x").upper()
        return CertMetadata(
            subject=subject,
            issuer=issuer,
            not_before=not_before,
            not_after=not_after,
            serial_number=serial,
        )
    except Exception as exc:
        logger.warning("Error parseando certificado PEM: %s", exc)
        return None
