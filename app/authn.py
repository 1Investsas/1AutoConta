"""
Autenticación — Fase 4 (Microsoft Entra ID vía App Service Authentication).

Resuelve *quién* es el usuario de la petición actual. Dos modos (config.AUTH_MODE):

- "dev"   : stub de desarrollo. Si no hay un usuario elegido en la sesión, se
            resuelve el administrador local (config.DEV_AUTH_EMAIL), que se
            autoprovisiona con rol de administrador global. La UI permite
            "iniciar sesión" como otro usuario para probar roles, y "cerrar
            sesión" suprime el autologin hasta el próximo login.
- "entra" : la identidad la provee App Service Authentication (Easy Auth) con
            Microsoft Entra ID. La plataforma valida el token OIDC y le inyecta
            a la app las cabeceras X-MS-CLIENT-PRINCIPAL* (que además ELIMINA
            de cualquier petición externa, por lo que dentro de App Service son
            de confianza). De ahí se extraen email, nombre, objeto (oid) y
            tenant (tid); el usuario se resuelve/autoprovisiona en la tabla
            `usuarios` y los roles los asigna un administrador. El login lo
            inicia /login redirigiendo a /.auth/login/aad y el logout cierra
            también la sesión de Easy Auth (/.auth/logout).

La autorización (qué puede hacer) vive en `app/authz.py`; el aislamiento por
empresa (a qué empresas accede) en `app/tenancy.py`.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from urllib.parse import quote

from flask import g, has_request_context, redirect, request, session, url_for

from app import config
from app import authz
from app import database as _db

logger = logging.getLogger(__name__)

# Clave de sesión: email del usuario elegido (login real o stub dev).
SESSION_EMAIL_KEY = "auth_email"
# Bandera que suprime el autologin del stub dev tras cerrar sesión.
SESSION_LOGOUT_KEY = "auth_logout"
# Email cuyo último acceso ya quedó registrado en esta sesión (modo entra):
# evita un UPDATE a `usuarios` en cada petición.
SESSION_ACCESO_KEY = "auth_acceso"

# Cabeceras que App Service Authentication inyecta con la identidad Entra.
# X-MS-CLIENT-PRINCIPAL trae el principal completo (claims en JSON base64);
# las otras dos son el fallback si por configuración no llegara el principal.
_HEADER_ENTRA_PRINCIPAL = "X-MS-CLIENT-PRINCIPAL"
_HEADER_ENTRA_EMAIL = "X-MS-CLIENT-PRINCIPAL-NAME"
_HEADER_ENTRA_OID = "X-MS-CLIENT-PRINCIPAL-ID"

# Tipos de claim aceptados por dato. Easy Auth entrega según configuración los
# nombres cortos del token v2 (preferred_username, oid, tid…) o los URIs largos
# de WS-Fed; se aceptan ambos, en orden de preferencia.
_CLAIMS_EMAIL = (
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "email",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "upn",
)
_CLAIMS_NOMBRE = ("name", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
_CLAIMS_OID = ("http://schemas.microsoft.com/identity/claims/objectidentifier", "oid")
_CLAIMS_TID = ("http://schemas.microsoft.com/identity/claims/tenantid", "tid")

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


def _primer_claim(claims: dict[str, str], tipos: tuple[str, ...]) -> str:
    """Primer valor no vacío entre los tipos de claim dados."""
    for t in tipos:
        val = (claims.get(t) or "").strip()
        if val:
            return val
    return ""


def principal_entra() -> dict | None:
    """Identidad Entra de la petición, según App Service Authentication.

    Decodifica la cabecera X-MS-CLIENT-PRINCIPAL (JSON base64 con los claims
    del token) y retorna ``{"email", "nombre", "oid", "tid"}``. Si el principal
    no llega, cae a las cabeceras simples X-MS-CLIENT-PRINCIPAL-NAME/-ID.

    Retorna None si no hay identidad o si el tenant no es el permitido
    (config.ENTRA_TENANT_ID, cuando está definido).
    """
    email = (request.headers.get(_HEADER_ENTRA_EMAIL) or "").strip().lower()
    nombre = ""
    oid = (request.headers.get(_HEADER_ENTRA_OID) or "").strip()
    tid = ""

    b64 = (request.headers.get(_HEADER_ENTRA_PRINCIPAL) or "").strip()
    if b64:
        try:
            # Easy Auth usa base64 sin padding garantizado; se completa.
            payload = json.loads(base64.b64decode(b64 + "=" * (-len(b64) % 4)))
            claims: dict[str, str] = {}
            for c in payload.get("claims", []):
                claims.setdefault(c.get("typ") or "", c.get("val") or "")
            email = (_primer_claim(claims, _CLAIMS_EMAIL) or email).lower()
            nombre = _primer_claim(claims, _CLAIMS_NOMBRE)
            oid = _primer_claim(claims, _CLAIMS_OID) or oid
            tid = _primer_claim(claims, _CLAIMS_TID).lower()
        except (ValueError, TypeError):
            logger.warning(
                "Cabecera %s ilegible; se usa el fallback de cabeceras simples",
                _HEADER_ENTRA_PRINCIPAL,
            )

    if not email:
        return None

    # Con tenant configurado se exige el claim `tid` y que coincida: una
    # identidad de otro tenant (o sin tenant verificable) no entra.
    if config.ENTRA_TENANT_ID and tid != config.ENTRA_TENANT_ID:
        logger.warning(
            "Identidad Entra rechazada por tenant no permitido: %s (tid=%s)",
            email, tid or "?",
        )
        return None

    return {"email": email, "nombre": nombre, "oid": oid, "tid": tid}


def _email_solicitado_dev() -> str | None:
    """Email del usuario para esta petición en modo dev (stub)."""
    # Usuario elegido en la sesión, o el admin local por defecto
    # (salvo que se haya cerrado sesión explícitamente).
    elegido = session.get(SESSION_EMAIL_KEY)
    if elegido:
        return elegido.strip().lower()
    if not session.get(SESSION_LOGOUT_KEY):
        return config.DEV_AUTH_EMAIL
    return None


def _resolver_usuario_entra(principal: dict, path: str) -> dict | None:
    """Resuelve/autoprovisiona el usuario Entra y sincroniza nombre/oid.

    El usuario nuevo se crea SIN roles (los asigna un administrador después),
    salvo el admin de bootstrap que maneja `usuario_actual`.
    """
    usuario = _db.obtener_usuario_por_email(principal["email"], db_path=path)
    if usuario is None:
        _db.crear_usuario(
            principal["email"], nombre=principal["nombre"],
            entra_oid=principal["oid"], db_path=path,
        )
        usuario = _db.obtener_usuario_por_email(principal["email"], db_path=path)
        logger.info("Usuario Entra autoprovisionado (sin roles): %s", principal["email"])
    else:
        # Entra es la fuente de verdad del nombre y el oid: si trae valores
        # nuevos (o el registro aún no los tenía), se sincronizan.
        nombre = principal["nombre"] or None
        oid = principal["oid"] or None
        if (nombre and nombre != usuario["nombre"]) or (oid and oid != usuario["entra_oid"]):
            _db.actualizar_usuario(usuario["id"], nombre=nombre, entra_oid=oid, db_path=path)
            usuario = _db.obtener_usuario_por_email(principal["email"], db_path=path)

    # Último acceso: una sola vez por sesión (no un UPDATE por petición).
    if usuario and usuario["activo"] and session.get(SESSION_ACCESO_KEY) != usuario["email"]:
        _db.registrar_acceso_usuario(usuario["id"], db_path=path)
        session[SESSION_ACCESO_KEY] = usuario["email"]
    return usuario


def usuario_actual() -> dict | None:
    """Resuelve el usuario de la petición actual (o None si no hay sesión válida).

    El resultado se cachea en `flask.g` durante la petición.
    """
    if has_request_context() and "usuario_actual" in g:
        return g.usuario_actual

    _asegurar_auth()
    path = _system_db_path()
    usuario: dict | None = None

    if _modo_entra():
        # En producción Entra la identidad SOLO sale de las cabeceras de
        # confianza que inyecta App Service Authentication (no de la sesión,
        # que el cliente podría manipular).
        principal = principal_entra()
        if principal:
            usuario = _resolver_usuario_entra(principal, path)
    else:
        email = _email_solicitado_dev()
        if email:
            usuario = _db.obtener_usuario_por_email(email, db_path=path)

    if usuario and config.BOOTSTRAP_ADMIN_EMAIL and \
            usuario["email"] == config.BOOTSTRAP_ADMIN_EMAIL:
        rid = _db.obtener_o_crear_rol(authz.ROL_ADMIN, db_path=path)
        _db.asignar_rol_global(usuario["id"], rid, db_path=path)
    if usuario and not usuario["activo"]:
        usuario = None  # cuenta desactivada → sin acceso

    if has_request_context():
        g.usuario_actual = usuario
    return usuario


def iniciar_sesion(email: str) -> dict | None:
    """Marca a `email` como el usuario activo de la sesión (login, SOLO modo dev).

    En modo entra el login lo hace App Service Authentication; elegir usuario
    por formulario queda deshabilitado. Retorna el usuario si existe y está
    activo; None en caso contrario.
    """
    if _modo_entra():
        return None
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
    session.pop(SESSION_ACCESO_KEY, None)
    session[SESSION_LOGOUT_KEY] = True
    if has_request_context():
        g.pop("usuario_actual", None)


def url_login_entra(destino: str = "/") -> str:
    """URL de Easy Auth que inicia el login con Entra y vuelve a `destino`."""
    if not destino.startswith("/") or destino.startswith("//"):
        destino = "/"
    return f"{config.ENTRA_LOGIN_PATH}?post_login_redirect_uri={quote(destino, safe='')}"


def url_logout_entra() -> str:
    """URL de Easy Auth que cierra la sesión Entra y aterriza en /login."""
    destino = quote(url_for("web.login"), safe="")
    return f"{config.ENTRA_LOGOUT_PATH}?post_logout_redirect_uri={destino}"


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
