"""Filtro multiempresa armado una vez por empresa/sucursal (tuwayki_core.utils.tenant).

El filtro tiene que dar EXACTAMENTE el mismo SQL que la versión anterior (que armaba
las opciones en cada consulta) y cada empresa/sucursal tiene que ver solo lo suyo,
aunque se alternen y se reusen las opciones guardadas.
"""
from __future__ import annotations

from typing import Optional

import pytest
from sqlalchemy.orm import with_loader_criteria
from sqlmodel import Field, Session, SQLModel, create_engine, select

from tuwayki_core.utils import tenant as T


class FiltroProducto(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    company_id: int = Field(nullable=False, index=True)
    branch_id: int = Field(nullable=False, index=True)


class FiltroCliente(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    company_id: int = Field(nullable=False, index=True)


class FiltroMoneda(SQLModel, table=True):
    """Tabla global: sin company_id, nunca se filtra."""

    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str


def _old_apply(statement, company_id, branch_id):
    """La versión anterior: una opción por modelo, agregada de a una."""
    for model in T._TENANT_COMPANY_MODELS:
        statement = statement.options(
            with_loader_criteria(model, lambda cls: cls.company_id == company_id, include_aliases=True)
        )
    if branch_id is not None:
        for model in T._TENANT_BRANCH_MODELS:
            statement = statement.options(
                with_loader_criteria(model, lambda cls: cls.branch_id == branch_id, include_aliases=True)
            )
    return statement


def _new_apply(statement, company_id, branch_id):
    criteria = T._company_criteria(T._TENANT_COMPANY_MODELS, company_id)
    if branch_id is not None:
        criteria += T._branch_criteria(T._TENANT_BRANCH_MODELS, branch_id)
    return statement.options(*criteria)


def _compiled(statement, engine):
    compiled = statement.compile(engine)
    return str(compiled), compiled.params


@pytest.fixture()
def engine():
    T.register_tenant_listeners()
    T._refresh_tenant_models()
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with T.tenant_bypass(), Session(eng) as s:
        for cid, bid, nombre in [(1, 10, "a1"), (1, 11, "a1-otra-sucursal"), (2, 20, "b2"), (3, 30, "c3")]:
            s.add(FiltroProducto(nombre=nombre, company_id=cid, branch_id=bid))
        for cid, nombre in [(1, "cliente-1"), (2, "cliente-2")]:
            s.add(FiltroCliente(nombre=nombre, company_id=cid))
        s.add(FiltroMoneda(codigo="ARS"))
        s.commit()
    yield eng
    T.set_tenant_context(None, None)


def _consultas():
    return [
        select(FiltroProducto),
        select(FiltroProducto).where(FiltroProducto.nombre == "a1"),
        select(FiltroCliente).order_by(FiltroCliente.id),
        select(FiltroProducto.id, FiltroCliente.id),
        select(FiltroProducto).where(FiltroProducto.id.in_(select(FiltroCliente.id))),
        select(FiltroProducto).join(FiltroCliente, FiltroCliente.id == FiltroProducto.id),
        select(FiltroMoneda),
    ]


@pytest.mark.parametrize("company_id, branch_id", [(1, 10), (2, None), (3, 30)])
def test_el_sql_es_el_mismo_que_antes(engine, company_id, branch_id):
    for stmt in _consultas():
        assert _compiled(_new_apply(stmt, company_id, branch_id), engine) == _compiled(
            _old_apply(stmt, company_id, branch_id), engine
        )


def test_decide_igual_que_antes_que_consultas_son_de_empresa():
    T._REQUIRES_COMPANY_CACHE.clear()
    for _ in range(2):  # la segunda vuelta sale de lo recordado
        for stmt in _consultas():
            assert T._statement_requires_company_cached(stmt) == T._statement_requires_company(stmt)


def _nombres(engine, company_id, branch_id):
    T.set_tenant_context(company_id, branch_id)
    try:
        with Session(engine) as s:
            return sorted(p.nombre for p in s.exec(select(FiltroProducto)).all())
    finally:
        T.set_tenant_context(None, None)


def test_empresas_y_sucursales_alternadas_ven_solo_lo_suyo(engine):
    esperado = {
        (1, 10): ["a1"],
        (1, 11): ["a1-otra-sucursal"],
        (2, 20): ["b2"],
        (3, 30): ["c3"],
        (2, 10): [],  # sucursal de otra empresa: nada
    }
    for _ in range(3):  # las opciones guardadas se reusan en cada vuelta
        for (cid, bid), nombres in esperado.items():
            assert _nombres(engine, cid, bid) == nombres, (cid, bid)
        for cid, nombre in [(1, "cliente-1"), (2, "cliente-2"), (3, None)]:
            T.set_tenant_context(cid, None)  # tabla sin sucursal: solo empresa
            with Session(engine) as s:
                assert [c.nombre for c in s.exec(select(FiltroCliente)).all()] == ([nombre] if nombre else [])
            T.set_tenant_context(None, None)


def test_el_bypass_sigue_viendo_todo(engine):
    _nombres(engine, 1, 10)  # deja opciones guardadas
    with T.tenant_bypass(), Session(engine) as s:
        assert len(s.exec(select(FiltroProducto)).all()) == 4


def test_las_tablas_globales_no_se_filtran(engine):
    T.set_tenant_context(2, 20)
    with Session(engine) as s:
        assert [m.codigo for m in s.exec(select(FiltroMoneda)).all()] == ["ARS"]


def test_al_llenarse_lo_recordado_se_vacia_y_sigue_filtrando(engine, monkeypatch):
    monkeypatch.setattr(T, "_REQUIRES_COMPANY_CACHE_MAX", 3)
    T._REQUIRES_COMPANY_CACHE.clear()
    for stmt in _consultas() * 2:
        T._statement_requires_company_cached(stmt)
        assert len(T._REQUIRES_COMPANY_CACHE) <= 3
    assert _nombres(engine, 2, 20) == ["b2"]


def test_las_opciones_se_arman_una_vez_por_empresa(engine):
    T._company_criteria.cache_clear()
    for _ in range(5):
        _nombres(engine, 1, 10)
        _nombres(engine, 2, 20)
    info = T._company_criteria.cache_info()
    assert info.misses == 2 and info.hits >= 8


def test_un_modelo_nuevo_entra_en_el_filtro(engine):
    _nombres(engine, 1, 10)

    class FiltroTardio(SQLModel, table=True):
        id: Optional[int] = Field(default=None, primary_key=True)
        company_id: int = Field(nullable=False, index=True)

    SQLModel.metadata.create_all(engine, tables=[FiltroTardio.__table__])
    with T.tenant_bypass(), Session(engine) as s:
        s.add(FiltroTardio(company_id=1))
        s.add(FiltroTardio(company_id=2))
        s.commit()
    T.set_tenant_context(2, None)
    with Session(engine) as s:
        assert [t.company_id for t in s.exec(select(FiltroTardio)).all()] == [2]


class _FiltroMixinEmpresa(SQLModel):
    company_id: int = Field(nullable=False, index=True)


class FiltroDiamante(_FiltroMixinEmpresa, SQLModel, table=True):
    """Hereda de SQLModel por dos caminos, como los modelos con TenantMixin."""

    id: Optional[int] = Field(default=None, primary_key=True)


def test_la_lista_de_modelos_no_se_rearma_en_cada_consulta(monkeypatch):
    T._refresh_tenant_models()
    rearmados = []
    original = T._refresh_tenant_models
    monkeypatch.setattr(T, "_refresh_tenant_models", lambda: (rearmados.append(1), original()))
    for _ in range(5):
        T._ensure_models_fresh()
    assert rearmados == []
    assert FiltroDiamante in T._TENANT_COMPANY_MODELS
