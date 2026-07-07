"""Automatización: sincronización mensual programada del ejecutado.

Usa APScheduler. Por defecto corre el día 3 de cada mes a las 02:00
(cuando ya suele estar registrada la contabilidad del mes anterior) y
sincroniza el mes anterior de TODOS los presupuestos activos cuyo
conector no sea 'manual'.

Integración FastAPI:

    from presupuesto.automation import iniciar_programador

    @app.on_event("startup")
    def _startup():
        iniciar_programador()

En Django, llamar iniciar_programador() desde AppConfig.ready().
Configurable con variables de entorno:
    PRESUPUESTO_SYNC_DIA (default 3), PRESUPUESTO_SYNC_HORA (default 2)
"""
import logging
import os
from datetime import date

from .database import SessionLocal
from .models import Empresa, FuenteDato, Presupuesto
from .services.sincronizacion import sincronizar_ejecutado

logger = logging.getLogger("presupuesto.automation")

_scheduler = None


def sincronizar_todo(anio: int | None = None, mes: int | None = None) -> list[dict]:
    """Sincroniza el ejecutado de todos los presupuestos activos con conector
    automático. Sin argumentos → mes anterior al actual."""
    hoy = date.today()
    if anio is None or mes is None:
        mes_anterior = hoy.month - 1 or 12
        anio_objetivo = hoy.year if hoy.month > 1 else hoy.year - 1
        anio, mes = anio_objetivo, mes_anterior

    resultados = []
    db = SessionLocal()
    try:
        presupuestos = (
            db.query(Presupuesto)
            .join(Empresa)
            .filter(
                Presupuesto.activo.is_(True),
                Presupuesto.anio == anio,
                Empresa.conector != FuenteDato.MANUAL,
            )
            .all()
        )
        for pres in presupuestos:
            r = sincronizar_ejecutado(db, pres.id, mes)
            logger.info("Sync presupuesto %s (%s/%s): %s", pres.id, mes, anio, r.mensaje)
            resultados.append(r.model_dump())
    finally:
        db.close()
    return resultados


def iniciar_programador():
    """Arranca el job mensual. Idempotente."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from apscheduler.schedulers.background import BackgroundScheduler

    dia = int(os.getenv("PRESUPUESTO_SYNC_DIA", "3"))
    hora = int(os.getenv("PRESUPUESTO_SYNC_HORA", "2"))

    _scheduler = BackgroundScheduler(timezone="America/Bogota")
    _scheduler.add_job(
        sincronizar_todo,
        trigger="cron",
        day=dia,
        hour=hora,
        id="sync_presupuesto_mensual",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Programador iniciado: sync mensual el día %s a las %s:00", dia, hora)
    return _scheduler
