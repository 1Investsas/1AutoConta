# ═══════════════════════════════════════════════════════════════════════════
# RBAC — usuarios, roles, permisos y auditoría (BD de sistema · Fase 3)
# ───────────────────────────────────────────────────────────────────────────
# Estas tablas viven en la MISMA BD de sistema que `empresas` (central en SQLite,
# compartida en Azure SQL). Modelan la autenticación y autorización:
#   - usuarios               : identidades (email es la clave de login).
#   - roles / permisos        : catálogo RBAC (un rol agrupa permisos).
#   - role_permissions        : qué permisos otorga cada rol.
#   - usuario_global_roles    : roles que aplican en TODAS las empresas
#                               (p. ej. administrador global → acceso total).
#   - usuario_empresa_roles   : roles acotados a UNA empresa (tenancy + RBAC).
#                               El conjunto de empresa_id con filas aquí define
#                               a qué empresas puede acceder el usuario.
#   - audit_log               : bitácora de acciones clave (incluye intentos
#                               denegados, relevantes para seguridad).
#
# A diferencia de las tablas por-empresa, NO se filtran por `empresa_id`: son el
# control de acceso transversal. El aislamiento de datos lo siguen dando el
# db_path (SQLite) y el filtro empresa_id (Azure SQL) en las tablas operativas.
# ═══════════════════════════════════════════════════════════════════════════

import logging
from datetime import datetime
from typing import Optional

from app.config import SYSTEM_DB_PATH

from . import core
from .core import (
    DbConnection,
)

logger = logging.getLogger(__name__)

def _insert_id(conn: "DbConnection") -> int:
    """Id autoincremental de la última inserción (ambos backends)."""
    if conn.is_sqlite:
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return int(conn.execute("SELECT @@IDENTITY").fetchone()[0])


def inicializar_db_auth(db_path: str = SYSTEM_DB_PATH) -> None:
    """Crea las tablas de RBAC/auditoría en la BD de sistema (idempotente)."""
    conn = core.get_connection(db_path)
    try:
        if conn.is_sqlite:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    email         TEXT NOT NULL UNIQUE,
                    nombre        TEXT,
                    entra_oid     TEXT,
                    activo        INTEGER NOT NULL DEFAULT 1,
                    creado        TEXT,
                    ultimo_acceso TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roles (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre      TEXT NOT NULL UNIQUE,
                    descripcion TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS permisos (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre      TEXT NOT NULL UNIQUE,
                    descripcion TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_id    INTEGER NOT NULL,
                    permiso_id INTEGER NOT NULL,
                    PRIMARY KEY (role_id, permiso_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usuario_global_roles (
                    usuario_id INTEGER NOT NULL,
                    role_id    INTEGER NOT NULL,
                    PRIMARY KEY (usuario_id, role_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usuario_empresa_roles (
                    usuario_id INTEGER NOT NULL,
                    empresa_id TEXT    NOT NULL,
                    role_id    INTEGER NOT NULL,
                    PRIMARY KEY (usuario_id, empresa_id, role_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT NOT NULL,
                    usuario_id    INTEGER,
                    usuario_email TEXT,
                    empresa_id    TEXT,
                    accion        TEXT NOT NULL,
                    detalle       TEXT,
                    ip            TEXT,
                    resultado     TEXT
                )
            """)
        else:
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'usuarios')
                CREATE TABLE usuarios (
                    id            INT IDENTITY(1,1) PRIMARY KEY,
                    email         NVARCHAR(320) NOT NULL UNIQUE,
                    nombre        NVARCHAR(300),
                    entra_oid     NVARCHAR(100),
                    activo        BIT NOT NULL DEFAULT 1,
                    creado        NVARCHAR(50),
                    ultimo_acceso NVARCHAR(50)
                )
            """)
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'roles')
                CREATE TABLE roles (
                    id          INT IDENTITY(1,1) PRIMARY KEY,
                    nombre      NVARCHAR(100) NOT NULL UNIQUE,
                    descripcion NVARCHAR(300)
                )
            """)
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'permisos')
                CREATE TABLE permisos (
                    id          INT IDENTITY(1,1) PRIMARY KEY,
                    nombre      NVARCHAR(100) NOT NULL UNIQUE,
                    descripcion NVARCHAR(300)
                )
            """)
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'role_permissions')
                CREATE TABLE role_permissions (
                    role_id    INT NOT NULL,
                    permiso_id INT NOT NULL,
                    PRIMARY KEY (role_id, permiso_id)
                )
            """)
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'usuario_global_roles')
                CREATE TABLE usuario_global_roles (
                    usuario_id INT NOT NULL,
                    role_id    INT NOT NULL,
                    PRIMARY KEY (usuario_id, role_id)
                )
            """)
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'usuario_empresa_roles')
                CREATE TABLE usuario_empresa_roles (
                    usuario_id INT           NOT NULL,
                    empresa_id NVARCHAR(100) NOT NULL,
                    role_id    INT           NOT NULL,
                    PRIMARY KEY (usuario_id, empresa_id, role_id)
                )
            """)
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'audit_log')
                CREATE TABLE audit_log (
                    id            INT IDENTITY(1,1) PRIMARY KEY,
                    timestamp     NVARCHAR(50) NOT NULL,
                    usuario_id    INT,
                    usuario_email NVARCHAR(320),
                    empresa_id    NVARCHAR(100),
                    accion        NVARCHAR(100) NOT NULL,
                    detalle       NVARCHAR(MAX),
                    ip            NVARCHAR(64),
                    resultado     NVARCHAR(20)
                )
            """)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Usuarios
# --------------------------------------------------------------------------

def _fila_usuario(row) -> dict:
    return {
        "id":            int(row["id"]),
        "email":         row["email"],
        "nombre":        row["nombre"] or "",
        "entra_oid":     row["entra_oid"] or "",
        "activo":        bool(row["activo"]),
        "creado":        row["creado"] or "",
        "ultimo_acceso": row["ultimo_acceso"] or "",
    }


def obtener_usuario_por_email(
    email: str, db_path: str = SYSTEM_DB_PATH
) -> Optional[dict]:
    """Retorna el usuario por email (insensible a mayúsculas), o None."""
    conn = core.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, email, nombre, entra_oid, activo, creado, ultimo_acceso "
            "FROM usuarios WHERE LOWER(email) = ?",
            ((email or "").strip().lower(),),
        ).fetchone()
        return _fila_usuario(row) if row else None
    finally:
        conn.close()


def crear_usuario(
    email: str,
    nombre: str = "",
    entra_oid: str = "",
    activo: bool = True,
    db_path: str = SYSTEM_DB_PATH,
) -> int:
    """Crea un usuario y retorna su id."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO usuarios (email, nombre, entra_oid, activo, creado) "
            "VALUES (?,?,?,?,?)",
            ((email or "").strip().lower(), nombre or "", entra_oid or "",
             1 if activo else 0, datetime.now().isoformat()),
        )
        uid = _insert_id(conn)
        conn.commit()
        return uid
    finally:
        conn.close()


def actualizar_usuario(
    usuario_id: int,
    *,
    nombre: Optional[str] = None,
    activo: Optional[bool] = None,
    entra_oid: Optional[str] = None,
    db_path: str = SYSTEM_DB_PATH,
) -> None:
    """Actualiza campos del usuario (solo los pasados; COALESCE conserva el resto)."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "UPDATE usuarios SET nombre = COALESCE(?, nombre), "
            "activo = COALESCE(?, activo), entra_oid = COALESCE(?, entra_oid) "
            "WHERE id = ?",
            (nombre, None if activo is None else (1 if activo else 0),
             entra_oid, usuario_id),
        )
        conn.commit()
    finally:
        conn.close()


def registrar_acceso_usuario(usuario_id: int, db_path: str = SYSTEM_DB_PATH) -> None:
    """Marca la fecha del último acceso del usuario."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?",
            (datetime.now().isoformat(), usuario_id),
        )
        conn.commit()
    finally:
        conn.close()


def listar_usuarios(db_path: str = SYSTEM_DB_PATH) -> list[dict]:
    """Lista todos los usuarios ordenados por email."""
    conn = core.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, email, nombre, entra_oid, activo, creado, ultimo_acceso "
            "FROM usuarios ORDER BY email"
        ).fetchall()
        return [_fila_usuario(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Roles y permisos
# --------------------------------------------------------------------------

def obtener_o_crear_rol(
    nombre: str, descripcion: str = "", db_path: str = SYSTEM_DB_PATH
) -> int:
    """Retorna el id del rol `nombre`, creándolo si no existe."""
    conn = core.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM roles WHERE nombre = ?", (nombre,)
        ).fetchone()
        if row:
            return int(row["id"])
        conn.execute(
            "INSERT INTO roles (nombre, descripcion) VALUES (?,?)",
            (nombre, descripcion or ""),
        )
        rid = _insert_id(conn)
        conn.commit()
        return rid
    finally:
        conn.close()


def obtener_o_crear_permiso(
    nombre: str, descripcion: str = "", db_path: str = SYSTEM_DB_PATH
) -> int:
    """Retorna el id del permiso `nombre`, creándolo si no existe."""
    conn = core.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM permisos WHERE nombre = ?", (nombre,)
        ).fetchone()
        if row:
            return int(row["id"])
        conn.execute(
            "INSERT INTO permisos (nombre, descripcion) VALUES (?,?)",
            (nombre, descripcion or ""),
        )
        pid = _insert_id(conn)
        conn.commit()
        return pid
    finally:
        conn.close()


def vincular_rol_permiso(
    role_id: int, permiso_id: int, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Asocia un permiso a un rol (idempotente)."""
    conn = core.get_connection(db_path)
    try:
        existe = conn.execute(
            "SELECT 1 FROM role_permissions WHERE role_id = ? AND permiso_id = ?",
            (role_id, permiso_id),
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO role_permissions (role_id, permiso_id) VALUES (?,?)",
                (role_id, permiso_id),
            )
            conn.commit()
    finally:
        conn.close()


def listar_roles(db_path: str = SYSTEM_DB_PATH) -> list[dict]:
    """Lista los roles del catálogo."""
    conn = core.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, nombre, descripcion FROM roles ORDER BY nombre"
        ).fetchall()
        return [{"id": int(r["id"]), "nombre": r["nombre"],
                 "descripcion": r["descripcion"] or ""} for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Asignación de roles a usuarios
# --------------------------------------------------------------------------

def asignar_rol_global(
    usuario_id: int, role_id: int, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Otorga un rol global al usuario (aplica en todas las empresas)."""
    conn = core.get_connection(db_path)
    try:
        existe = conn.execute(
            "SELECT 1 FROM usuario_global_roles WHERE usuario_id = ? AND role_id = ?",
            (usuario_id, role_id),
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO usuario_global_roles (usuario_id, role_id) VALUES (?,?)",
                (usuario_id, role_id),
            )
            conn.commit()
    finally:
        conn.close()


def asignar_rol_empresa(
    usuario_id: int, empresa_id: str, role_id: int, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Otorga al usuario un rol acotado a una empresa."""
    conn = core.get_connection(db_path)
    try:
        existe = conn.execute(
            "SELECT 1 FROM usuario_empresa_roles "
            "WHERE usuario_id = ? AND empresa_id = ? AND role_id = ?",
            (usuario_id, empresa_id, role_id),
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO usuario_empresa_roles (usuario_id, empresa_id, role_id) "
                "VALUES (?,?,?)",
                (usuario_id, empresa_id, role_id),
            )
            conn.commit()
    finally:
        conn.close()


def revocar_rol_empresa(
    usuario_id: int, empresa_id: str, role_id: int, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Quita un rol de empresa al usuario."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "DELETE FROM usuario_empresa_roles "
            "WHERE usuario_id = ? AND empresa_id = ? AND role_id = ?",
            (usuario_id, empresa_id, role_id),
        )
        conn.commit()
    finally:
        conn.close()


def revocar_rol_global(
    usuario_id: int, role_id: int, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Quita un rol global al usuario."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "DELETE FROM usuario_global_roles WHERE usuario_id = ? AND role_id = ?",
            (usuario_id, role_id),
        )
        conn.commit()
    finally:
        conn.close()


def revocar_roles_empresa_usuario(
    usuario_id: int, empresa_id: str, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Quita TODOS los roles del usuario en una empresa (para reasignar)."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "DELETE FROM usuario_empresa_roles WHERE usuario_id = ? AND empresa_id = ?",
            (usuario_id, empresa_id),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Consultas de autorización (hot path)
# --------------------------------------------------------------------------

def tiene_rol_global(usuario_id: int, db_path: str = SYSTEM_DB_PATH) -> bool:
    """True si el usuario tiene algún rol global (acceso a todas las empresas)."""
    conn = core.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM usuario_global_roles WHERE usuario_id = ?",
            (usuario_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def permisos_usuario(
    usuario_id: int, empresa_id: Optional[str], db_path: str = SYSTEM_DB_PATH
) -> set:
    """Conjunto de nombres de permiso del usuario para la empresa dada.

    Une los permisos de sus roles GLOBALES (aplican en cualquier empresa) con
    los de sus roles acotados a `empresa_id`. Si `empresa_id` es None solo se
    consideran los roles globales.
    """
    conn = core.get_connection(db_path)
    try:
        emp = empresa_id or ""
        rows = conn.execute(
            """
            SELECT DISTINCT p.nombre
            FROM permisos p
            JOIN role_permissions rp ON rp.permiso_id = p.id
            WHERE rp.role_id IN (
                SELECT role_id FROM usuario_global_roles WHERE usuario_id = ?
                UNION
                SELECT role_id FROM usuario_empresa_roles
                WHERE usuario_id = ? AND empresa_id = ?
            )
            """,
            (usuario_id, usuario_id, emp),
        ).fetchall()
        return {r["nombre"] for r in rows}
    finally:
        conn.close()


def empresas_de_usuario(usuario_id: int, db_path: str = SYSTEM_DB_PATH) -> set:
    """Conjunto de empresa_id a los que el usuario tiene acceso explícito."""
    conn = core.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT empresa_id FROM usuario_empresa_roles WHERE usuario_id = ?",
            (usuario_id,),
        ).fetchall()
        return {r["empresa_id"] for r in rows}
    finally:
        conn.close()


def roles_de_usuario(usuario_id: int, db_path: str = SYSTEM_DB_PATH) -> list[dict]:
    """Lista los roles del usuario (globales y por empresa) para la UI de admin."""
    conn = core.get_connection(db_path)
    try:
        globales = conn.execute(
            """
            SELECT r.nombre AS rol
            FROM usuario_global_roles ug JOIN roles r ON r.id = ug.role_id
            WHERE ug.usuario_id = ?
            """,
            (usuario_id,),
        ).fetchall()
        por_empresa = conn.execute(
            """
            SELECT ue.empresa_id AS empresa_id, r.nombre AS rol
            FROM usuario_empresa_roles ue JOIN roles r ON r.id = ue.role_id
            WHERE ue.usuario_id = ?
            """,
            (usuario_id,),
        ).fetchall()
        out = [{"ambito": "global", "empresa_id": None, "rol": r["rol"]}
               for r in globales]
        out += [{"ambito": "empresa", "empresa_id": r["empresa_id"], "rol": r["rol"]}
                for r in por_empresa]
        return out
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Auditoría
# --------------------------------------------------------------------------

def registrar_evento_auditoria(
    accion: str,
    usuario_id: Optional[int] = None,
    usuario_email: str = "",
    empresa_id: Optional[str] = None,
    detalle: str = "",
    ip: str = "",
    resultado: str = "ok",
    db_path: str = SYSTEM_DB_PATH,
) -> None:
    """Inserta un evento en la bitácora de auditoría (best-effort)."""
    conn = core.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO audit_log "
            "(timestamp, usuario_id, usuario_email, empresa_id, accion, detalle, ip, resultado) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), usuario_id, usuario_email or "",
             empresa_id, accion, detalle or "", ip or "", resultado or "ok"),
        )
        conn.commit()
    finally:
        conn.close()


def listar_auditoria(
    limite: int = 200, db_path: str = SYSTEM_DB_PATH
) -> list[dict]:
    """Retorna los eventos de auditoría más recientes."""
    conn = core.get_connection(db_path)
    try:
        if conn.is_sqlite:
            rows = conn.execute(
                "SELECT timestamp, usuario_email, empresa_id, accion, detalle, "
                "ip, resultado FROM audit_log ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT TOP (?) timestamp, usuario_email, empresa_id, accion, "
                "detalle, ip, resultado FROM audit_log ORDER BY id DESC",
                (limite,),
            ).fetchall()
        return [
            {
                "timestamp":     r["timestamp"],
                "usuario_email": r["usuario_email"] or "",
                "empresa_id":    r["empresa_id"] or "",
                "accion":        r["accion"],
                "detalle":       r["detalle"] or "",
                "ip":            r["ip"] or "",
                "resultado":     r["resultado"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


