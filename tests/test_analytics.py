"""
Tests del dashboard de Analytics (/analytics).

Regresión del "Error interno del servidor" que aparecía al abrir Analíticas con
datos reales: la serialización para Chart.js asumía que `clasificacion` y los
nombres de tercero nunca eran NULL. En la práctica un documento RADIAN puede
quedar sin clasificar, o un tercero traer NIT pero no nombre, lo que rompía la
vista con un 500. Aquí se cubre que la página renderiza pese a esos NULL.
"""

import os

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import pytest  # noqa: E402

import app.database as db                       # noqa: E402
from app import config, authn                   # noqa: E402
from app import empresas as emp_mod             # noqa: E402
from app.web import create_app                  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    monkeypatch.setattr(config, "AUTH_MODE", "dev")
    emp_mod._sistema_listo.clear()
    authn.reset_estado()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


def _insertar_doc(db_path, cufe, clasificacion, nit_emisor, nombre_emisor,
                  nit_receptor, nombre_receptor, total):
    conn = db.get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO documentos_importados
               (cufe, clasificacion, nit_emisor, nombre_emisor,
                nit_receptor, nombre_receptor, total, fecha_emision,
                fecha_proceso, archivo_origen)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cufe, clasificacion, nit_emisor, nombre_emisor, nit_receptor,
             nombre_receptor, total, "2026-06-01", "2026-06-01T10:00:00",
             "radian.xlsx"),
        )
        conn.commit()
    finally:
        conn.close()


def test_analytics_renderiza_sin_datos(client):
    """Sin documentos, la vista responde 200 (estado vacío)."""
    resp = client.get("/analytics")
    assert resp.status_code == 200


def test_analytics_renderiza_con_clasificacion_y_nombre_nulos(client):
    """Un documento sin clasificar y un tercero sin nombre no deben dar 500.

    Antes del arreglo, la serialización hacía `clasificacion.replace(...)` y
    `nombre[:25]` sobre valores NULL, lanzando AttributeError/TypeError → 500.
    """
    db_path = config.DB_PATH
    db.inicializar_db(db_path)

    # Compra con NIT de emisor pero sin nombre y sin clasificación.
    _insertar_doc(db_path, "CUFE-1", None, "900111", None,
                  "800222", None, 1000.0)
    # Venta con clasificación válida pero receptor sin nombre.
    _insertar_doc(db_path, "CUFE-2", "COMPRA_BIEN", "900333", None,
                  "800444", None, 500.0)

    resp = client.get("/analytics")
    assert resp.status_code == 200
    cuerpo = resp.get_data(as_text=True)
    # El documento sin clasificar aparece etiquetado, no rompe la página.
    assert "Sin clasificar" in cuerpo
