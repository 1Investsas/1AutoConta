"""
Tests del módulo Flujos Mixtos.

Flujos Mixtos funciona igual que Caja General pero SIN límite de período
mensual/anual: un «flujo» puede cubrir cualquier rango de fechas o correr de
forma continua. Reutiliza el modelo de dominio, la plantilla, el importador y el
exportador SIIGO de Caja General; solo cambia el almacenamiento (tablas mixed_*).

Cubren:
- La persistencia (cuentas, flujos y movimientos) con round-trip.
- Las rutas web: crear cuenta/flujo, guardar movimientos con saldo automático,
  registrar movimientos de fechas de distintos meses/años sin error de período,
  transiciones de estado, plantillas y exportación SIIGO.
"""

import io
import json
import os
from decimal import Decimal

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import app.database as db                      # noqa: E402
from app import config                          # noqa: E402
from app.caja import modelo_caja as mc          # noqa: E402
from app.caja import plantilla_caja as pl       # noqa: E402
from app.web import create_app                  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Persistencia
# ═══════════════════════════════════════════════════════════════════════════

def test_round_trip_cuenta_flujo_movimientos(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    acc = db.crear_mixed_account("Flujo mixto", account_code="11050501", db_path=p)
    assert db.obtener_mixed_account(acc, p)["account_code"] == "11050501"

    per = db.crear_mixed_period(acc, "Flujo 2026", start_date="2026-01-01",
                                opening_balance="500000", db_path=p)
    flujo = db.obtener_mixed_period(per, p)
    assert flujo["name"] == "Flujo 2026"
    assert flujo["mixed_account_id"] == acc
    assert flujo["status"] == "borrador"

    db.reemplazar_mixed_movements(per, [{"sequence": 1, "concept": "a"}], p)
    db.reemplazar_mixed_movements(per, [{"sequence": 1, "concept": "b"},
                                        {"sequence": 2, "concept": "c"}], p)
    movs = db.listar_mixed_movements(per, p)
    assert [m["concept"] for m in movs] == ["b", "c"]


def test_flujos_no_tienen_restriccion_unica_de_periodo(tmp_path):
    # A diferencia de Caja General (UNIQUE año/mes), aquí una cuenta puede tener
    # varios flujos con el mismo nombre o rangos solapados.
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    acc = db.crear_mixed_account("Flujo mixto", account_code="1105", db_path=p)
    db.crear_mixed_period(acc, "Flujo", db_path=p)
    db.crear_mixed_period(acc, "Flujo", db_path=p)
    assert len(db.listar_mixed_periods(acc, p)) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Rutas web
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    import app.empresas as emp_mod
    import app.authn as authn
    emp_mod._sistema_listo.clear()
    authn.reset_estado()

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


def _crear_cuenta_y_flujo(client):
    r = client.post("/flujos-mixtos/cuenta",
                    data={"name": "Flujo mixto", "account_code": "11050501"})
    acc_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
    r = client.post(f"/flujos-mixtos/cuenta/{acc_id}/flujo",
                    data={"name": "Flujo 2026", "opening_balance": "500000"})
    per_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
    return acc_id, per_id


def test_ruta_landing(client):
    r = client.get("/flujos-mixtos")
    assert r.status_code == 200
    assert "Flujos Mixtos" in r.get_data(as_text=True)


def test_disponible_en_ambas_categorias(client):
    # El módulo aparece como botón tanto en Flujos indirectos como directos.
    for slug in ("flujos-indirectos", "flujos-directos"):
        r = client.get(f"/modulos/{slug}")
        assert r.status_code == 200
        assert "/flujos-mixtos" in r.get_data(as_text=True)


def test_crear_cuenta_requiere_cuenta_contable(client):
    r = client.post("/flujos-mixtos/cuenta", data={"name": "Sin cuenta"},
                    follow_redirects=True)
    assert "cuenta contable" in r.get_data(as_text=True).lower()
    assert db.listar_mixed_accounts(config.DB_PATH, incluir_inactivas=True) == []


def test_crear_cuenta_flujo_y_guardar(client):
    acc_id, per_id = _crear_cuenta_y_flujo(client)
    movs = [
        {"movement_date": "2026-06-02", "movement_type": "entrada",
         "concept": "Recaudo", "inflow_amount": 100000, "outflow_amount": 0},
        {"movement_date": "2026-06-03", "movement_type": "salida",
         "concept": "Transporte", "inflow_amount": 0, "outflow_amount": 20000},
    ]
    r = client.post(f"/flujos-mixtos/flujo/{per_id}/guardar",
                    data={"movimientos_json": json.dumps(movs), "opening_balance": "500000"})
    assert r.status_code == 302
    cp = db.obtener_mixed_period(per_id, config.DB_PATH)
    assert cp["total_inflows"] == "100000"
    assert cp["total_outflows"] == "20000"
    assert cp["closing_balance"] == "580000"
    assert len(db.listar_mixed_movements(per_id, config.DB_PATH)) == 2


def test_flujo_continuo_sin_limite_de_periodo(client):
    # El diferenciador clave: movimientos de distintos meses Y años en un mismo
    # flujo se guardan como válidos (sin error de "fuera del período").
    _, per_id = _crear_cuenta_y_flujo(client)
    movs = [
        {"movement_date": "2026-01-15", "concept": "Enero",
         "inflow_amount": 100000, "outflow_amount": 0},
        {"movement_date": "2026-08-20", "concept": "Agosto",
         "inflow_amount": 0, "outflow_amount": 30000},
        {"movement_date": "2027-03-05", "concept": "Marzo siguiente año",
         "inflow_amount": 50000, "outflow_amount": 0},
    ]
    r = client.post(f"/flujos-mixtos/flujo/{per_id}/guardar",
                    data={"movimientos_json": json.dumps(movs), "opening_balance": "0"},
                    follow_redirects=True)
    texto = r.get_data(as_text=True)
    assert "datos incompletos o inconsistentes" not in texto
    assert "fuera del período" not in texto
    cp = db.obtener_mixed_period(per_id, config.DB_PATH)
    # 100000 - 30000 + 50000 = 120000
    assert cp["closing_balance"] == "120000"
    assert len(db.listar_mixed_movements(per_id, config.DB_PATH)) == 3


def test_transiciones_de_estado(client):
    _, per_id = _crear_cuenta_y_flujo(client)
    for accion, esperado in [("enviar-revision", "en_revision"),
                             ("aprobar", "aprobado"),
                             ("cerrar", "cerrado"),
                             ("reabrir", "reabierto")]:
        client.post(f"/flujos-mixtos/flujo/{per_id}/estado/{accion}")
        assert db.obtener_mixed_period(per_id, config.DB_PATH)["status"] == esperado


def test_guardar_en_flujo_cerrado_bloqueado(client):
    _, per_id = _crear_cuenta_y_flujo(client)
    client.post(f"/flujos-mixtos/flujo/{per_id}/estado/cerrar")
    movs = [{"movement_date": "2026-06-02", "concept": "x",
             "inflow_amount": 100, "outflow_amount": 0}]
    r = client.post(f"/flujos-mixtos/flujo/{per_id}/guardar",
                    data={"movimientos_json": json.dumps(movs), "opening_balance": "0"},
                    follow_redirects=True)
    assert "cerrado o aprobado" in r.get_data(as_text=True)
    assert len(db.listar_mixed_movements(per_id, config.DB_PATH)) == 0


def test_descargar_plantillas(client):
    _, per_id = _crear_cuenta_y_flujo(client)
    r1 = client.get(f"/flujos-mixtos/flujo/{per_id}/plantilla")
    r2 = client.get(f"/flujos-mixtos/flujo/{per_id}/plantilla-prediligenciada")
    assert r1.status_code == 200 and r1.data[:2] == b"PK"
    assert r2.status_code == 200 and r2.data[:2] == b"PK"


def test_generar_siigo(client):
    _, per_id = _crear_cuenta_y_flujo(client)  # cuenta con account_code 11050501
    movs = [{"movement_date": "2026-06-02", "comprobante": "111", "concept": "Recaudo",
             "contrapartida": "41350101", "inflow_amount": 100000, "outflow_amount": 0}]
    client.post(f"/flujos-mixtos/flujo/{per_id}/guardar",
                data={"movimientos_json": json.dumps(movs), "opening_balance": "0"})
    r = client.post(f"/flujos-mixtos/flujo/{per_id}/exportar-siigo")
    assert r.status_code == 200
    assert r.data[:2] == b"PK"  # xlsx (un solo archivo, no zip)


def test_generar_siigo_sin_movimientos_avisa(client):
    _, per_id = _crear_cuenta_y_flujo(client)
    r = client.post(f"/flujos-mixtos/flujo/{per_id}/exportar-siigo", follow_redirects=True)
    assert "No hay movimientos" in r.get_data(as_text=True)


def test_importar_plantilla_continua_valida(client):
    # Plantilla generada sin mes/año (flujo continuo) con movimientos de varios
    # meses: se importa sin errores de período.
    _, per_id = _crear_cuenta_y_flujo(client)
    movs = [
        mc.a_dict(mc.desde_dict({"movement_date": "2026-02-05", "concept": "Feb",
                                 "inflow_amount": "200000"})),
        mc.a_dict(mc.desde_dict({"movement_date": "2026-11-05", "concept": "Nov",
                                 "outflow_amount": "50000"})),
    ]
    data = pl.generar_plantilla(empresa="X", cuenta_caja="Flujo mixto", anio=None,
                                mes=None, saldo_inicial="500000", movimientos=movs,
                                titulo="FLUJOS MIXTOS — MOVIMIENTOS DE EFECTIVO")
    r = client.post(f"/flujos-mixtos/flujo/{per_id}/importar",
                    data={"archivo": (io.BytesIO(data), "flujo.xlsx")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "errores de validación" not in r.get_data(as_text=True)
    cp = db.obtener_mixed_period(per_id, config.DB_PATH)
    # 500000 + 200000 - 50000 = 650000
    assert cp["closing_balance"] == "650000"
