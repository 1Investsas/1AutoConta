"""
Bitácora del sistema contable-auto.

Registra todas las acciones relevantes tanto en consola (con rich)
como en la base de datos SQLite, para trazabilidad y auditoría.
"""

import logging
from datetime import datetime
from typing import Optional

from app.database import registrar_bitacora_db
from app.models import RegistroBitacora

logger = logging.getLogger(__name__)

# Acumulador en memoria para exportar la bitácora al Excel de salida
_registros_sesion: list[RegistroBitacora] = []


def registrar(
    nivel: str,
    modulo: str,
    accion: str,
    detalle: str,
    cufe: Optional[str] = None,
    persistir: bool = True,
    db_path: Optional[str] = None,
) -> RegistroBitacora:
    """
    Registra una acción en la bitácora (memoria + BD).

    Args:
        nivel:    Nivel de log: 'INFO', 'WARNING' o 'ERROR'.
        modulo:   Nombre del módulo que genera el registro.
        accion:   Nombre corto de la acción (p. ej. 'IMPORTAR', 'CLASIFICAR').
        detalle:  Descripción detallada del evento.
        cufe:     CUFE/CUDE relacionado si aplica.
        persistir: Si True, guarda en la BD SQLite.
        db_path:  Ruta a la BD (usa default si es None).

    Returns:
        El objeto RegistroBitacora creado.
    """
    registro = RegistroBitacora(
        timestamp=datetime.now(),
        nivel=nivel.upper(),
        modulo=modulo,
        accion=accion,
        detalle=detalle,
        cufe=cufe,
    )
    _registros_sesion.append(registro)

    # Enviar también al logger estándar
    msg = f"[{modulo}] {accion}: {detalle}"
    if nivel.upper() == "ERROR":
        logger.error(msg)
    elif nivel.upper() == "WARNING":
        logger.warning(msg)
    else:
        logger.info(msg)

    if persistir:
        try:
            kwargs = {} if db_path is None else {"db_path": db_path}
            registrar_bitacora_db(nivel, modulo, accion, detalle, cufe, **kwargs)
        except Exception as exc:
            logger.warning("No se pudo persistir en BD: %s", exc)

    return registro


def obtener_registros_sesion() -> list[RegistroBitacora]:
    """
    Retorna todos los registros acumulados durante la sesión actual.

    Returns:
        Lista de RegistroBitacora de la sesión en curso.
    """
    return list(_registros_sesion)


def limpiar_sesion() -> None:
    """Limpia los registros acumulados en memoria (útil para tests)."""
    _registros_sesion.clear()
