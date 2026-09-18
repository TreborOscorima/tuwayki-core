"""Login/registro con Google (OAuth 2.0 - Authorization Code, cliente confidencial).

Helper COMPARTIDO por la familia TUWAYKI (SHOP/FOOD/LIFE). Vive en core para
no duplicar la lógica en cada sistema.

Topología: TUWAYKISHOP es la "central de identidad" (SSO) — el único que habla
con Google. FOOD y LIFE confían en una *assertion* firmada por SHOP con un
secreto compartido (`SSO_SHARED_SECRET`). Así, el `GOOGLE_CLIENT_SECRET` vive
solo en el entorno de SHOP y hay una única URL de retorno registrada en Google.

Flujo:
  1. /auth/google/start?producto=...  → redirige a la pantalla de consentimiento
     de Google con un `state` firmado (anti-CSRF, lleva producto + return_to).
  2. Google vuelve a /auth/google/callback con `code` + `state`.
  3. Se intercambia `code` por tokens SERVIDOR-A-SERVIDOR (con client_secret sobre
     TLS) y se pide el userinfo. Como el token vino directo de Google por un canal
     autenticado, se confía en el userinfo sin verificar la firma del id_token
     localmente (patrón estándar de cliente confidencial).
  4. Según el producto: SHOP resuelve local; FOOD/LIFE reciben una assertion firmada.

Scopes mínimos: openid email profile (no sensibles → sin revisión de Google).
"""
from __future__ import annotations

import os
import time
import urllib.parse
from typing import Optional

import httpx
import jwt

# No se importa SECRET_KEY de tuwayki_core.utils.auth a nivel de módulo a propósito:
# ese módulo EXIGE AUTH_SECRET_KEY al importarse, y hay sistemas de la familia
# (p. ej. TUWAYKIFOOD) que no usan el JWT de core y no definen AUTH_SECRET_KEY.
# Acá firmamos con una clave resuelta de forma perezosa (lazy).
ALGORITHM = "HS256"


def _signing_key() -> str:
    """Clave para firmar el `state` (anti-CSRF) y el one-time-token (OTT).

    Prefiere AUTH_SECRET_KEY (la que usa SHOP); si no existe (FOOD/LIFE no la
    definen), cae a SSO_SHARED_SECRET. state/OTT se firman y verifican SIEMPRE
    dentro del mismo sistema, así que basta con que sea consistente ahí."""
    return (os.getenv("AUTH_SECRET_KEY") or os.getenv("SSO_SHARED_SECRET") or "").strip()


GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

OAUTH_HTTP_TIMEOUT = 10

# Ventanas de validez (segundos)
STATE_TTL = 600          # el paseo por Google no debería tardar más de 10 min
OTT_TTL = 90             # one-time token: solo cruza del callback a la página Reflex
ASSERTION_TTL = 120      # assertion SHOP → FOOD/LIFE


def is_configured() -> bool:
    """True si hay credenciales de Google cargadas en el entorno (solo SHOP)."""
    return bool(_client_id() and _client_secret())


def _client_id() -> str:
    return (os.getenv("GOOGLE_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()


def sso_secret() -> str:
    """Secreto compartido SHOP↔FOOD↔LIFE para firmar/verificar assertions.

    Cae a AUTH_SECRET_KEY solo si no se define uno dedicado (útil en dev), pero
    en producción los tres sistemas deben compartir el mismo SSO_SHARED_SECRET.
    """
    return (os.getenv("SSO_SHARED_SECRET") or _signing_key() or "").strip()


def redirect_uri(request_base: str = "") -> str:
    """URI de retorno registrada en Google. Env `GOOGLE_REDIRECT_URI` manda;
    si no, se deriva de la base de la request (la host que sirve /auth)."""
    explicit = (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    base = (request_base or os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    return f"{base}/auth/google/callback"


# ─────────────────────────── state (anti-CSRF) ───────────────────────────

def sign_state(producto: str, return_to: str = "") -> str:
    now = int(time.time())
    payload = {
        "typ": "gstate",
        "producto": producto,
        "return_to": return_to,
        "iat": now,
        "exp": now + STATE_TTL,
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def verify_state(state: str) -> Optional[dict]:
    try:
        payload = jwt.decode(state, _signing_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "gstate":
        return None
    return payload


# ─────────────────────── one-time token (callback → Reflex) ───────────────────────

def sign_ott(claims: dict) -> str:
    now = int(time.time())
    payload = {**claims, "typ": "gott", "iat": now, "exp": now + OTT_TTL}
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def verify_ott(ott: str) -> Optional[dict]:
    try:
        payload = jwt.decode(ott, _signing_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "gott":
        return None
    return payload


# ─────────────────────── assertion (SHOP → FOOD/LIFE) ───────────────────────

def sign_assertion(producto: str, email: str, google_sub: str, name: str = "") -> str:
    now = int(time.time())
    payload = {
        "typ": "sso",
        "iss": "tuwayki-shop",
        "producto": producto,
        "email": email,
        "google_sub": google_sub,
        "name": name,
        "iat": now,
        "exp": now + ASSERTION_TTL,
    }
    return jwt.encode(payload, sso_secret(), algorithm=ALGORITHM)


def verify_assertion(assertion: str) -> Optional[dict]:
    """Usado por FOOD/LIFE para confiar en la identidad verificada por SHOP."""
    try:
        payload = jwt.decode(assertion, sso_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "sso" or payload.get("iss") != "tuwayki-shop":
        return None
    return payload


# ─────────────────────────── llamadas a Google ───────────────────────────

def build_auth_url(state: str, request_base: str = "") -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri(request_base),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str, request_base: str = "") -> Optional[dict]:
    """Cambia el `code` por tokens. Devuelve el dict de tokens o None."""
    data = {
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": redirect_uri(request_base),
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=OAUTH_HTTP_TIMEOUT) as client:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=data)
        if resp.status_code != 200:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


async def fetch_userinfo(access_token: str) -> Optional[dict]:
    """Pide el perfil (sub, email, email_verified, name, picture)."""
    try:
        async with httpx.AsyncClient(timeout=OAUTH_HTTP_TIMEOUT) as client:
            resp = await client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


async def resolve_google_identity(code: str, request_base: str = "") -> Optional[dict]:
    """Del `code` de Google al perfil verificado. None si algo falla.

    Devuelve: {sub, email, email_verified(bool), name, picture}.
    """
    tokens = await exchange_code(code, request_base)
    if not tokens or not tokens.get("access_token"):
        return None
    info = await fetch_userinfo(tokens["access_token"])
    if not info or not info.get("sub") or not info.get("email"):
        return None
    return {
        "sub": str(info["sub"]),
        "email": str(info["email"]).strip().lower(),
        "email_verified": bool(info.get("email_verified", False)),
        "name": str(info.get("name") or "").strip(),
        "picture": str(info.get("picture") or "").strip(),
    }
