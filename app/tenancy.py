"""
Multi-tenencia — Fase 3.

Controla *a qué empresas* puede acceder un usuario y resuelve la empresa activa
de la sesión de forma **validada**. Es el arreglo del bloqueante #1: hasta ahora
cualquiera podía fijar `session["empresa_id"]` y ver datos de otra empresa; aquí
se comprueba que el usuario tenga un rol (global o de esa empresa) antes de
devolverla.

Reglas de acceso:
- Un usuario con cualquier **rol global** accede a TODAS las empresas.
- En caso contrario, accede solo a las empresas en las que tiene un rol
  (`usuario_empresa_roles`).
"""

from __future__ import annotations

import logging

from flask import g, has_request_context, session

from app import config
from app import database as _db
from app import empresas as _empresas

logger = logging.getLogger(__name__)

# Clave de sesión con la empresa activa (compartida con las rutas web).
KEY_EMPRESA = "empresa_id"
_G_CACHE = "tenancy_empresa_actual"


def _system_db_path() -> str:
    return config.SYSTEM_DB_PATH


def puede_acceder_empresa(usuario: dict | None, empresa_id: str | None) -> bool:
    """True si el usuario puede operar la empresa indicada."""
    if not usuario or not empresa_id:
        return False
    path = _system_db_path()
    if _db.tiene_rol_global(usuario["id"], db_path=path):
        return True
    return empresa_id in _db.empresas_de_usuario(usuario["id"], db_path=path)


def empresas_accesibles(usuario: dict | None) -> list:
    """Empresas que el usuario puede ver/seleccionar (lista de `Empresa`)."""
    todas = _empresas.listar_empresas()
    if usuario is None:
        return todas
    path = _system_db_path()
    if _db.tiene_rol_global(usuario["id"], db_path=path):
        return todas
    ids = _db.empresas_de_usuario(usuario["id"], db_path=path)
    return [e for e in todas if e.id in ids]


def empresa_actual():
    """Resuelve y valida la empresa activa de la sesión.

    Si la empresa seleccionada no es accesible (o no hay ninguna), cae a la
    primera empresa accesible y corrige la sesión. Retorna None si el usuario
    no tiene acceso a ninguna empresa.
    """
    if has_request_context() and _G_CACHE in g:
        return g.get(_G_CACHE)

    from app import authn
    usuario = authn.usuario_actual()
    seleccion = session.get(KEY_EMPRESA)

    if usuario is None:
        # Sin usuario el gate normalmente ya redirigió; mantener comportamiento
        # legacy por robustez (devuelve la principal).
        emp = _empresas.obtener_empresa(seleccion)
        return emp

    if seleccion and puede_acceder_empresa(usuario, seleccion):
        emp = _empresas.obtener_empresa(seleccion)
    else:
        accesibles = empresas_accesibles(usuario)
        emp = accesibles[0] if accesibles else None
        if emp is not None and has_request_context() and seleccion != emp.id:
            # Corrige una selección ausente/no autorizada de forma silenciosa.
            session[KEY_EMPRESA] = emp.id

    if has_request_context():
        setattr(g, _G_CACHE, emp)
    return emp


def seleccionar_empresa(empresa_id: str):
    """Fija la empresa activa si el usuario tiene acceso. Retorna la `Empresa` o None."""
    from app import authn
    usuario = authn.usuario_actual()
    if not puede_acceder_empresa(usuario, empresa_id):
        return None
    session[KEY_EMPRESA] = empresa_id
    if has_request_context():
        g.pop(_G_CACHE, None)
    return _empresas.obtener_empresa(empresa_id)
