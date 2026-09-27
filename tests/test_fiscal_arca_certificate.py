"""Argentina: clave privada, solicitud (CSR) y certificado de ARCA."""
from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tuwayki_core.fiscal.argentina_certificate import (
    build_csr_pem,
    certificate_cuit,
    certificate_matches_key,
    credential_warning,
    generate_private_key_pem,
)
from tuwayki_core.utils.fiscal_validators import validate_private_key_pem

CUIT = "20409378472"   # CUIT de prueba con dígito verificador válido


@pytest.fixture(scope="module")
def key_pem() -> str:
    return generate_private_key_pem()


def _cert_for(key_pem: str, cuit: str = CUIT) -> str:
    """Certificado como el que devuelve ARCA para la clave dada."""
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "tuwayki-test"),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
    ])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Computadores Test")])
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=730))
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_clave_generada_es_rsa_2048_valida(key_pem):
    assert key_pem.startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert validate_private_key_pem(key_pem) == (True, "")
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    assert key.key_size == 2048


def test_csr_con_el_formato_que_pide_arca(key_pem):
    csr = x509.load_pem_x509_csr(
        build_csr_pem(key_pem, "20-40937847-2", "Kiosco Ñandú S.R.L.", alias="mi-sistema").encode()
    )
    subject = {attr.oid: attr.value for attr in csr.subject}
    assert subject[NameOID.COUNTRY_NAME] == "AR"
    assert subject[NameOID.SERIAL_NUMBER] == f"CUIT {CUIT}"
    assert subject[NameOID.ORGANIZATION_NAME] == "Kiosco Ñandú S.R.L."
    assert subject[NameOID.COMMON_NAME] == "mi-sistema"
    assert csr.is_signature_valid
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    assert csr.public_key().public_numbers() == key.public_key().public_numbers()


def test_csr_sin_razon_social_usa_el_cuit(key_pem):
    csr = x509.load_pem_x509_csr(build_csr_pem(key_pem, CUIT, "", alias="x").encode())
    assert csr.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == CUIT


def test_csr_exige_cuit_de_11_digitos_y_alias(key_pem):
    with pytest.raises(ValueError, match="11 dígitos"):
        build_csr_pem(key_pem, "2040937", "Empresa", alias="x")
    with pytest.raises(ValueError, match="alias"):
        build_csr_pem(key_pem, CUIT, "Empresa", alias="  ")


def test_certificado_y_clave(key_pem):
    cert = _cert_for(key_pem)
    assert certificate_matches_key(cert, key_pem) is True
    assert certificate_matches_key(cert, generate_private_key_pem()) is False
    assert certificate_matches_key("no es un certificado", key_pem) is False
    assert certificate_cuit(cert) == CUIT
    assert certificate_cuit("basura") == ""


def test_avisos_del_par_certificado_clave(key_pem):
    cert = _cert_for(key_pem)
    assert credential_warning(cert, key_pem, CUIT) == ""
    assert credential_warning(None, key_pem, CUIT) == ""   # falta uno: aún no se sabe
    assert "no corresponde" in credential_warning(cert, generate_private_key_pem(), CUIT)
    assert f"CUIT {CUIT}" in credential_warning(cert, key_pem, "30712345670")
