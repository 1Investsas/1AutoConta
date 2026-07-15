# ═══════════════════════════════════════════════════════════════════════════
# Cartera y Cuentas por Pagar — obligaciones (CxC/CxP), cuotas y pagos
# ═══════════════════════════════════════════════════════════════════════════
"""
Módulo de datos de «Cartera y Cuentas por Pagar» (Finanzas).

Modelo:
- ``cartera_obligaciones``: una fila por documento que genera un valor a cobrar
  (``tipo='cxc'``, facturas de venta) o a pagar (``tipo='cxp'``, facturas de
  compra y documentos soporte). Nace de los módulos de **Flujos Indirectos de
  Efectivo** (RADIAN → ``documentos_importados``) vía
  :func:`sincronizar_desde_documentos`, o se crea manualmente.
- ``cartera_cuotas``: si la obligación es **a crédito**, se reparte en N cuotas,
  cada una con su propia fecha de vencimiento y valor. Al contado la fecha de
  vencimiento vive en la obligación misma.
- ``cartera_pagos``: abonos aplicados a la obligación. Los generan los módulos
  de **Flujos Directos de Efectivo** (Bancos al exportar, Caja/Flujos mixtos al
  cerrar) vía :func:`aplicar_pagos_flujos_directos`, o el usuario (abono
  manual). Cada pago baja el saldo de la obligación y de sus cuotas (FIFO por
  fecha de vencimiento).

Estados de la obligación: ``pendiente`` (sin abonos), ``parcial`` (abonada),
``pagada`` (saldo en cero) y ``anulada`` (descartada). «Vencida» no es un
estado persistido: se calcula al listar comparando el próximo vencimiento
impago con la fecha actual.
"""

import logging
from datetime import date, datetime
from typing import Optional

from app.config import DB_PATH

from . import core
from .core import _and_empresa, _where_empresa, _empresa_id_desde_db_path, _ultimo_id

logger = logging.getLogger(__name__)

# Tolerancia de cuadre monetario (misma convención que el resto del sistema).
_TOL = 0.01

TIPO_CXC = "cxc"   # cuentas por cobrar (cartera)
TIPO_CXP = "cxp"   # cuentas por pagar

# Clasificaciones RADIAN que generan una obligación y su tipo. Las notas
# crédito/débito y la nómina quedan fuera de la sincronización automática:
# sus ajustes se registran como abonos manuales sobre la obligación afectada.
CLASIFICACION_A_TIPO = {
    "FACTURA_VENTA":     TIPO_CXC,
    "FACTURA_COMPRA":    TIPO_CXP,
    "DOCUMENTO_SOPORTE": TIPO_CXP,
}


def _estado_por_saldo(valor_total: float, saldo: float) -> str:
    """Estado derivado del saldo: pendiente → parcial → pagada."""
    if saldo <= _TOL:
        return "pagada"
    if saldo < (valor_total or 0) - _TOL:
        return "parcial"
    return "pendiente"


# ── Obligaciones ─────────────────────────────────────────────────────────────

def registrar_obligacion(
    tipo: str,
    nit_tercero: str,
    valor_total: float,
    nombre_tercero: str = "",
    cufe: str = "",
    clasificacion: str = "",
    documento: str = "",
    fecha_emision: str = "",
    fecha_vencimiento: str = "",
    origen: str = "radian",
    observaciones: str = "",
    db_path: str = DB_PATH,
) -> Optional[int]:
    """Crea una obligación y retorna su id.

    Idempotente por ``cufe``: si se pasa un CUFE que ya tiene obligación en la
    empresa, no se duplica y se retorna ``None`` (permite re-sincronizar sin
    perder condiciones de pago ni abonos ya registrados).
    """
    if tipo not in (TIPO_CXC, TIPO_CXP) or not nit_tercero:
        return None
    conn = core.get_connection(db_path)
    ahora = datetime.now().isoformat()
    try:
        if cufe:
            and_emp, p_emp = _and_empresa(conn, db_path)
            existe = conn.execute(
                f"SELECT id FROM cartera_obligaciones WHERE cufe = ?{and_emp}",
                (cufe,) + p_emp,
            ).fetchone()
            if existe:
                return None
        valor = round(float(valor_total or 0), 2)
        params = (
            tipo, origen, cufe or None, clasificacion, documento,
            nit_tercero, nombre_tercero, fecha_emision or None,
            valor, valor, fecha_vencimiento or None, ahora, ahora,
        )
        cols = ("tipo, origen, cufe, clasificacion, documento, nit_tercero, "
                "nombre_tercero, fecha_emision, valor_total, saldo, "
                "fecha_vencimiento, created_at, updated_at")
        if conn.is_sqlite:
            conn.execute(
                f"INSERT INTO cartera_obligaciones ({cols}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                params,
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                f"INSERT INTO cartera_obligaciones (empresa_id, {cols}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp_id,) + params,
            )
        new_id = _ultimo_id(conn)
        conn.commit()
        return new_id
    finally:
        conn.close()


def obtener_obligacion(oblig_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Retorna una obligación por id (acotada a la empresa), o None."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT * FROM cartera_obligaciones WHERE id = ?{and_emp}",
            (oblig_id,) + p_emp,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def listar_obligaciones(
    db_path: str = DB_PATH,
    tipo: Optional[str] = None,
    incluir_cerradas: bool = True,
) -> list[dict]:
    """Lista las obligaciones (sin anuladas) con su próximo vencimiento.

    A cada fila se le añaden campos calculados:
    - ``prox_vencimiento``: la fecha de la cuota impaga más próxima (o la fecha
      de vencimiento de la obligación si no hay cuotas).
    - ``vencida``: True si tiene saldo y el próximo vencimiento ya pasó.
    - ``dias_vencimiento``: días hasta el próximo vencimiento (negativo si ya
      venció); None si no tiene fecha definida.

    Con ``incluir_cerradas=False`` se omiten las pagadas.
    """
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        sql = f"SELECT * FROM cartera_obligaciones{where_emp}"
        params = p_emp
        sql += (" AND" if where_emp else " WHERE") + " estado != 'anulada'"
        if tipo:
            sql += " AND tipo = ?"
            params = params + (tipo,)
        sql += " ORDER BY nombre_tercero, fecha_emision, id"
        obligaciones = [dict(r) for r in conn.execute(sql, params).fetchall()]

        # Próxima cuota impaga por obligación (una sola consulta).
        prox: dict[int, str] = {}
        and_emp, p_emp2 = _and_empresa(conn, db_path)
        rows = conn.execute(
            "SELECT obligacion_id, MIN(fecha_vencimiento) AS f "
            "FROM cartera_cuotas "
            f"WHERE estado != 'pagada' AND fecha_vencimiento IS NOT NULL{and_emp} "
            "GROUP BY obligacion_id",
            p_emp2,
        ).fetchall()
        for r in rows:
            prox[r["obligacion_id"]] = r["f"]
    finally:
        conn.close()

    hoy = date.today().isoformat()
    for o in obligaciones:
        o["prox_vencimiento"] = prox.get(o["id"]) or o.get("fecha_vencimiento")
        venc = (o["prox_vencimiento"] or "")[:10]
        con_saldo = (o.get("saldo") or 0) > _TOL
        o["vencida"] = bool(venc and con_saldo and venc < hoy)
        o["dias_vencimiento"] = None
        if venc:
            try:
                o["dias_vencimiento"] = (date.fromisoformat(venc) - date.today()).days
            except ValueError:
                pass
    if not incluir_cerradas:
        obligaciones = [o for o in obligaciones if o["estado"] != "pagada"]
    return obligaciones


def actualizar_datos_obligacion(
    oblig_id: int,
    db_path: str = DB_PATH,
    **campos,
) -> None:
    """Actualiza datos de gestión de la obligación (solo los campos pasados).

    Campos permitidos: ``fuente_recursos``, ``contacto_nombre``,
    ``contacto_telefono``, ``contacto_correo``, ``observaciones``,
    ``nombre_tercero``.
    """
    permitidos = {
        "fuente_recursos", "contacto_nombre", "contacto_telefono",
        "contacto_correo", "observaciones", "nombre_tercero",
    }
    sets, valores = [], []
    for campo, valor in campos.items():
        if campo in permitidos and valor is not None:
            sets.append(f"{campo} = ?")
            valores.append(valor)
    if not sets:
        return
    sets.append("updated_at = ?")
    valores.append(datetime.now().isoformat())
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"UPDATE cartera_obligaciones SET {', '.join(sets)} WHERE id = ?{and_emp}",
            tuple(valores) + (oblig_id,) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


def anular_obligacion(oblig_id: int, db_path: str = DB_PATH) -> None:
    """Marca una obligación como anulada (deja de aparecer en la cartera)."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            "UPDATE cartera_obligaciones SET estado = 'anulada', updated_at = ? "
            f"WHERE id = ?{and_emp}",
            (datetime.now().isoformat(), oblig_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


# ── Sincronización desde Flujos Indirectos (documentos RADIAN) ──────────────

def sincronizar_desde_documentos(db_path: str = DB_PATH) -> dict:
    """Crea las obligaciones que falten a partir de ``documentos_importados``.

    Facturas de venta → CxC (tercero = receptor); facturas de compra y
    documentos soporte → CxP (tercero = emisor). Es idempotente: los CUFE que
    ya tienen obligación se saltan, conservando condiciones de pago y abonos.

    Returns:
        ``{"creadas": n, "revisadas": m}``.
    """
    conn = core.get_connection(db_path)
    try:
        where_emp, p_emp = _where_empresa(conn, db_path)
        marcas = ",".join("?" for _ in CLASIFICACION_A_TIPO)
        sql = (
            "SELECT cufe, clasificacion, folio, prefijo, nit_emisor, "
            "nombre_emisor, nit_receptor, nombre_receptor, total, fecha_emision "
            f"FROM documentos_importados{where_emp}"
        )
        sql += (" AND" if where_emp else " WHERE") + f" clasificacion IN ({marcas})"
        docs = conn.execute(sql, p_emp + tuple(CLASIFICACION_A_TIPO)).fetchall()
    finally:
        conn.close()

    creadas = 0
    for d in docs:
        tipo = CLASIFICACION_A_TIPO[d["clasificacion"]]
        if tipo == TIPO_CXC:
            nit, nombre = d["nit_receptor"], d["nombre_receptor"]
        else:
            nit, nombre = d["nit_emisor"], d["nombre_emisor"]
        if not nit:
            continue
        prefijo = (d["prefijo"] or "").strip()
        folio = (d["folio"] or "").strip()
        doc_ref = f"{prefijo}-{folio}" if prefijo and folio else (folio or prefijo)
        nuevo = registrar_obligacion(
            tipo=tipo,
            nit_tercero=str(nit),
            valor_total=float(d["total"] or 0),
            nombre_tercero=nombre or "",
            cufe=d["cufe"],
            clasificacion=d["clasificacion"],
            documento=doc_ref,
            fecha_emision=(d["fecha_emision"] or "")[:10],
            db_path=db_path,
        )
        if nuevo is not None:
            creadas += 1
    return {"creadas": creadas, "revisadas": len(docs)}


# ── Condiciones de pago y cuotas ─────────────────────────────────────────────

def listar_cuotas(oblig_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Cuotas de una obligación ordenadas por número."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(
            f"SELECT * FROM cartera_cuotas WHERE obligacion_id = ?{and_emp} "
            "ORDER BY numero, id",
            (oblig_id,) + p_emp,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def definir_condiciones_pago(
    oblig_id: int,
    condicion: str,
    fecha_vencimiento: str = "",
    cuotas: Optional[list[dict]] = None,
    db_path: str = DB_PATH,
) -> None:
    """Define la condición de pago de una obligación.

    - ``condicion='contado'``: un solo vencimiento (``fecha_vencimiento``);
      se eliminan las cuotas que hubiera.
    - ``condicion='credito'``: se reemplazan las cuotas por las recibidas
      (``[{"fecha_vencimiento", "valor"}, …]``, una fecha de vencimiento por
      cuota). La suma de las cuotas debe igualar el valor total (±0.01).

    Los abonos ya registrados se conservan: el total pagado se re-aplica FIFO
    sobre las cuotas nuevas para dejar sus saldos coherentes.

    Raises:
        ValueError: si la obligación no existe, la condición es inválida o la
        suma de las cuotas no cuadra con el valor total.
    """
    oblig = obtener_obligacion(oblig_id, db_path)
    if not oblig:
        raise ValueError("La obligación no existe.")
    if condicion not in ("contado", "credito"):
        raise ValueError("Condición de pago inválida (contado o credito).")

    cuotas = cuotas or []
    if condicion == "credito":
        if not cuotas:
            raise ValueError("Una obligación a crédito requiere al menos una cuota.")
        if any(not (c.get("fecha_vencimiento") or "").strip() for c in cuotas):
            raise ValueError("Cada cuota debe tener su fecha de vencimiento.")
        suma = round(sum(float(c.get("valor") or 0) for c in cuotas), 2)
        total = round(float(oblig["valor_total"] or 0), 2)
        if abs(suma - total) >= _TOL:
            raise ValueError(
                f"La suma de las cuotas (${suma:,.2f}) debe igualar el valor "
                f"total de la obligación (${total:,.2f})."
            )

    pagado = round(float(oblig["valor_total"] or 0) - float(oblig["saldo"] or 0), 2)
    ahora = datetime.now().isoformat()
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        conn.execute(
            f"DELETE FROM cartera_cuotas WHERE obligacion_id = ?{and_emp}",
            (oblig_id,) + p_emp,
        )
        primera = fecha_vencimiento or None
        if condicion == "credito":
            ordenadas = sorted(cuotas, key=lambda c: c["fecha_vencimiento"])
            primera = ordenadas[0]["fecha_vencimiento"]
            restante = pagado  # re-aplicar lo ya abonado, FIFO
            for i, c in enumerate(ordenadas, start=1):
                valor = round(float(c.get("valor") or 0), 2)
                abono = round(min(restante, valor), 2)
                restante = round(restante - abono, 2)
                saldo_cuota = round(valor - abono, 2)
                params = (
                    oblig_id, i, c["fecha_vencimiento"], valor, saldo_cuota,
                    _estado_por_saldo(valor, saldo_cuota),
                )
                if conn.is_sqlite:
                    conn.execute(
                        "INSERT INTO cartera_cuotas (obligacion_id, numero, "
                        "fecha_vencimiento, valor, saldo, estado) VALUES (?,?,?,?,?,?)",
                        params,
                    )
                else:
                    emp_id = _empresa_id_desde_db_path(db_path)
                    conn.execute(
                        "INSERT INTO cartera_cuotas (empresa_id, obligacion_id, "
                        "numero, fecha_vencimiento, valor, saldo, estado) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (emp_id,) + params,
                    )
        conn.execute(
            "UPDATE cartera_obligaciones SET condicion_pago = ?, num_cuotas = ?, "
            f"fecha_vencimiento = ?, updated_at = ? WHERE id = ?{and_emp}",
            (condicion, max(len(cuotas), 1) if condicion == "credito" else 1,
             primera, ahora, oblig_id) + p_emp,
        )
        conn.commit()
    finally:
        conn.close()


# ── Pagos / abonos ───────────────────────────────────────────────────────────

def registrar_pago(
    oblig_id: int,
    valor: float,
    fecha: str = "",
    origen: str = "manual",
    referencia: str = "",
    detalle: str = "",
    db_path: str = DB_PATH,
) -> float:
    """Registra un abono sobre una obligación y actualiza los saldos.

    El abono se aplica primero al saldo de la obligación (tope: no queda
    negativo) y luego FIFO sobre sus cuotas por fecha de vencimiento.

    Returns:
        El valor efectivamente aplicado (puede ser menor al recibido si la
        obligación tenía un saldo inferior).
    """
    oblig = obtener_obligacion(oblig_id, db_path)
    if not oblig or oblig["estado"] == "anulada":
        return 0.0
    valor = round(float(valor or 0), 2)
    if valor <= 0:
        return 0.0
    saldo = round(float(oblig["saldo"] or 0), 2)
    aplicado = round(min(valor, saldo), 2)
    if aplicado <= 0:
        return 0.0

    ahora = datetime.now().isoformat()
    fecha = fecha or date.today().isoformat()
    nuevo_saldo = round(saldo - aplicado, 2)
    estado = _estado_por_saldo(float(oblig["valor_total"] or 0), nuevo_saldo)

    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        params = (oblig_id, fecha, aplicado, origen, referencia, detalle, ahora)
        if conn.is_sqlite:
            conn.execute(
                "INSERT INTO cartera_pagos (obligacion_id, fecha, valor, origen, "
                "referencia, detalle, created_at) VALUES (?,?,?,?,?,?,?)",
                params,
            )
        else:
            emp_id = _empresa_id_desde_db_path(db_path)
            conn.execute(
                "INSERT INTO cartera_pagos (empresa_id, obligacion_id, fecha, "
                "valor, origen, referencia, detalle, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (emp_id,) + params,
            )
        conn.execute(
            "UPDATE cartera_obligaciones SET saldo = ?, estado = ?, updated_at = ? "
            f"WHERE id = ?{and_emp}",
            (nuevo_saldo, estado, ahora, oblig_id) + p_emp,
        )
        # FIFO sobre las cuotas (si la obligación es a crédito).
        cuotas = conn.execute(
            f"SELECT * FROM cartera_cuotas WHERE obligacion_id = ?{and_emp} "
            "ORDER BY fecha_vencimiento, numero, id",
            (oblig_id,) + p_emp,
        ).fetchall()
        restante = aplicado
        for c in cuotas:
            if restante <= 0:
                break
            saldo_cuota = round(float(c["saldo"] or 0), 2)
            if saldo_cuota <= 0:
                continue
            abono = round(min(restante, saldo_cuota), 2)
            restante = round(restante - abono, 2)
            nuevo = round(saldo_cuota - abono, 2)
            conn.execute(
                "UPDATE cartera_cuotas SET saldo = ?, estado = ? "
                f"WHERE id = ?{and_emp}",
                (nuevo, _estado_por_saldo(float(c["valor"] or 0), nuevo),
                 c["id"]) + p_emp,
            )
        conn.commit()
    finally:
        conn.close()
    return aplicado


def listar_pagos(oblig_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Abonos de una obligación (más recientes primero)."""
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        rows = conn.execute(
            f"SELECT * FROM cartera_pagos WHERE obligacion_id = ?{and_emp} "
            "ORDER BY fecha DESC, id DESC",
            (oblig_id,) + p_emp,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def pagos_ya_aplicados(origen: str, referencia: str, db_path: str = DB_PATH) -> bool:
    """True si ya se aplicaron pagos con ese (origen, referencia).

    Es la llave de idempotencia de los módulos de Flujos Directos: reexportar
    el mismo proceso de banco o re-cerrar el mismo período no duplica abonos.
    """
    if not referencia:
        return False
    conn = core.get_connection(db_path)
    try:
        and_emp, p_emp = _and_empresa(conn, db_path)
        row = conn.execute(
            f"SELECT 1 FROM cartera_pagos WHERE origen = ? AND referencia = ?{and_emp}",
            (origen, referencia) + p_emp,
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def aplicar_pago_tercero(
    nit_tercero: str,
    tipo: str,
    valor: float,
    fecha: str = "",
    origen: str = "manual",
    referencia: str = "",
    detalle: str = "",
    db_path: str = DB_PATH,
) -> float:
    """Aplica un pago a las obligaciones abiertas de un tercero.

    Reparte el valor entre las obligaciones con saldo del tercero (las de
    vencimiento más antiguo primero). Lo que no alcance a cubrirse se ignora
    (retorna solo lo aplicado): el módulo no inventa saldos a favor.
    """
    nit = "".join(ch for ch in str(nit_tercero or "") if ch.isdigit())
    valor = round(float(valor or 0), 2)
    if not nit or valor <= 0:
        return 0.0

    abiertas = [
        o for o in listar_obligaciones(db_path, tipo=tipo)
        if o["nit_tercero"] == nit and (o["saldo"] or 0) > _TOL
    ]
    # Vencimiento más antiguo primero; sin fecha al final, luego por emisión.
    abiertas.sort(key=lambda o: (
        (o["prox_vencimiento"] or "9999-12-31"),
        o["fecha_emision"] or "9999-12-31",
        o["id"],
    ))
    aplicado_total = 0.0
    restante = valor
    for o in abiertas:
        if restante <= _TOL:
            break
        aplicado = registrar_pago(
            o["id"], restante, fecha=fecha, origen=origen,
            referencia=referencia, detalle=detalle, db_path=db_path,
        )
        aplicado_total = round(aplicado_total + aplicado, 2)
        restante = round(restante - aplicado, 2)
    return aplicado_total


def aplicar_pagos_flujos_directos(
    movimientos: list[dict],
    origen: str,
    referencia: str,
    db_path: str = DB_PATH,
) -> dict:
    """Actualiza la cartera con los pagos hechos en un módulo de Flujos Directos.

    Cada movimiento es ``{"nit", "valor", "sentido", "fecha", "detalle"}``:
    los ingresos (``sentido='ingreso'``) abonan la cartera (CxC) del tercero y
    los egresos (``sentido='egreso'``) abonan sus cuentas por pagar (CxP).
    Los movimientos sin NIT o sin obligación abierta del tercero se ignoran.

    Idempotente por ``(origen, referencia)``: si ya hay pagos registrados con
    esa referencia (p. ej. una reexportación del mismo proceso de banco), no
    se vuelve a aplicar nada.

    Returns:
        ``{"aplicado": total, "n_pagos": n, "omitido": True|False}``.
    """
    if pagos_ya_aplicados(origen, referencia, db_path):
        return {"aplicado": 0.0, "n_pagos": 0, "omitido": True}

    total, n = 0.0, 0
    for m in movimientos:
        tipo = TIPO_CXC if m.get("sentido") == "ingreso" else TIPO_CXP
        aplicado = aplicar_pago_tercero(
            m.get("nit", ""), tipo, m.get("valor", 0),
            fecha=m.get("fecha", ""), origen=origen, referencia=referencia,
            detalle=m.get("detalle", ""), db_path=db_path,
        )
        if aplicado > 0:
            total = round(total + aplicado, 2)
            n += 1
    return {"aplicado": total, "n_pagos": n, "omitido": False}


# ── Resumen para el tablero del módulo ───────────────────────────────────────

def resumen_cartera(db_path: str = DB_PATH) -> dict:
    """Totales para las tarjetas del módulo.

    Returns:
        Dict con ``por_cobrar``, ``por_pagar`` (saldos abiertos),
        ``vencido_cxc``, ``vencido_cxp`` y ``proximos_30`` (saldo que vence en
        los próximos 30 días, ambos tipos).
    """
    resumen = {
        "por_cobrar": 0.0, "por_pagar": 0.0,
        "vencido_cxc": 0.0, "vencido_cxp": 0.0, "proximos_30": 0.0,
    }
    for o in listar_obligaciones(db_path):
        saldo = round(float(o["saldo"] or 0), 2)
        if saldo <= _TOL:
            continue
        if o["tipo"] == TIPO_CXC:
            resumen["por_cobrar"] = round(resumen["por_cobrar"] + saldo, 2)
            if o["vencida"]:
                resumen["vencido_cxc"] = round(resumen["vencido_cxc"] + saldo, 2)
        else:
            resumen["por_pagar"] = round(resumen["por_pagar"] + saldo, 2)
            if o["vencida"]:
                resumen["vencido_cxp"] = round(resumen["vencido_cxp"] + saldo, 2)
        dias = o.get("dias_vencimiento")
        if dias is not None and 0 <= dias <= 30:
            resumen["proximos_30"] = round(resumen["proximos_30"] + saldo, 2)
    return resumen
