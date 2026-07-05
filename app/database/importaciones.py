"""Importaciones RADIAN y procesos de banco — histórico durable con snapshot."""

import json
import logging
from datetime import datetime
from typing import Optional

from app.config import DB_PATH

from . import core
from .core import (
    _and_empresa, _where_empresa, _empresa_id_desde_db_path,
)

logger = logging.getLogger(__name__)

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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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

