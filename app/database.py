"""
Gestión de la base de datos del sistema contable-auto.

Soporta dos backends según la variable de entorno USE_SQLITE:
- SQLite  (local, desarrollo) — comportamiento original.
- Azure SQL Database (producción en la nube) — vía pyodbc.

Proporciona la inicialización del esquema, funciones CRUD básicas
y registro de documentos procesados para detección de duplicados.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import DB_PATH, USE_SQLITE, DATABASE_URL

logger = logging.getLogger(__name__)


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

    def __init__(self, conn, is_sqlite: bool):
        self._conn = conn
        self.is_sqlite = is_sqlite

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
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return DbConnection(conn, is_sqlite=True)
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

    Tablas: documentos_importados, bitacora, historial_cuentas, importaciones.
    """
    conn = get_connection(db_path)
    try:
        if conn.is_sqlite:
            _create_tables_sqlite(conn)
        else:
            _create_tables_mssql(conn)
        conn.commit()
        logger.info("Base de datos inicializada correctamente.")
    finally:
        conn.close()


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
            error           TEXT
        )
    """)


def _create_tables_mssql(conn: DbConnection) -> None:
    """Crea tablas con sintaxis T-SQL (Azure SQL Database)."""
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'documentos_importados')
        CREATE TABLE documentos_importados (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            cufe            NVARCHAR(500)  NOT NULL UNIQUE,
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
            archivo_origen  NVARCHAR(500)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'bitacora')
        CREATE TABLE bitacora (
            id          INT IDENTITY(1,1) PRIMARY KEY,
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
            clasificacion   NVARCHAR(100)  NOT NULL,
            nit_tercero     NVARCHAR(50)   NOT NULL,
            tipo_linea      NVARCHAR(100)  NOT NULL,
            cuenta          NVARCHAR(50)   NOT NULL,
            usos            INT DEFAULT 1,
            ultima_vez      NVARCHAR(50),
            CONSTRAINT uq_historial UNIQUE(clasificacion, nit_tercero, tipo_linea)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'importaciones')
        CREATE TABLE importaciones (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            fecha           NVARCHAR(50)   NOT NULL,
            archivo_nombre  NVARCHAR(300),
            archivo_ref     NVARCHAR(500),
            n_docs          INT DEFAULT 0,
            n_excepciones   INT DEFAULT 0,
            excel_ref       NVARCHAR(500),
            estado          NVARCHAR(30)   NOT NULL DEFAULT 'procesando',
            error           NVARCHAR(MAX)
        )
    """)


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
        row = conn.execute(
            "SELECT 1 FROM documentos_importados WHERE cufe = ?", (cufe,)
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
            # T-SQL: verificar existencia antes de insertar
            conn.execute(
                """
                IF NOT EXISTS (SELECT 1 FROM documentos_importados WHERE cufe = ?)
                INSERT INTO documentos_importados
                (cufe, tipo_documento, clasificacion, folio, prefijo,
                 nit_emisor, nombre_emisor, nit_receptor, nombre_receptor,
                 total, fecha_emision, fecha_proceso, archivo_origen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (cufe,) + params,
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
    try:
        conn.execute(
            """
            INSERT INTO bitacora (timestamp, nivel, modulo, accion, detalle, cufe)
            VALUES (?,?,?,?,?,?)
            """,
            (datetime.now().isoformat(), nivel, modulo, accion, detalle, cufe),
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
        row = conn.execute(
            """
            SELECT cuenta FROM historial_cuentas
            WHERE clasificacion=? AND nit_tercero=? AND tipo_linea=?
            ORDER BY usos DESC
            """,
            (clasificacion, nit_tercero, tipo_linea),
        ).fetchone()
        return row["cuenta"] if row else None
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

        row = conn.execute("""
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
            FROM documentos_importados
        """).fetchone()

        docs_mes = conn.execute(f"""
            SELECT COUNT(*) FROM documentos_importados
            WHERE {month} = ?
        """, (mes_actual,)).fetchone()[0]

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
              AND {date_filter}
            GROUP BY {month}
            ORDER BY mes ASC
        """, (param,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def obtener_distribucion_clasificacion(db_path: str = DB_PATH) -> list[dict]:
    """
    Retorna el conteo y monto total por clasificación.
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT
                clasificacion,
                COUNT(*)             AS count,
                SUM(COALESCE(total,0)) AS monto
            FROM documentos_importados
            GROUP BY clasificacion
            ORDER BY count DESC
        """).fetchall()
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

        rows = conn.execute(f"""
            SELECT
                {nit_col}    AS nit,
                {nombre_col} AS nombre,
                COUNT(*)               AS count,
                SUM(COALESCE(total,0)) AS monto
            FROM documentos_importados
            WHERE {filtro}
              AND {nit_col} IS NOT NULL AND {nit_col} != ''
            GROUP BY {nit_col}
            ORDER BY monto DESC
        """).fetchall()
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

        rows = conn.execute(f"""
            SELECT
                {sub_fe} AS fecha_emision,
                clasificacion,
                nombre_emisor,
                nombre_receptor,
                total,
                {sub_fp} AS fecha_proceso
            FROM documentos_importados
            ORDER BY fecha_proceso DESC
        """).fetchall()
        # Apply limit in Python
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


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
            conn.execute(
                """
                INSERT INTO importaciones (fecha, archivo_nombre, archivo_ref, estado)
                VALUES (?,?,?,?)
                """,
                params,
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
    db_path: str = DB_PATH,
) -> None:
    """Actualiza el estado y los resultados de una importación existente."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            UPDATE importaciones
            SET estado = ?, n_docs = ?, n_excepciones = ?,
                excel_ref = COALESCE(?, excel_ref), error = ?
            WHERE id = ?
            """,
            (estado, n_docs, n_excepciones, excel_ref, error, imp_id),
        )
        conn.commit()
    finally:
        conn.close()


def obtener_importacion(imp_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Retorna una importación por id, o None si no existe."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM importaciones WHERE id = ?", (imp_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def listar_importaciones(db_path: str = DB_PATH, limite: int = 50) -> list[dict]:
    """Retorna las importaciones más recientes (descendente por fecha)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM importaciones ORDER BY id DESC"
        ).fetchall()
        # Límite en Python para evitar diferencias TOP vs LIMIT entre backends
        return [dict(r) for r in rows[:limite]]
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
            # T-SQL: MERGE para UPSERT
            conn.execute(
                """
                MERGE historial_cuentas AS target
                USING (SELECT ? AS clasificacion, ? AS nit_tercero,
                              ? AS tipo_linea, ? AS cuenta, ? AS ultima_vez) AS source
                ON target.clasificacion = source.clasificacion
                   AND target.nit_tercero = source.nit_tercero
                   AND target.tipo_linea = source.tipo_linea
                WHEN MATCHED THEN
                    UPDATE SET usos = target.usos + 1,
                               ultima_vez = source.ultima_vez,
                               cuenta = source.cuenta
                WHEN NOT MATCHED THEN
                    INSERT (clasificacion, nit_tercero, tipo_linea, cuenta, usos, ultima_vez)
                    VALUES (source.clasificacion, source.nit_tercero, source.tipo_linea,
                            source.cuenta, 1, source.ultima_vez);
                """,
                (clasificacion, nit_tercero, tipo_linea, cuenta, ahora),
            )
        conn.commit()
    finally:
        conn.close()
