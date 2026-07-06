"""
Gestión de la base de datos del sistema 1ContaBot.

Soporta dos backends según la variable de entorno USE_SQLITE:
- SQLite  (local, desarrollo) — comportamiento original.
- Azure SQL Database (producción en la nube) — vía pyodbc.

Proporciona la inicialización del esquema, funciones CRUD básicas
y registro de documentos procesados para detección de duplicados.
"""

import atexit
import logging
import threading
from pathlib import Path
from typing import Optional

from app.config import (
    DB_PATH, USE_SQLITE, DATABASE_URL, DB_JOURNAL_MODE,
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
        # True cuando la conexión se comparte durante toda la petición Flask
        # (ver get_connection): close() pasa a ser un no-op y el cierre real
        # ocurre en el teardown de la petición.
        self._compartida = False

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
        if self._compartida:
            return  # la cierra el teardown de la petición (_cerrar_conexiones_peticion)
        self._conn.close()

    def _cerrar_real(self):
        self._conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Fábrica de conexiones
# ═══════════════════════════════════════════════════════════════════════════

# Clave en flask.g con las conexiones compartidas de la petición actual.
_G_CONEXIONES = "_db_conexiones_peticion"


def get_connection(db_path: str = DB_PATH):
    """
    Retorna una conexión a la base de datos.

    Cuando USE_SQLITE es True (por defecto), usa SQLite en la ruta indicada.
    Cuando USE_SQLITE es False, usa Azure SQL Database vía pyodbc.
    El parámetro db_path se ignora en modo Azure SQL.

    Dentro de una petición Flask la conexión se REUTILIZA (cacheada en
    ``flask.g`` por ruta de BD): una sola página ejecuta muchas consultas
    (autenticación, permisos, registro de empresas, datos de la vista) y abrir
    una conexión nueva por consulta es costoso —handshake TLS+login con Azure
    SQL, o reapertura del archivo SQLite sobre el SMB de /home en App Service—.
    Para estas conexiones compartidas ``close()`` es un no-op; el cierre real
    ocurre al terminar la petición (ver ``init_app``). Fuera de un contexto
    Flask (CLI, tests, hilos de fondo) el comportamiento es el original:
    conexión nueva por llamada.
    """
    try:
        from flask import g, has_app_context
    except ImportError:
        return _abrir_conexion(db_path)
    if not has_app_context():
        return _abrir_conexion(db_path)

    clave = str(Path(db_path).resolve()) if USE_SQLITE else "_mssql"
    conexiones = g.setdefault(_G_CONEXIONES, {})
    conn = conexiones.get(clave)
    if conn is None:
        conn = _abrir_conexion(db_path)
        conn._compartida = True
        conexiones[clave] = conn
    return conn


def _cerrar_conexiones_peticion(exc=None) -> None:
    """``teardown_appcontext``: cierra las conexiones compartidas de la petición."""
    from flask import g
    conexiones = g.pop(_G_CONEXIONES, None)
    if not conexiones:
        return
    for conn in conexiones.values():
        try:
            # Cerrar sin commit descarta cualquier transacción sin confirmar,
            # igual que el close() explícito del patrón original.
            conn._cerrar_real()
        except Exception:
            logger.debug("Error cerrando conexión de la petición", exc_info=True)


def init_app(app) -> None:
    """Engancha el cierre de las conexiones por-petición en la app Flask."""
    app.teardown_appcontext(_cerrar_conexiones_peticion)


def _aplicar_journal_mode(conn) -> None:
    """Aplica PRAGMA journal_mode tolerando la carrera del arranque multi-worker.

    Cambiar el modo de journal exige un lock exclusivo y, cuando varios workers
    de gunicorn abren la misma BD a la vez (cada arranque en App Service lanza
    2), SQLite puede responder "database is locked" DE INMEDIATO —sin esperar
    el busy_timeout— para no crear un deadlock entre locks compartidos. Ese
    error tumbaba el worker en el boot ("Worker failed to boot" → página
    ":( Application Error" en Azure). Se reintenta con una espera corta y, si
    la BD sigue ocupada, se continúa sin cambiar el modo: WAL es un atributo
    persistente del archivo (ya lo fijó quien ganó la carrera) y DELETE es el
    modo por defecto de SQLite, así que operar con el modo actual es seguro.
    """
    import sqlite3
    import time

    for intento in range(5):
        try:
            conn.execute(f"PRAGMA journal_mode={DB_JOURNAL_MODE}")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            time.sleep(0.2 * (intento + 1))
    logger.warning(
        "BD ocupada: no se pudo fijar journal_mode=%s; se continúa con el modo actual.",
        DB_JOURNAL_MODE,
    )


def _abrir_conexion(db_path: str = DB_PATH):
    """Abre una conexión NUEVA, sin pasar por el caché por-petición."""
    if USE_SQLITE:
        import sqlite3
        # Si la BD vive en Blob y no existe localmente, restaurarla antes de
        # abrir la conexión (evita "empezar desde cero" en disco efímero).
        _restaurar_db_desde_blob(db_path)
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        # Espera (en ms) si otra conexión tiene la BD bloqueada, en vez de
        # fallar de inmediato con "database is locked". Relevante con varios
        # workers de gunicorn sobre el mismo archivo SQLite. Debe fijarse
        # ANTES del PRAGMA journal_mode, que compite por un lock exclusivo.
        conn.execute("PRAGMA busy_timeout=30000")
        _aplicar_journal_mode(conn)
        conn.execute("PRAGMA foreign_keys=ON")
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



def _ultimo_id(conn: "DbConnection") -> int:
    """Retorna el id autogenerado por el último INSERT en la conexión actual."""
    if conn.is_sqlite:
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return int(conn.execute("SELECT @@IDENTITY").fetchone()[0])

