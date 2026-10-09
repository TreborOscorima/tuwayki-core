"""Filtro multiempresa en consultas con JOIN (tuwayki_core.utils.tenant).

Antes, el control miraba el JOIN entero, cuyas columnas se llaman
`joinitem_company_id`, `joinventa_company_id`…, no `company_id`: toda consulta con
join salía SIN el filtro automático de empresa y sucursal (y sin el error del modo
estricto cuando faltaba la empresa). Ahora se abren los JOIN y se mira cada tabla.
"""
from __future__ import annotations

from typing import Optional

import pytest
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Field, Session, SQLModel, col, create_engine, select

from tuwayki_core.utils import tenant as T


class JoinVenta(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(nullable=False, index=True)
    branch_id: int = Field(nullable=False, index=True)
    cliente_id: Optional[int] = None
    total: int = 0


class JoinItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    venta_id: int
    company_id: int = Field(nullable=False, index=True)
    branch_id: int = Field(nullable=False, index=True)
    cantidad: int = 1


class JoinCliente(SQLModel, table=True):
    """De empresa, sin sucursal."""

    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    company_id: int = Field(nullable=False, index=True)


class JoinMoneda(SQLModel, table=True):
    """Tabla global: sin company_id."""

    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str


class JoinPais(SQLModel, table=True):
    """Otra tabla global."""

    id: Optional[int] = Field(default=None, primary_key=True)
    moneda_id: int


# (venta_id, empresa, sucursal, cliente) — cada venta con un ítem de su misma
# empresa/sucursal y cantidad = venta_id, para reconocerlas en las sumas.
VENTAS = [(1, 1, 10, 1), (2, 1, 11, 1), (3, 2, 20, 2), (4, 1, 10, 2)]


@pytest.fixture()
def engine():
    T.register_tenant_listeners()
    T._refresh_tenant_models()
    T._REQUIRES_COMPANY_CACHE.clear()
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with T.tenant_bypass(), Session(eng) as s:
        s.add(JoinCliente(id=1, nombre="cliente-empresa-1", company_id=1))
        s.add(JoinCliente(id=2, nombre="cliente-empresa-2", company_id=2))
        for vid, cid, bid, cli in VENTAS:
            s.add(JoinVenta(id=vid, company_id=cid, branch_id=bid, cliente_id=cli, total=vid * 100))
            s.add(JoinItem(id=vid, venta_id=vid, company_id=cid, branch_id=bid, cantidad=vid))
        s.add(JoinMoneda(id=1, codigo="ARS"))
        s.add(JoinPais(id=1, moneda_id=1))
        s.commit()
    yield eng
    T.set_tenant_context(None, None)


def _ejecutar(engine, statement, company_id, branch_id):
    T.set_tenant_context(company_id, branch_id)
    try:
        with Session(engine) as s:
            return s.exec(statement).all()
    finally:
        T.set_tenant_context(None, None)


def _item_con_venta():
    return select(JoinItem).join(JoinVenta, col(JoinVenta.id) == col(JoinItem.venta_id))


def test_el_control_ve_las_tablas_dentro_del_join():
    assert T._statement_requires_company(_item_con_venta())
    assert T._statement_requires_branch(_item_con_venta())
    solo_empresa = select(JoinMoneda).join(JoinCliente, col(JoinCliente.id) == col(JoinMoneda.id))
    assert T._statement_requires_company(solo_empresa)
    assert not T._statement_requires_branch(solo_empresa)
    globales = select(JoinPais).join(JoinMoneda, col(JoinMoneda.id) == col(JoinPais.moneda_id))
    assert not T._statement_requires_company(globales)
    assert not T._statement_requires_branch(globales)


def test_join_sin_where_ve_solo_su_empresa_y_sucursal(engine):
    filas = _ejecutar(engine, _item_con_venta(), 1, 10)
    assert sorted(i.id for i in filas) == [1, 4]


def test_join_de_columnas_y_suma_quedan_filtrados(engine):
    columnas = select(JoinItem.id, JoinVenta.total).join(JoinVenta, col(JoinVenta.id) == col(JoinItem.venta_id))
    assert sorted(_ejecutar(engine, columnas, 1, 11)) == [(2, 200)]
    suma = select(func.sum(JoinItem.cantidad)).select_from(JoinVenta).join(
        JoinItem, col(JoinItem.venta_id) == col(JoinVenta.id)
    )
    assert _ejecutar(engine, suma, 1, 10) == [5]  # ventas 1 y 4
    assert _ejecutar(engine, suma, 2, 20) == [3]


def test_la_tabla_unida_tambien_se_filtra(engine):
    # Cliente de empresa 2 unido desde la venta 4 (empresa 1): con el filtro, el
    # outer join no trae el cliente de otra empresa.
    stmt = (
        select(JoinVenta.id, JoinCliente.nombre)
        .outerjoin(JoinCliente, col(JoinCliente.id) == col(JoinVenta.cliente_id))
        .order_by(col(JoinVenta.id))
    )
    assert _ejecutar(engine, stmt, 1, 10) == [(1, "cliente-empresa-1"), (4, None)]


def test_tabla_global_unida_a_una_de_empresa(engine):
    stmt = select(JoinMoneda.codigo, JoinVenta.id).join(JoinVenta, col(JoinVenta.id) == col(JoinMoneda.id))
    assert _ejecutar(engine, stmt, 1, 10) == [("ARS", 1)]
    assert _ejecutar(engine, stmt, 2, 20) == []


def test_join_entre_tablas_globales_no_pide_empresa(engine, monkeypatch):
    monkeypatch.setenv("TENANT_STRICT", "1")
    stmt = select(JoinPais.id, JoinMoneda.codigo).join(JoinMoneda, col(JoinMoneda.id) == col(JoinPais.moneda_id))
    assert _ejecutar(engine, stmt, None, None) == [(1, "ARS")]


def test_tres_tablas_y_alias(engine):
    VentaAlias = aliased(JoinVenta)
    stmt = (
        select(JoinItem.id, JoinCliente.nombre)
        .join(VentaAlias, col(VentaAlias.id) == col(JoinItem.venta_id))
        .join(JoinCliente, col(JoinCliente.id) == col(VentaAlias.cliente_id))
    )
    assert _ejecutar(engine, stmt, 1, 10) == [(1, "cliente-empresa-1")]
    assert _ejecutar(engine, stmt, 2, 20) == [(3, "cliente-empresa-2")]


def test_modo_estricto_exige_empresa_y_sucursal_tambien_con_join(engine, monkeypatch):
    monkeypatch.setenv("TENANT_STRICT", "1")
    with pytest.raises(RuntimeError, match="company_id faltante"):
        _ejecutar(engine, _item_con_venta(), None, None)
    with pytest.raises(RuntimeError, match="branch_id faltante"):
        _ejecutar(engine, _item_con_venta(), 1, None)
    # Join de tablas solo de empresa: alcanza con la empresa.
    stmt = select(JoinMoneda).join(JoinCliente, col(JoinCliente.id) == col(JoinMoneda.id))
    assert [m.codigo for m in _ejecutar(engine, stmt, 1, None)] == ["ARS"]


def test_el_bypass_sigue_viendo_todo_con_join(engine):
    with T.tenant_bypass(), Session(engine) as s:
        assert len(s.exec(_item_con_venta()).all()) == len(VENTAS)


def test_lo_recordado_decide_igual_con_join():
    T._REQUIRES_COMPANY_CACHE.clear()
    for _ in range(2):  # la segunda vuelta sale de lo recordado
        for stmt in (
            _item_con_venta(),
            select(JoinPais).join(JoinMoneda, col(JoinMoneda.id) == col(JoinPais.moneda_id)),
        ):
            assert T._statement_requires_company_cached(stmt) == T._statement_requires_company(stmt)
