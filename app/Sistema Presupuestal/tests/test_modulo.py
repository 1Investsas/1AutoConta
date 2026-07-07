"""Tests del módulo de presupuesto: motor, análisis, sincronización y API.

    pip install pytest httpx
    python -m pytest tests/ -v
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PRESUPUESTO_DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from presupuesto import database
from presupuesto.api import router
from presupuesto.connectors.base import MovimientoContable
from presupuesto.connectors.csv_file import parsear_contenido
from presupuesto.database import SessionLocal, init_db
from presupuesto.models import TipoValor
from presupuesto.services.analisis import analizar
from presupuesto.services.motor import construir_flujo_caja
from presupuesto.services.sincronizacion import sincronizar_ejecutado


@pytest.fixture(scope="module")
def client():
    init_db()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(scope="module")
def datos(client):
    """Crea empresa + presupuesto de prueba vía API."""
    emp = client.post("/api/presupuesto/empresas", json={
        "nombre": "Test SAS", "nit": "900.000.000-1",
    }).json()
    pres = client.post("/api/presupuesto/presupuestos", json={
        "empresa_id": emp["id"], "anio": 2026,
        "nombre": "Presupuesto test", "saldo_inicial_caja": 10_000_000,
        "categorias": [
            {"nombre": "Ingresos", "tipo": "ingreso", "lineas": [
                {"nombre": "Ventas", "cuentas": ["4135"]},
            ]},
            {"nombre": "Gastos", "tipo": "egreso", "lineas": [
                {"nombre": "Nómina", "cuentas": ["5105", "5205"]},
                {"nombre": "Arriendo", "cuentas": ["5120"]},
            ]},
        ],
    }).json()
    # Proyectado: ventas 100M/mes, nómina 40M, arriendo 10M
    lineas = {
        l["nombre"]: l["id"]
        for c in pres["categorias"] for l in c["lineas"]
    }
    valores = []
    for mes in range(1, 13):
        valores += [
            {"linea_id": lineas["Ventas"], "mes": mes, "valor": 100_000_000},
            {"linea_id": lineas["Nómina"], "mes": mes, "valor": 40_000_000},
            {"linea_id": lineas["Arriendo"], "mes": mes, "valor": 10_000_000},
        ]
    r = client.put(
        f"/api/presupuesto/presupuestos/{pres['id']}/valores",
        json={"tipo": "proyectado", "valores": valores},
    )
    assert r.status_code == 200
    return {"empresa": emp, "presupuesto": pres, "lineas": lineas}


def test_flujo_caja_proyectado(datos):
    db = SessionLocal()
    flujo = construir_flujo_caja(db, datos["presupuesto"]["id"])
    db.close()
    # Neto mensual = 100 - 50 = 50M
    assert flujo.flujo_neto_proyectado == [50_000_000.0] * 12
    # Saldo acumulado: 10M inicial + 50M/mes
    assert flujo.saldo_acumulado_proyectado[0] == 60_000_000.0
    assert flujo.saldo_acumulado_proyectado[11] == 610_000_000.0


def test_sincronizacion_por_mapeo_puc(datos):
    """El ejecutado se cruza por prefijo de cuenta PUC."""
    movimientos = [
        MovimientoContable("413501", "Venta mostrador", 80_000_000, "2026-01-31"),
        MovimientoContable("413524", "Venta mayorista", 15_000_000, "2026-01-31"),
        MovimientoContable("510506", "Sueldos", 30_000_000, "2026-01-31"),
        MovimientoContable("520506", "Sueldos ventas", 12_000_000, "2026-01-31"),
        MovimientoContable("512010", "Arriendo bodega", 10_500_000, "2026-01-31"),
        MovimientoContable("999999", "Cuenta sin mapear", 5_000_000, "2026-01-31"),
    ]
    db = SessionLocal()
    r = sincronizar_ejecutado(db, datos["presupuesto"]["id"], 1, movimientos=movimientos)
    assert r.exito
    assert r.lineas_actualizadas == 3
    assert "999999" in r.mensaje  # reporta cuentas sin mapear

    flujo = construir_flujo_caja(db, datos["presupuesto"]["id"])
    db.close()
    ventas = next(
        l for c in flujo.categorias for l in c.lineas if l.nombre == "Ventas"
    )
    assert ventas.ejecutado[0] == 95_000_000.0  # 413501 + 413524
    # Flujo neto ejecutado enero = 95 - (42 + 10.5) = 42.5M
    assert flujo.flujo_neto_ejecutado[0] == 42_500_000.0


def test_analisis_variaciones_y_semaforo(datos):
    db = SessionLocal()
    a = analizar(db, datos["presupuesto"]["id"], mes=1)
    db.close()
    ventas = next(l for l in a.lineas if l.nombre == "Ventas")
    assert ventas.proyectado == 100_000_000.0
    assert ventas.ejecutado == 95_000_000.0
    assert ventas.variacion_pct == -5.0
    assert ventas.semaforo == "amarillo"  # |−5| ≥ umbral_alerta 5
    assert ventas.favorable is False       # ingreso por debajo

    arriendo = next(l for l in a.lineas if l.nombre == "Arriendo")
    assert arriendo.variacion_pct == 5.0
    assert arriendo.favorable is False     # egreso por encima

    assert a.resumen["cumplimiento_ingresos_pct"] == 95.0


def test_analisis_acumulado_ytd(datos):
    db = SessionLocal()
    a = analizar(db, datos["presupuesto"]["id"])  # sin mes → YTD
    db.close()
    assert a.alcance == "acumulado"
    assert a.resumen["ultimo_mes_ejecutado"] == 1
    ventas = next(l for l in a.lineas if l.nombre == "Ventas")
    assert ventas.proyectado == 100_000_000.0  # solo enero (último ejecutado)


def test_importacion_csv(datos, client):
    csv = (
        "codigo_cuenta;nombre_cuenta;valor\n"
        "4135;Ventas;98.000.000,50\n"
        "5105;Nomina;41000000\n"
    )
    movs = parsear_contenido(csv)
    assert movs[0].valor == 98_000_000.50  # formato colombiano normalizado
    r = client.post(
        f"/api/presupuesto/presupuestos/{datos['presupuesto']['id']}/importar-csv/2",
        files={"archivo": ("balance.csv", csv, "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["lineas_actualizadas"] == 2


def test_endpoints_reportes(datos, client):
    pid = datos["presupuesto"]["id"]
    assert client.get(f"/api/presupuesto/presupuestos/{pid}/flujo-caja").status_code == 200
    assert client.get(f"/api/presupuesto/presupuestos/{pid}/analisis?mes=1").status_code == 200
    logs = client.get(f"/api/presupuesto/presupuestos/{pid}/sync-logs").json()
    assert len(logs) >= 2  # sync manual + csv
    assert client.get("/api/presupuesto/presupuestos/99999/flujo-caja").status_code == 404
