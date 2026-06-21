"""
Auditoría — Fase 3.

Registra en la BD de sistema (`audit_log`) las acciones clave del sistema:
inicios/cierres de sesión, cambios de empresa, procesos RADIAN/banco,
exportaciones a SIIGO, ediciones de preasientos, gestión de empresas/usuarios
e **intentos de acceso denegados** (relevantes para seguridad).

Es best-effort: un fallo al auditar nunca debe tumbar la acción del usuario.
"""

from __future__ import annotations

import logging

from flask import has_request_context, request

from app import config
from app import database as _db

logger = logging.getLogger(__name__)


def _system_db_path() -> str:
    return config.SYSTEM_DB_PATH


def _ip() -> str:
    if not has_request_context():
        return ""
    # Respeta el encabezado del proxy de Azure (App Service termina TLS allí).
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


def registrar(
    accion: str,
    *,
    empresa_id: str | None = None,
    detalle: str = "",
    resultado: str = "ok",
) -> None:
    """Registra un evento de auditoría con el usuario y la empresa del contexto.

    El usuario y la IP se resuelven automáticamente de la petición actual.
    """
    try:
        usuario_id = None
        usuario_email = ""
        # Import perezoso para evitar ciclos (authn → authz; audit lo usa el decorador).
        from app import authn
        usuario = authn.usuario_actual()
        if usuario:
            usuario_id = usuario["id"]
            usuario_email = usuario["email"]
        _db.registrar_evento_auditoria(
            accion,
            usuario_id=usuario_id,
            usuario_email=usuario_email,
            empresa_id=empresa_id,
            detalle=detalle,
            ip=_ip(),
            resultado=resultado,
            db_path=_system_db_path(),
        )
    except Exception:
        logger.exception("No se pudo registrar el evento de auditoría: %s", accion)


def listar(limite: int = 200) -> list[dict]:
    """Eventos de auditoría más recientes (para la vista de administración)."""
    try:
        return _db.listar_auditoria(limite, db_path=_system_db_path())
    except Exception:
        logger.exception("No se pudo listar la auditoría.")
        return []
