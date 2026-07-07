"""
Tests del Sistema Presupuestal (app/presupuesto + rutas web).

Cubren:
- El motor de flujo de caja: matriz mensual, flujo neto y saldo acumulado.
- La sincronización del ejecutado por prefijo de cuenta PUC.
- El análisis comparativo: variaciones, semáforos y favorabilidad.
- El parser del CSV de importación (formatos de número colombianos).
- Las rutas web: landing, creación con estructura estándar, valores,
  mapeo de cuentas, importación CSV y aislamiento por empresa.
"""

import io
import os

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")
# BD presupuestal en memoria, compartida por todo el proceso de tests
# (StaticPool: una sola conexión). Debe fijarse antes del primer import
# de app.presupuesto.database.
os.environ["PRESUPUESTO_DATABASE_URL"] = "sqlite:///:memory:"

from app import config                                        # noqa: E402
from app.presupuesto.connectors.base import MovimientoContable  # noqa: E402
from app.presupuesto.connectors.csv_file import parsear_contenido  # noqa: E402
from app.presupuesto.database import SessionLocal, init_db    # noqa: E402
from app.presupuesto.models import (                          # noqa: E402
    CategoriaPresupuesto, Empresa, FuenteDato, LineaPresupuesto, MapeoCuenta,
    Presupuesto, TipoFlujo, TipoValor,
)
from app.presupuesto.schemas import ValorItem                 # noqa: E402
from app.presupuesto.services.analisis import analizar        # noqa: E402
from app.presupuesto.services.motor import (                  # noqa: E402
    construir_flujo_caja, guardar_valores,
)
from app.presupuesto.services.sincronizacion import sincronizar_ejecutado  # noqa: E402
from app.web import create_app                                # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Servicios (motor, sincronización, análisis)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def datos():
    """Empresa + presupuesto 2026 con proyectado completo, creados directo en BD."""
    init_db()
    db = SessionLocal()
    emp = Empresa(nombre="Test SAS", nit="900.000.000-1")
    pres = Presupuesto(
        anio=2026, nombre="Presupuesto test", saldo_inicial_caja=10_000_000,
        umbral_alerta=5.0, umbral_critico=15.0,
    )
    cat_ing = CategoriaPresupuesto(nombre="Ingresos", tipo=TipoFlujo.INGRESO)
    cat_ing.lineas.append(LineaPresupuesto(
        nombre="Ventas", mapeos=[MapeoCuenta(codigo_cuenta="4135")],
    ))
    cat_egr = CategoriaPresupuesto(nombre="Gastos", tipo=TipoFlujo.EGRESO)
    cat_egr.lineas.append(LineaPresupuesto(
        nombre="Nómina",
        mapeos=[MapeoCuenta(codigo_cuenta="5105"), MapeoCuenta(codigo_cuenta="5205")],
    ))
    cat_egr.lineas.append(LineaPresupuesto(
        nombre="Arriendo", mapeos=[MapeoCuenta(codigo_cuenta="5120")],
    ))
    pres.categorias = [cat_ing, cat_egr]
    emp.presupuestos.append(pres)
    db.add(emp)
    db.commit()

    lineas = {l.nombre: l.id for c in pres.categorias for l in c.lineas}
    # Proyectado: ventas 100M/mes, nómina 40M, arriendo 10M
    items = []
    for mes in range(1, 13):
        items += [
            ValorItem(linea_id=lineas["Ventas"], mes=mes, valor=100_000_000),
            ValorItem(linea_id=lineas["Nómina"], mes=mes, valor=40_000_000),
            ValorItem(linea_id=lineas["Arriendo"], mes=mes, valor=10_000_000),
        ]
    guardar_valores(db, TipoValor.PROYECTADO, FuenteDato.MANUAL, items)
    pres_id = pres.id
    db.close()
    return {"presupuesto_id": pres_id, "lineas": lineas}


def test_flujo_caja_proyectado(datos):
    db = SessionLocal()
    flujo = construir_flujo_caja(db, datos["presupuesto_id"])
    db.close()
    # Neto mensual = 100 - 50 = 50M
    assert flujo.flujo_neto_proyectado == [50_000_000.0] * 12
    # Saldo acumulado: 10M inicial + 50M/mes
    assert flujo.saldo_acumulado_proyectado[0] == 60_000_000.0
    assert flujo.saldo_acumulado_proyectado[11] == 610_000_000.0


def test_sincronizacion_por_mapeo_puc(datos):
    """El ejecutado se cruza por prefijo de cuenta PUC (el más específico gana)."""
    movimientos = [
        MovimientoContable("413501", "Venta mostrador", 80_000_000, "2026-01-31"),
        MovimientoContable("413524", "Venta mayorista", 15_000_000, "2026-01-31"),
        MovimientoContable("510506", "Sueldos", 30_000_000, "2026-01-31"),
        MovimientoContable("520506", "Sueldos ventas", 12_000_000, "2026-01-31"),
        MovimientoContable("512010", "Arriendo bodega", 10_500_000, "2026-01-31"),
        MovimientoContable("999999", "Cuenta sin mapear", 5_000_000, "2026-01-31"),
    ]
    db = SessionLocal()
    r = sincronizar_ejecutado(db, datos["presupuesto_id"], 1, movimientos=movimientos)
    assert r.exito
    assert r.lineas_actualizadas == 3
    assert "999999" in r.mensaje  # reporta cuentas sin mapear

    flujo = construir_flujo_caja(db, datos["presupuesto_id"])
    db.close()
    ventas = next(
        l for c in flujo.categorias for l in c.lineas if l.nombre == "Ventas"
    )
    assert ventas.ejecutado[0] == 95_000_000.0  # 413501 + 413524
    # Flujo neto ejecutado enero = 95 - (42 + 10.5) = 42.5M
    assert flujo.flujo_neto_ejecutado[0] == 42_500_000.0


def test_analisis_variaciones_y_semaforo(datos):
    db = SessionLocal()
    a = analizar(db, datos["presupuesto_id"], mes=1)
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
    a = analizar(db, datos["presupuesto_id"])  # sin mes → YTD
    db.close()
    assert a.alcance == "acumulado"
    assert a.resumen["ultimo_mes_ejecutado"] == 1
    ventas = next(l for l in a.lineas if l.nombre == "Ventas")
    assert ventas.proyectado == 100_000_000.0  # solo enero (último ejecutado)


def test_parser_csv_formatos_colombianos():
    csv = (
        "codigo_cuenta;nombre_cuenta;valor\n"
        "4135;Ventas;98.000.000,50\n"
        "5105;Nomina;41000000\n"
        ";sin codigo;123\n"
    )
    movs = parsear_contenido(csv)
    assert len(movs) == 2
    assert movs[0].valor == 98_000_000.50
    assert movs[1].codigo_cuenta == "5105"


# ═══════════════════════════════════════════════════════════════════════════
# Rutas web
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    import app.authn as authn
    import app.empresas as emp_mod
    emp_mod._sistema_listo.clear()
    authn.reset_estado()

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


def _crear_presupuesto(client, anio, plantilla=True):
    data = {"nombre": f"Presupuesto {anio}", "anio": str(anio),
            "saldo_inicial": "5.000.000"}
    if plantilla:
        data["plantilla_estandar"] = "on"
    r = client.post("/presupuesto/crear", data=data)
    assert r.status_code == 302
    return int(r.headers["Location"].split("#")[0].rstrip("/").split("/")[-1])


def test_landing_y_navegacion(client):
    r = client.get("/presupuesto")
    assert r.status_code == 200
    assert "Sistema Presupuestal" in r.get_data(as_text=True)
    # El módulo reemplazó al anterior en la categoría Finanzas
    r = client.get("/modulos/finanzas")
    html = r.get_data(as_text=True)
    assert "Sistema Presupuestal" in html
    assert "Próximamente" not in html


def test_crear_presupuesto_con_estructura(client):
    pid = _crear_presupuesto(client, 2030)
    r = client.get(f"/presupuesto/{pid}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Presupuesto 2030" in html
    assert "Ingresos operacionales" in html   # estructura estándar
    db = SessionLocal()
    pres = db.get(Presupuesto, pid)
    assert pres.saldo_inicial_caja == 5_000_000.0
    assert {c.tipo for c in pres.categorias} == {TipoFlujo.INGRESO, TipoFlujo.EGRESO}
    db.close()


def test_anio_duplicado_rechazado(client):
    _crear_presupuesto(client, 2031)
    r = client.post("/presupuesto/crear",
                    data={"nombre": "Otro", "anio": "2031"},
                    follow_redirects=True)
    assert "Ya existe un presupuesto" in r.get_data(as_text=True)


def test_guardar_valores_proyectado(client):
    pid = _crear_presupuesto(client, 2032)
    db = SessionLocal()
    pres = db.get(Presupuesto, pid)
    linea = pres.categorias[0].lineas[0]
    linea_id = linea.id
    db.close()

    r = client.post(
        f"/presupuesto/{pid}/valores",
        data={"tipo": "proyectado",
              f"v_{linea_id}_1": "1.500.000,50",
              f"v_{linea_id}_2": "2000000",
              f"v_{linea_id}_3": ""},        # vacío: no se guarda
        follow_redirects=True,
    )
    assert "2 valores proyectados guardados" in r.get_data(as_text=True)

    db = SessionLocal()
    flujo = construir_flujo_caja(db, pid)
    db.close()
    linea_flujo = next(
        l for c in flujo.categorias for l in c.lineas if l.linea_id == linea_id
    )
    assert linea_flujo.proyectado[0] == 1_500_000.50
    assert linea_flujo.proyectado[1] == 2_000_000.0
    assert linea_flujo.proyectado[2] == 0.0


def test_cuentas_e_importacion_csv(client):
    pid = _crear_presupuesto(client, 2033)
    db = SessionLocal()
    pres = db.get(Presupuesto, pid)
    cat_ingreso = next(c for c in pres.categorias if c.tipo == TipoFlujo.INGRESO)
    linea_id = cat_ingreso.lineas[0].id
    db.close()

    # Mapear cuentas PUC a la línea
    r = client.post(f"/presupuesto/{pid}/linea/{linea_id}/cuentas",
                    data={"cuentas": "4135, 4210"}, follow_redirects=True)
    assert "actualizadas" in r.get_data(as_text=True)

    # Importar el ejecutado de enero por CSV
    csv = "codigo_cuenta,nombre_cuenta,valor\n413501,Ventas,7500000\n421001,Financieros,500000\n"
    r = client.post(
        f"/presupuesto/{pid}/importar-csv",
        data={"mes": "1", "archivo": (io.BytesIO(csv.encode()), "balance.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "1 líneas actualizadas" in r.get_data(as_text=True)

    db = SessionLocal()
    flujo = construir_flujo_caja(db, pid)
    db.close()
    linea_flujo = next(
        l for c in flujo.categorias for l in c.lineas if l.linea_id == linea_id
    )
    assert linea_flujo.ejecutado[0] == 8_000_000.0  # 4135* + 4210*


def test_estructura_agregar_y_eliminar(client):
    pid = _crear_presupuesto(client, 2034, plantilla=False)
    r = client.post(f"/presupuesto/{pid}/categoria",
                    data={"nombre": "Ingresos", "tipo": "ingreso"},
                    follow_redirects=True)
    assert "agregada" in r.get_data(as_text=True)

    db = SessionLocal()
    cat_id = db.get(Presupuesto, pid).categorias[0].id
    db.close()

    r = client.post(f"/presupuesto/{pid}/linea",
                    data={"categoria_id": str(cat_id), "nombre": "Ventas",
                          "cuentas": "4135"},
                    follow_redirects=True)
    assert "agregada" in r.get_data(as_text=True)

    db = SessionLocal()
    linea = db.get(Presupuesto, pid).categorias[0].lineas[0]
    assert [m.codigo_cuenta for m in linea.mapeos] == ["4135"]
    linea_id = linea.id
    db.close()

    client.post(f"/presupuesto/{pid}/linea/{linea_id}/eliminar")
    client.post(f"/presupuesto/{pid}/categoria/{cat_id}/eliminar")
    db = SessionLocal()
    assert db.get(Presupuesto, pid).categorias == []
    db.close()


def test_aislamiento_por_empresa(client):
    """Un presupuesto de otra empresa no es accesible desde la empresa activa."""
    db = SessionLocal()
    otra = Empresa(nombre="Otra SAS", nit="800.111.222-3", ref_externa="otra-empresa")
    otra.presupuestos.append(Presupuesto(anio=2035, nombre="Ajeno"))
    db.add(otra)
    db.commit()
    pid_ajeno = otra.presupuestos[0].id
    db.close()

    r = client.get(f"/presupuesto/{pid_ajeno}", follow_redirects=True)
    assert "no existe o pertenece a otra empresa" in r.get_data(as_text=True)


def test_eliminar_presupuesto_lo_oculta(client):
    pid = _crear_presupuesto(client, 2036)
    r = client.post(f"/presupuesto/{pid}/eliminar", follow_redirects=True)
    assert "eliminado" in r.get_data(as_text=True)
    db = SessionLocal()
    assert db.get(Presupuesto, pid).activo is False
    db.close()
    r = client.get(f"/presupuesto/{pid}", follow_redirects=True)
    assert "no existe o pertenece a otra empresa" in r.get_data(as_text=True)
