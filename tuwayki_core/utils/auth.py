from __future__ import annotations

import datetime
import logging
import os
from typing import Any

import jwt
from dotenv import load_dotenv
from jwt import ExpiredSignatureError, InvalidSignatureError, PyJWTError

logger = logging.getLogger("tuwayki_core.auth")

from tuwayki_core.constants import TOKEN_EXPIRY_HOURS, REFRESH_TOKEN_EXPIRY_DAYS

load_dotenv()


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(f"Variable de entorno requerida no encontrada: {var_name}")
    return value


def _is_prod_environment() -> bool:
    value = (os.getenv("ENV") or "dev").strip().lower()
    return value in {"prod", "production"}


SECRET_KEY = _require_env("AUTH_SECRET_KEY")
if _is_prod_environment():
    secret_lower = SECRET_KEY.strip().lower()
    if len(SECRET_KEY) < 32 or secret_lower in {"change_me", "changeme", "default"}:
        raise RuntimeError(
            "AUTH_SECRET_KEY insegura para producción. Usa mínimo 32 caracteres aleatorios."
        )
ALGORITHM = "HS256"


def create_access_token(
    subject: str | Any,
    token_version: int | None = None,
    company_id: int | None = None,
    branch_id: int | None = None,
) -> str:
    expire = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(hours=TOKEN_EXPIRY_HOURS)
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire}
    if token_version is not None:
        payload["ver"] = int(token_version)
    if company_id is not None:
        payload["cid"] = int(company_id)
    if branch_id is not None:
        payload["bid"] = int(branch_id)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            leeway=datetime.timedelta(seconds=10),
        )
        if not payload.get("sub"):
            return None
        return payload
    except InvalidSignatureError:
        logger.warning("JWT firma inválida — posible token manipulado", extra={"token_prefix": token[:20]})
        return None
    except (ExpiredSignatureError, PyJWTError):
        return None


def verify_token(token: str) -> str | None:
    payload = decode_token(token)
    if not payload:
        return None
    return str(payload.get("sub"))


def create_refresh_token(
    subject: str | Any,
    token_version: int | None = None,
    company_id: int | None = None,
) -> str:
    expire = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(
        days=REFRESH_TOKEN_EXPIRY_DAYS,
    )
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "typ": "refresh"}
    if token_version is not None:
        payload["ver"] = int(token_version)
    if company_id is not None:
        payload["cid"] = int(company_id)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_refresh_token(token: str) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            leeway=datetime.timedelta(seconds=10),
        )
        if payload.get("typ") != "refresh":
            return None
        if not payload.get("sub"):
            return None
        return payload
    except InvalidSignatureError:
        logger.warning("JWT refresh firma inválida — posible token manipulado", extra={"token_prefix": token[:20]})
        return None
    except (ExpiredSignatureError, PyJWTError):
        return None


def refresh_access_token(refresh_tok: str) -> tuple[str, str] | None:
    payload = decode_refresh_token(refresh_tok)
    if not payload:
        return None
    subject = payload["sub"]
    version = payload.get("ver")
    company_id = payload.get("cid")
    new_access = create_access_token(subject, token_version=version, company_id=company_id)
    new_refresh = create_refresh_token(subject, token_version=version, company_id=company_id)
    return new_access, new_refresh
