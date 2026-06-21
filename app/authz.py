"""
Autorización (RBAC) — Fase 3.

Define el catálogo de permisos y roles, los siembra en la BD de sistema y
expone la comprobación de permisos y el decorador `require_permission` que
protege las rutas web.

Modelo:
- Un **permiso** es una capacidad atómica (p. ej. `siigo.exportar`).
- Un **rol** agrupa permisos (p. ej. `contador`).
- Un usuario recibe roles de forma **global** (aplican en todas las empresas)
  o **por empresa** (acotados a una). El conjunto efectivo de permisos para una
  empresa es la unión de ambos (ver `app/database.py::permisos_usuario`).

Los roles se alinean con la sección «Usuarios» del menú lateral
(Digitación → auxiliar, Tributario y fiscal → contador, Visualización →
consulta) más un rol `admin` (administrador) que suele asignarse global.
"""

from __future__ import annotations

import functools
import logging

from app import config
from app import database as _db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catálogo de permisos (nombre → descripción)
# ---------------------------------------------------------------------------
PERMISOS: dict[str, str] = {
    "dashboard.ver":          "Ver el panel principal",
    "radian.ver":             "Ver el módulo RADIAN y sus importaciones",
    "radian.procesar":        "Cargar y procesar reportes RADIAN",
    "radian.editar":          "Editar preasientos RADIAN (tercero, dividir, confirmar)",
    "radian.exportar":        "Generar el archivo SIIGO de RADIAN",
    "banco.ver":              "Ver el módulo de Bancos y su historial",
    "banco.procesar":         "Cargar y previsualizar extractos bancarios",
    "banco.exportar":         "Generar el archivo SIIGO de Bancos",
    "importaciones.ver":      "Ver el listado de importaciones",
    "importaciones.gestionar": "Retomar, regenerar, eliminar y descargar importaciones",
    "analitica.ver":          "Ver analíticas y reportes",
    "ml.ver":                 "Ver el historial de aprendizaje (machine learning)",
    "empresas.ver":           "Ver y seleccionar empresas",
    "empresas.gestionar":     "Crear, editar y eliminar empresas y sus maestros",
    "usuarios.gestionar":     "Administrar usuarios y roles",
    "auditoria.ver":          "Ver la bitácora de auditoría",
}

# Permisos de solo lectura (compartidos por todos los roles operativos).
_VER = (
    "dashboard.ver", "radian.ver", "banco.ver",
    "importaciones.ver", "analitica.ver", "ml.ver", "empresas.ver",
)

# ---------------------------------------------------------------------------
# Catálogo de roles (nombre → (descripción, permisos))
# ---------------------------------------------------------------------------
ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "admin": (
        "Administrador — acceso total (usuarios, empresas, auditoría)",
        tuple(PERMISOS.keys()),
    ),
    "contador": (
        "Contador (tributario y fiscal) — opera, exporta y revisa",
        _VER + (
            "radian.procesar", "radian.editar", "radian.exportar",
            "banco.procesar", "banco.exportar",
            "importaciones.gestionar", "auditoria.ver",
        ),
    ),
    "auxiliar": (
        "Auxiliar (digitación) — captura y edita, sin exportar a SIIGO",
        _VER + (
            "radian.procesar", "radian.editar",
            "banco.procesar",
            "importaciones.gestionar",
        ),
    ),
    "consulta": (
        "Visualización — solo lectura",
        _VER,
    ),
}

ROL_ADMIN = "admin"


def _system_db_path() -> str:
    """Ruta (dinámica) de la BD de sistema; se lee de config para tests/overrides."""
    return config.SYSTEM_DB_PATH


def seed_rbac(db_path: str | None = None) -> None:
    """Siembra (idempotente) el catálogo de permisos, roles y sus vínculos."""
    path = db_path or _system_db_path()
    permiso_id: dict[str, int] = {}
    for nombre, desc in PERMISOS.items():
        permiso_id[nombre] = _db.obtener_o_crear_permiso(nombre, desc, db_path=path)
    for nombre, (desc, permisos) in ROLES.items():
        rid = _db.obtener_o_crear_rol(nombre, desc, db_path=path)
        for p in permisos:
            _db.vincular_rol_permiso(rid, permiso_id[p], db_path=path)


# ---------------------------------------------------------------------------
# Comprobación de permisos
# ---------------------------------------------------------------------------

def permisos_de(usuario: dict | None, empresa_id: str | None) -> set:
    """Conjunto de permisos efectivos del usuario para la empresa dada."""
    if not usuario:
        return set()
    return _db.permisos_usuario(usuario["id"], empresa_id, db_path=_system_db_path())


def tiene_permiso(usuario: dict | None, empresa_id: str | None, permiso: str) -> bool:
    """True si el usuario tiene `permiso` en el contexto de `empresa_id`."""
    if not usuario:
        return False
    return permiso in permisos_de(usuario, empresa_id)


def require_permission(permiso: str):
    """Decorador de ruta: exige `permiso` para la empresa activa.

    Resuelve el usuario y la empresa activa (validada por tenancy), comprueba el
    permiso y, si falta, registra el intento denegado en auditoría y responde
    403. Si no hay usuario autenticado, delega en el flujo de login.
    """
    def decorador(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from flask import abort, request
            from app import authn, tenancy, audit

            usuario = authn.usuario_actual()
            if usuario is None:
                return authn.redirigir_login()

            emp = tenancy.empresa_actual()
            emp_id = emp.id if emp else None
            if not tiene_permiso(usuario, emp_id, permiso):
                audit.registrar(
                    "permiso.denegado",
                    empresa_id=emp_id,
                    detalle=f"{permiso} · {request.method} {request.path}",
                    resultado="denegado",
                )
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorador
