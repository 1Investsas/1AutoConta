"""
Tests del modelo durable del módulo Bancos (snapshot + retomar/corregir/anular).

Cubren:
- La migración aditiva de las columnas `archivo_ref` y `snapshot_json`.
- El round-trip del snapshot durable y que `actualizar_proceso_banco` conserve el
  snapshot previo en transiciones de estado (COALESCE).
- Que `listar_procesos_banco` exponga `tiene_snapshot` sin traer el JSON pesado.
- Las rutas web: «Retomar/Corregir» (abrir conservando asignaciones) y «Anular».
"""

import json
import os
import sqlite3

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import app.database as db                      # noqa: E402
from app import config                          # noqa: E402
from app.web import create_app                  # noqa: E402
from app.web.routes import KEY_BANCO            # noqa: E402


def _snap(proceso_id):
    """Snapshot mínimo válido: un movimiento con su asignación."""
    return {
        "proceso_id": proceso_id,
        "movimientos": [{
            "idx": 0, "cuenta_banco_num": "001", "codigo_banco": "01",
            "fecha": "2026-05-01", "valor": "100000", "codigo_detalle": "10",
            "descripcion": "PAGO", "es_4x1000": False, "es_bancario": False,
            "idx_padre": None,
        }],
        "cuenta_banco": "11100501", "nit_banco": "860",
        "asignaciones": [{"idx": 0, "cuenta_contrapartida": "41350101",
                          "nit_tercero": "900", "tipo_comprobante": "111"}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Nivel BD
# ═══════════════════════════════════════════════════════════════════════════

def test_migracion_agrega_columnas_banco(tmp_path):
    """Una BD con el esquema viejo de `procesos_banco` gana las columnas nuevas."""
    p = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(p)
    conn.execute("""
        CREATE TABLE procesos_banco (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL,
            archivo_nombre TEXT, cuenta_banco TEXT, nit_banco TEXT,
            n_movimientos INTEGER DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'procesando', error TEXT
        )
    """)
    conn.execute(
        "INSERT INTO procesos_banco (fecha, archivo_nombre, estado) "
        "VALUES ('2025-01-01T00:00:00','viejo.csv','completada')"
    )
    conn.commit()
    conn.close()

    db.inicializar_db(p)  # debe ALTER ADD archivo_ref + snapshot_json

    cols = [c[1] for c in sqlite3.connect(p)
            .execute("PRAGMA table_info(procesos_banco)").fetchall()]
    assert "archivo_ref" in cols
    assert "snapshot_json" in cols

    row = db.listar_procesos_banco(p)[0]
    assert row["archivo_nombre"] == "viejo.csv"
    assert row["tiene_snapshot"] == 0


def test_snapshot_round_trip_banco(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("x.csv", archivo_ref="uploads/x", db_path=p)

    snap = _snap(pid)
    db.actualizar_proceso_banco(
        pid, estado="completada", snapshot_json=json.dumps(snap), db_path=p,
    )

    assert db.obtener_snapshot_proceso_banco(pid, db_path=p) == snap

    row = db.listar_procesos_banco(p)[0]
    assert row["tiene_snapshot"] == 1
    assert row["archivo_ref"] == "uploads/x"
    # El listado NO arrastra el JSON pesado.
    assert "snapshot_json" not in row


def test_transicion_conserva_snapshot_banco(tmp_path):
    """Cambiar el estado sin reenviar el snapshot debe conservar el anterior (COALESCE)."""
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("x.csv", db_path=p)

    db.actualizar_proceso_banco(
        pid, estado="procesando", snapshot_json=json.dumps({"v": 1}), db_path=p,
    )
    db.actualizar_proceso_banco(pid, estado="completada", db_path=p)

    assert db.obtener_snapshot_proceso_banco(pid, db_path=p) == {"v": 1}
    assert db.listar_procesos_banco(p)[0]["estado"] == "completada"


def test_obtener_proceso_banco(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("x.csv", n_movimientos=3, db_path=p)
    proc = db.obtener_proceso_banco(pid, db_path=p)
    assert proc and proc["id"] == pid and proc["n_movimientos"] == 3
    assert db.obtener_proceso_banco(9999, db_path=p) is None


# ═══════════════════════════════════════════════════════════════════════════
# Nivel rutas web
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(tmp_path, monkeypatch):
    # La empresa principal usa config.DB_PATH; redirigirlo a un tmp aísla la BD.
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    import app.empresas as emp_mod
    emp_mod._sistema_listo.clear()

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()


def _dbp(tmp_path):
    return str(tmp_path / "contable.db")


def test_abrir_carga_snapshot_banco(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("X.csv", archivo_ref="uploads/x", db_path=p)
    db.actualizar_proceso_banco(
        pid, estado="completada", snapshot_json=json.dumps(_snap(pid)), db_path=p,
    )

    resp = client.post(f"/banco/historial/{pid}/abrir")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # La asignación guardada se restaura como valor de la cuenta contrapartida.
    assert "41350101" in body

    with client.session_transaction() as sess:
        assert sess.get(KEY_BANCO)


def test_abrir_sin_snapshot_redirige(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("X.csv", db_path=p)

    resp = client.post(f"/banco/historial/{pid}/abrir")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/banco/historial")


def test_anular_proceso_banco(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("X.csv", n_movimientos=5, db_path=p)

    resp = client.post(f"/banco/historial/{pid}/anular")
    assert resp.status_code == 302
    row = db.listar_procesos_banco(p)[0]
    assert row["estado"] == "anulada"
    # No se pierde el conteo al anular.
    assert row["n_movimientos"] == 5


def test_historial_banco_render_menu(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    pid = db.registrar_proceso_banco("X.csv", archivo_ref="uploads/x", db_path=p)
    db.actualizar_proceso_banco(
        pid, estado="completada", snapshot_json=json.dumps(_snap(pid)), db_path=p,
    )

    resp = client.get("/banco/historial")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Historial de Automatizaciones Bancos" in body
    # Un proceso ya exportado (completada) ofrece «Corregir».
    assert "Corregir" in body
