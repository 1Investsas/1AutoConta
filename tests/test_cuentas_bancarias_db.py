"""
Tests de las cuentas bancarias de terceros (tabla cuentas_bancarias_tercero).

Cubre el CRUD de la BD: registrar (con UPSERT por NIT+cuenta), listar (todas y
por tercero), contar y eliminar.
"""

import pytest

from app.database import (
    inicializar_db,
    registrar_cuenta_bancaria_tercero,
    listar_cuentas_bancarias_tercero,
    contar_cuentas_bancarias_tercero,
    eliminar_cuenta_bancaria_tercero,
)


@pytest.fixture
def db_tmp(tmp_path):
    """Base de datos SQLite temporal en disco."""
    db_path = str(tmp_path / "test_cuentas_banco.db")
    inicializar_db(db_path)
    return db_path


def _registrar_pj(db_tmp, **over):
    datos = dict(
        nit_tercero="900669897",
        numero_cuenta="55116315903",
        nombre_tercero="CHICA BOTERO SAS",
        tipo_documento="NIT",
        banco="BANCOLOMBIA S.A.",
        tipo_producto="CUENTA DE AHORROS",
        fecha_apertura="2013/11/26",
        estado="ACTIVA",
        archivo_origen="cert_pj.pdf",
        db_path=db_tmp,
    )
    datos.update(over)
    registrar_cuenta_bancaria_tercero(**datos)


class TestRegistrarYListar:
    def test_vacio_al_inicio(self, db_tmp):
        assert listar_cuentas_bancarias_tercero(db_tmp) == []
        assert contar_cuentas_bancarias_tercero(db_tmp) == 0

    def test_registrar_y_listar(self, db_tmp):
        _registrar_pj(db_tmp)
        cuentas = listar_cuentas_bancarias_tercero(db_tmp)
        assert len(cuentas) == 1
        c = cuentas[0]
        assert c["nit_tercero"] == "900669897"
        assert c["numero_cuenta"] == "55116315903"
        assert c["nombre_tercero"] == "CHICA BOTERO SAS"
        assert c["banco"] == "BANCOLOMBIA S.A."
        assert c["estado"] == "ACTIVA"

    def test_nit_o_cuenta_vacios_no_registra(self, db_tmp):
        registrar_cuenta_bancaria_tercero("", "555", db_path=db_tmp)
        registrar_cuenta_bancaria_tercero("900", "", db_path=db_tmp)
        assert contar_cuentas_bancarias_tercero(db_tmp) == 0


class TestUpsert:
    def test_misma_cuenta_no_duplica_y_actualiza(self, db_tmp):
        _registrar_pj(db_tmp, estado="ACTIVA")
        _registrar_pj(db_tmp, estado="INACTIVA")
        cuentas = listar_cuentas_bancarias_tercero(db_tmp)
        assert len(cuentas) == 1
        assert cuentas[0]["estado"] == "INACTIVA"

    def test_misma_persona_distinta_cuenta_son_dos(self, db_tmp):
        _registrar_pj(db_tmp)
        _registrar_pj(db_tmp, numero_cuenta="99999999999", tipo_producto="CUENTA CORRIENTE")
        assert contar_cuentas_bancarias_tercero(db_tmp) == 2


class TestFiltrarPorTercero:
    def test_filtra_por_nit(self, db_tmp):
        _registrar_pj(db_tmp)
        registrar_cuenta_bancaria_tercero(
            nit_tercero="1000398865", numero_cuenta="33118436798",
            nombre_tercero="JUAN DAVID MUNERA LOPEZ", tipo_documento="CC",
            banco="BANCOLOMBIA S.A.", db_path=db_tmp,
        )
        assert contar_cuentas_bancarias_tercero(db_tmp) == 2
        solo_pn = listar_cuentas_bancarias_tercero(db_tmp, nit_tercero="1000398865")
        assert len(solo_pn) == 1
        assert solo_pn[0]["nombre_tercero"] == "JUAN DAVID MUNERA LOPEZ"


class TestEliminar:
    def test_eliminar_por_id(self, db_tmp):
        _registrar_pj(db_tmp)
        cuenta_id = listar_cuentas_bancarias_tercero(db_tmp)[0]["id"]
        eliminar_cuenta_bancaria_tercero(cuenta_id, db_tmp)
        assert contar_cuentas_bancarias_tercero(db_tmp) == 0

    def test_eliminar_id_inexistente_no_falla(self, db_tmp):
        _registrar_pj(db_tmp)
        eliminar_cuenta_bancaria_tercero(99999, db_tmp)
        assert contar_cuentas_bancarias_tercero(db_tmp) == 1
