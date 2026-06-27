"""
Tests del módulo Caja General.

Cubren:
- El modelo de dominio: parseo de montos, recálculo de saldo acumulado en orden
  cronológico, totales/cierre y validación de movimientos.
- La persistencia (cuentas, períodos y movimientos) con round-trip.
- La plantilla Excel: round-trip generar → importar y validación de filas.
- Las rutas web: crear cuenta/período, guardar movimientos con saldo automático,
  transiciones de estado y bloqueo de períodos cerrados.
"""

import io
import json
import os
from datetime import date
from decimal import Decimal

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import app.database as db                      # noqa: E402
from app import config                          # noqa: E402
from app.caja import modelo_caja as mc          # noqa: E402
from app.caja import plantilla_caja as pl       # noqa: E402
from app.caja.importador_caja import importar_plantilla  # noqa: E402
from app.web import create_app                  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Modelo de dominio
# ═══════════════════════════════════════════════════════════════════════════

def test_a_decimal_acepta_formatos():
    assert mc.a_decimal("5000") == Decimal("5000")
    assert mc.a_decimal(1234.5) == Decimal("1234.5")
    assert mc.a_decimal("1.234.567,89") == Decimal("1234567.89")
    assert mc.a_decimal("1,234,567.89") == Decimal("1234567.89")
    assert mc.a_decimal("") == Decimal("0")
    assert mc.a_decimal(None) == Decimal("0")
    # Con separador de miles y decimal explícitos no hay ambigüedad.
    assert mc.a_decimal("$ 2.000,75") == Decimal("2000.75")


def test_recalcular_saldos_orden_cronologico():
    movs = [
        mc.desde_dict({"sequence": 2, "movement_date": "2026-06-03",
                       "movement_type": "salida", "concept": "Pago", "outflow_amount": "20000"}),
        mc.desde_dict({"sequence": 1, "movement_date": "2026-06-02",
                       "movement_type": "entrada", "concept": "Recaudo", "inflow_amount": "100000"}),
    ]
    ordenados = mc.recalcular_saldos(movs, "500000")
    # Se ordenan por fecha: primero el 02, luego el 03.
    assert [m.movement_date for m in ordenados] == [date(2026, 6, 2), date(2026, 6, 3)]
    assert ordenados[0].running_balance == Decimal("600000")
    assert ordenados[1].running_balance == Decimal("580000")


def test_totales_y_saldo_final():
    movs = [
        mc.desde_dict({"movement_type": "entrada", "inflow_amount": "100000",
                       "concept": "x", "movement_date": "2026-06-01"}),
        mc.desde_dict({"movement_type": "salida", "outflow_amount": "30000",
                       "concept": "y", "movement_date": "2026-06-02"}),
    ]
    entradas, salidas = mc.totales(movs)
    assert entradas == Decimal("100000")
    assert salidas == Decimal("30000")
    assert mc.saldo_final("500000", movs) == Decimal("570000")


def test_validar_movimiento_detecta_errores():
    # Sin concepto, en cero y fuera del período.
    bad = mc.desde_dict({"movement_date": "2026-07-05", "movement_type": "entrada",
                         "concept": "", "inflow_amount": "0"})
    errores = mc.validar_movimiento(bad, 2026, 6)
    assert any("concepto" in e.lower() for e in errores)
    assert any("cero" in e.lower() for e in errores)
    assert any("período" in e.lower() or "periodo" in e.lower() for e in errores)


def test_validar_movimiento_entrada_y_salida_simultanea():
    m = mc.desde_dict({"movement_date": "2026-06-05", "movement_type": "entrada",
                       "concept": "x", "inflow_amount": "100", "outflow_amount": "50"})
    errores = mc.validar_movimiento(m, 2026, 6)
    assert any("entrada y salida" in e.lower() for e in errores)


def test_validar_movimiento_valido_sin_errores():
    m = mc.desde_dict({"movement_date": "2026-06-05", "movement_type": "salida",
                       "concept": "Compra", "outflow_amount": "50000"})
    assert mc.validar_movimiento(m, 2026, 6) == []


# ═══════════════════════════════════════════════════════════════════════════
# Persistencia
# ═══════════════════════════════════════════════════════════════════════════

def test_db_round_trip(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    acc = db.crear_cash_account("Caja menor", "Gastos", responsible="Ana", db_path=p)
    assert db.obtener_cash_account(acc, p)["name"] == "Caja menor"

    per = db.crear_cash_period(acc, 2026, 6, opening_balance="500000", db_path=p)
    assert db.obtener_cash_period_por_mes(acc, 2026, 6, p)["id"] == per

    movs = [
        {"sequence": 1, "movement_date": "2026-06-02", "movement_type": "entrada",
         "concept": "Recaudo", "inflow_amount": "100000", "outflow_amount": "0",
         "running_balance": "600000"},
    ]
    db.reemplazar_cash_movements(per, movs, p)
    guardados = db.listar_cash_movements(per, p)
    assert len(guardados) == 1
    assert guardados[0]["concept"] == "Recaudo"

    db.actualizar_cash_period_saldos(per, "500000", "100000", "0", "600000", p)
    db.actualizar_cash_period_estado(per, "cerrado", closed_by="ana@x.com",
                                     closed_at="2026-06-30", db_path=p)
    cp = db.obtener_cash_period(per, p)
    assert cp["status"] == "cerrado"
    assert cp["closing_balance"] == "600000"
    assert cp["closed_by"] == "ana@x.com"


def test_reemplazar_movimientos_es_total(tmp_path):
    p = str(tmp_path / "c.db")
    db.inicializar_db(p)
    acc = db.crear_cash_account("Caja", db_path=p)
    per = db.crear_cash_period(acc, 2026, 6, db_path=p)
    db.reemplazar_cash_movements(per, [{"sequence": 1, "concept": "a"}], p)
    db.reemplazar_cash_movements(per, [{"sequence": 1, "concept": "b"},
                                       {"sequence": 2, "concept": "c"}], p)
    movs = db.listar_cash_movements(per, p)
    assert [m["concept"] for m in movs] == ["b", "c"]


# ═══════════════════════════════════════════════════════════════════════════
# Plantilla Excel
# ═══════════════════════════════════════════════════════════════════════════

def test_plantilla_round_trip(tmp_path):
    movs = [
        mc.a_dict(mc.desde_dict({"movement_date": "2026-06-02", "movement_type": "entrada",
                                 "concept": "Recaudo", "third_party_nit": "900123",
                                 "third_party_name": "CLIENTE", "inflow_amount": "100000"})),
        mc.a_dict(mc.desde_dict({"movement_date": "2026-06-03", "movement_type": "salida",
                                 "concept": "Transporte", "outflow_amount": "20000"})),
    ]
    data = pl.generar_plantilla(
        empresa="1 INVEST SAS", cuenta_caja="Caja menor", anio=2026, mes=6,
        saldo_inicial="500000", responsable="Ana", movimientos=movs,
        terceros=[{"nit": "900123", "nombre": "CLIENTE ABC"}],
    )
    fp = tmp_path / "caja.xlsx"
    fp.write_bytes(data)

    res = importar_plantilla(fp)
    assert not res.tiene_errores
    assert res.empresa == "1 INVEST SAS"
    assert res.cuenta_caja == "Caja menor"
    assert res.mes == 6 and res.anio == 2026
    assert res.saldo_inicial == Decimal("500000")
    assert len(res.movimientos) == 2
    # Saldo recalculado, no el digitado.
    assert res.movimientos[0].running_balance == Decimal("600000")
    assert res.movimientos[1].running_balance == Decimal("580000")


def test_plantilla_vacia_se_genera(tmp_path):
    data = pl.generar_plantilla(empresa="X", cuenta_caja="Caja", anio=2026, mes=6)
    assert data[:2] == b"PK"  # xlsx es un zip
    fp = tmp_path / "v.xlsx"
    fp.write_bytes(data)
    res = importar_plantilla(fp)
    assert res.movimientos == []
    assert not res.tiene_errores


def test_importar_marca_errores_por_fila(tmp_path):
    # Movimiento con fecha fuera del período.
    movs = [mc.a_dict(mc.desde_dict({"movement_date": "2026-08-05",
                                     "movement_type": "entrada", "concept": "X",
                                     "inflow_amount": "100"}))]
    data = pl.generar_plantilla(empresa="X", cuenta_caja="Caja", anio=2026, mes=6,
                                saldo_inicial="0", movimientos=movs)
    fp = tmp_path / "bad.xlsx"
    fp.write_bytes(data)
    res = importar_plantilla(fp)
    assert res.tiene_errores
    assert any(res.errores_por_fila.values())


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


def _crear_cuenta_y_periodo(client):
    r = client.post("/caja/cuenta", data={"name": "Caja menor"})
    acc_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
    r = client.post(f"/caja/cuenta/{acc_id}/periodo",
                    data={"year": "2026", "month": "6", "opening_balance": "500000"})
    per_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
    return acc_id, per_id


def test_ruta_landing(client):
    r = client.get("/caja")
    assert r.status_code == 200
    assert "Caja General" in r.get_data(as_text=True)


def test_crear_cuenta_y_periodo_y_guardar(client, tmp_path):
    acc_id, per_id = _crear_cuenta_y_periodo(client)
    movs = [
        {"movement_date": "2026-06-02", "movement_type": "entrada",
         "concept": "Recaudo", "inflow_amount": 100000, "outflow_amount": 0},
        {"movement_date": "2026-06-03", "movement_type": "salida",
         "concept": "Transporte", "inflow_amount": 0, "outflow_amount": 20000},
    ]
    r = client.post(f"/caja/periodo/{per_id}/guardar",
                    data={"movimientos_json": json.dumps(movs), "opening_balance": "500000"})
    assert r.status_code == 302
    cp = db.obtener_cash_period(per_id, config.DB_PATH)
    assert cp["total_inflows"] == "100000"
    assert cp["total_outflows"] == "20000"
    assert cp["closing_balance"] == "580000"
    assert len(db.listar_cash_movements(per_id, config.DB_PATH)) == 2


def test_periodo_duplicado_rechazado(client):
    acc_id, _ = _crear_cuenta_y_periodo(client)
    r = client.post(f"/caja/cuenta/{acc_id}/periodo",
                    data={"year": "2026", "month": "6", "opening_balance": "0"},
                    follow_redirects=True)
    assert "Ya existe un período" in r.get_data(as_text=True)


def test_transiciones_de_estado(client):
    _, per_id = _crear_cuenta_y_periodo(client)
    for accion, esperado in [("enviar-revision", "en_revision"),
                             ("aprobar", "aprobado"),
                             ("cerrar", "cerrado"),
                             ("reabrir", "reabierto")]:
        client.post(f"/caja/periodo/{per_id}/estado/{accion}")
        assert db.obtener_cash_period(per_id, config.DB_PATH)["status"] == esperado


def test_guardar_en_periodo_cerrado_bloqueado(client):
    _, per_id = _crear_cuenta_y_periodo(client)
    client.post(f"/caja/periodo/{per_id}/estado/cerrar")
    movs = [{"movement_date": "2026-06-02", "movement_type": "entrada",
             "concept": "x", "inflow_amount": 100, "outflow_amount": 0}]
    r = client.post(f"/caja/periodo/{per_id}/guardar",
                    data={"movimientos_json": json.dumps(movs), "opening_balance": "0"},
                    follow_redirects=True)
    assert "cerrado o aprobado" in r.get_data(as_text=True)
    assert len(db.listar_cash_movements(per_id, config.DB_PATH)) == 0


def test_descargar_plantillas(client):
    _, per_id = _crear_cuenta_y_periodo(client)
    r1 = client.get(f"/caja/periodo/{per_id}/plantilla")
    r2 = client.get(f"/caja/periodo/{per_id}/plantilla-prediligenciada")
    assert r1.status_code == 200 and r1.data[:2] == b"PK"
    assert r2.status_code == 200 and r2.data[:2] == b"PK"


def test_importar_plantilla_valida(client):
    _, per_id = _crear_cuenta_y_periodo(client)
    movs = [mc.a_dict(mc.desde_dict({"movement_date": "2026-06-05",
                                     "movement_type": "entrada", "concept": "Venta",
                                     "inflow_amount": "200000"}))]
    data = pl.generar_plantilla(empresa="X", cuenta_caja="Caja menor", anio=2026,
                                mes=6, saldo_inicial="500000", movimientos=movs)
    r = client.post(f"/caja/periodo/{per_id}/importar",
                    data={"archivo": (io.BytesIO(data), "caja.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    cp = db.obtener_cash_period(per_id, config.DB_PATH)
    assert cp["closing_balance"] == "700000"
