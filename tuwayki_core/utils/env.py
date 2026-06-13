"""Fail-fast environment variable helpers."""
from __future__ import annotations

import os
from typing import Optional

_VALID_APP_SURFACES: frozenset[str] = frozenset({"all", "landing", "app", "owner"})


def _resolve_app_surface() -> str:
    raw = os.getenv("APP_SURFACE")
    if raw is None or not raw.strip():
        return "all"
    value = raw.strip().lower()
    if value not in _VALID_APP_SURFACES:
        raise RuntimeError(
            f"[env] APP_SURFACE inválido: {raw!r}. "
            f"Valores válidos: {sorted(_VALID_APP_SURFACES)}."
        )
    return value


APP_SURFACE: str = _resolve_app_surface()


def require_int_env(var: str, default: Optional[int] = None) -> int:
    raw = os.getenv(var)
    if raw is None or not raw.strip():
        if default is None:
            raise RuntimeError(f"[env] Variable obligatoria: {var}")
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"[env] {var} debe ser un entero. Valor recibido: {raw!r}."
        ) from exc
