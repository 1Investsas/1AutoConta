"""Analytics — KPIs, evolución mensual y resumen del dashboard (Fase 4)."""

import logging
from datetime import datetime

from app.config import DB_PATH

from . import core
from .core import (
    _and_empresa, _where_empresa, _month_expr, _substr_expr,
)

logger = logging.getLogger(__name__)

def obtener_kpis(db_path: str = DB_PATH) -> dict:
    """
    Retorna KPIs generales del historial de documentos.

    Returns:
        Dict con: total_docs, total_ventas, total_compras, total_otros,
                  monto_ventas, monto_compras, monto_total,
                  promedio_por_doc, docs_este_mes, archivos_procesados.
    """
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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

