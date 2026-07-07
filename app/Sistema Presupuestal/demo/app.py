"""Demo ejecutable del módulo de presupuesto.

    pip install -r requirements.txt
    python -m uvicorn demo.app:app --reload

Abrir http://localhost:8000  (dashboard) y http://localhost:8000/docs (API).
Crea una empresa de ejemplo con presupuesto 2026 y ejecución enero-junio.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PRESUPUESTO_DATABASE_URL", "sqlite:///./demo_presupuesto.db")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from presupuesto.api import router  # noqa: E402
from presupuesto.database import SessionLocal, init_db  # noqa: E402
from presupuesto.models import (  # noqa: E402
    CategoriaPresupuesto, Empresa, FuenteDato, LineaPresupuesto, MapeoCuenta,
    Presupuesto, TipoFlujo, TipoValor, ValorMensual,
)

app = FastAPI(title="1ContaBot – Módulo Presupuesto (demo)")
app.include_router(router)

DASHBOARD = Path(__file__).parent / "dashboard.html"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(DASHBOARD)


# ---------------- Datos semilla ----------------
ESTRUCTURA = [
    ("Ingresos operacionales", TipoFlujo.INGRESO, [
        ("Ventas de contado", ["4135"], [42, 44, 47, 45, 50, 52, 54, 53, 55, 58, 62, 70]),
        ("Recaudo de cartera", ["1305"], [18, 19, 20, 20, 21, 22, 23, 23, 24, 25, 27, 30]),
    ]),
    ("Otros ingresos", TipoFlujo.INGRESO, [
        ("Rendimientos financieros", ["4210"], [0.8] * 12),
    ]),
    ("Costos y gastos operacionales", TipoFlujo.EGRESO, [
        ("Compra de mercancía", ["6205", "1435"], [24, 25, 27, 26, 29, 30, 31, 30, 32, 33, 36, 40]),
        ("Nómina y seguridad social", ["5105", "5205"], [14, 14, 14, 14.5, 14.5, 14.5, 15, 15, 15, 15, 15, 18]),
        ("Arriendo", ["5120"], [4.2] * 12),
        ("Servicios públicos", ["5135"], [1.6, 1.6, 1.7, 1.6, 1.7, 1.8, 1.8, 1.8, 1.7, 1.7, 1.8, 2.0]),
        ("Honorarios y asesorías", ["5110"], [2.5] * 12),
    ]),
    ("Gastos financieros", TipoFlujo.EGRESO, [
        ("Intereses de crédito", ["5305"], [1.9, 1.9, 1.8, 1.8, 1.7, 1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4]),
    ]),
    ("Inversión (CAPEX)", TipoFlujo.EGRESO, [
        ("Equipos y tecnología", ["1524", "1528"], [0, 0, 6, 0, 0, 0, 8, 0, 0, 0, 0, 0]),
    ]),
    ("Impuestos", TipoFlujo.EGRESO, [
        ("IVA y retenciones", ["2408", "2365"], [5.5, 5.7, 6.1, 5.9, 6.5, 6.8, 7.0, 6.9, 7.2, 7.5, 8.1, 9.1]),
    ]),
]

# Ejecutado enero-junio (variaciones realistas sobre lo proyectado)
FACTOR_EJECUCION = {
    "Ventas de contado": [0.96, 1.02, 0.88, 1.05, 0.97, 1.01],
    "Recaudo de cartera": [0.90, 0.95, 0.85, 1.00, 0.92, 0.96],
    "Rendimientos financieros": [1.1, 1.1, 1.0, 1.05, 1.0, 1.02],
    "Compra de mercancía": [1.03, 0.99, 1.08, 1.01, 1.12, 1.04],
    "Nómina y seguridad social": [1.0, 1.0, 1.0, 1.02, 1.02, 1.02],
    "Arriendo": [1.0] * 6,
    "Servicios públicos": [1.05, 0.98, 1.22, 1.1, 1.03, 1.18],
    "Honorarios y asesorías": [1.0, 1.0, 1.4, 1.0, 1.0, 1.2],
    "Intereses de crédito": [1.0, 1.0, 1.0, 0.98, 0.98, 0.97],
    "Equipos y tecnología": [0, 0, 1.15, 0, 0, 0],
    "IVA y retenciones": [0.98, 1.01, 0.95, 1.03, 1.0, 1.02],
}

MILLON = 1_000_000


def sembrar():
    db = SessionLocal()
    try:
        if db.query(Empresa).first():
            return
        emp = Empresa(
            nombre="Comercializadora Andina SAS", nit="901.234.567-8",
            conector=FuenteDato.CSV,
        )
        db.add(emp)
        db.flush()
        pres = Presupuesto(
            empresa_id=emp.id, anio=2026,
            nombre="Presupuesto de flujo de caja 2026",
            saldo_inicial_caja=25 * MILLON,
        )
        db.add(pres)
        db.flush()
        for orden_c, (nombre_cat, tipo, lineas) in enumerate(ESTRUCTURA):
            cat = CategoriaPresupuesto(
                presupuesto_id=pres.id, nombre=nombre_cat, tipo=tipo, orden=orden_c
            )
            db.add(cat)
            db.flush()
            for orden_l, (nombre_lin, cuentas, proyectado) in enumerate(lineas):
                lin = LineaPresupuesto(categoria_id=cat.id, nombre=nombre_lin, orden=orden_l)
                db.add(lin)
                db.flush()
                for cta in cuentas:
                    db.add(MapeoCuenta(linea_id=lin.id, codigo_cuenta=cta))
                factores = FACTOR_EJECUCION.get(nombre_lin, [1.0] * 6)
                for mes in range(1, 13):
                    p = round(float(proyectado[mes - 1]) * MILLON, 2)
                    db.add(ValorMensual(
                        linea_id=lin.id, mes=mes, tipo=TipoValor.PROYECTADO, valor=p,
                    ))
                    if mes <= 6:
                        db.add(ValorMensual(
                            linea_id=lin.id, mes=mes, tipo=TipoValor.EJECUTADO,
                            valor=round(p * factores[mes - 1], 2),
                            fuente=FuenteDato.CSV,
                        ))
        db.commit()
        print("Datos de demostración creados.")
    finally:
        db.close()


init_db()
sembrar()
