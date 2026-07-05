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

import json
import logging
from datetime import datetime
from typing import Optional

from app.config import SYSTEM_DB_PATH

from . import core

logger = logging.getLogger(__name__)
from .schema import _asegurar_columna

# Columnas cuyo valor se persiste serializado como JSON.
_EMPRESA_JSON_COLS = (
    "cuentas_contraparte", "cuentas_impuestos",
    "cuentas_banco", "bancos", "formato_banco", "dian_config",
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
    "cuentas_contraparte, cuentas_impuestos, cuentas_banco, bancos, formato_banco, "
    "dian_config"
)


def inicializar_db_sistema(db_path: str = SYSTEM_DB_PATH) -> None:
    """Crea la tabla `empresas` en la BD de sistema si no existe (idempotente)."""
    conn = core.get_connection(db_path)
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
                    dian_config          TEXT,
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
                    dian_config          NVARCHAR(MAX),
                    creada               NVARCHAR(50),
                    actualizada          NVARCHAR(50)
                )
            """)
        # Migración aditiva para registros de empresas ya existentes.
        _asegurar_columna(conn, "empresas", "dian_config", "TEXT", "NVARCHAR(MAX)")
        conn.commit()
    finally:
        conn.close()


def contar_empresas_registro(db_path: str = SYSTEM_DB_PATH) -> int:
    """Número de empresas registradas (sirve para decidir la migración inicial)."""
    conn = core.get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM empresas").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def listar_empresas_registro(db_path: str = SYSTEM_DB_PATH) -> dict:
    """Retorna el registro completo {empresa_id: {campos}} desde la BD."""
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
    conn = core.get_connection(db_path)
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
        _json_dump(datos.get("dian_config")),
    )
    try:
        if conn.is_sqlite:
            conn.execute(
                """
                INSERT INTO empresas
                    (id, nit, nombre, sigla, cuenta_banco_default, nit_banco,
                     cuentas_contraparte, cuentas_impuestos, cuentas_banco,
                     bancos, formato_banco, dian_config, creada, actualizada)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    dian_config          = excluded.dian_config,
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
                               formato_banco=?, dian_config=?, actualizada=?
                WHEN NOT MATCHED THEN
                    INSERT (id, nit, nombre, sigla, cuenta_banco_default, nit_banco,
                            cuentas_contraparte, cuentas_impuestos, cuentas_banco,
                            bancos, formato_banco, dian_config, creada, actualizada)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?);
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
    conn = core.get_connection(db_path)
    try:
        conn.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
        conn.commit()
    finally:
        conn.close()


