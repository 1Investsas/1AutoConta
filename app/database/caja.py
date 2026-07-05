# ═══════════════════════════════════════════════════════════════════════════
# Caja General — cuentas de caja, períodos mensuales y movimientos de efectivo
# ═══════════════════════════════════════════════════════════════════════════

import json
import logging
from datetime import datetime
from typing import Optional

from app.config import DB_PATH

from . import core
from .core import (
    _and_empresa, _where_empresa, _empresa_id_desde_db_path, _ultimo_id,
)

logger = logging.getLogger(__name__)

# ── Cuentas de caja ─────────────────────────────────────────────────────────

def crear_cash_account(
    name: str,
    description: str = "",
    currency: str = "COP",
    responsible: str = "",
    account_code: str = "",
    account_name: str = "",
    db_path: str = DB_PATH,
) -> int:
    """Crea una cuenta de caja y retorna su id.

    `account_code`/`account_name` asocian la caja con la cuenta contable del
    plan de cuentas (maestro) de la empresa.
    """
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        params = (name, description, currency, responsible,
                  account_code, account_name, 1, ahora, ahora)
        if conn.is_sqlite:
            conn.execute(
                """INSERT INTO cash_accounts
                   (name, description, currency, responsible, account_code, account_name,
                    active, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                params,
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """INSERT INTO cash_accounts
                   (empresa_id, name, description, currency, responsible, account_code,
                    account_name, active, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (emp_id,) + params,
            )
        new_id = _ultimo_id(conn)
        conn.commit()
        return new_id
    finally:
        conn.close()


def listar_cash_accounts(
    db_path: str = DB_PATH, incluir_inactivas: bool = False
) -> list[dict]:
    """Lista las cuentas de caja de la empresa (activas primero, por nombre)."""
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        rows = conn.execute(
            f"SELECT * FROM cash_accounts{where_emp} ORDER BY active DESC, name",
            p_emp,
        ).fetchall()
        cuentas = [dict(r) for r in rows]
        if not incluir_inactivas:
            cuentas = [c for c in cuentas if c.get("active")]
        return cuentas
    finally:
        conn.close()


def obtener_cash_account(account_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Retorna una cuenta de caja por id, o None."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT * FROM cash_accounts WHERE id = ?{and_emp}",
            (account_id,) + p_emp,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def actualizar_cash_account(
    account_id: int,
    name: str,
    description: str = "",
    currency: str = "COP",
    responsible: str = "",
    account_code: str = "",
    account_name: str = "",
    active: bool = True,
    db_path: str = DB_PATH,
) -> None:
    """Actualiza los datos de una cuenta de caja."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"""UPDATE cash_accounts
                SET name = ?, description = ?, currency = ?, responsible = ?,
                    account_code = ?, account_name = ?, active = ?, updated_at = ?
                WHERE id = ?{and_emp}""",
            (name, description, currency, responsible, account_code, account_name,
             1 if active else 0, datetime.now().isoformat(), account_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


# ── Períodos mensuales ──────────────────────────────────────────────────────

def crear_cash_period(
    cash_account_id: int,
    year: int,
    month: int,
    opening_balance: str = "0",
    responsible: str = "",
    created_by: str = "",
    db_path: str = DB_PATH,
) -> int:
    """Crea un período mensual de caja y retorna su id."""
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        params = (cash_account_id, year, month, opening_balance, "0", "0",
                  opening_balance, "borrador", responsible, created_by, ahora, ahora)
        if conn.is_sqlite:
            conn.execute(
                """INSERT INTO cash_periods
                   (cash_account_id, year, month, opening_balance, total_inflows,
                    total_outflows, closing_balance, status, responsible, created_by,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                params,
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                """INSERT INTO cash_periods
                   (empresa_id, cash_account_id, year, month, opening_balance, total_inflows,
                    total_outflows, closing_balance, status, responsible, created_by,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (emp_id,) + params,
            )
        new_id = _ultimo_id(conn)
        conn.commit()
        return new_id
    finally:
        conn.close()


def listar_cash_periods(
    cash_account_id: int, db_path: str = DB_PATH
) -> list[dict]:
    """Lista los períodos de una cuenta de caja (más recientes primero)."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(
            f"""SELECT * FROM cash_periods
                WHERE cash_account_id = ?{and_emp}
                ORDER BY year DESC, month DESC""",
            (cash_account_id,) + p_emp,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def obtener_cash_period(period_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Retorna un período de caja por id, o None."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT * FROM cash_periods WHERE id = ?{and_emp}",
            (period_id,) + p_emp,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def obtener_cash_period_por_mes(
    cash_account_id: int, year: int, month: int, db_path: str = DB_PATH
) -> Optional[dict]:
    """Retorna el período de una cuenta para un año/mes dado, o None."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"""SELECT * FROM cash_periods
                WHERE cash_account_id = ? AND year = ? AND month = ?{and_emp}""",
            (cash_account_id, year, month) + p_emp,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def actualizar_cash_period_saldos(
    period_id: int,
    opening_balance: str,
    total_inflows: str,
    total_outflows: str,
    closing_balance: str,
    db_path: str = DB_PATH,
) -> None:
    """Actualiza saldo inicial, totales y saldo final de un período."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"""UPDATE cash_periods
                SET opening_balance = ?, total_inflows = ?, total_outflows = ?,
                    closing_balance = ?, updated_at = ?
                WHERE id = ?{and_emp}""",
            (opening_balance, total_inflows, total_outflows, closing_balance,
             datetime.now().isoformat(), period_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


def actualizar_cash_period_estado(
    period_id: int,
    status: str,
    *,
    approved_by: Optional[str] = None,
    closed_by: Optional[str] = None,
    closed_at: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Cambia el estado de un período, registrando trazabilidad del actor.

    `approved_by`, `closed_by` y `closed_at` solo se sobrescriben cuando se
    pasan (COALESCE), para conservar la traza de transiciones previas.
    """
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"""UPDATE cash_periods
                SET status = ?,
                    approved_by = COALESCE(?, approved_by),
                    closed_by = COALESCE(?, closed_by),
                    closed_at = COALESCE(?, closed_at),
                    updated_at = ?
                WHERE id = ?{and_emp}""",
            (status, approved_by, closed_by, closed_at,
             datetime.now().isoformat(), period_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


# ── Movimientos ─────────────────────────────────────────────────────────────

def listar_cash_movements(period_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Lista los movimientos de un período ordenados por consecutivo."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(
            f"""SELECT * FROM cash_movements
                WHERE cash_period_id = ?{and_emp}
                ORDER BY sequence, id""",
            (period_id,) + p_emp,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def reemplazar_cash_movements(
    period_id: int, movimientos: list[dict], db_path: str = DB_PATH
) -> None:
    """Reemplaza por completo los movimientos de un período (borra + inserta).

    Recibe una lista de dicts ya validados/recalculados (saldo acumulado
    incluido). Es el modelo de guardado de la hoja de trabajo: el cliente envía
    la tabla completa y aquí se persiste de forma atómica.
    """
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"DELETE FROM cash_movements WHERE cash_period_id = ?{and_emp}",
            (period_id,) + p_emp,
        )
        emp_id = None if conn.is_sqlite else _empresa_id_desde_db_path(db_path)
        for m in movimientos:
            partes = m.get("contrapartidas") or []
            partes_json = json.dumps(partes, ensure_ascii=False) if partes else None
            campos = (
                period_id, int(m.get("sequence") or 0), m.get("movement_date", ""),
                m.get("movement_type", ""), m.get("concept", ""),
                m.get("third_party_nit", ""), m.get("third_party_name", ""),
                m.get("cost_center", ""), m.get("category", ""),
                m.get("contrapartida", ""), partes_json, m.get("comprobante", ""),
                str(m.get("inflow_amount", "0")), str(m.get("outflow_amount", "0")),
                str(m.get("running_balance", "0")), m.get("observations", ""),
                ahora, ahora,
            )
            if conn.is_sqlite:
                conn.execute(
                    """INSERT INTO cash_movements
                       (cash_period_id, sequence, movement_date, movement_type, concept,
                        third_party_nit, third_party_name, cost_center, category,
                        contrapartida, contrapartidas_json, comprobante,
                        inflow_amount, outflow_amount, running_balance, observations,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    campos,
                )
            else:
                conn.execute(
                    """INSERT INTO cash_movements
                       (empresa_id, cash_period_id, sequence, movement_date, movement_type, concept,
                        third_party_nit, third_party_name, cost_center, category,
                        contrapartida, contrapartidas_json, comprobante,
                        inflow_amount, outflow_amount, running_balance, observations,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (emp_id,) + campos,
                )
        conn.commit()
    finally:
        conn.close()



