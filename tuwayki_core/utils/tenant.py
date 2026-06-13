"""
Aislamiento multi-tenant enforzado en la capa ORM.

Garantías:
1. Todo SELECT contra tablas con `company_id` se filtra por el tenant activo.
2. Todo INSERT en tablas tenant recibe company_id/branch_id del contexto si falta.
3. Todo UPDATE que intente cambiar company_id/branch_id existente es bloqueado.
4. Bypass disponible a dos niveles:
   - ContextVar `tenant_bypass()` para operaciones cross-tenant (jobs, owner backoffice).
   - `session.info[TENANT_OPTION_BYPASS] = True` (scope de una sesión específica).
5. Registro de modelos dinámico: `_refresh_tenant_models()` se vuelve a correr
   automáticamente cuando el número de subclases SQLModel cambia -> soporta modelos
   importados lazy sin abrir huecos de aislamiento.

Nota: los listeners se registran sobre `sqlalchemy.orm.Session` con `propagate=True`,
lo que cubre `AsyncSession` (envuelve a Session vía run_sync).
"""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from typing import Any, Iterable, Optional, Type

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session, with_loader_criteria
from sqlmodel import SQLModel

TENANT_OPTION_COMPANY = "tenant_company_id"
TENANT_OPTION_BRANCH = "tenant_branch_id"
TENANT_OPTION_BYPASS = "tenant_bypass"


def _strict_tenant() -> bool:
    return os.getenv("TENANT_STRICT", "1").strip().lower() not in {"0", "false", "no"}


_tenant_company_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "tenant_company_id", default=None
)
_tenant_branch_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "tenant_branch_id", default=None
)
_tenant_bypass_cv: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tenant_bypass", default=False
)

_TENANT_COMPANY_MODELS: tuple[Type[SQLModel], ...] = ()
_TENANT_BRANCH_MODELS: tuple[Type[SQLModel], ...] = ()
_LAST_SUBCLASS_COUNT = 0
_TENANT_LISTENERS_INSTALLED = False


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def set_tenant_context(company_id: Any, branch_id: Any) -> None:
    _tenant_company_id.set(_coerce_int(company_id))
    _tenant_branch_id.set(_coerce_int(branch_id))


def get_tenant_context() -> tuple[Optional[int], Optional[int]]:
    return _tenant_company_id.get(), _tenant_branch_id.get()


@contextmanager
def tenant_context(company_id: Any, branch_id: Any):
    t_c = _tenant_company_id.set(_coerce_int(company_id))
    t_b = _tenant_branch_id.set(_coerce_int(branch_id))
    try:
        yield
    finally:
        _tenant_company_id.reset(t_c)
        _tenant_branch_id.reset(t_b)


@contextmanager
def tenant_bypass():
    tok = _tenant_bypass_cv.set(True)
    try:
        yield
    finally:
        _tenant_bypass_cv.reset(tok)


def _collect_subclasses() -> list[Type[SQLModel]]:
    seen: set[Type[SQLModel]] = set()
    stack: list[Type[Any]] = [SQLModel]
    while stack:
        base = stack.pop()
        for sub in base.__subclasses__():
            stack.append(sub)
            if getattr(sub, "__table__", None) is not None:
                seen.add(sub)
    return list(seen)


def _refresh_tenant_models() -> None:
    global _TENANT_COMPANY_MODELS, _TENANT_BRANCH_MODELS, _LAST_SUBCLASS_COUNT
    subclasses = _collect_subclasses()
    company_models: list[Type[SQLModel]] = []
    branch_models: list[Type[SQLModel]] = []
    for model in subclasses:
        table = model.__table__  # type: ignore[attr-defined]
        cols = table.c
        if "company_id" in cols:
            company_models.append(model)
        if "branch_id" in cols:
            branch_col = cols.get("branch_id")
            if branch_col is not None and not getattr(branch_col, "nullable", True):
                branch_models.append(model)
    _TENANT_COMPANY_MODELS = tuple(company_models)
    _TENANT_BRANCH_MODELS = tuple(branch_models)
    _LAST_SUBCLASS_COUNT = len(subclasses)


def _ensure_models_fresh() -> None:
    current = sum(1 for _ in _iter_subclasses(SQLModel))
    if current != _LAST_SUBCLASS_COUNT:
        _refresh_tenant_models()


def _iter_subclasses(root: Type[Any]) -> Iterable[Type[Any]]:
    stack = [root]
    while stack:
        base = stack.pop()
        for sub in base.__subclasses__():
            stack.append(sub)
            if getattr(sub, "__table__", None) is not None:
                yield sub


def _statement_froms(statement: Any) -> Iterable[Any]:
    getter = getattr(statement, "get_final_froms", None)
    if callable(getter):
        try:
            return getter() or ()
        except Exception:
            return ()
    return ()


def _statement_requires_company(statement: Any) -> bool:
    return any(
        getattr(f, "c", None) is not None and "company_id" in f.c
        for f in _statement_froms(statement)
    )


def _statement_requires_branch(statement: Any) -> bool:
    for f in _statement_froms(statement):
        cols = getattr(f, "c", None)
        if cols is None or "branch_id" not in cols:
            continue
        bc = cols.get("branch_id")
        if bc is not None and not getattr(bc, "nullable", True):
            return True
    return False


def _bypass_active(session: Session, execution_options: dict[str, Any] | None) -> bool:
    if _tenant_bypass_cv.get():
        return True
    if session.info.get(TENANT_OPTION_BYPASS):
        return True
    if execution_options and execution_options.get(TENANT_OPTION_BYPASS):
        return True
    return False


def _resolve_tenant_ids(
    execution_options: dict[str, Any] | None,
) -> tuple[Optional[int], Optional[int]]:
    exec_opts = execution_options or {}
    cid = _coerce_int(_tenant_company_id.get()) or _coerce_int(exec_opts.get(TENANT_OPTION_COMPANY))
    bid = _coerce_int(_tenant_branch_id.get()) or _coerce_int(exec_opts.get(TENANT_OPTION_BRANCH))
    return cid, bid


def _apply_tenant_criteria(orm_execute_state) -> None:
    session = orm_execute_state.session
    if _bypass_active(session, orm_execute_state.execution_options):
        return
    if not orm_execute_state.is_select:
        return

    statement = orm_execute_state.statement
    if not _statement_requires_company(statement):
        return

    company_id, branch_id = _resolve_tenant_ids(orm_execute_state.execution_options)
    strict = _strict_tenant()

    if company_id is None:
        if strict:
            raise RuntimeError(
                "Tenant company_id faltante. Usa set_tenant_context() o tenant_bypass()."
            )
        return

    if branch_id is None and strict and _statement_requires_branch(statement):
        raise RuntimeError(
            "Tenant branch_id faltante para una entidad con branch_id requerido."
        )

    _ensure_models_fresh()

    for model in _TENANT_COMPANY_MODELS:
        statement = statement.options(
            with_loader_criteria(
                model,
                lambda cls, _cid=company_id: cls.company_id == _cid,
                include_aliases=True,
            )
        )

    if branch_id is not None:
        for model in _TENANT_BRANCH_MODELS:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda cls, _bid=branch_id: cls.branch_id == _bid,
                    include_aliases=True,
                )
            )

    orm_execute_state.statement = statement


def _before_flush(session: Session, flush_context, instances) -> None:
    if _bypass_active(session, None):
        return

    _ensure_models_fresh()
    company_ctx, branch_ctx = get_tenant_context()

    for obj in session.new:
        table = getattr(obj, "__table__", None)
        if table is None:
            continue
        cols = table.c

        if "company_id" in cols and _coerce_int(getattr(obj, "company_id", None)) is None:
            if company_ctx is None:
                raise RuntimeError(
                    f"company_id faltante al crear {type(obj).__name__}. "
                    "Establece tenant_context o asigna company_id explícitamente."
                )
            obj.company_id = company_ctx

        if "branch_id" in cols:
            branch_required = not getattr(cols.get("branch_id"), "nullable", True)
            current_branch = _coerce_int(getattr(obj, "branch_id", None))
            if current_branch is None:
                if branch_required:
                    if branch_ctx is None:
                        raise RuntimeError(
                            f"branch_id faltante al crear {type(obj).__name__}."
                        )
                    obj.branch_id = branch_ctx
                elif branch_ctx is not None:
                    obj.branch_id = branch_ctx

    for obj in session.dirty:
        table = getattr(obj, "__table__", None)
        if table is None:
            continue
        insp = sa_inspect(obj, raiseerr=False)
        if insp is None:
            continue
        cols = table.c

        for tenant_col in ("company_id", "branch_id"):
            if tenant_col not in cols:
                continue
            if tenant_col == "branch_id" and getattr(cols[tenant_col], "nullable", True):
                continue
            attr = insp.attrs.get(tenant_col)
            if attr is None:
                continue
            hist = attr.history
            if not (hist.has_changes() and hist.deleted):
                continue
            old = hist.deleted[0]
            new = getattr(obj, tenant_col, None)
            if _coerce_int(old) is not None and old != new:
                raise RuntimeError(
                    f"Intento de cambiar {tenant_col} de {old!r} a {new!r} "
                    f"en {type(obj).__name__}. Operación bloqueada por seguridad."
                )


def register_tenant_listeners() -> None:
    global _TENANT_LISTENERS_INSTALLED
    if _TENANT_LISTENERS_INSTALLED:
        return
    event.listen(Session, "do_orm_execute", _apply_tenant_criteria, propagate=True)
    event.listen(Session, "before_flush", _before_flush, propagate=True)
    _TENANT_LISTENERS_INSTALLED = True
    _refresh_tenant_models()
