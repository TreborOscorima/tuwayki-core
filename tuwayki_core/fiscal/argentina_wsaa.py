"""Argentina: WSAA (autenticación de ARCA, ex AFIP) para usar WSFEv1.

Portado de TUWAYKISHOP (``app/services/afip_wsaa.py``) sin base de datos ni
descifrado: el sistema que llama descifra el certificado y la clave de la
empresa y los pasa en claro (en memoria, nunca a disco).

Flujo: TRA (XML) → firma CMS/PKCS#7 con el certificado de la empresa →
LoginCms → Token + Sign (válidos ~12 h, se cachean por certificado).

Endpoints:
    - Homologación: https://wsaahomo.afip.gov.ar/ws/services/LoginCms
    - Producción:   https://wsaa.afip.gov.ar/ws/services/LoginCms
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
# defusedxml evita XXE en respuestas externas; API compatible con ElementTree.
from defusedxml import ElementTree as ET

logger = logging.getLogger(__name__)

WSAA_URLS = {
    "sandbox": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    "production": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
}
_TOKEN_RENEW_MARGIN_SECONDS = 600     # renovar 10 min antes de vencer
_WSAA_TIMEOUT_SECONDS = 30
_TRA_DURATION_HOURS = 12


@dataclass
class WSAACredentials:
    token: str
    sign: str
    expiration: float                 # time.time() en que vence
    service: str = "wsfe"

    @property
    def is_valid(self) -> bool:
        return time.time() < (self.expiration - _TOKEN_RENEW_MARGIN_SECONDS)


# ── Cache en memoria (por certificado + ambiente + servicio) ──────────────
_credentials_cache: dict[str, WSAACredentials] = {}
_cache_locks: dict[str, asyncio.Lock] = {}
_cache_locks_mutex = asyncio.Lock()


def _as_bytes(value: bytes | str) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


def cache_key_for(certificate_pem: bytes | str, environment: str, service: str) -> str:
    """El ambiente es parte de la clave: un token de homologación nunca se usa
    en producción (y viceversa)."""
    digest = hashlib.sha256(_as_bytes(certificate_pem)).hexdigest()[:16]
    return f"{digest}:{environment}:{service}"


def get_cached_credentials(key: str) -> WSAACredentials | None:
    creds = _credentials_cache.get(key)
    if creds and creds.is_valid:
        return creds
    _credentials_cache.pop(key, None)
    return None


def clear_cache() -> None:
    _credentials_cache.clear()
    _cache_locks.clear()


async def _lock_for(key: str) -> asyncio.Lock:
    async with _cache_locks_mutex:
        if key not in _cache_locks:
            _cache_locks[key] = asyncio.Lock()
        return _cache_locks[key]


# ── TRA y firma ──────────────────────────────────────────────────────────

def build_tra_xml(service: str = "wsfe") -> bytes:
    now = datetime.now(timezone.utc)
    gen_time = now - timedelta(minutes=5)          # tolera desfase de reloj
    exp_time = now + timedelta(hours=_TRA_DURATION_HOURS)
    tra = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<loginTicketRequest>"
        "<header>"
        # Milisegundos: ARCA exige un uniqueId distinto por TRA (reintentos rápidos).
        f"<uniqueId>{time.time_ns() // 1_000_000}</uniqueId>"
        f'<generationTime>{gen_time.strftime("%Y-%m-%dT%H:%M:%S%z")}</generationTime>'
        f'<expirationTime>{exp_time.strftime("%Y-%m-%dT%H:%M:%S%z")}</expirationTime>'
        "</header>"
        f"<service>{service}</service>"
        "</loginTicketRequest>"
    )
    return tra.encode("utf-8")


def sign_tra(tra_xml: bytes, certificate_pem: bytes | str, private_key_pem: bytes | str) -> str:
    """Firma CMS (PKCS#7, DER) en Base64, como pide WSAA."""
    try:
        cert = x509.load_pem_x509_certificate(_as_bytes(certificate_pem))
    except Exception as exc:
        raise ValueError(f"Certificado PEM inválido: {exc}") from exc
    try:
        private_key = serialization.load_pem_private_key(_as_bytes(private_key_pem), password=None)
    except Exception as exc:
        raise ValueError(f"Clave privada PEM inválida: {exc}") from exc
    try:
        signed = (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(tra_xml)
            .add_signer(cert, private_key, hashes.SHA256())
            .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
        )
    except Exception as exc:
        raise ValueError(f"Error al firmar CMS: {exc}") from exc
    return base64.b64encode(signed).decode("ascii")


def _build_login_cms_soap(cms_base64: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov">'
        "<soapenv:Body><wsaa:loginCms>"
        f"<wsaa:in0>{cms_base64}</wsaa:in0>"
        "</wsaa:loginCms></soapenv:Body></soapenv:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def parse_login_response(response_xml: str) -> WSAACredentials:
    try:
        root = ET.fromstring(response_xml)
    except ET.ParseError as exc:
        raise ValueError(f"XML de respuesta WSAA inválido: {exc}") from exc

    return_text = None
    for elem in root.iter():
        if _local(elem.tag) == "loginCmsReturn":
            return_text = elem.text
            break
    if not return_text:
        fault = next(
            (e.text or "" for e in root.iter() if _local(e.tag) == "faultstring"), ""
        )
        raise ValueError(
            f"WSAA no devolvió loginCmsReturn. Error SOAP: {fault or 'desconocido'}"
        )
    try:
        ticket = ET.fromstring(return_text)
    except ET.ParseError as exc:
        raise ValueError(f"loginTicketResponse inválido: {exc}") from exc

    token = sign = expiration_str = ""
    header = ticket.find("header")
    if header is not None and header.find("expirationTime") is not None:
        expiration_str = header.find("expirationTime").text or ""
    credentials = ticket.find("credentials")
    if credentials is not None:
        token = (credentials.findtext("token") or "").strip()
        sign = (credentials.findtext("sign") or "").strip()
    if not token or not sign:
        raise ValueError("WSAA loginTicketResponse no contiene token/sign válidos.")

    expiration = time.time() + _TRA_DURATION_HOURS * 3600
    if expiration_str:
        try:
            expiration = datetime.fromisoformat(expiration_str).timestamp()
        except (ValueError, TypeError):
            logger.warning("WSAA: expirationTime ilegible: %s", expiration_str)
    return WSAACredentials(token=token, sign=sign, expiration=expiration)


async def authenticate(
    certificate_pem: bytes | str,
    private_key_pem: bytes | str,
    environment: str = "sandbox",
    service: str = "wsfe",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WSAACredentials:
    """Token + Sign para ``service``. Usa la cache si el token sigue vigente.

    Raises:
        ValueError: certificado/clave inválidos, ambiente inválido o rechazo de WSAA.
        ConnectionError: no se pudo contactar a WSAA.
    """
    if environment not in WSAA_URLS:
        raise ValueError(
            f"Ambiente WSAA inválido: {environment!r}. Válidos: {sorted(WSAA_URLS)}."
        )
    key = cache_key_for(certificate_pem, environment, service)
    cached = get_cached_credentials(key)
    if cached:
        return cached
    async with await _lock_for(key):
        cached = get_cached_credentials(key)   # otro pedido pudo renovarlo
        if cached:
            return cached
        cms = sign_tra(build_tra_xml(service), certificate_pem, private_key_pem)
        url = WSAA_URLS[environment]
        try:
            async with httpx.AsyncClient(timeout=_WSAA_TIMEOUT_SECONDS, transport=transport) as client:
                response = await client.post(
                    url,
                    content=_build_login_cms_soap(cms).encode("utf-8"),
                    headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
                )
        except httpx.TimeoutException as exc:
            raise ConnectionError(f"WSAA no respondió a tiempo ({url}).") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(f"No se pudo conectar a WSAA ({url}): {exc}") from exc
        if response.status_code != 200:
            logger.debug("WSAA HTTP %s body=%r", response.status_code, response.text)
            # Un 500 con "El CEE ya posee un TA valido" = ya hay un token vigente
            # emitido en otro proceso: hay que esperar a que venza o reusarlo.
            raise ValueError(
                f"WSAA respondió HTTP {response.status_code}: {response.text[:100].strip()}"
            )
        credentials = parse_login_response(response.text)
        credentials.service = service
        _credentials_cache[key] = credentials
        return credentials
