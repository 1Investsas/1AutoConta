"""
Gestión de la base de datos SQLite del sistema contable-auto.

Proporciona la inicialización del esquema, funciones CRUD básicas
y registro de documentos procesados para detección de duplicados.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """
    Retorna una conexión a la base de datos SQLite.

    Crea el directorio padre si no existe.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def inicializar_db(db_path: str = DB_PATH) -> None:
    """
    Crea todas las tablas necesarias si no existen.

    Tablas: documentos_importados, bitacora, historial_cuentas.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        cursor.execute("""
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

        cursor.execute("""
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

        cursor.execute("""
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

        conn.commit()
        logger.info("Base de datos inicializada correctamente en %s", db_path)
    finally:
        conn.close()


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
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO documentos_importados
            (cufe, tipo_documento, clasificacion, folio, prefijo,
             nit_emisor, nombre_emisor, nit_receptor, nombre_receptor,
             total, fecha_emision, fecha_proceso, archivo_origen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cufe, tipo_documento, clasificacion, folio, prefijo,
                nit_emisor, nombre_emisor, nit_receptor, nombre_receptor,
                total,
                fecha_emision.isoformat() if fecha_emision else None,
                datetime.now().isoformat(),
                archivo_origen,
            ),
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
            ORDER BY usos DESC LIMIT 1
            """,
            (clasificacion, nit_tercero, tipo_linea),
        ).fetchone()
        return row["cuenta"] if row else None
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
    try:
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
            (clasificacion, nit_tercero, tipo_linea, cuenta, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
