"""Argentina: clave privada y solicitud de certificado (CSR) para ARCA.

ARCA firma el acceso a sus Web Services con un certificado X.509 propio de
cada CUIT. Para pedirlo hace falta una clave privada RSA y una solicitud
(CSR). Con estas funciones el sistema genera ambas: la empresa solo sube el
CSR a ARCA y pega el certificado que ARCA le devuelve, sin usar OpenSSL.

Sin base de datos: cada sistema guarda la clave (cifrada) y el certificado
en su propia tabla.
"""
from __future__ import annotations

import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_KEY_SIZE = 2048
_MAX_NAME = 64  # límite de X.509 para O y CN


def generate_private_key_pem() -> str:
    """Clave RSA de 2048 bits en PEM ("BEGIN RSA PRIVATE KEY")."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("ascii")


def build_csr_pem(
    private_key_pem: str, cuit: str, business_name: str, *, alias: str
) -> str:
    """Solicitud de certificado con el formato que pide ARCA.

    Sujeto: C=AR, O=<razón social>, CN=<alias>, serialNumber=CUIT <cuit>.
    ``alias`` es el nombre del "computador fiscal" que la empresa ve en ARCA
    al asociar el certificado (cada sistema usa el suyo).
    """
    cuit_digits = re.sub(r"\D", "", cuit or "")
    if len(cuit_digits) != 11:
        raise ValueError("El CUIT debe tener 11 dígitos.")
    common_name = (alias or "").strip()[:_MAX_NAME]
    if not common_name:
        raise ValueError("Falta el alias del certificado.")
    key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    organization = (business_name or "").strip()[:_MAX_NAME] or cuit_digits
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit_digits}"),
    ])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def certificate_matches_key(certificate_pem: str, private_key_pem: str) -> bool:
    """True si el certificado se emitió para esa clave privada."""
    try:
        cert = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
        key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
        return cert.public_key().public_numbers() == key.public_key().public_numbers()
    except Exception:
        return False


def certificate_cuit(certificate_pem: str) -> str:
    """CUIT del sujeto del certificado ("serialNumber=CUIT 20..."), o ""."""
    try:
        cert = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    except Exception:
        return ""
    for attribute in cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER):
        digits = re.sub(r"\D", "", str(attribute.value))
        if len(digits) == 11:
            return digits
    return ""


def credential_warning(
    certificate_pem: str | None, private_key_pem: str | None, cuit: str = ""
) -> str:
    """Motivo por el que el par certificado + clave no servirá, o ""."""
    if not certificate_pem or not private_key_pem:
        return ""
    if not certificate_matches_key(certificate_pem, private_key_pem):
        return (
            "El certificado no corresponde a la clave privada guardada. "
            "Pide el certificado a ARCA con la solicitud (CSR) que genera el sistema."
        )
    cert_cuit = certificate_cuit(certificate_pem)
    cuit_digits = re.sub(r"\D", "", cuit or "")
    if cert_cuit and cuit_digits and cert_cuit != cuit_digits:
        return (
            f"El certificado es del CUIT {cert_cuit}, pero la empresa tiene "
            f"el CUIT {cuit_digits}."
        )
    return ""
