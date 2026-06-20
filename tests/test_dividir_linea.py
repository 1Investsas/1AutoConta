"""
Tests del endpoint POST /dividir-linea (Fase 1 — dividir/agregar movimientos).

Verifican que una línea de un preasiento RADIAN se pueda partir en varias
cuentas conservando el lado contable (débito/crédito) y el cuadre, y que las
validaciones (suma ≠ original, cuenta faltante, < 2 partes) rechacen la división
dejando el resultado intacto.
"""

import json
import os

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

from app.web import create_app          # noqa: E402
from app import storage as store         # noqa: E402
from app.web.routes import KEY_RESULTADO  # noqa: E402


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


def _make_datos():
    """Preasiento de factura de compra: CxP (crédito) + base/gasto pendiente (débito)."""
    return {
        "n_docs": 1,
        "n_excepciones": 1,
        "excepciones": [],
        "excel_path": "",
        "archivo_origen": "",
        "preasientos": [{
            "cufe": "CUFE-FC-001",
            "cufe_full": "CUFE-FC-001",
            "clasificacion": "FACTURA_COMPRA",
            "tipo_documento": "Factura electrónica",
            "codigo_comprobante": "50",
            "titulo_comprobante": "Facturas de compra",
            "base_gravable": 1000000.0,
            "fecha_emision": "01/03/2025",
            "folio": "2001",
            "prefijo": "FC",
            "tercero_nit": "800123456",
            "tercero_nombre": "PROVEEDOR SA",
            "tercero_encontrado": True,
            "tercero_nit_original": "800123456",
            "tercero_corregido": False,
            "total": 1000000.0,
            "cuadra": True,
            "excepciones": ["1 línea(s) con cuenta [PENDIENTE]"],
            "lineas": [
                {"numero_linea": 1, "cuenta": "22050501",
                 "descripcion_cuenta": "Proveedores nacionales",
                 "debito": 0.0, "credito": 1000000.0, "concepto": "CxP Proveedor",
                 "es_pendiente": False, "es_sugerida": False},
                {"numero_linea": 2, "cuenta": "[PENDIENTE]",
                 "descripcion_cuenta": "Gasto/Costo",
                 "debito": 1000000.0, "credito": 0.0, "concepto": "Base gravable",
                 "es_pendiente": True, "es_sugerida": False},
            ],
        }],
    }


def _seed(client, datos):
    """Guarda `datos` en el storage y deja la referencia en la sesión del cliente."""
    payload = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    ref = store.save_file(payload, "web_sessions", "test_dividir.json")
    with client.session_transaction() as sess:
        sess[KEY_RESULTADO] = ref
    return ref


def _leer(ref):
    return json.loads(store.get_download_bytes(ref).decode("utf-8"))


def test_dividir_linea_debito_ok(client):
    """Partir la base/gasto pendiente (débito) en capital + intereses."""
    ref = _seed(client, _make_datos())
    resp = client.post("/dividir-linea", data={
        "cufe_full": "CUFE-FC-001",
        "numero_linea": "2",
        "parte_cuenta": ["51050501", "53050501"],
        "parte_monto": ["600000", "400000"],
        "parte_concepto": ["Capital", "Intereses"],
    })
    assert resp.status_code == 302

    p = _leer(ref)["preasientos"][0]
    lineas = p["lineas"]
    assert len(lineas) == 3
    # Renumeración consecutiva
    assert [l["numero_linea"] for l in lineas] == [1, 2, 3]

    partes = [l for l in lineas if l["cuenta"] in ("51050501", "53050501")]
    assert len(partes) == 2
    assert sum(l["debito"] for l in partes) == 1000000.0
    assert all(l["credito"] == 0.0 for l in partes)
    assert all(not l["es_pendiente"] for l in partes)
    # Conceptos personalizados conservados
    assert {l["concepto"] for l in partes} == {"Capital", "Intereses"}

    # Cuadra y ya no quedan líneas pendientes
    assert p["cuadra"] is True
    assert p["excepciones"] == []


def test_dividir_linea_credito_ok(client):
    """Partir la línea de crédito (CxP) conserva el lado crédito y el cuadre."""
    ref = _seed(client, _make_datos())
    resp = client.post("/dividir-linea", data={
        "cufe_full": "CUFE-FC-001",
        "numero_linea": "1",
        "parte_cuenta": ["22050501", "23659001"],
        "parte_monto": ["700000", "300000"],
    })
    assert resp.status_code == 302

    p = _leer(ref)["preasientos"][0]
    lineas = p["lineas"]
    assert len(lineas) == 3
    nuevas = [l for l in lineas if l["numero_linea"] in (1, 2) and l["credito"] > 0]
    assert sum(l["credito"] for l in nuevas) == 1000000.0
    assert all(l["debito"] == 0.0 for l in nuevas)
    assert p["cuadra"] is True
    # Sigue habiendo una pendiente (la base no se tocó)
    assert any("PENDIENTE" in e for e in p["excepciones"])


def test_dividir_linea_suma_invalida(client):
    """Si la suma de las partes no iguala el original, no se modifica nada."""
    ref = _seed(client, _make_datos())
    resp = client.post("/dividir-linea", data={
        "cufe_full": "CUFE-FC-001",
        "numero_linea": "2",
        "parte_cuenta": ["51050501", "53050501"],
        "parte_monto": ["600000", "300000"],  # 900k ≠ 1M
    })
    assert resp.status_code == 302
    p = _leer(ref)["preasientos"][0]
    assert len(p["lineas"]) == 2  # intacto


def test_dividir_linea_cuenta_faltante(client):
    """Una parte sin cuenta rechaza la división."""
    ref = _seed(client, _make_datos())
    client.post("/dividir-linea", data={
        "cufe_full": "CUFE-FC-001",
        "numero_linea": "2",
        "parte_cuenta": ["51050501", ""],
        "parte_monto": ["600000", "400000"],
    })
    p = _leer(ref)["preasientos"][0]
    assert len(p["lineas"]) == 2  # intacto


def test_dividir_linea_una_sola_parte(client):
    """Una sola parte (no es división) se rechaza."""
    ref = _seed(client, _make_datos())
    client.post("/dividir-linea", data={
        "cufe_full": "CUFE-FC-001",
        "numero_linea": "2",
        "parte_cuenta": ["51050501"],
        "parte_monto": ["1000000"],
    })
    p = _leer(ref)["preasientos"][0]
    assert len(p["lineas"]) == 2  # intacto


def test_dividir_linea_documento_inexistente(client):
    """Un cufe inexistente no rompe ni modifica el resultado."""
    ref = _seed(client, _make_datos())
    resp = client.post("/dividir-linea", data={
        "cufe_full": "NO-EXISTE",
        "numero_linea": "2",
        "parte_cuenta": ["51050501", "53050501"],
        "parte_monto": ["600000", "400000"],
    })
    assert resp.status_code == 302
    p = _leer(ref)["preasientos"][0]
    assert len(p["lineas"]) == 2  # intacto
