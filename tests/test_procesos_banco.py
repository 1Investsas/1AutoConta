"""Tests del histórico persistente del módulo Bancos (tabla procesos_banco)."""

from app.database import (
    inicializar_db, registrar_proceso_banco,
    actualizar_proceso_banco, listar_procesos_banco,
)


def test_registrar_y_listar(tmp_path):
    db = str(tmp_path / "test.db")
    inicializar_db(db)
    pid = registrar_proceso_banco(
        archivo_nombre="Extracto_Mayo.csv", n_movimientos=124,
        cuenta_banco="11100501", nit_banco="860", db_path=db,
    )
    assert isinstance(pid, int)
    procesos = listar_procesos_banco(db)
    assert len(procesos) == 1
    p = procesos[0]
    assert p["archivo_nombre"] == "Extracto_Mayo.csv"
    assert p["n_movimientos"] == 124
    assert p["cuenta_banco"] == "11100501"
    assert p["nit_banco"] == "860"
    assert p["estado"] == "procesando"


def test_actualizar_a_completada(tmp_path):
    db = str(tmp_path / "test.db")
    inicializar_db(db)
    pid = registrar_proceso_banco("X.csv", n_movimientos=10, db_path=db)
    actualizar_proceso_banco(pid, estado="completada", n_movimientos=12, db_path=db)
    p = listar_procesos_banco(db)[0]
    assert p["estado"] == "completada"
    assert p["n_movimientos"] == 12


def test_actualizar_conserva_conteo_si_no_se_pasa(tmp_path):
    db = str(tmp_path / "test.db")
    inicializar_db(db)
    pid = registrar_proceso_banco("X.csv", n_movimientos=7, db_path=db)
    actualizar_proceso_banco(pid, estado="completada", db_path=db)
    assert listar_procesos_banco(db)[0]["n_movimientos"] == 7


def test_orden_descendente_y_limite(tmp_path):
    db = str(tmp_path / "test.db")
    inicializar_db(db)
    for i in range(5):
        registrar_proceso_banco(f"f{i}.csv", db_path=db)
    procesos = listar_procesos_banco(db, limite=3)
    assert len(procesos) == 3
    # El más reciente (id mayor) va primero
    assert procesos[0]["archivo_nombre"] == "f4.csv"


def test_estado_error(tmp_path):
    db = str(tmp_path / "test.db")
    inicializar_db(db)
    pid = registrar_proceso_banco("bad.csv", db_path=db)
    actualizar_proceso_banco(pid, estado="error", error="boom", db_path=db)
    p = listar_procesos_banco(db)[0]
    assert p["estado"] == "error"
    assert p["error"] == "boom"
