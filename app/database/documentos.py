"""Documentos importados, bitácora, historial de cuentas,
correcciones de tercero y cuentas bancarias de terceros."""

import logging
from datetime import datetime
from typing import Optional

from app.config import DB_PATH

from . import core
from .core import (
    _and_empresa, _where_empresa, _empresa_id_desde_db_path, _substr_expr,
)

logger = logging.getLogger(__name__)

def cufe_existe(cufe: str, db_path: str = DB_PATH) -> bool:
    """
    Verifica si un CUFE/CUDE ya fue procesado anteriormente.

    Returns:
        True si el CUFE ya existe en la base de datos.
    """
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
# Cuentas bancarias de terceros — importadas del certificado bancario
# ---------------------------------------------------------------------------

def registrar_cuenta_bancaria_tercero(
    nit_tercero: str,
    numero_cuenta: str,
    nombre_tercero: str = "",
    tipo_documento: str = "",
    banco: str = "",
    tipo_producto: str = "",
    fecha_apertura: str = "",
    estado: str = "",
    archivo_origen: str = "",
    db_path: str = DB_PATH,
) -> None:
    """
    Registra (o actualiza) una cuenta bancaria de un tercero.

    Hace un UPSERT por ``(nit_tercero, numero_cuenta)``: si la cuenta ya estaba
    registrada para ese tercero, refresca sus datos (banco, estado, etc.) en vez
    de duplicarla. Así reimportar el mismo certificado es idempotente.

    Args:
        nit_tercero:    Identificación del tercero (solo dígitos) titular de la cuenta.
        numero_cuenta:  Número de la cuenta/producto bancario.
        nombre_tercero: Nombre o razón social del titular.
        tipo_documento: Tipo de documento del titular (NIT, CC, CE…).
        banco:          Entidad bancaria (p. ej. 'BANCOLOMBIA S.A.').
        tipo_producto:  Tipo de cuenta (p. ej. 'CUENTA DE AHORROS').
        fecha_apertura: Fecha de apertura de la cuenta (texto, como en el certificado).
        estado:         Estado de la cuenta (p. ej. 'ACTIVA').
        archivo_origen: Nombre del archivo del certificado de origen (trazabilidad).
    """
    if not nit_tercero or not numero_cuenta:
        return
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO cuentas_bancarias_tercero
                    (nit_tercero, nombre_tercero, tipo_documento, banco,
                     tipo_producto, numero_cuenta, fecha_apertura, estado,
                     archivo_origen, fecha_registro)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(nit_tercero, numero_cuenta) DO UPDATE SET
                    nombre_tercero = excluded.nombre_tercero,
                    tipo_documento = excluded.tipo_documento,
                    banco          = excluded.banco,
                    tipo_producto  = excluded.tipo_producto,
                    fecha_apertura = excluded.fecha_apertura,
                    estado         = excluded.estado,
                    archivo_origen = excluded.archivo_origen,
                    fecha_registro = excluded.fecha_registro
                """,
                (nit_tercero, nombre_tercero, tipo_documento, banco,
                 tipo_producto, numero_cuenta, fecha_apertura, estado,
                 archivo_origen, ahora),
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                MERGE cuentas_bancarias_tercero AS target
                USING (SELECT ? AS empresa_id, ? AS nit_tercero, ? AS numero_cuenta,
                              ? AS nombre_tercero, ? AS tipo_documento, ? AS banco,
                              ? AS tipo_producto, ? AS fecha_apertura, ? AS estado,
                              ? AS archivo_origen, ? AS fecha_registro) AS source
                ON target.empresa_id = source.empresa_id
                   AND target.nit_tercero = source.nit_tercero
                   AND target.numero_cuenta = source.numero_cuenta
                WHEN MATCHED THEN
                    UPDATE SET nombre_tercero = source.nombre_tercero,
                               tipo_documento = source.tipo_documento,
                               banco          = source.banco,
                               tipo_producto  = source.tipo_producto,
                               fecha_apertura = source.fecha_apertura,
                               estado         = source.estado,
                               archivo_origen = source.archivo_origen,
                               fecha_registro = source.fecha_registro
                WHEN NOT MATCHED THEN
                    INSERT (empresa_id, nit_tercero, nombre_tercero, tipo_documento,
                            banco, tipo_producto, numero_cuenta, fecha_apertura,
                            estado, archivo_origen, fecha_registro)
                    VALUES (source.empresa_id, source.nit_tercero, source.nombre_tercero,
                            source.tipo_documento, source.banco, source.tipo_producto,
                            source.numero_cuenta, source.fecha_apertura, source.estado,
                            source.archivo_origen, source.fecha_registro);
                """,
                (emp_id, nit_tercero, numero_cuenta, nombre_tercero, tipo_documento,
                 banco, tipo_producto, fecha_apertura, estado, archivo_origen, ahora),
            )
        conn.commit()
    finally:
        conn.close()


_CUENTAS_BANCARIAS_COLS = (
    "id, nit_tercero, nombre_tercero, tipo_documento, banco, tipo_producto, "
    "numero_cuenta, fecha_apertura, estado, archivo_origen, fecha_registro"
)


def listar_cuentas_bancarias_tercero(
    db_path: str = DB_PATH,
    nit_tercero: Optional[str] = None,
    limite: int = 500,
) -> list[dict]:
    """Lista las cuentas bancarias registradas (más recientes primero).

    Si se pasa ``nit_tercero`` solo se devuelven las cuentas de ese tercero.
    """
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        params = p_emp
        sql = (
            f"SELECT {_CUENTAS_BANCARIAS_COLS} FROM cuentas_bancarias_tercero{where_emp}"
        )
        if nit_tercero:
            sql += (" AND" if where_emp else " WHERE") + " nit_tercero = ?"
            params = params + (nit_tercero,)
        sql += " ORDER BY nombre_tercero, id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


def contar_cuentas_bancarias_tercero(db_path: str = DB_PATH) -> int:
    """Número de cuentas bancarias de terceros registradas en la empresa."""
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT COUNT(*) FROM cuentas_bancarias_tercero{where_emp}", p_emp
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def eliminar_cuenta_bancaria_tercero(
    cuenta_id: int, db_path: str = DB_PATH
) -> None:
    """Elimina una cuenta bancaria de tercero por id (acotado a la empresa)."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"DELETE FROM cuentas_bancarias_tercero WHERE id = ?{and_emp}",
            (cuenta_id,) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


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
    conn = core.get_connection(db_path)
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


def actualizar_historial_cuenta(
    clasificacion: str,
    nit_tercero: str,
    tipo_linea: str,
    cuenta: str,
    db_path: str = DB_PATH,
) -> None:
    """Incrementa el contador de uso de una cuenta en el historial."""
    conn = core.get_connection(db_path)
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



