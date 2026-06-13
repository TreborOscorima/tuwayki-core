"""Centralized rate limiting with Redis (prod) and in-memory (dev) backends."""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from tuwayki_core.constants import MAX_LOGIN_ATTEMPTS, LOGIN_LOCKOUT_MINUTES

logger = logging.getLogger("RateLimit")

_redis_client: "redis.Redis | None" = None  # type: ignore[name-defined]
_memory_store: Dict[str, List[datetime]] = defaultdict(list)


def _get_environment() -> str:
    env = (os.getenv("ENV") or "dev").strip().lower()
    return "prod" if env in {"prod", "production"} else "dev"


def _allow_memory_fallback_in_prod() -> bool:
    value = (os.getenv("ALLOW_MEMORY_RATE_LIMIT_FALLBACK") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _strict_rate_limit_backend() -> bool:
    return _get_environment() == "prod" and not _allow_memory_fallback_in_prod()


def _get_redis():
    global _redis_client
    if not REDIS_AVAILABLE:
        return None
    if _redis_client is not None:
        return _redis_client
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return None
    try:
        import redis as _redis
        _redis_client = _redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        _redis_client.ping()
        return _redis_client
    except Exception:
        _redis_client = None
        return None


def is_rate_limited(
    username: str,
    max_attempts: int = MAX_LOGIN_ATTEMPTS,
    window_minutes: int = LOGIN_LOCKOUT_MINUTES,
    ip_address: str | None = None,
) -> bool:
    key = _build_key(username, ip_address)
    if not key:
        return True
    r = _get_redis()
    if r is None and _strict_rate_limit_backend():
        return True
    if r is not None:
        try:
            attempts = r.get(f"login_attempts:{key}")
            return int(attempts or 0) >= max_attempts
        except Exception:
            if _strict_rate_limit_backend():
                return True
            return _is_rate_limited_memory(key, max_attempts, window_minutes)
    return _is_rate_limited_memory(key, max_attempts, window_minutes)


def _normalize_ip(ip_address: str | None) -> str | None:
    if not ip_address:
        return None
    ip = str(ip_address).strip()
    if "," in ip:
        ip = ip.split(",", 1)[0].strip()
    return ip or None


def _build_key(username: str, ip_address: str | None = None) -> str | None:
    key = (username or "").lower().strip()
    if not key:
        return None
    ip = _normalize_ip(ip_address)
    if ip:
        return f"{key}|{ip}"
    return key


def _is_rate_limited_memory(username: str, max_attempts: int, window_minutes: int) -> bool:
    now = datetime.now()
    cutoff = now - timedelta(minutes=window_minutes)
    _memory_store[username] = [t for t in _memory_store[username] if t > cutoff]
    return len(_memory_store[username]) >= max_attempts


def record_failed_attempt(
    username: str,
    window_minutes: int = LOGIN_LOCKOUT_MINUTES,
    ip_address: str | None = None,
) -> None:
    key = _build_key(username, ip_address)
    if not key:
        return
    r = _get_redis()
    if r is not None:
        try:
            redis_key = f"login_attempts:{key}"
            pipe = r.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_minutes * 60)
            pipe.execute()
            return
        except Exception:
            pass
    _memory_store[key].append(datetime.now())


def clear_login_attempts(username: str, ip_address: str | None = None) -> None:
    key = _build_key(username, ip_address)
    if not key:
        return
    r = _get_redis()
    if r is not None:
        try:
            r.delete(f"login_attempts:{key}")
        except Exception:
            pass
    _memory_store.pop(key, None)


def remaining_lockout_time(
    username: str,
    window_minutes: int = LOGIN_LOCKOUT_MINUTES,
    ip_address: str | None = None,
) -> int:
    key = _build_key(username, ip_address)
    if not key:
        return 0
    r = _get_redis()
    if r is None and _strict_rate_limit_backend():
        return max(1, int(window_minutes))
    if r is not None:
        try:
            ttl = r.ttl(f"login_attempts:{key}")
            if ttl and ttl > 0:
                return max(1, (ttl + 59) // 60)
            return 0
        except Exception:
            pass
    if not _memory_store.get(key):
        return 0
    oldest_attempt = min(_memory_store[key])
    unlock_time = oldest_attempt + timedelta(minutes=window_minutes)
    remaining = (unlock_time - datetime.now()).total_seconds() / 60
    return max(0, int(remaining) + 1)


def get_rate_limit_status() -> dict:
    r = _get_redis()
    return {
        "backend": "redis" if r else "memory",
        "redis_available": REDIS_AVAILABLE,
        "redis_connected": r is not None,
        "strict_backend": _strict_rate_limit_backend(),
        "memory_fallback_allowed": _allow_memory_fallback_in_prod(),
        "memory_entries": len(_memory_store),
    }
