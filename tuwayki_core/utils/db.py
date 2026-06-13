"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from tuwayki_core.utils.env import require_int_env
from tuwayki_core.utils.logger import get_logger

load_dotenv()

logger = get_logger("db")


def _require_env(var: str) -> str:
    v = os.getenv(var)
    if not v:
        raise RuntimeError(f"[db] Variable obligatoria: {var}")
    return v


_DB_USER = _require_env("DB_USER")
_DB_PASSWORD = _require_env("DB_PASSWORD")
_DB_HOST = _require_env("DB_HOST")
_DB_PORT = require_int_env("DB_PORT", 3306)
_DB_NAME = _require_env("DB_NAME")

ASYNC_DATABASE_URL = (
    f"mysql+aiomysql://{quote_plus(_DB_USER)}:{quote_plus(_DB_PASSWORD)}"
    f"@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}?charset=utf8mb4"
)

_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "15"))
_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "10"))
_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

async_engine: AsyncEngine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    pool_timeout=_POOL_TIMEOUT,
    pool_recycle=_POOL_RECYCLE,
    pool_use_lifo=True,
    isolation_level="READ COMMITTED",
    connect_args={"init_command": "SET time_zone='+00:00'"},
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_async_session(*, commit: bool = False) -> AsyncIterator[AsyncSession]:
    session = AsyncSessionLocal()
    try:
        yield session
        if commit and session.in_transaction():
            await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("[db] rollback por excepción en sesión async")
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    await async_engine.dispose()
