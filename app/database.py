"""
Gestión de la base de datos del sistema contable-auto.

Soporta dos backends según la variable de entorno USE_SQLITE:
- SQLite  (local, desarrollo) — comportamiento original.
- Azure SQL Database (producción en la nube) — vía pyodbc.

Proporciona la inicialización del esquema, funciones CRUD básicas
y registro de documentos procesados para detección de duplicados.
"""

import atexit
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import (
    DB_PATH, SYSTEM_DB_PATH, USE_SQLITE, DATABASE_URL, DB_JOURNAL_MODE,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Persistencia de la BD SQLite en Blob Storage
# ───────────────────────────────────────────────────────────────────────────
# El disco local del contenedor puede ser efímero (Azure App Service for
# Containers sin almacenamiento persistente, Container Apps, etc.). En esa
# situación, con SQLite la app "empieza desde cero" en cada reinicio o
# despliegue aunque los archivos (uploads, Excel, maestros) sí persistan en
# Blob. Para evitarlo: cuando hay Blob configurado (modo cloud) y se usa
# SQLite, el archivo .db se respalda en Blob y se restaura al abrir la primera
# conexión.
#
# Las muchas escrituras de un mismo proceso (p. ej. registrar cada documento
# del RADIAN) se coalescen con un pequeño "debounce": tras cada commit se
# reprograma una única subida en segundo plano. Al salir el proceso se sube
# cualquier respaldo pendiente.
#
# Nota: esto NO da concurrencia real entre varios workers/instancias (la última
# subida gana). Para robustez/concurrencia, migrar a Azure SQL (USE_SQLITE=false).
# ═══════════════════════════════════════════════════════════════════════════

_DB_BLOB_CATEGORY = "db"
_DB_BACKUP_DEBOUNCE_SEG = 2.0

_sync_lock = threading.Lock()
_db_restauradas: set[str] = set()
_db_timers: dict[str, "threading.Timer"] = {}

# Esquemas ya asegurados en este proceso. `inicializar_db` ejecuta DDL idempotente
# (CREATE TABLE IF NOT EXISTS + migraciones aditivas) que no cambia durante la
# vida del proceso, así que basta correrlo una vez por ruta de BD. Sin esto el
# DDL se reejecutaba en CADA request (dashboard, banco, radian, …): sobre un
# sistema de archivos de red (Azure /home es SMB) cada sentencia es una ida y
# vuelta lenta, y en modo nube el commit agendaba además una subida completa de
# la BD a Blob por visita. Mismo patrón que _db_restauradas / authn._auth_listo.
_init_lock = threading.Lock()
_db_inicializadas: set[str] = set()


def _db_respaldable(db_path: str) -> bool:
    """True si la BD SQLite debe respaldarse en Blob Storage."""
    if not USE_SQLITE:
        return False
    from app import storage as store
    return store.is_cloud()


def _blob_ref_db(db_path: str) -> str:
    """Referencia de Blob donde se respalda una BD SQLite."""
    return f"blob://{_DB_BLOB_CATEGORY}/{Path(db_path).name}"


def _restaurar_db_desde_blob(db_path: str) -> None:
    """Descarga la BD desde Blob si no existe localmente (una sola vez por ruta)."""
    if not _db_respaldable(db_path):
        return
    with _sync_lock:
        if db_path in _db_restauradas:
            return
    from app import storage as store
    local = Path(db_path)
    if local.exists():
        # Ya hay una BD local (en este contenedor o en almacenamiento
        # persistente compartido); es la fuente de verdad, no se sobrescribe.
        with _sync_lock:
            _db_restauradas.add(db_path)
        return
    ref = _blob_ref_db(db_path)
    try:
        if store.file_exists(ref):
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(store.get_download_bytes(ref))
            logger.info("BD SQLite restaurada desde Blob: %s", ref)
        with _sync_lock:
            _db_restauradas.add(db_path)
    except Exception:
        # Error transitorio: no marcar como restaurada para reintentar luego.
        logger.exception("No se pudo restaurar la BD desde Blob: %s", db_path)


def _checkpoint_wal(local: Path) -> None:
    """Integra el WAL en el archivo .db para que el respaldo sea consistente.

    En modo WAL los últimos cambios confirmados pueden vivir en el archivo
    `-wal`; sin un checkpoint, respaldar solo el `.db` perdería esos datos.
    En modo DELETE es una operación inocua.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(str(local), timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("No se pudo hacer checkpoint WAL de %s", local)


def _subir_db_a_blob(db_path: str) -> None:
    """Sube el archivo .db actual a Blob (best-effort)."""
    if not _db_respaldable(db_path):
        return
    from app import storage as store
    local = Path(db_path)
    if not local.exists():
        return
    try:
        _checkpoint_wal(local)
        store.save_file(local.read_bytes(), _DB_BLOB_CATEGORY, local.name)
        logger.debug("BD SQLite respaldada en Blob: %s", local.name)
    except Exception:
        logger.exception("No se pudo respaldar la BD en Blob: %s", db_path)


def _flush_respaldo_db(db_path: str) -> None:
    """Ejecuta la subida pendiente de una BD y olvida su timer."""
    with _sync_lock:
        _db_timers.pop(db_path, None)
    _subir_db_a_blob(db_path)


def _programar_respaldo_db(db_path: str) -> None:
    """Agenda (con debounce) la subida de la BD a Blob tras un commit."""
    if not _db_respaldable(db_path):
        return
    with _sync_lock:
        timer = _db_timers.get(db_path)
        if timer is not None:
            timer.cancel()
        nuevo = threading.Timer(
            _DB_BACKUP_DEBOUNCE_SEG, _flush_respaldo_db, args=(db_path,)
        )
        nuevo.daemon = True
        _db_timers[db_path] = nuevo
        nuevo.start()


def _flush_todos_los_respaldos() -> None:
    """Sube cualquier respaldo pendiente (se invoca al salir del proceso)."""
    with _sync_lock:
        pendientes = list(_db_timers.keys())
        for timer in _db_timers.values():
            timer.cancel()
        _db_timers.clear()
    for db_path in pendientes:
        _subir_db_a_blob(db_path)


atexit.register(_flush_todos_los_respaldos)


# ═══════════════════════════════════════════════════════════════════════════
# Aislamiento por empresa en Azure SQL (tablas compartidas)
# ───────────────────────────────────────────────────────────────────────────
# Con SQLite cada empresa tiene su PROPIO archivo .db (ver app/empresas.py), así
# que los datos ya quedan aislados y este módulo NO añade ninguna condición: el
# comportamiento de SQLite es idéntico al original.
#
# Con Azure SQL todas las empresas comparten las mismas tablas, por lo que se
# usa una columna discriminadora `empresa_id`. El id se deriva del nombre del
# archivo .db que cada empresa pasa como db_path (convención de empresas.py):
#   db/contable.db        → 'principal'
#   db/contable_<id>.db   → '<id>'
# De este modo el aislamiento funciona sin cambiar las firmas existentes ni el
# comportamiento de SQLite. Hoy esto no se activa (USE_SQLITE=true por defecto);
# queda listo para cuando se migre a Azure SQL (USE_SQLITE=false + DATABASE_URL).
# ═══════════════════════════════════════════════════════════════════════════

_EMPRESA_PRINCIPAL_ID = "principal"  # debe coincidir con empresas.EMPRESA_PRINCIPAL_ID
_PREFIJO_DB_EMPRESA = "contable_"


def _empresa_id_desde_db_path(db_path: Optional[str]) -> str:
    """Deriva el id de empresa a partir de la ruta de su BD SQLite."""
    nombre = Path(str(db_path or DB_PATH)).stem  # 'contable' o 'contable_<id>'
    if nombre.startswith(_PREFIJO_DB_EMPRESA):
        return nombre[len(_PREFIJO_DB_EMPRESA):] or _EMPRESA_PRINCIPAL_ID
    return _EMPRESA_PRINCIPAL_ID


def _cond_empresa(conn: "DbConnection", db_path: Optional[str]):
    """Condición de aislamiento por empresa.

    Retorna (condicion_sql, params):
    - SQLite:    ('', ())                        — cada empresa ya tiene su archivo.
    - Azure SQL: ('empresa_id = ?', ('<id>',))   — tablas compartidas.
    """
    if conn.is_sqlite:
        return "", ()
    return "empresa_id = ?", (_empresa_id_desde_db_path(db_path),)


def _and_empresa(conn: "DbConnection", db_path: Optional[str]):
    """Cláusula para anexar a un WHERE existente (' AND empresa_id = ?')."""
    cond, params = _cond_empresa(conn, db_path)
    return (f" AND {cond}" if cond else ""), params


def _where_empresa(conn: "DbConnection", db_path: Optional[str]):
    """Cláusula WHERE completa por empresa (vacía en SQLite)."""
    cond, params = _cond_empresa(conn, db_path)
    return (f" WHERE {cond}" if cond else ""), params


# ═══════════════════════════════════════════════════════════════════════════
# Abstracción de conexión — permite usar sqlite3 o pyodbc sin cambios en
# el código consumidor.
# ═══════════════════════════════════════════════════════════════════════════

class DictRow(dict):
    """Fila que soporta acceso por nombre (row['col']) y por índice (row[0])."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _CursorResult:
    """Envuelve un cursor pyodbc para retornar DictRow."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._description = cursor.description

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._description is None:
            return row
        cols = [d[0] for d in self._description]
        return DictRow(zip(cols, row))

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows or self._description is None:
            return []
        cols = [d[0] for d in self._description]
        return [DictRow(zip(cols, row)) for row in rows]


class DbConnection:
    """Conexión unificada que funciona con sqlite3 y pyodbc."""

    def __init__(self, conn, is_sqlite: bool, db_path: Optional[str] = None):
        self._conn = conn
        self.is_sqlite = is_sqlite
        self._db_path = db_path

    def execute(self, sql, params=None):
        if self.is_sqlite:
            return self._conn.execute(sql, params) if params else self._conn.execute(sql)
        else:
            cursor = self._conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return _CursorResult(cursor)

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()
        # Tras confirmar cambios, agendar el respaldo de la BD en Blob (si aplica).
        if self.is_sqlite and self._db_path:
            _programar_respaldo_db(self._db_path)

    def close(self):
        self._conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Fábrica de conexiones
# ═══════════════════════════════════════════════════════════════════════════

def get_connection(db_path: str = DB_PATH):
    """
    Retorna una conexión a la base de datos.

    Cuando USE_SQLITE es True (por defecto), usa SQLite en la ruta indicada.
    Cuando USE_SQLITE es False, usa Azure SQL Database vía pyodbc.
    El parámetro db_path se ignora en modo Azure SQL.
    """
    if USE_SQLITE:
        import sqlite3
        # Si la BD vive en Blob y no existe localmente, restaurarla antes de
        # abrir la conexión (evita "empezar desde cero" en disco efímero).
        _restaurar_db_desde_blob(db_path)
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA journal_mode={DB_JOURNAL_MODE}")
        conn.execute("PRAGMA foreign_keys=ON")
        # Espera (en ms) si otra conexión tiene la BD bloqueada, en vez de
        # fallar de inmediato con "database is locked". Relevante con varios
        # workers de gunicorn sobre el mismo archivo SQLite.
        conn.execute("PRAGMA busy_timeout=5000")
        return DbConnection(conn, is_sqlite=True, db_path=str(path))
    else:
        import pyodbc
        if not DATABASE_URL:
            raise RuntimeError(
                "USE_SQLITE=false pero DATABASE_URL no está configurada. "
                "Agrega la cadena de conexión de Azure SQL en las variables de entorno."
            )
        conn = pyodbc.connect(DATABASE_URL)
        return DbConnection(conn, is_sqlite=False)


# ═══════════════════════════════════════════════════════════════════════════
# Inicialización del esquema
# ═══════════════════════════════════════════════════════════════════════════

def inicializar_db(db_path: str = DB_PATH) -> None:
    """
    Crea todas las tablas necesarias si no existen.

    Tablas: documentos_importados, bitacora, historial_cuentas, importaciones,
    procesos_banco.

    El esquema se asegura una sola vez por proceso y por ruta de BD: el DDL es
    idempotente y estático, de modo que reejecutarlo en cada request solo añade
    latencia (relevante con SQLite sobre un FS de red). Para reinicializar —p. ej.
    en tests que recrean la BD— usar `reset_inicializacion_db()`.
    """
    if db_path in _db_inicializadas:
        return
    with _init_lock:
        if db_path in _db_inicializadas:
            return
        conn = get_connection(db_path)
        try:
            if conn.is_sqlite:
                _create_tables_sqlite(conn)
            else:
                _create_tables_mssql(conn)
            # Migraciones aditivas para BD ya existentes (las tablas nuevas ya
            # incluyen la columna; este ALTER cubre instalaciones previas).
            _asegurar_columna(conn, "importaciones", "preasientos_json",
                              "TEXT", "NVARCHAR(MAX)")
            # Modelo durable del módulo Bancos: archivo original + snapshot editable.
            _asegurar_columna(conn, "procesos_banco", "archivo_ref",
                              "TEXT", "NVARCHAR(500)")
            _asegurar_columna(conn, "procesos_banco", "snapshot_json",
                              "TEXT", "NVARCHAR(MAX)")
            # Índices tenant-aware: solo en Azure SQL (tablas compartidas). En SQLite
            # cada empresa tiene su propio archivo y no hay columna empresa_id.
            if not conn.is_sqlite:
                _asegurar_indices_mssql(conn)
            conn.commit()
            logger.info("Base de datos inicializada correctamente.")
        finally:
            conn.close()
        _db_inicializadas.add(db_path)


def reset_inicializacion_db() -> None:
    """Olvida qué esquemas se aseguraron (para aislar tests que recrean la BD)."""
    with _init_lock:
        _db_inicializadas.clear()


def _columna_existe(conn: "DbConnection", tabla: str, columna: str) -> bool:
    """True si `tabla` ya tiene la columna `columna` (en ambos backends)."""
    if conn.is_sqlite:
        rows = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
        return any(r["name"] == columna for r in rows)
    row = conn.execute(
        "SELECT 1 FROM sys.columns "
        "WHERE object_id = OBJECT_ID(?) AND name = ?",
        (tabla, columna),
    ).fetchone()
    return row is not None


def _asegurar_columna(
    conn: "DbConnection", tabla: str, columna: str,
    tipo_sqlite: str, tipo_mssql: str,
) -> None:
    """Agrega una columna a una tabla existente si aún no está (migración aditiva)."""
    if _columna_existe(conn, tabla, columna):
        return
    if conn.is_sqlite:
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo_sqlite}")
    else:
        conn.execute(f"ALTER TABLE {tabla} ADD {columna} {tipo_mssql}")


# Índices tenant-aware para Azure SQL (tablas compartidas entre empresas). Cada
# entrada es (nombre_índice, tabla, columnas). El nombre lleva el prefijo de la
# tabla porque los nombres de índice son únicos por base de datos en SQL Server.
#
# Las tablas con UNIQUE(empresa_id, …) (documentos_importados, historial_cuentas,
# correcciones_tercero) ya tienen un índice que cubre el filtro por empresa, así
# que no se repiten aquí. `bitacora` solo se escribe (no se lee por empresa), por
# lo que indexarla solo añadiría costo de escritura.
_INDICES_MSSQL = (
    # Listados por empresa ordenados por id descendente.
    ("ix_importaciones_empresa",     "importaciones",        "empresa_id, id"),
    ("ix_procesos_banco_empresa",    "procesos_banco",       "empresa_id, id"),
    # Analítica: distribución/evolución agrupada por clasificación dentro de la empresa.
    ("ix_documentos_empresa_clasif", "documentos_importados", "empresa_id, clasificacion"),
)


def _asegurar_indices_mssql(conn: "DbConnection") -> None:
    """Crea los índices tenant-aware en Azure SQL si no existen (idempotente).

    No aplica a SQLite: allí cada empresa tiene su propio archivo .db y las tablas
    no tienen columna `empresa_id`.
    """
    for nombre, tabla, columnas in _INDICES_MSSQL:
        conn.execute(
            f"IF NOT EXISTS (SELECT 1 FROM sys.indexes "
            f"WHERE name = '{nombre}' AND object_id = OBJECT_ID('{tabla}')) "
            f"CREATE INDEX {nombre} ON {tabla} ({columnas})"
        )


def _create_tables_sqlite(conn: DbConnection) -> None:
    """Crea tablas con sintaxis SQLite."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documentos_importados (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cufe            TEXT    NOT NULL UNIQUE,
            tipo_documento  TEXT,
            clasificacion   TEXT,
            folio           TEXT,
            prefijo         TEXT,
            nit_emisor      TEXT,
            nombre_emisor   TEXT,
            nit_receptor    TEXT,
            nombre_receptor TEXT,
            total           REAL,
            fecha_emision   TEXT,
            fecha_proceso   TEXT    NOT NULL,
            archivo_origen  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bitacora (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            nivel       TEXT    NOT NULL,
            modulo      TEXT,
            accion      TEXT,
            detalle     TEXT,
            cufe        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historial_cuentas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            clasificacion   TEXT    NOT NULL,
            nit_tercero     TEXT    NOT NULL,
            tipo_linea      TEXT    NOT NULL,
            cuenta          TEXT    NOT NULL,
            usos            INTEGER DEFAULT 1,
            ultima_vez      TEXT,
            UNIQUE(clasificacion, nit_tercero, tipo_linea)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS importaciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TEXT    NOT NULL,
            archivo_nombre  TEXT,
            archivo_ref     TEXT,
            n_docs          INTEGER DEFAULT 0,
            n_excepciones   INTEGER DEFAULT 0,
            excel_ref       TEXT,
            estado          TEXT    NOT NULL DEFAULT 'procesando',
            error           TEXT,
            preasientos_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS procesos_banco (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TEXT    NOT NULL,
            archivo_nombre  TEXT,
            archivo_ref     TEXT,
            cuenta_banco    TEXT,
            nit_banco       TEXT,
            n_movimientos   INTEGER DEFAULT 0,
            estado          TEXT    NOT NULL DEFAULT 'procesando',
            error           TEXT,
            snapshot_json   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS correcciones_tercero (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nit_original     TEXT    NOT NULL,
            nombre_original  TEXT,
            nit_corregido    TEXT    NOT NULL,
            nombre_corregido TEXT,
            clasificacion    TEXT,
            usos             INTEGER DEFAULT 1,
            ultima_vez       TEXT,
            UNIQUE(nit_original)
        )
    """)


def _create_tables_mssql(conn: DbConnection) -> None:
    """Crea tablas con sintaxis T-SQL (Azure SQL Database)."""
    # Nota: cada tabla lleva una columna `empresa_id` discriminadora porque en
    # Azure SQL todas las empresas comparten las mismas tablas (a diferencia de
    # SQLite, donde cada empresa tiene su propio archivo). El valor por defecto
    # 'principal' preserva los datos de instalaciones de una sola empresa.
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'documentos_importados')
        CREATE TABLE documentos_importados (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            cufe            NVARCHAR(500)  NOT NULL,
            tipo_documento  NVARCHAR(100),
            clasificacion   NVARCHAR(100),
            folio           NVARCHAR(100),
            prefijo         NVARCHAR(100),
            nit_emisor      NVARCHAR(50),
            nombre_emisor   NVARCHAR(300),
            nit_receptor    NVARCHAR(50),
            nombre_receptor NVARCHAR(300),
            total           FLOAT,
            fecha_emision   NVARCHAR(50),
            fecha_proceso   NVARCHAR(50)   NOT NULL,
            archivo_origen  NVARCHAR(500),
            CONSTRAINT uq_doc_empresa_cufe UNIQUE(empresa_id, cufe)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'bitacora')
        CREATE TABLE bitacora (
            id          INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id  NVARCHAR(100) NOT NULL DEFAULT 'principal',
            timestamp   NVARCHAR(50)  NOT NULL,
            nivel       NVARCHAR(20)  NOT NULL,
            modulo      NVARCHAR(100),
            accion      NVARCHAR(100),
            detalle     NVARCHAR(MAX),
            cufe        NVARCHAR(500)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'historial_cuentas')
        CREATE TABLE historial_cuentas (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            clasificacion   NVARCHAR(100)  NOT NULL,
            nit_tercero     NVARCHAR(50)   NOT NULL,
            tipo_linea      NVARCHAR(100)  NOT NULL,
            cuenta          NVARCHAR(50)   NOT NULL,
            usos            INT DEFAULT 1,
            ultima_vez      NVARCHAR(50),
            CONSTRAINT uq_historial UNIQUE(empresa_id, clasificacion, nit_tercero, tipo_linea)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'importaciones')
        CREATE TABLE importaciones (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            fecha           NVARCHAR(50)   NOT NULL,
            archivo_nombre  NVARCHAR(300),
            archivo_ref     NVARCHAR(500),
            n_docs          INT DEFAULT 0,
            n_excepciones   INT DEFAULT 0,
            excel_ref       NVARCHAR(500),
            estado          NVARCHAR(30)   NOT NULL DEFAULT 'procesando',
            error           NVARCHAR(MAX),
            preasientos_json NVARCHAR(MAX)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'procesos_banco')
        CREATE TABLE procesos_banco (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            fecha           NVARCHAR(50)   NOT NULL,
            archivo_nombre  NVARCHAR(300),
            archivo_ref     NVARCHAR(500),
            cuenta_banco    NVARCHAR(50),
            nit_banco       NVARCHAR(50),
            n_movimientos   INT DEFAULT 0,
            estado          NVARCHAR(30)   NOT NULL DEFAULT 'procesando',
            error           NVARCHAR(MAX),
            snapshot_json   NVARCHAR(MAX)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'correcciones_tercero')
        CREATE TABLE correcciones_tercero (
            id               INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id       NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            nit_original     NVARCHAR(50)   NOT NULL,
            nombre_original  NVARCHAR(300),
            nit_corregido    NVARCHAR(50)   NOT NULL,
            nombre_corregido NVARCHAR(300),
            clasificacion    NVARCHAR(100),
            usos             INT DEFAULT 1,
            ultima_vez       NVARCHAR(50),
            CONSTRAINT uq_correccion_tercero UNIQUE(empresa_id, nit_original)
        )
    """)


# ═══════════════════════════════════════════════════════════════════════════
# Registro de empresas (BD de sistema)
# ───────────────────────────────────────────────────────────────────────────
# La fuente de verdad del registro de empresas vive en la tabla `empresas`. A
# diferencia de las demás tablas (que son por-empresa en SQLite y se aíslan por
# `empresa_id` en Azure SQL), esta tabla NO se filtra por empresa: ES el catálogo
# de empresas y debe consultarse antes de saber cuál está activa.
#
# - SQLite:    vive en una BD CENTRAL (config.SYSTEM_DB_PATH = db/sistema.db),
#              separada de los contable_<id>.db de cada empresa.
# - Azure SQL: es una tabla compartida más (db_path se ignora).
#
# Los campos de configuración con estructura (cuentas_*, bancos, formato_banco)
# se guardan serializados como JSON en columnas de texto, para no normalizar de
# más: el objetivo de esta fase es mover la fuente de verdad a SQL (base para el
# RBAC), no rediseñar el modelo de configuración de la empresa.
# ═══════════════════════════════════════════════════════════════════════════

# Columnas cuyo valor se persiste serializado como JSON.
_EMPRESA_JSON_COLS = (
    "cuentas_contraparte", "cuentas_impuestos",
    "cuentas_banco", "bancos", "formato_banco",
)


def _json_dump(valor) -> Optional[str]:
    """Serializa un dict/list a JSON, o None si está vacío."""
    if not valor:
        return None
    return json.dumps(valor, ensure_ascii=False)


def _json_load(raw):
    """Deserializa una columna JSON; None/'' → None (el llamador normaliza)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _fila_a_registro(row) -> dict:
    """Convierte una fila de `empresas` al dict del registro (forma JSON legada)."""
    datos = {
        "id":                   row["id"],
        "nit":                  row["nit"] or "",
        "nombre":               row["nombre"] or "",
        "sigla":                row["sigla"] or "",
        "cuenta_banco_default": row["cuenta_banco_default"] or "",
        "nit_banco":            row["nit_banco"] or "",
    }
    for col in _EMPRESA_JSON_COLS:
        datos[col] = _json_load(row[col])
    return datos


_EMPRESA_COLS_SELECT = (
    "id, nit, nombre, sigla, cuenta_banco_default, nit_banco, "
    "cuentas_contraparte, cuentas_impuestos, cuentas_banco, bancos, formato_banco"
)


def inicializar_db_sistema(db_path: str = SYSTEM_DB_PATH) -> None:
    """Crea la tabla `empresas` en la BD de sistema si no existe (idempotente)."""
    conn = get_connection(db_path)
    try:
        if conn.is_sqlite:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS empresas (
                    id                   TEXT PRIMARY KEY,
                    nit                  TEXT,
                    nombre               TEXT,
                    sigla                TEXT,
                    cuenta_banco_default TEXT,
                    nit_banco            TEXT,
                    cuentas_contraparte  TEXT,
                    cuentas_impuestos    TEXT,
                    cuentas_banco        TEXT,
                    bancos               TEXT,
                    formato_banco        TEXT,
                    creada               TEXT,
                    actualizada          TEXT
                )
            """)
        else:
            conn.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'empresas')
                CREATE TABLE empresas (
                    id                   NVARCHAR(100) PRIMARY KEY,
                    nit                  NVARCHAR(50),
                    nombre               NVARCHAR(300),
                    sigla                NVARCHAR(100),
                    cuenta_banco_default NVARCHAR(50),
                    nit_banco            NVARCHAR(50),
                    cuentas_contraparte  NVARCHAR(MAX),
                    cuentas_impuestos    NVARCHAR(MAX),
                    cuentas_banco        NVARCHAR(MAX),
                    bancos               NVARCHAR(MAX),
                    formato_banco        NVARCHAR(MAX),
                    creada               NVARCHAR(50),
                    actualizada          NVARCHAR(50)
                )
            """)
        conn.commit()
    finally:
        conn.close()


def contar_empresas_registro(db_path: str = SYSTEM_DB_PATH) -> int:
    """Número de empresas registradas (sirve para decidir la migración inicial)."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM empresas").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def listar_empresas_registro(db_path: str = SYSTEM_DB_PATH) -> dict:
    """Retorna el registro completo {empresa_id: {campos}} desde la BD."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_EMPRESA_COLS_SELECT} FROM empresas"
        ).fetchall()
        return {r["id"]: _fila_a_registro(r) for r in rows}
    finally:
        conn.close()


def obtener_empresa_registro(
    empresa_id: str, db_path: str = SYSTEM_DB_PATH
) -> Optional[dict]:
    """Retorna los campos de una empresa por id, o None si no existe."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {_EMPRESA_COLS_SELECT} FROM empresas WHERE id = ?",
            (empresa_id,),
        ).fetchone()
        return _fila_a_registro(row) if row else None
    finally:
        conn.close()


def guardar_empresa_registro(datos: dict, db_path: str = SYSTEM_DB_PATH) -> None:
    """Inserta o actualiza una empresa en el registro (UPSERT por id)."""
    emp_id = datos.get("id")
    if not emp_id:
        raise ValueError("guardar_empresa_registro requiere la clave 'id'.")
    conn = get_connection(db_path)
    ahora = datetime.now().isoformat()
    # Orden de columnas usado tanto en INSERT como en UPDATE/MERGE.
    vals = (
        datos.get("nit", "") or "",
        datos.get("nombre", "") or "",
        datos.get("sigla", "") or "",
        datos.get("cuenta_banco_default", "") or "",
        datos.get("nit_banco", "") or "",
        _json_dump(datos.get("cuentas_contraparte")),
        _json_dump(datos.get("cuentas_impuestos")),
        _json_dump(datos.get("cuentas_banco")),
        _json_dump(datos.get("bancos")),
        _json_dump(datos.get("formato_banco")),
    )
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO empresas
                    (id, nit, nombre, sigla, cuenta_banco_default, nit_banco,
                     cuentas_contraparte, cuentas_impuestos, cuentas_banco,
                     bancos, formato_banco, creada, actualizada)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    nit                  = excluded.nit,
                    nombre               = excluded.nombre,
                    sigla                = excluded.sigla,
                    cuenta_banco_default = excluded.cuenta_banco_default,
                    nit_banco            = excluded.nit_banco,
                    cuentas_contraparte  = excluded.cuentas_contraparte,
                    cuentas_impuestos    = excluded.cuentas_impuestos,
                    cuentas_banco        = excluded.cuentas_banco,
                    bancos               = excluded.bancos,
                    formato_banco        = excluded.formato_banco,
                    actualizada          = excluded.actualizada
                """,
                (emp_id,) + vals + (ahora, ahora),
            )
        else:
            conn.execute(
                """
                MERGE empresas AS target
                USING (SELECT ? AS id) AS source
                ON target.id = source.id
                WHEN MATCHED THEN
                    UPDATE SET nit=?, nombre=?, sigla=?, cuenta_banco_default=?,
                               nit_banco=?, cuentas_contraparte=?,
                               cuentas_impuestos=?, cuentas_banco=?, bancos=?,
                               formato_banco=?, actualizada=?
                WHEN NOT MATCHED THEN
                    INSERT (id, nit, nombre, sigla, cuenta_banco_default, nit_banco,
                            cuentas_contraparte, cuentas_impuestos, cuentas_banco,
                            bancos, formato_banco, creada, actualizada)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                (emp_id,) + vals + (ahora,) + (emp_id,) + vals + (ahora, ahora),
            )
        conn.commit()
    finally:
        conn.close()


def eliminar_empresa_registro(
    empresa_id: str, db_path: str = SYSTEM_DB_PATH
) -> None:
    """Elimina una empresa del registro."""
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
        conn.commit()
    finally:
        conn.close()


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


def _insert_id(conn: "DbConnection") -> int:
    """Id autoincremental de la última inserción (ambos backends)."""
    if conn.is_sqlite:
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return int(conn.execute("SELECT @@IDENTITY").fetchone()[0])


def inicializar_db_auth(db_path: str = SYSTEM_DB_PATH) -> None:
    """Crea las tablas de RBAC/auditoría en la BD de sistema (idempotente)."""
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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
    conn = get_connection(db_path)
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


# ═══════════════════════════════════════════════════════════════════════════
# Funciones CRUD
# ═══════════════════════════════════════════════════════════════════════════

def cufe_existe(cufe: str, db_path: str = DB_PATH) -> bool:
    """
    Verifica si un CUFE/CUDE ya fue procesado anteriormente.

    Returns:
        True si el CUFE ya existe en la base de datos.
    """
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT 1 FROM documentos_importados WHERE cufe = ?{and_emp}",
            (cufe,) + p_emp,
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def registrar_documento(
    cufe: str,
    tipo_documento: str,
    clasificacion: str,
    folio: str,
    prefijo: str,
    nit_emisor: str,
    nombre_emisor: str,
    nit_receptor: str,
    nombre_receptor: str,
    total: float,
    fecha_emision: Optional[datetime],
    archivo_origen: str,
    db_path: str = DB_PATH,
) -> None:
    """Inserta un documento procesado en la tabla documentos_importados."""
    conn = get_connection(db_path)
    fecha_em = fecha_emision.isoformat() if fecha_emision else None
    fecha_proc = datetime.now().isoformat()
    params = (
        cufe, tipo_documento, clasificacion, folio, prefijo,
        nit_emisor, nombre_emisor, nit_receptor, nombre_receptor,
        total, fecha_em, fecha_proc, archivo_origen,
    )
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT OR IGNORE INTO documentos_importados
                (cufe, tipo_documento, clasificacion, folio, prefijo,
                 nit_emisor, nombre_emisor, nit_receptor, nombre_receptor,
                 total, fecha_emision, fecha_proceso, archivo_origen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                params,
            )
        else:
            # T-SQL: verificar existencia antes de insertar (duplicado por empresa)
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                IF NOT EXISTS (SELECT 1 FROM documentos_importados
                               WHERE cufe = ? AND empresa_id = ?)
                INSERT INTO documentos_importados
                (empresa_id, cufe, tipo_documento, clasificacion, folio, prefijo,
                 nit_emisor, nombre_emisor, nit_receptor, nombre_receptor,
                 total, fecha_emision, fecha_proceso, archivo_origen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (cufe, emp_id, emp_id) + params,
            )
        conn.commit()
    finally:
        conn.close()


def registrar_bitacora_db(
    nivel: str,
    modulo: str,
    accion: str,
    detalle: str,
    cufe: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Inserta un registro en la tabla de bitácora."""
    conn = get_connection(db_path)
    ts = datetime.now().isoformat()
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO bitacora (timestamp, nivel, modulo, accion, detalle, cufe)
                VALUES (?,?,?,?,?,?)
                """,
                (ts, nivel, modulo, accion, detalle, cufe),
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                INSERT INTO bitacora
                    (empresa_id, timestamp, nivel, modulo, accion, detalle, cufe)
                VALUES (?,?,?,?,?,?,?)
                """,
                (emp_id, ts, nivel, modulo, accion, detalle, cufe),
            )
        conn.commit()
    finally:
        conn.close()


def obtener_historial_cuenta(
    clasificacion: str,
    nit_tercero: str,
    tipo_linea: str,
    db_path: str = DB_PATH,
) -> Optional[str]:
    """
    Retorna la cuenta más usada históricamente para una combinación
    clasificacion/tercero/tipo_linea, o None si no hay historial.
    """
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"""
            SELECT cuenta FROM historial_cuentas
            WHERE clasificacion=? AND nit_tercero=? AND tipo_linea=?{and_emp}
            ORDER BY usos DESC
            """,
            (clasificacion, nit_tercero, tipo_linea) + p_emp,
        ).fetchone()
        return row["cuenta"] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Correcciones de tercero — trazabilidad y aprendizaje (Fase 1)
# ---------------------------------------------------------------------------

def obtener_correccion_tercero(
    nit_original: str,
    db_path: str = DB_PATH,
) -> Optional[dict]:
    """
    Retorna la corrección de tercero registrada para un NIT original, o None.

    El NIT original es el que identificó el pipeline desde RADIAN antes de
    cualquier corrección manual. Si el usuario lo corrigió alguna vez, aquí
    se devuelve el NIT/nombre corregido para reaplicarlo automáticamente.

    Returns:
        Dict con 'nit_corregido' y 'nombre_corregido', o None si no hay registro.
    """
    if not nit_original:
        return None
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"""
            SELECT nit_corregido, nombre_corregido FROM correcciones_tercero
            WHERE nit_original=?{and_emp}
            """,
            (nit_original,) + p_emp,
        ).fetchone()
        if not row:
            return None
        return {
            "nit_corregido": row["nit_corregido"],
            "nombre_corregido": row["nombre_corregido"] or "",
        }
    finally:
        conn.close()


def registrar_correccion_tercero(
    nit_original: str,
    nombre_original: str,
    nit_corregido: str,
    nombre_corregido: str,
    clasificacion: str = "",
    db_path: str = DB_PATH,
) -> None:
    """
    Registra (o actualiza) una corrección de tercero.

    Hace un UPSERT por `nit_original`: si ya existía una corrección para ese
    NIT, actualiza el destino e incrementa `usos`. Sirve para trazabilidad
    (qué se corrigió) y aprendizaje (reaplicar la corrección en el futuro).
    """
    if not nit_original or not nit_corregido:
        return
    conn = get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO correcciones_tercero
                    (nit_original, nombre_original, nit_corregido,
                     nombre_corregido, clasificacion, usos, ultima_vez)
                VALUES (?,?,?,?,?,1,?)
                ON CONFLICT(nit_original) DO UPDATE SET
                    nombre_original  = excluded.nombre_original,
                    nit_corregido    = excluded.nit_corregido,
                    nombre_corregido = excluded.nombre_corregido,
                    clasificacion    = excluded.clasificacion,
                    usos             = usos + 1,
                    ultima_vez       = excluded.ultima_vez
                """,
                (nit_original, nombre_original, nit_corregido,
                 nombre_corregido, clasificacion, ahora),
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                MERGE correcciones_tercero AS target
                USING (SELECT ? AS empresa_id, ? AS nit_original, ? AS nombre_original,
                              ? AS nit_corregido, ? AS nombre_corregido,
                              ? AS clasificacion, ? AS ultima_vez) AS source
                ON target.empresa_id = source.empresa_id
                   AND target.nit_original = source.nit_original
                WHEN MATCHED THEN
                    UPDATE SET nombre_original  = source.nombre_original,
                               nit_corregido    = source.nit_corregido,
                               nombre_corregido = source.nombre_corregido,
                               clasificacion    = source.clasificacion,
                               usos             = target.usos + 1,
                               ultima_vez       = source.ultima_vez
                WHEN NOT MATCHED THEN
                    INSERT (empresa_id, nit_original, nombre_original, nit_corregido,
                            nombre_corregido, clasificacion, usos, ultima_vez)
                    VALUES (source.empresa_id, source.nit_original, source.nombre_original,
                            source.nit_corregido, source.nombre_corregido,
                            source.clasificacion, 1, source.ultima_vez);
                """,
                (emp_id, nit_original, nombre_original, nit_corregido,
                 nombre_corregido, clasificacion, ahora),
            )
        conn.commit()
    finally:
        conn.close()


def listar_correcciones_tercero(
    db_path: str = DB_PATH,
    limite: int = 200,
) -> list[dict]:
    """Lista las correcciones de tercero registradas (más recientes primero)."""
    conn = get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        top = "" if conn.is_sqlite else f"TOP {int(limite)} "
        limit_sql = f" LIMIT {int(limite)}" if conn.is_sqlite else ""
        rows = conn.execute(
            f"""
            SELECT {top}nit_original, nombre_original, nit_corregido,
                   nombre_corregido, clasificacion, usos, ultima_vez
            FROM correcciones_tercero{where_emp}
            ORDER BY ultima_vez DESC{limit_sql}
            """,
            p_emp,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics — Fase 4
# ---------------------------------------------------------------------------

def _month_expr(col: str, is_sqlite: bool) -> str:
    """Retorna la expresión SQL para extraer YYYY-MM de una columna de fecha."""
    if is_sqlite:
        return f"strftime('%Y-%m', {col})"
    else:
        return f"LEFT({col}, 7)"


def _substr_expr(col: str, start: int, length: int, is_sqlite: bool) -> str:
    """Retorna la expresión SQL para substring, compatible con ambos backends."""
    if is_sqlite:
        return f"SUBSTR({col}, {start}, {length})"
    else:
        return f"SUBSTRING({col}, {start}, {length})"


def obtener_kpis(db_path: str = DB_PATH) -> dict:
    """
    Retorna KPIs generales del historial de documentos.

    Returns:
        Dict con: total_docs, total_ventas, total_compras, total_otros,
                  monto_ventas, monto_compras, monto_total,
                  promedio_por_doc, docs_este_mes, archivos_procesados.
    """
    conn = get_connection(db_path)
    try:
        mes_actual = datetime.now().strftime("%Y-%m")
        month = _month_expr("fecha_proceso", conn.is_sqlite)
        where_emp, p_emp = _where_empresa(conn, db_path)
        and_emp, _ = _and_empresa(conn, db_path)

        row = conn.execute(f"""
            SELECT
                COUNT(*)                                          AS total_docs,
                SUM(CASE WHEN clasificacion LIKE '%VENTA%'  THEN 1 ELSE 0 END) AS total_ventas,
                SUM(CASE WHEN clasificacion LIKE '%COMPRA%' THEN 1 ELSE 0 END) AS total_compras,
                SUM(CASE WHEN clasificacion NOT LIKE '%VENTA%'
                          AND clasificacion NOT LIKE '%COMPRA%' THEN 1 ELSE 0 END) AS total_otros,
                SUM(CASE WHEN clasificacion LIKE '%VENTA%'  THEN total ELSE 0 END) AS monto_ventas,
                SUM(CASE WHEN clasificacion LIKE '%COMPRA%' THEN total ELSE 0 END) AS monto_compras,
                SUM(COALESCE(total, 0))                           AS monto_total,
                AVG(COALESCE(total, 0))                           AS promedio_por_doc,
                COUNT(DISTINCT archivo_origen)                    AS archivos_procesados
            FROM documentos_importados{where_emp}
        """, p_emp).fetchone()

        docs_mes = conn.execute(f"""
            SELECT COUNT(*) FROM documentos_importados
            WHERE {month} = ?{and_emp}
        """, (mes_actual,) + p_emp).fetchone()[0]

        return {
            "total_docs":          row["total_docs"]          or 0,
            "total_ventas":        row["total_ventas"]        or 0,
            "total_compras":       row["total_compras"]       or 0,
            "total_otros":         row["total_otros"]         or 0,
            "monto_ventas":        row["monto_ventas"]        or 0.0,
            "monto_compras":       row["monto_compras"]       or 0.0,
            "monto_total":         row["monto_total"]         or 0.0,
            "promedio_por_doc":    row["promedio_por_doc"]    or 0.0,
            "archivos_procesados": row["archivos_procesados"] or 0,
            "docs_este_mes":       docs_mes                   or 0,
        }
    finally:
        conn.close()


def obtener_evolucion_mensual(db_path: str = DB_PATH, meses: int = 12) -> list[dict]:
    """
    Retorna la evolución mensual de montos y conteos por clasificación macro
    (VENTAS, COMPRAS, OTROS) para los últimos `meses` meses.
    """
    conn = get_connection(db_path)
    try:
        month = _month_expr("fecha_emision", conn.is_sqlite)

        if conn.is_sqlite:
            date_filter = f"{month} >= strftime('%Y-%m', 'now', ?)"
            param = f"-{meses} months"
        else:
            # Para T-SQL con fechas ISO almacenadas como strings
            cutoff = datetime.now()
            from dateutil.relativedelta import relativedelta
            cutoff = cutoff - relativedelta(months=meses)
            date_filter = f"{month} >= ?"
            param = cutoff.strftime("%Y-%m")

        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(f"""
            SELECT
                {month}  AS mes,
                SUM(CASE WHEN clasificacion LIKE '%VENTA%'  THEN COALESCE(total,0) ELSE 0 END) AS ventas_monto,
                SUM(CASE WHEN clasificacion LIKE '%COMPRA%' THEN COALESCE(total,0) ELSE 0 END) AS compras_monto,
                SUM(CASE WHEN clasificacion NOT LIKE '%VENTA%'
                          AND clasificacion NOT LIKE '%COMPRA%'  THEN COALESCE(total,0) ELSE 0 END) AS otros_monto,
                SUM(CASE WHEN clasificacion LIKE '%VENTA%'  THEN 1 ELSE 0 END) AS ventas_count,
                SUM(CASE WHEN clasificacion LIKE '%COMPRA%' THEN 1 ELSE 0 END) AS compras_count,
                SUM(CASE WHEN clasificacion NOT LIKE '%VENTA%'
                          AND clasificacion NOT LIKE '%COMPRA%'  THEN 1 ELSE 0 END) AS otros_count
            FROM documentos_importados
            WHERE fecha_emision IS NOT NULL
              AND {date_filter}{and_emp}
            GROUP BY {month}
            ORDER BY mes ASC
        """, (param,) + p_emp).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def obtener_distribucion_clasificacion(db_path: str = DB_PATH) -> list[dict]:
    """
    Retorna el conteo y monto total por clasificación.
    """
    conn = get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        rows = conn.execute(f"""
            SELECT
                clasificacion,
                COUNT(*)             AS count,
                SUM(COALESCE(total,0)) AS monto
            FROM documentos_importados{where_emp}
            GROUP BY clasificacion
            ORDER BY count DESC
        """, p_emp).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def obtener_top_terceros(
    db_path: str = DB_PATH,
    limite: int = 10,
    tipo: str = "compra",
) -> list[dict]:
    """
    Retorna los terceros más activos por monto total.
    """
    conn = get_connection(db_path)
    try:
        if tipo == "venta":
            nit_col    = "nit_receptor"
            nombre_col = "nombre_receptor"
            filtro     = "clasificacion LIKE '%VENTA%'"
        else:
            nit_col    = "nit_emisor"
            nombre_col = "nombre_emisor"
            filtro     = "clasificacion LIKE '%COMPRA%'"

        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(f"""
            SELECT
                {nit_col}    AS nit,
                {nombre_col} AS nombre,
                COUNT(*)               AS count,
                SUM(COALESCE(total,0)) AS monto
            FROM documentos_importados
            WHERE {filtro}
              AND {nit_col} IS NOT NULL AND {nit_col} != ''{and_emp}
            GROUP BY {nit_col}
            ORDER BY monto DESC
        """, p_emp).fetchall()
        # Apply limit in Python to avoid SQL dialect issues with TOP vs LIMIT
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


def obtener_actividad_reciente(db_path: str = DB_PATH, limite: int = 30) -> list[dict]:
    """
    Retorna los últimos documentos procesados.
    """
    conn = get_connection(db_path)
    try:
        sub_fe = _substr_expr("fecha_emision", 1, 10, conn.is_sqlite)
        sub_fp = _substr_expr("fecha_proceso", 1, 10, conn.is_sqlite)
        where_emp, p_emp = _where_empresa(conn, db_path)

        rows = conn.execute(f"""
            SELECT
                {sub_fe} AS fecha_emision,
                clasificacion,
                nombre_emisor,
                nombre_receptor,
                total,
                {sub_fp} AS fecha_proceso
            FROM documentos_importados{where_emp}
            ORDER BY fecha_proceso DESC
        """, p_emp).fetchall()
        # Apply limit in Python
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


def obtener_resumen_dashboard(db_path: str = DB_PATH) -> dict:
    """
    Resumen para el dashboard principal de una empresa.

    Compatible con ambos backends y aislado por empresa (en Azure SQL). Retorna:
    total_docs, ultimas (conteo por clasificación), ultima_fecha, total_historial.
    """
    conn = get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)

        total_docs = conn.execute(
            f"SELECT COUNT(*) FROM documentos_importados{where_emp}", p_emp
        ).fetchone()[0]

        ultimas = conn.execute(
            f"""
            SELECT clasificacion, COUNT(*) as cnt
            FROM documentos_importados{where_emp}
            GROUP BY clasificacion
            ORDER BY cnt DESC
            """,
            p_emp,
        ).fetchall()

        ultima_fecha = conn.execute(
            f"SELECT MAX(fecha_proceso) FROM documentos_importados{where_emp}", p_emp
        ).fetchone()[0]

        total_historial = conn.execute(
            f"SELECT COUNT(*) FROM historial_cuentas{where_emp}", p_emp
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "total_docs": total_docs or 0,
        "ultimas": [dict(r) for r in ultimas],
        "ultima_fecha": ultima_fecha,
        "total_historial": total_historial or 0,
    }


def listar_historial_cuentas(
    db_path: str = DB_PATH, limite: int = 200
) -> tuple[list[dict], int]:
    """
    Cuentas aprendidas por el motor de sugerencias (para la vista /historial).

    Compatible con ambos backends (el límite se aplica en Python para evitar
    diferencias TOP vs LIMIT) y aislado por empresa en Azure SQL.

    Returns:
        (entradas, total) — lista ordenada por usos DESC y total de filas.
    """
    conn = get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        sub_uv = _substr_expr("ultima_vez", 1, 10, conn.is_sqlite)
        rows = conn.execute(
            f"""
            SELECT clasificacion, nit_tercero, tipo_linea, cuenta, usos,
                   {sub_uv} as ultima_fecha
            FROM historial_cuentas{where_emp}
            ORDER BY usos DESC
            """,
            p_emp,
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM historial_cuentas{where_emp}", p_emp
        ).fetchone()[0]
    finally:
        conn.close()

    return [dict(r) for r in rows[:limite]], (total or 0)


# ---------------------------------------------------------------------------
# Importaciones — registro persistente de cada proceso RADIAN
# ---------------------------------------------------------------------------

def registrar_importacion(
    archivo_nombre: str,
    archivo_ref: str,
    db_path: str = DB_PATH,
) -> int:
    """
    Crea el registro de una importación en estado 'procesando' y retorna su id.

    El registro persiste el archivo RADIAN original (archivo_ref) para poder
    retomar la importación si algo falla o regenerar el Excel más adelante.
    """
    conn = get_connection(db_path)
    try:
        params = (datetime.now().isoformat(), archivo_nombre, archivo_ref, "procesando")
        if conn.is_sqlite:
            cur = conn.execute(
                """
                INSERT INTO importaciones (fecha, archivo_nombre, archivo_ref, estado)
                VALUES (?,?,?,?)
                """,
                params,
            )
            imp_id = cur.lastrowid
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                INSERT INTO importaciones
                    (empresa_id, fecha, archivo_nombre, archivo_ref, estado)
                VALUES (?,?,?,?,?)
                """,
                (emp_id,) + params,
            )
            imp_id = int(conn.execute("SELECT @@IDENTITY").fetchone()[0])
        conn.commit()
        return imp_id
    finally:
        conn.close()


def actualizar_importacion(
    imp_id: int,
    estado: str,
    n_docs: int = 0,
    n_excepciones: int = 0,
    excel_ref: Optional[str] = None,
    error: Optional[str] = None,
    preasientos_json: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Actualiza el estado y los resultados de una importación existente.

    `preasientos_json` (snapshot editable durable) y `excel_ref` solo se
    sobrescriben cuando se pasan (COALESCE); así una transición de estado puede
    conservar el snapshot/Excel previos sin reenviarlos.
    """
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"""
            UPDATE importaciones
            SET estado = ?, n_docs = ?, n_excepciones = ?,
                excel_ref = COALESCE(?, excel_ref),
                preasientos_json = COALESCE(?, preasientos_json),
                error = ?
            WHERE id = ?{and_emp}
            """,
            (estado, n_docs, n_excepciones, excel_ref, preasientos_json,
             error, imp_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


def obtener_importacion(imp_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Retorna una importación por id, o None si no existe."""
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT * FROM importaciones WHERE id = ?{and_emp}", (imp_id,) + p_emp
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def obtener_snapshot_importacion(
    imp_id: int, db_path: str = DB_PATH
) -> Optional[dict]:
    """Retorna el snapshot editable durable de una importación (o None).

    Es el mismo dict que vive en la sesión de trabajo (preasientos, excepciones,
    conteos, ruta del Excel). Permite "abrir" una importación conservando las
    correcciones manuales, en lugar de reprocesar el archivo desde cero.
    """
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT preasientos_json FROM importaciones WHERE id = ?{and_emp}",
            (imp_id,) + p_emp,
        ).fetchone()
        if not row or not row["preasientos_json"]:
            return None
        try:
            return json.loads(row["preasientos_json"])
        except (ValueError, TypeError):
            return None
    finally:
        conn.close()


# Columnas livianas de la importación para los listados (sin el snapshot JSON,
# que puede ser grande). `tiene_snapshot` indica si hay estado guardado.
_IMPORTACION_COLS_LISTA = (
    "id, fecha, archivo_nombre, archivo_ref, n_docs, n_excepciones, "
    "excel_ref, estado, error, "
    "CASE WHEN preasientos_json IS NOT NULL THEN 1 ELSE 0 END AS tiene_snapshot"
)


def listar_importaciones(db_path: str = DB_PATH, limite: int = 50) -> list[dict]:
    """Retorna las importaciones más recientes (descendente por fecha).

    No incluye el snapshot JSON (puede ser grande); expone `tiene_snapshot`.
    """
    conn = get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        rows = conn.execute(
            f"SELECT {_IMPORTACION_COLS_LISTA} FROM importaciones{where_emp} "
            f"ORDER BY id DESC",
            p_emp,
        ).fetchall()
        # Límite en Python para evitar diferencias TOP vs LIMIT entre backends
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Procesos de banco — histórico persistente del módulo Bancos
# ---------------------------------------------------------------------------

def registrar_proceso_banco(
    archivo_nombre: str,
    n_movimientos: int = 0,
    cuenta_banco: str = "",
    nit_banco: str = "",
    estado: str = "procesando",
    archivo_ref: str = "",
    db_path: str = DB_PATH,
) -> int:
    """
    Crea el registro de un proceso del módulo Bancos y retorna su id.

    Se registra al previsualizar un extracto (estado 'procesando') y se marca
    'completada' cuando el usuario genera el archivo SIIGO. `archivo_ref` guarda
    el CSV original para poder descargarlo o retomar el proceso más tarde.
    """
    conn = get_connection(db_path)
    try:
        params = (datetime.now().isoformat(), archivo_nombre, archivo_ref,
                  cuenta_banco, nit_banco, n_movimientos, estado)
        if conn.is_sqlite:
            cur = conn.execute(
                """
                INSERT INTO procesos_banco
                    (fecha, archivo_nombre, archivo_ref, cuenta_banco, nit_banco,
                     n_movimientos, estado)
                VALUES (?,?,?,?,?,?,?)
                """,
                params,
            )
            proc_id = cur.lastrowid
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                INSERT INTO procesos_banco
                    (empresa_id, fecha, archivo_nombre, archivo_ref, cuenta_banco,
                     nit_banco, n_movimientos, estado)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (emp_id,) + params,
            )
            proc_id = int(conn.execute("SELECT @@IDENTITY").fetchone()[0])
        conn.commit()
        return proc_id
    finally:
        conn.close()


def actualizar_proceso_banco(
    proceso_id: int,
    estado: str,
    n_movimientos: Optional[int] = None,
    error: Optional[str] = None,
    snapshot_json: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Actualiza el estado (y opcionalmente el conteo/snapshot) de un proceso de banco.

    `snapshot_json` (estado editable durable) solo se sobrescribe cuando se pasa
    (COALESCE), igual que el modelo de importaciones.
    """
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"""
            UPDATE procesos_banco
            SET estado = ?, n_movimientos = COALESCE(?, n_movimientos),
                snapshot_json = COALESCE(?, snapshot_json), error = ?
            WHERE id = ?{and_emp}
            """,
            (estado, n_movimientos, snapshot_json, error, proceso_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


# Columnas livianas del proceso de banco para los listados (sin el snapshot JSON,
# que puede ser grande). `tiene_snapshot` indica si hay estado guardado.
_PROCESO_BANCO_COLS_LISTA = (
    "id, fecha, archivo_nombre, archivo_ref, cuenta_banco, nit_banco, "
    "n_movimientos, estado, error, "
    "CASE WHEN snapshot_json IS NOT NULL THEN 1 ELSE 0 END AS tiene_snapshot"
)


def listar_procesos_banco(db_path: str = DB_PATH, limite: int = 50) -> list[dict]:
    """Retorna los procesos de banco más recientes (descendente por fecha).

    No incluye el snapshot JSON (puede ser grande); expone `tiene_snapshot`.
    """
    conn = get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        rows = conn.execute(
            f"SELECT {_PROCESO_BANCO_COLS_LISTA} FROM procesos_banco{where_emp} "
            f"ORDER BY id DESC",
            p_emp,
        ).fetchall()
        # Límite en Python para evitar diferencias TOP vs LIMIT entre backends
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


def obtener_proceso_banco(proceso_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Retorna un proceso de banco por id, o None si no existe."""
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT * FROM procesos_banco WHERE id = ?{and_emp}",
            (proceso_id,) + p_emp,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def obtener_snapshot_proceso_banco(
    proceso_id: int, db_path: str = DB_PATH
) -> Optional[dict]:
    """Retorna el snapshot editable durable de un proceso de banco (o None).

    Es el mismo dict que vive en la sesión de trabajo (movimientos, cuenta y NIT
    del banco, y las asignaciones del usuario). Permite "retomar" o "corregir" un
    proceso conservando lo trabajado, sin volver a parsear el CSV manualmente.
    """
    conn = get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT snapshot_json FROM procesos_banco WHERE id = ?{and_emp}",
            (proceso_id,) + p_emp,
        ).fetchone()
        if not row or not row["snapshot_json"]:
            return None
        try:
            return json.loads(row["snapshot_json"])
        except (ValueError, TypeError):
            return None
    finally:
        conn.close()


def actualizar_historial_cuenta(
    clasificacion: str,
    nit_tercero: str,
    tipo_linea: str,
    cuenta: str,
    db_path: str = DB_PATH,
) -> None:
    """Incrementa el contador de uso de una cuenta en el historial."""
    conn = get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO historial_cuentas
                    (clasificacion, nit_tercero, tipo_linea, cuenta, usos, ultima_vez)
                VALUES (?,?,?,?,1,?)
                ON CONFLICT(clasificacion, nit_tercero, tipo_linea) DO UPDATE SET
                    usos = usos + 1,
                    ultima_vez = excluded.ultima_vez,
                    cuenta = excluded.cuenta
                """,
                (clasificacion, nit_tercero, tipo_linea, cuenta, ahora),
            )
        else:
            # T-SQL: MERGE para UPSERT (aislado por empresa)
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                MERGE historial_cuentas AS target
                USING (SELECT ? AS empresa_id, ? AS clasificacion, ? AS nit_tercero,
                              ? AS tipo_linea, ? AS cuenta, ? AS ultima_vez) AS source
                ON target.empresa_id = source.empresa_id
                   AND target.clasificacion = source.clasificacion
                   AND target.nit_tercero = source.nit_tercero
                   AND target.tipo_linea = source.tipo_linea
                WHEN MATCHED THEN
                    UPDATE SET usos = target.usos + 1,
                               ultima_vez = source.ultima_vez,
                               cuenta = source.cuenta
                WHEN NOT MATCHED THEN
                    INSERT (empresa_id, clasificacion, nit_tercero, tipo_linea, cuenta, usos, ultima_vez)
                    VALUES (source.empresa_id, source.clasificacion, source.nit_tercero,
                            source.tipo_linea, source.cuenta, 1, source.ultima_vez);
                """,
                (emp_id, clasificacion, nit_tercero, tipo_linea, cuenta, ahora),
            )
        conn.commit()
    finally:
        conn.close()
