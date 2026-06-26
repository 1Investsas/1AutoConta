"""
Programador de la importación automática diaria de RADIAN.

Arranca un hilo en segundo plano que, una vez al día y a la hora configurada de
cada empresa, ejecuta la importación automática. Es deliberadamente simple (sin
dependencias externas) para encajar en el despliegue actual:

- Se activa con ``RADIAN_SCHEDULER_ENABLED=true`` (ver app/config.py) y arranca
  desde la fábrica de la app web.
- En despliegues con **varias** instancias conviene dejarlo desactivado y
  disparar la importación con un cron externo contra el endpoint
  ``POST /radian/auto/cron`` (token compartido) o con el comando de CLI
  ``python main.py radian-auto``, para no ejecutarla varias veces en paralelo.

El control de «una vez al día» es por fecha local: cada empresa se marca como
ejecutada el día en que corre, de modo que reintentos dentro del mismo minuto no
la disparen de nuevo.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_hilo: Optional[threading.Thread] = None
_iniciado = False

# empresa_id → fecha (ISO) de la última ejecución, para no repetir en el día.
_ultima_ejecucion: dict[str, str] = {}


def _hora_actual() -> str:
    return datetime.now().strftime("%H:%M")


def _debe_correr(dcfg, ahora_hhmm: str, empresa_id: str, hoy: str) -> bool:
    """True si la empresa debe ejecutarse ahora (hora coincide y no corrió hoy)."""
    if not dcfg.habilitado or not dcfg.configurado():
        return False
    if dcfg.hora_efectiva() != ahora_hhmm:
        return False
    return _ultima_ejecucion.get(empresa_id) != hoy


def ejecutar_pendientes(ahora_hhmm: Optional[str] = None) -> list:
    """Ejecuta las importaciones cuya hora programada coincide con `ahora_hhmm`.

    Se separa del bucle para poder probarse de forma directa. Retorna la lista de
    `ResultadoAuto` de las empresas que corrieron en esta pasada.
    """
    from app.empresas import listar_empresas
    from app.radian_auto.auto_importador import importar_empresa

    ahora_hhmm = ahora_hhmm or _hora_actual()
    hoy = date.today().isoformat()
    resultados = []
    for emp in listar_empresas():
        dcfg = emp.dian()
        if not _debe_correr(dcfg, ahora_hhmm, emp.id, hoy):
            continue
        # Marcar antes de correr: evita re-disparos si el proceso tarda > 1 min.
        _ultima_ejecucion[emp.id] = hoy
        logger.info("Scheduler: ejecutando importación automática de %s.", emp.id)
        try:
            resultados.append(importar_empresa(emp))
        except Exception:
            logger.exception("Scheduler: error inesperado importando %s", emp.id)
    return resultados


def _bucle(intervalo_seg: int) -> None:
    """Bucle del hilo: revisa cada `intervalo_seg` segundos si hay algo que correr."""
    logger.info("Scheduler RADIAN iniciado (revisión cada %ds).", intervalo_seg)
    while True:
        try:
            ejecutar_pendientes()
        except Exception:
            logger.exception("Scheduler: error en la pasada de revisión.")
        time.sleep(intervalo_seg)


def iniciar_scheduler(intervalo_seg: int = 30) -> bool:
    """Arranca el hilo del scheduler una sola vez. Retorna True si lo inició."""
    global _hilo, _iniciado
    with _lock:
        if _iniciado:
            return False
        _hilo = threading.Thread(
            target=_bucle, args=(intervalo_seg,),
            name="radian-scheduler", daemon=True,
        )
        _hilo.start()
        _iniciado = True
        return True
