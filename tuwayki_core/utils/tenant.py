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
from functools import lru_cache
from typing import Any, Iterable, Optional, Type

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql.selectable import Join
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
    # Se cuenta igual que en `_ensure_models_fresh` (con repetidos: una clase que
    # hereda de SQLModel por dos caminos aparece dos veces). Antes se guardaba
    # `len(subclasses)` (sin repetidos), los números nunca coincidían y la lista
    # de modelos se rearmaba en CADA consulta.
    _LAST_SUBCLASS_COUNT = _subclass_count()


def _subclass_count() -> int:
    return sum(1 for _ in _iter_subclasses(SQLModel))


def _ensure_models_fresh() -> None:
    if _subclass_count() != _LAST_SUBCLASS_COUNT:
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


def _leaf_froms(statement: Any) -> Iterable[Any]:
    """FROM finales con los JOIN abiertos: `a JOIN b` da `a` y `b`.

    Las columnas de un JOIN se llaman `saleitem_company_id`, `sale_company_id`…
    (no `company_id`), así que mirando el JOIN entero ninguna consulta con join
    pasaba el control y salía SIN el filtro automático.
    """
    stack = list(_statement_froms(statement))
    while stack:
        f = stack.pop()
        if isinstance(f, Join):
            stack.append(f.right)
            stack.append(f.left)
            continue
        yield f


def _statement_requires_company(statement: Any) -> bool:
    return any(
        getattr(f, "c", None) is not None and "company_id" in f.c
        for f in _leaf_froms(statement)
    )


# `get_final_froms()` arma el estado de compilación del ORM: ~1 ms por consulta. Dos
# consultas con la misma clave de caché de SQLAlchemy tienen la misma forma (solo
# cambian los valores) y por lo tanto los mismos FROM, así que la respuesta se
# recuerda por esa clave. Tope simple: al llenarse se vacía.
_REQUIRES_COMPANY_CACHE: dict[Any, bool] = {}
_REQUIRES_COMPANY_CACHE_MAX = 4096


def _statement_requires_company_cached(statement: Any) -> bool:
    try:
        cache_key = statement._generate_cache_key()
        key = cache_key.key if cache_key is not None else None
        hit = _REQUIRES_COMPANY_CACHE.get(key) if key is not None else None
    except Exception:
        key = None
        hit = None
    if hit is not None:
        return hit
    result = _statement_requires_company(statement)
    if key is not None:
        if len(_REQUIRES_COMPANY_CACHE) >= _REQUIRES_COMPANY_CACHE_MAX:
            _REQUIRES_COMPANY_CACHE.clear()
        _REQUIRES_COMPANY_CACHE[key] = result
    return result


# Las opciones del filtro se arman una vez por empresa (y por sucursal) y se reusan:
# antes se armaban las ~84 en cada consulta (~3 ms). Cada opción guarda su propio
# valor de empresa/sucursal en la closure de su lambda, que SQLAlchemy convierte en
# parámetro al ejecutar, así que reusarlas entre consultas da el mismo SQL y los
# mismos valores. La tupla de modelos va en la clave: si aparecen modelos nuevos
# (`_refresh_tenant_models`), se arman de nuevo. ~90 KB por empresa y ~70 KB por
# sucursal; con 128 de cada una, ~20 MB como máximo por proceso.
@lru_cache(maxsize=128)
def _company_criteria(models: tuple[Type[SQLModel], ...], company_id: int) -> tuple[Any, ...]:
    return tuple(
        with_loader_criteria(
            model,
            lambda cls: cls.company_id == company_id,
            include_aliases=True,
        )
        for model in models
    )


@lru_cache(maxsize=128)
def _branch_criteria(models: tuple[Type[SQLModel], ...], branch_id: int) -> tuple[Any, ...]:
    return tuple(
        with_loader_criteria(
            model,
            lambda cls: cls.branch_id == branch_id,
            include_aliases=True,
        )
        for model in models
    )


def _statement_requires_branch(statement: Any) -> bool:
    for f in _leaf_froms(statement):
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
    if not _statement_requires_company_cached(statement):
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

    # IMPORTANTE: el valor del tenant va como variable de CLOSURE, no como
    # argumento por defecto. SQLAlchemy rastrea las variables de closure de la
    # lambda y las convierte en bindparams (cache-safe); un default arg
    # (`_bid=branch_id`) NO se rastrea y hornea el PRIMER valor en la caché de
    # statements → al cambiar de sucursal se reutiliza el branch anterior y las
    # queries del nuevo branch devuelven vacío. (company_id/branch_id son
    # constantes dentro de cada armado, así que el late-binding es correcto.)
    # Ver `_company_criteria`: se arman una vez por empresa/sucursal y se agregan
    # todas en una sola llamada (antes, una copia de la consulta por cada modelo).
    criteria = _company_criteria(_TENANT_COMPANY_MODELS, company_id)
    if branch_id is not None:
        criteria += _branch_criteria(_TENANT_BRANCH_MODELS, branch_id)

    orm_execute_state.statement = statement.options(*criteria)


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
