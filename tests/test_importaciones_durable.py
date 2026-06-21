"""
Tests del modelo durable de importaciones (Fase 2 — parte 2).

Cubren:
- La migración aditiva de la columna `preasientos_json` en BD ya existentes.
- El round-trip del snapshot durable y que `actualizar_importacion` conserve el
  snapshot/Excel previos en transiciones de estado (COALESCE).
- Que `listar_importaciones` exponga `tiene_snapshot` sin traer el JSON pesado.
- Las rutas web: «Abrir» (retomar conservando correcciones), que editar persista
  el snapshot como 'corregida', y «Anular».
"""

import json
import os
import sqlite3

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import app.database as db                      # noqa: E402
from app import config                          # noqa: E402
from app import storage as store                # noqa: E402
from app.web import create_app                  # noqa: E402
from app.web.routes import KEY_RESULTADO        # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Nivel BD
# ═══════════════════════════════════════════════════════════════════════════

def test_migracion_agrega_columna_preasientos_json(tmp_path):
    """Una BD con el esquema viejo de `importaciones` gana la columna sin perder datos."""
    p = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(p)
    conn.execute("""
        CREATE TABLE importaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL,
            archivo_nombre TEXT, archivo_ref TEXT, n_docs INTEGER DEFAULT 0,
            n_excepciones INTEGER DEFAULT 0, excel_ref TEXT,
            estado TEXT NOT NULL DEFAULT 'procesando', error TEXT
        )
    """)
    conn.execute(
        "INSERT INTO importaciones (fecha, archivo_nombre, estado) "
        "VALUES ('2025-01-01T00:00:00','viejo.xlsx','completada')"
    )
    conn.commit()
    conn.close()

    db.inicializar_db(p)  # debe ALTER ADD preasientos_json (migración aditiva)

    cols = [c[1] for c in sqlite3.connect(p)
            .execute("PRAGMA table_info(importaciones)").fetchall()]
    assert "preasientos_json" in cols

    imps = db.listar_importaciones(p)
    assert len(imps) == 1
    assert imps[0]["archivo_nombre"] == "viejo.xlsx"
    assert imps[0]["tiene_snapshot"] == 0


def test_snapshot_round_trip(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("x.xlsx", "uploads/x", db_path=p)

    snap = {"importacion_id": imp_id, "n_docs": 1, "n_excepciones": 0,
            "preasientos": [{"cufe_full": "A", "lineas": []}]}
    db.actualizar_importacion(
        imp_id, estado="corregida", n_docs=1, n_excepciones=0,
        preasientos_json=json.dumps(snap), db_path=p,
    )

    assert db.obtener_snapshot_importacion(imp_id, db_path=p) == snap

    row = db.listar_importaciones(p)[0]
    assert row["tiene_snapshot"] == 1
    assert row["estado"] == "corregida"
    # El listado NO arrastra el JSON pesado.
    assert "preasientos_json" not in row


def test_transicion_estado_conserva_snapshot(tmp_path):
    """Cambiar el estado sin reenviar el snapshot debe conservar el anterior (COALESCE)."""
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("x.xlsx", "uploads/x", db_path=p)

    db.actualizar_importacion(
        imp_id, estado="procesada", n_docs=2,
        preasientos_json=json.dumps({"v": 1}), db_path=p,
    )
    db.actualizar_importacion(imp_id, estado="exportada", n_docs=2, db_path=p)

    assert db.obtener_snapshot_importacion(imp_id, db_path=p) == {"v": 1}
    assert db.listar_importaciones(p)[0]["estado"] == "exportada"


def test_snapshot_ausente_retorna_none(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("x.xlsx", "uploads/x", db_path=p)
    assert db.obtener_snapshot_importacion(imp_id, db_path=p) is None


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


def _seed_session(client, datos):
    ref = store.save_file(
        json.dumps(datos, ensure_ascii=False).encode("utf-8"),
        "web_sessions", "test_durable.json",
    )
    with client.session_transaction() as sess:
        sess[KEY_RESULTADO] = ref


def _preasiento_divisible(imp_id):
    return {
        "importacion_id": imp_id, "n_docs": 1, "n_excepciones": 1,
        "excepciones": [], "excel_path": "", "archivo_origen": "",
        "preasientos": [{
            "cufe": "CUFE-FC-001", "cufe_full": "CUFE-FC-001",
            "clasificacion": "FACTURA_COMPRA", "tipo_documento": "Factura electrónica",
            "codigo_comprobante": "50", "titulo_comprobante": "Facturas de compra",
            "base_gravable": 1000000.0, "fecha_emision": "01/03/2025",
            "folio": "2001", "prefijo": "FC", "tercero_nit": "800123456",
            "tercero_nombre": "PROVEEDOR SA", "tercero_encontrado": True,
            "tercero_nit_original": "800123456", "tercero_corregido": False,
            "total": 1000000.0, "cuadra": True,
            "excepciones": ["1 línea(s) con cuenta [PENDIENTE]"],
            "lineas": [
                {"numero_linea": 1, "cuenta": "22050501",
                 "descripcion_cuenta": "Proveedores nacionales",
                 "debito": 0.0, "credito": 1000000.0, "concepto": "CxP",
                 "es_pendiente": False, "es_sugerida": False},
                {"numero_linea": 2, "cuenta": "[PENDIENTE]",
                 "descripcion_cuenta": "Gasto/Costo",
                 "debito": 1000000.0, "credito": 0.0, "concepto": "Base",
                 "es_pendiente": True, "es_sugerida": False},
            ],
        }],
    }


def test_abrir_carga_snapshot_en_sesion(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("RAD.xlsx", "uploads/RAD", db_path=p)
    snap = {"importacion_id": imp_id, "n_docs": 1, "n_excepciones": 0,
            "excel_path": "", "preasientos": [{"cufe_full": "Z", "lineas": []}]}
    db.actualizar_importacion(
        imp_id, estado="corregida", n_docs=1,
        preasientos_json=json.dumps(snap), db_path=p,
    )

    resp = client.post(f"/importaciones/{imp_id}/abrir")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/resultado")

    with client.session_transaction() as sess:
        ref = sess.get(KEY_RESULTADO)
    assert ref
    cargado = json.loads(store.get_download_bytes(ref).decode("utf-8"))
    assert cargado["preasientos"][0]["cufe_full"] == "Z"
    assert cargado["importacion_id"] == imp_id


def test_abrir_sin_snapshot_redirige_con_error(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("RAD.xlsx", "uploads/RAD", db_path=p)

    resp = client.post(f"/importaciones/{imp_id}/abrir")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/importaciones")
    with client.session_transaction() as sess:
        assert sess.get(KEY_RESULTADO) is None


def test_dividir_linea_persiste_snapshot_corregida(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("RAD.xlsx", "uploads/RAD", db_path=p)
    _seed_session(client, _preasiento_divisible(imp_id))

    resp = client.post("/dividir-linea", data={
        "cufe_full": "CUFE-FC-001", "numero_linea": "2",
        "parte_cuenta": ["51050501", "53050501"],
        "parte_monto": ["600000", "400000"],
        "parte_concepto": ["Capital", "Intereses"],
    })
    assert resp.status_code == 302

    # El snapshot durable refleja la división y queda en estado 'corregida'.
    snap = db.obtener_snapshot_importacion(imp_id, db_path=p)
    assert snap is not None
    assert len(snap["preasientos"][0]["lineas"]) == 3
    assert db.listar_importaciones(p)[0]["estado"] == "corregida"


def test_anular_marca_estado(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("RAD.xlsx", "uploads/RAD", db_path=p)
    db.actualizar_importacion(imp_id, estado="procesada", n_docs=3, db_path=p)

    resp = client.post(f"/importaciones/{imp_id}/anular")
    assert resp.status_code == 302
    row = db.listar_importaciones(p)[0]
    assert row["estado"] == "anulada"
    # No se pierden los conteos al anular.
    assert row["n_docs"] == 3


def test_importaciones_render_muestra_retomar_y_estado(client, tmp_path):
    p = _dbp(tmp_path)
    db.inicializar_db(p)
    imp_id = db.registrar_importacion("RAD.xlsx", "uploads/RAD", db_path=p)
    db.actualizar_importacion(
        imp_id, estado="corregida",
        preasientos_json=json.dumps({"importacion_id": imp_id, "preasientos": []}),
        db_path=p,
    )

    resp = client.get("/importaciones")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # La acción «Abrir» se renombró a «Retomar» en el menú de 3 puntos.
    assert "Retomar" in body
    assert "Corregida" in body
