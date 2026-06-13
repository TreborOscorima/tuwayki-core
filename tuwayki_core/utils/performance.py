"""Query performance monitoring utilities."""
import asyncio
import os
import time
from contextlib import contextmanager
from functools import wraps
from typing import Generator

from tuwayki_core.utils.logger import get_logger

logger = get_logger("Performance")

SLOW_QUERY_THRESHOLD = float(os.getenv("SLOW_QUERY_THRESHOLD", "1.0"))
CRITICAL_QUERY_THRESHOLD = float(os.getenv("CRITICAL_QUERY_THRESHOLD", "5.0"))


def log_slow_query(
    operation: str,
    elapsed: float,
    threshold: float = SLOW_QUERY_THRESHOLD,
    extra_context: dict | None = None,
) -> None:
    if elapsed < threshold:
        return

    elapsed_ms = round(elapsed * 1000, 2)
    context_str = ""
    if extra_context:
        safe_context = {
            k: v for k, v in extra_context.items()
            if k.lower() not in ("password", "token", "secret", "key")
        }
        context_str = f" | Context: {safe_context}"

    if elapsed >= CRITICAL_QUERY_THRESHOLD:
        logger.error(
            f"QUERY CRITICA: '{operation}' tardo {elapsed_ms}ms "
            f"(umbral critico: {CRITICAL_QUERY_THRESHOLD * 1000}ms){context_str}"
        )
    else:
        logger.warning(
            f"Query lenta: '{operation}' tardo {elapsed_ms}ms "
            f"(umbral: {threshold * 1000}ms){context_str}"
        )


@contextmanager
def query_timer(
    operation: str,
    threshold: float = SLOW_QUERY_THRESHOLD,
    extra_context: dict | None = None,
) -> Generator[dict, None, None]:
    timing_info: dict = {"elapsed": 0.0, "operation": operation}
    start = time.perf_counter()
    try:
        yield timing_info
    finally:
        elapsed = time.perf_counter() - start
        timing_info["elapsed"] = elapsed
        log_slow_query(operation, elapsed, threshold, extra_context)


def timed_operation(operation_name: str | None = None, threshold: float = SLOW_QUERY_THRESHOLD):
    def decorator(func):
        name = operation_name or func.__name__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                log_slow_query(name, time.perf_counter() - start, threshold)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                log_slow_query(name, time.perf_counter() - start, threshold)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


class QueryStats:
    def __init__(self):
        self._queries: list[dict] = []

    @contextmanager
    def track(self, operation: str) -> Generator[None, None, None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._queries.append({"operation": operation, "elapsed_ms": round(elapsed * 1000, 2)})

    def summary(self) -> dict:
        if not self._queries:
            return {"total_queries": 0, "total_time_ms": 0}
        total_time = sum(q["elapsed_ms"] for q in self._queries)
        slowest = max(self._queries, key=lambda q: q["elapsed_ms"])
        return {
            "total_queries": len(self._queries),
            "total_time_ms": round(total_time, 2),
            "avg_time_ms": round(total_time / len(self._queries), 2),
            "slowest": slowest["operation"],
            "slowest_time_ms": slowest["elapsed_ms"],
        }

    def reset(self) -> None:
        self._queries.clear()

    @property
    def queries(self) -> list[dict]:
        return self._queries.copy()
