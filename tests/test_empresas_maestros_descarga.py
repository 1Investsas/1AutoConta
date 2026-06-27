"""
Tests de descarga de archivos maestros de una empresa.

Cubren la nueva ruta `GET /empresas/<id>/maestros/<tipo>/descargar` y los
ayudantes que la sostienen (`_ref_maestro`, `_maestros_disponibles`), así como
la integración con la subida existente en la misma área de la pantalla Empresas.
"""

import os

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

from io import BytesIO  # noqa: E402

from app import config, authn                      # noqa: E402
from app import empresas as emp_mod                # noqa: E402
from app import storage as store                   # noqa: E402
from app.web import create_app                     # noqa: E402
from app.web import routes                         # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Cliente web con BD y raíz de archivos aisladas en un directorio temporal."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    monkeypatch.setattr(config, "AUTH_MODE", "dev")
    # Aísla los archivos maestros: subida y descarga deben coincidir en la misma
    # raíz temporal (de lo contrario escribiríamos dentro del repo).
    monkeypatch.setattr(store, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(routes, "_project_root", lambda: str(tmp_path))
    emp_mod._sistema_listo.clear()
    authn.reset_estado()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


def _subir(client, **archivos):
    """Sube maestros para la empresa principal vía el formulario existente."""
    data = {"empresa_id": "principal"}
    for campo, contenido in archivos.items():
        data[campo] = (BytesIO(contenido), f"{campo}.xlsx")
    return client.post("/empresas/maestros", data=data,
                       content_type="multipart/form-data")


# ── Ayudantes ───────────────────────────────────────────────────────────────

def test_ref_maestro_local(client):
    emp = emp_mod.obtener_empresa("principal")
    ref = routes._ref_maestro(emp, "Listado_de_Terceros.xlsx")
    assert ref.endswith("data/Listado_de_Terceros.xlsx")


def test_maestros_disponibles_refleja_subidas(client):
    emp = emp_mod.obtener_empresa("principal")
    assert routes._maestros_disponibles([emp]) == {"principal": []}
    _subir(client, terceros=b"contenido-terceros")
    assert routes._maestros_disponibles([emp]) == {"principal": ["terceros"]}


# ── Ruta de descarga ──────────────────────────────────────────────────────────

def test_descargar_maestro_subido(client):
    _subir(client, cuentas=b"PLAN-DE-CUENTAS")
    resp = client.get("/empresas/principal/maestros/cuentas/descargar")
    assert resp.status_code == 200
    assert resp.data == b"PLAN-DE-CUENTAS"
    cd = resp.headers["Content-Disposition"]
    assert "attachment" in cd
    assert "Listado_de_Cuentas_Contables.xlsx" in cd


def test_descargar_maestro_inexistente_redirige(client):
    resp = client.get("/empresas/principal/maestros/terceros/descargar")
    assert resp.status_code == 302
    assert "/empresas" in resp.headers["Location"]


def test_descargar_tipo_invalido_redirige(client):
    resp = client.get("/empresas/principal/maestros/inexistente/descargar")
    assert resp.status_code == 302
    assert "/empresas" in resp.headers["Location"]


def test_pagina_empresas_muestra_enlace_de_descarga(client):
    resp = client.get("/empresas")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # El control de descarga y su cableado JS están presentes en el área de subida.
    assert 'class="maestro-dl"' in html
    assert 'data-tipo="terceros"' in html
    assert "empresas_maestros_descargar" not in html  # se usa la url ya resuelta
