"""API REST del módulo de presupuesto (FastAPI APIRouter).

Montaje en 1ContaBot:

    from presupuesto.api import router as presupuesto_router
    from presupuesto.database import init_db

    init_db()
    app.include_router(presupuesto_router)

Todos los endpoints quedan bajo /api/presupuesto/*
"""
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .connectors import crear_conector
from .connectors.csv_file import parsear_contenido
from .database import get_db
from .models import (
    CategoriaPresupuesto, Empresa, FuenteDato, LineaPresupuesto,
    LogSincronizacion, MapeoCuenta, Presupuesto, TipoValor,
)
from .schemas import (
    AnalisisComparativo, CargaValores, EmpresaCrear, EmpresaOut, FlujoCaja,
    PresupuestoCrear, PresupuestoOut, ResultadoSync,
)
from .services import analisis as srv_analisis
from .services import motor as srv_motor
from .services.sincronizacion import sincronizar_ejecutado

router = APIRouter(prefix="/api/presupuesto", tags=["Presupuesto"])


# ---------- Empresas ----------
@router.post("/empresas", response_model=EmpresaOut)
def crear_empresa(datos: EmpresaCrear, db: Session = Depends(get_db)):
    emp = Empresa(**datos.model_dump())
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/empresas", response_model=list[EmpresaOut])
def listar_empresas(db: Session = Depends(get_db)):
    return db.query(Empresa).all()


@router.post("/empresas/{empresa_id}/probar-conexion")
def probar_conexion(empresa_id: int, db: Session = Depends(get_db)):
    emp = db.get(Empresa, empresa_id)
    if not emp:
        raise HTTPException(404, "Empresa no encontrada")
    if emp.conector == FuenteDato.MANUAL:
        return {"exito": False, "mensaje": "La empresa no tiene conector automático configurado."}
    conector = crear_conector(emp.conector, json.loads(emp.conector_config or "{}"))
    exito, mensaje = conector.probar_conexion()
    return {"exito": exito, "mensaje": mensaje}


# ---------- Presupuestos ----------
@router.post("/presupuestos", response_model=PresupuestoOut)
def crear_presupuesto(datos: PresupuestoCrear, db: Session = Depends(get_db)):
    if not db.get(Empresa, datos.empresa_id):
        raise HTTPException(404, "Empresa no encontrada")
    pres = Presupuesto(
        empresa_id=datos.empresa_id, anio=datos.anio, nombre=datos.nombre,
        saldo_inicial_caja=datos.saldo_inicial_caja,
        umbral_alerta=datos.umbral_alerta, umbral_critico=datos.umbral_critico,
    )
    for cat_in in datos.categorias:
        cat = CategoriaPresupuesto(
            nombre=cat_in.nombre, tipo=cat_in.tipo, orden=cat_in.orden
        )
        for lin_in in cat_in.lineas:
            linea = LineaPresupuesto(nombre=lin_in.nombre, orden=lin_in.orden)
            for cuenta in lin_in.cuentas:
                linea.mapeos.append(MapeoCuenta(codigo_cuenta=cuenta))
            cat.lineas.append(linea)
        pres.categorias.append(cat)
    db.add(pres)
    db.commit()
    return srv_motor.obtener_presupuesto(db, pres.id)


@router.get("/presupuestos", response_model=list[PresupuestoOut])
def listar_presupuestos(empresa_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Presupuesto).filter(Presupuesto.activo.is_(True))
    if empresa_id:
        q = q.filter(Presupuesto.empresa_id == empresa_id)
    return q.all()


@router.get("/presupuestos/{presupuesto_id}", response_model=PresupuestoOut)
def obtener_presupuesto(presupuesto_id: int, db: Session = Depends(get_db)):
    pres = srv_motor.obtener_presupuesto(db, presupuesto_id)
    if not pres:
        raise HTTPException(404, "Presupuesto no encontrado")
    return pres


# ---------- Valores (proyectado / ejecutado manual) ----------
@router.put("/presupuestos/{presupuesto_id}/valores")
def cargar_valores(
    presupuesto_id: int, carga: CargaValores, db: Session = Depends(get_db)
):
    if not srv_motor.obtener_presupuesto(db, presupuesto_id):
        raise HTTPException(404, "Presupuesto no encontrado")
    n = srv_motor.guardar_valores(db, carga.tipo, carga.fuente, carga.valores)
    return {"actualizados": n, "tipo": carga.tipo}


# ---------- Ejecutado: importación CSV ----------
@router.post(
    "/presupuestos/{presupuesto_id}/importar-csv/{mes}",
    response_model=ResultadoSync,
)
async def importar_csv(
    presupuesto_id: int, mes: int,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Sube un balance de prueba/auxiliar CSV y actualiza el ejecutado del mes."""
    contenido = (await archivo.read()).decode("utf-8-sig")
    movimientos = parsear_contenido(contenido)
    if not movimientos:
        raise HTTPException(422, "El CSV no contiene movimientos válidos "
                                 "(columnas: codigo_cuenta, nombre_cuenta, valor)")
    return sincronizar_ejecutado(db, presupuesto_id, mes, movimientos=movimientos)


# ---------- Ejecutado: sincronización automática ----------
@router.post(
    "/presupuestos/{presupuesto_id}/sincronizar/{mes}",
    response_model=ResultadoSync,
)
def sincronizar(presupuesto_id: int, mes: int, db: Session = Depends(get_db)):
    """Trae el ejecutado del mes desde Siigo/Alegra según el conector de la empresa."""
    try:
        return sincronizar_ejecutado(db, presupuesto_id, mes)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/presupuestos/{presupuesto_id}/sync-logs")
def logs_sincronizacion(presupuesto_id: int, db: Session = Depends(get_db)):
    logs = (
        db.query(LogSincronizacion)
        .filter_by(presupuesto_id=presupuesto_id)
        .order_by(LogSincronizacion.ejecutado_en.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "fecha": l.ejecutado_en.isoformat(), "fuente": l.fuente,
            "anio": l.anio, "mes": l.mes, "exito": l.exito,
            "lineas_actualizadas": l.lineas_actualizadas, "mensaje": l.mensaje,
        }
        for l in logs
    ]


# ---------- Reportes ----------
@router.get("/presupuestos/{presupuesto_id}/flujo-caja", response_model=FlujoCaja)
def flujo_caja(presupuesto_id: int, db: Session = Depends(get_db)):
    """Matriz completa: proyectado vs ejecutado, flujo neto y saldo acumulado."""
    flujo = srv_motor.construir_flujo_caja(db, presupuesto_id)
    if not flujo:
        raise HTTPException(404, "Presupuesto no encontrado")
    return flujo


@router.get(
    "/presupuestos/{presupuesto_id}/analisis",
    response_model=AnalisisComparativo,
)
def analisis_comparativo(
    presupuesto_id: int,
    mes: int | None = None,
    db: Session = Depends(get_db),
):
    """Variaciones, semáforos y alertas. Sin `mes` → acumulado YTD."""
    if mes is not None and not 1 <= mes <= 12:
        raise HTTPException(422, "mes debe estar entre 1 y 12")
    resultado = srv_analisis.analizar(db, presupuesto_id, mes)
    if not resultado:
        raise HTTPException(404, "Presupuesto no encontrado")
    return resultado
