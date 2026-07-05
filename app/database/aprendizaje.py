# Motor de aprendizaje generalizado (machine learning)
# ───────────────────────────────────────────────────────────────────────────
# Dos memorias complementarias, ambas por empresa:
#   - patrones_aprendidos: contexto normalizado exacto → valor (con contador).
#   - tokens_aprendidos:   frecuencia token→valor para el clasificador de
#     texto (Naive Bayes) que generaliza a descripciones nunca vistas.
# La lógica de normalización, tokenización y predicción vive en
# app/aprendizaje.py; aquí solo la persistencia (compatible SQLite y T-SQL).
# ═══════════════════════════════════════════════════════════════════════════


import logging
from datetime import datetime
from typing import Optional

from app.config import DB_PATH

from . import core
from .core import (
    DbConnection, _and_empresa, _where_empresa, _cond_empresa,
    _empresa_id_desde_db_path,
)

logger = logging.getLogger(__name__)

def _upsert_patron(conn: "DbConnection", db_path: str, modulo: str, campo: str,
                   contexto: str, valor: str, peso: int, ahora: str) -> None:
    """UPSERT de un patrón exacto (suma `peso` a usos si ya existe)."""
    if conn.is_sqlite:
        conn.execute(
            """
            INSERT INTO patrones_aprendidos
                (modulo, campo, contexto, valor, usos, ultima_vez)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(modulo, campo, contexto, valor) DO UPDATE SET
                usos = usos + excluded.usos,
                ultima_vez = excluded.ultima_vez
            """,
            (modulo, campo, contexto, valor, peso, ahora),
        )
    else:
        emp_id = _empresa_id_desde_db_path(db_path)
        conn.execute(
            """
            MERGE patrones_aprendidos AS target
            USING (SELECT ? AS empresa_id, ? AS modulo, ? AS campo,
                          ? AS contexto, ? AS valor, ? AS usos, ? AS ultima_vez) AS source
            ON target.empresa_id = source.empresa_id
               AND target.modulo = source.modulo AND target.campo = source.campo
               AND target.contexto = source.contexto AND target.valor = source.valor
            WHEN MATCHED THEN
                UPDATE SET usos = target.usos + source.usos,
                           ultima_vez = source.ultima_vez
            WHEN NOT MATCHED THEN
                INSERT (empresa_id, modulo, campo, contexto, valor, usos, ultima_vez)
                VALUES (source.empresa_id, source.modulo, source.campo,
                        source.contexto, source.valor, source.usos, source.ultima_vez);
            """,
            (emp_id, modulo, campo, contexto, valor, peso, ahora),
        )


def _upsert_token(conn: "DbConnection", db_path: str, modulo: str, campo: str,
                  token: str, valor: str, peso: int) -> None:
    """UPSERT de una frecuencia token→valor (suma `peso` a usos si ya existe)."""
    if conn.is_sqlite:
        conn.execute(
            """
            INSERT INTO tokens_aprendidos (modulo, campo, token, valor, usos)
            VALUES (?,?,?,?,?)
            ON CONFLICT(modulo, campo, token, valor) DO UPDATE SET
                usos = usos + excluded.usos
            """,
            (modulo, campo, token, valor, peso),
        )
    else:
        emp_id = _empresa_id_desde_db_path(db_path)
        conn.execute(
            """
            MERGE tokens_aprendidos AS target
            USING (SELECT ? AS empresa_id, ? AS modulo, ? AS campo,
                          ? AS token, ? AS valor, ? AS usos) AS source
            ON target.empresa_id = source.empresa_id
               AND target.modulo = source.modulo AND target.campo = source.campo
               AND target.token = source.token AND target.valor = source.valor
            WHEN MATCHED THEN
                UPDATE SET usos = target.usos + source.usos
            WHEN NOT MATCHED THEN
                INSERT (empresa_id, modulo, campo, token, valor, usos)
                VALUES (source.empresa_id, source.modulo, source.campo,
                        source.token, source.valor, source.usos);
            """,
            (emp_id, modulo, campo, token, valor, peso),
        )


def registrar_aprendizaje_lote(
    observaciones: list[dict],
    db_path: str = DB_PATH,
) -> int:
    """
    Persiste un lote de observaciones del motor de aprendizaje en UNA conexión.

    Cada observación es un dict con:
        modulo, campo, contexto (texto normalizado), valor,
        tokens (list[str]), peso (int, default 1).

    Un lote por conexión/commit es clave para el entrenamiento con archivos
    externos (miles de filas) y para el modo nube (una sola subida a Blob).

    Returns:
        Número de observaciones registradas.
    """
    if not observaciones:
        return 0
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    total = 0
    try:
        for obs in observaciones:
            modulo   = obs["modulo"]
            campo    = obs["campo"]
            contexto = obs["contexto"]
            valor    = obs["valor"]
            peso     = int(obs.get("peso", 1))
            if not (modulo and campo and contexto and valor):
                continue
            _upsert_patron(conn, db_path, modulo, campo, contexto, valor, peso, ahora)
            for token in obs.get("tokens") or ():
                _upsert_token(conn, db_path, modulo, campo, token, valor, peso)
            total += 1
        conn.commit()
    finally:
        conn.close()
    return total


def obtener_patrones_exactos(
    modulo: str,
    campo: str,
    contexto: str,
    db_path: str = DB_PATH,
) -> list[dict]:
    """
    Valores aprendidos para un contexto exacto, ordenados por usos DESC.

    Returns:
        Lista de dicts {valor, usos}.
    """
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(
            f"""
            SELECT valor, usos FROM patrones_aprendidos
            WHERE modulo=? AND campo=? AND contexto=?{and_emp}
            ORDER BY usos DESC
            """,
            (modulo, campo, contexto) + p_emp,
        ).fetchall()
        return [{"valor": r["valor"], "usos": r["usos"]} for r in rows]
    finally:
        conn.close()


def obtener_tokens_aprendidos(
    modulo: str,
    campo: str,
    tokens: list[str],
    db_path: str = DB_PATH,
) -> list[dict]:
    """
    Frecuencias token→valor para los tokens dados (clasificador de texto).

    Returns:
        Lista de dicts {token, valor, usos}.
    """
    if not tokens:
        return []
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        marcas = ",".join("?" * len(tokens))
        rows = conn.execute(
            f"""
            SELECT token, valor, usos FROM tokens_aprendidos
            WHERE modulo=? AND campo=? AND token IN ({marcas}){and_emp}
            """,
            (modulo, campo, *tokens) + p_emp,
        ).fetchall()
        return [
            {"token": r["token"], "valor": r["valor"], "usos": r["usos"]}
            for r in rows
        ]
    finally:
        conn.close()


def totales_tokens_por_valor(
    modulo: str,
    campo: str,
    db_path: str = DB_PATH,
) -> dict:
    """
    Agregados del vocabulario aprendido para un módulo/campo.

    Returns:
        Dict {'por_valor': {valor: total_usos}, 'vocabulario': n_tokens_distintos}.
    """
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(
            f"""
            SELECT valor, SUM(usos) AS total FROM tokens_aprendidos
            WHERE modulo=? AND campo=?{and_emp}
            GROUP BY valor
            """,
            (modulo, campo) + p_emp,
        ).fetchall()
        por_valor = {r["valor"]: int(r["total"]) for r in rows}
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT token) AS n FROM tokens_aprendidos
            WHERE modulo=? AND campo=?{and_emp}
            """,
            (modulo, campo) + p_emp,
        ).fetchone()
        return {"por_valor": por_valor, "vocabulario": int(row["n"] or 0)}
    finally:
        conn.close()


def listar_patrones_aprendidos(
    db_path: str = DB_PATH,
    modulo: Optional[str] = None,
    q: str = "",
    limite: int = 200,
) -> list[dict]:
    """
    Patrones exactos aprendidos (para la página de Machine learning),
    ordenados por usos DESC. Filtro opcional por módulo y por texto.
    """
    conn = core.get_connection(db_path)
    try:
        cond, params = _cond_empresa(conn, db_path)
        condiciones = [cond] if cond else []
        if modulo:
            condiciones.append("modulo = ?")
            params = params + (modulo,)
        if q:
            condiciones.append("(contexto LIKE ? OR valor LIKE ?)")
            params = params + (f"%{q}%", f"%{q}%")
        where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
        rows = conn.execute(
            f"""
            SELECT id, modulo, campo, contexto, valor, usos, ultima_vez
            FROM patrones_aprendidos{where}
            ORDER BY usos DESC, id DESC
            """,
            params,
        ).fetchall()
        # LIMIT en Python para compatibilidad SQLite / T-SQL.
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()


def estadisticas_aprendizaje(db_path: str = DB_PATH) -> dict:
    """
    Métricas del conocimiento acumulado, para la página de Machine learning.

    Returns:
        Dict con 'total_patrones', 'total_confirmaciones', 'vocabulario' y
        'por_modulo' (lista de {modulo, patrones, confirmaciones}).
    """
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n, COALESCE(SUM(usos), 0) AS usos
            FROM patrones_aprendidos{where_emp}
            """,
            p_emp,
        ).fetchone()
        total_patrones = int(row["n"] or 0)
        total_conf = int(row["usos"] or 0)
        row = conn.execute(
            f"SELECT COUNT(DISTINCT token) AS n FROM tokens_aprendidos{where_emp}",
            p_emp,
        ).fetchone()
        vocabulario = int(row["n"] or 0)
        rows = conn.execute(
            f"""
            SELECT modulo, COUNT(*) AS patrones, COALESCE(SUM(usos), 0) AS confirmaciones
            FROM patrones_aprendidos{where_emp}
            GROUP BY modulo
            ORDER BY modulo
            """,
            p_emp,
        ).fetchall()
        return {
            "total_patrones": total_patrones,
            "total_confirmaciones": total_conf,
            "vocabulario": vocabulario,
            "por_modulo": [
                {"modulo": r["modulo"], "patrones": int(r["patrones"]),
                 "confirmaciones": int(r["confirmaciones"])}
                for r in rows
            ],
        }
    finally:
        conn.close()


def eliminar_patron_aprendido(patron_id: int, db_path: str = DB_PATH) -> None:
    """
    Elimina un patrón exacto aprendido (corrección manual desde la UI).

    No toca tokens_aprendidos: el conocimiento estadístico del texto se diluye
    solo a medida que se confirman otros valores.
    """
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"DELETE FROM patrones_aprendidos WHERE id=?{and_emp}",
            (patron_id,) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


def registrar_importacion_conocimiento(
    archivo_nombre: str,
    modulo: str,
    filas: int,
    aprendidos: int,
    estado: str = "completada",
    detalle: str = "",
    db_path: str = DB_PATH,
) -> None:
    """Registra un entrenamiento con archivo externo en el histórico."""
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO importaciones_conocimiento
                    (fecha, archivo_nombre, modulo, filas, aprendidos, estado, detalle)
                VALUES (?,?,?,?,?,?,?)
                """,
                (ahora, archivo_nombre, modulo, filas, aprendidos, estado, detalle),
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """
                INSERT INTO importaciones_conocimiento
                    (empresa_id, fecha, archivo_nombre, modulo, filas,
                     aprendidos, estado, detalle)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (emp_id, ahora, archivo_nombre, modulo, filas,
                 aprendidos, estado, detalle),
            )
        conn.commit()
    finally:
        conn.close()


def listar_importaciones_conocimiento(
    db_path: str = DB_PATH,
    limite: int = 20,
) -> list[dict]:
    """Histórico de entrenamientos con archivos externos (descendente por id)."""
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        rows = conn.execute(
            f"""
            SELECT id, fecha, archivo_nombre, modulo, filas, aprendidos, estado, detalle
            FROM importaciones_conocimiento{where_emp}
            ORDER BY id DESC
            """,
            p_emp,
        ).fetchall()
        return [dict(r) for r in rows[:limite]]
    finally:
        conn.close()

