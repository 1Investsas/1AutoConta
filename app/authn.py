"""
Autenticación — Fase 3 (con stub de desarrollo, listo para Entra en Fase 4).

Resuelve *quién* es el usuario de la petición actual. Dos modos (config.AUTH_MODE):

- "dev"   : stub de desarrollo. Si no hay un usuario elegido en la sesión, se
            resuelve el administrador local (config.DEV_AUTH_EMAIL), que se
            autoprovisiona con rol de administrador global. La UI permite
            "iniciar sesión" como otro usuario para probar roles, y "cerrar
            sesión" suprime el autologin hasta el próximo login.
- "entra" : la identidad llega en la cabecera X-MS-CLIENT-PRINCIPAL-NAME que
            inyecta App Service Authentication. El usuario se resuelve/crea en
            la tabla `usuarios`; los roles los asigna un administrador.

La autorización (qué puede hacer) vive en `app/authz.py`; el aislamiento por
empresa (a qué empresas accede) en `app/tenancy.py`.
"""

from __future__ import annotations

import logging
import threading

from flask import g, has_request_context, redirect, request, session, url_for

from app import config
from app import authz
from app import database as _db

logger = logging.getLogger(__name__)

# Clave de sesión: email del usuario elegido (login real o stub dev).
SESSION_EMAIL_KEY = "auth_email"
# Bandera que suprime el autologin del stub dev tras cerrar sesión.
SESSION_LOGOUT_KEY = "auth_logout"

# Cabecera que App Service Authentication inyecta con el email del usuario Entra.
_HEADER_ENTRA_EMAIL = "X-MS-CLIENT-PRINCIPAL-NAME"

# Endpoints accesibles sin sesión iniciada (además de los estáticos).
# `web.radian_auto_cron` se protege con su propio token compartido, no por sesión.
_ENDPOINTS_PUBLICOS = {
    "web.login", "web.logout", "web.health", "web.radian_auto_cron", "static",
}

_auth_lock = threading.Lock()
_auth_listo: set[str] = set()


def _system_db_path() -> str:
    return config.SYSTEM_DB_PATH


def _modo_entra() -> bool:
    return config.AUTH_MODE == "entra"


def _asegurar_auth() -> None:
    """Crea el esquema RBAC, siembra el catálogo y provisiona el admin dev (1ª vez)."""
    path = _system_db_path()
    if path in _auth_listo:
        return
    with _auth_lock:
        if path in _auth_listo:
            return
        _db.inicializar_db_auth(path)
        authz.seed_rbac(path)
        if not _modo_entra():
            _provisionar_admin(config.DEV_AUTH_EMAIL, config.DEV_AUTH_NOMBRE, path)
        _auth_listo.add(path)


def reset_estado() -> None:
    """Olvida el estado de inicialización (para aislar tests entre sí)."""
    _auth_listo.clear()


def _provisionar_admin(email: str, nombre: str, path: str) -> dict:
    """Asegura un usuario con rol de administrador global. Retorna el usuario."""
    usuario = _db.obtener_usuario_por_email(email, db_path=path)
    if usuario is None:
        _db.crear_usuario(email, nombre=nombre, db_path=path)
        usuario = _db.obtener_usuario_por_email(email, db_path=path)
    rid = _db.obtener_o_crear_rol(authz.ROL_ADMIN, db_path=path)
    _db.asignar_rol_global(usuario["id"], rid, db_path=path)
    return usuario


def _email_solicitado() -> str | None:
    """Email del usuario para esta petición, según el modo de autenticación."""
    if _modo_entra():
        # En producción Entra la identidad SOLO sale de la cabecera de confianza
        # que inyecta App Service Authentication (no de la sesión, que el cliente
        # podría manipular).
        return (request.headers.get(_HEADER_ENTRA_EMAIL) or "").strip().lower() or None

    # Modo dev: usuario elegido en la sesión, o el admin local por defecto
    # (salvo que se haya cerrado sesión explícitamente).
    elegido = session.get(SESSION_EMAIL_KEY)
    if elegido:
        return elegido.strip().lower()
    if not session.get(SESSION_LOGOUT_KEY):
        return config.DEV_AUTH_EMAIL
    return None


def usuario_actual() -> dict | None:
    """Resuelve el usuario de la petición actual (o None si no hay sesión válida).

    El resultado se cachea en `flask.g` durante la petición.
    """
    if has_request_context() and "usuario_actual" in g:
        return g.usuario_actual

    _asegurar_auth()
    path = _system_db_path()
    email = _email_solicitado()
    usuario: dict | None = None

    if email:
        usuario = _db.obtener_usuario_por_email(email, db_path=path)
        # En Entra el usuario puede no existir aún: se autoprovisiona (sin roles,
        # salvo que sea el admin de bootstrap configurado).
        if usuario is None and _modo_entra():
            nombre = (request.headers.get("X-MS-CLIENT-PRINCIPAL-ID") or "").strip()
            _db.crear_usuario(email, nombre=nombre, db_path=path)
            usuario = _db.obtener_usuario_por_email(email, db_path=path)
        if usuario and config.BOOTSTRAP_ADMIN_EMAIL and \
                email == config.BOOTSTRAP_ADMIN_EMAIL:
            rid = _db.obtener_o_crear_rol(authz.ROL_ADMIN, db_path=path)
            _db.asignar_rol_global(usuario["id"], rid, db_path=path)
        if usuario and not usuario["activo"]:
            usuario = None  # cuenta desactivada → sin acceso

    if has_request_context():
        g.usuario_actual = usuario
    return usuario


def iniciar_sesion(email: str) -> dict | None:
    """Marca a `email` como el usuario activo de la sesión (login).

    Retorna el usuario si existe y está activo; None en caso contrario.
    """
    _asegurar_auth()
    email = (email or "").strip().lower()
    usuario = _db.obtener_usuario_por_email(email, db_path=_system_db_path())
    if usuario is None or not usuario["activo"]:
        return None
    session[SESSION_EMAIL_KEY] = email
    session.pop(SESSION_LOGOUT_KEY, None)
    _db.registrar_acceso_usuario(usuario["id"], db_path=_system_db_path())
    if has_request_context():
        g.pop("usuario_actual", None)
    return usuario


def cerrar_sesion() -> None:
    """Cierra la sesión del usuario (y suprime el autologin del stub dev)."""
    session.pop(SESSION_EMAIL_KEY, None)
    session[SESSION_LOGOUT_KEY] = True
    if has_request_context():
        g.pop("usuario_actual", None)


def redirigir_login():
    """Respuesta de redirección a la página de login (conservando el destino)."""
    return redirect(url_for("web.login", next=request.path))


def gate():
    """`before_request`: exige sesión iniciada salvo en endpoints públicos."""
    endpoint = request.endpoint
    if endpoint is None or endpoint in _ENDPOINTS_PUBLICOS:
        return None
    if usuario_actual() is None:
        return redirigir_login()
    return None


def registrar(app) -> None:
    """Engancha la compuerta de autenticación en la app Flask."""
    app.before_request(gate)
