"""
Mapeador de movimientos de Caja General al formato SIIGO.

Por cada movimiento de efectivo genera un asiento de dos líneas, igual que el
módulo Bancos pero usando la **cuenta contable de la caja** como lado fijo:

  - Entrada (ingreso de efectivo):
      Línea 1: cuenta caja   → DÉBITO  abs(valor)
      Línea 2: contrapartida → CRÉDITO abs(valor)

  - Salida (egreso de efectivo):
      Línea 1: cuenta caja   → CRÉDITO abs(valor)
      Línea 2: contrapartida → DÉBITO  abs(valor)

Tipo de comprobante por movimiento (columna "Tipo comprobante"):
  - 111 (Recibo de caja)   → entrada (por defecto)
  - 112 (Recibo de pago)   → salida  (por defecto)
  - 110 (Traslado)         → seleccionable manualmente

Consecutivo: formato yyyymmNN independiente por (tipo_comprobante, año-mes),
igual que en los mapeadores RADIAN y Bancos.
"""

from __future__ import annotations

from app.caja.modelo_caja import MovimientoCaja
from app.siigo.mapeador import FilaSiigo


def mapear_caja_a_siigo(
    movimientos: list[MovimientoCaja],
    cuenta_caja: str,
    df_cuentas=None,
) -> list[FilaSiigo]:
    """Genera las FilaSiigo de los movimientos de un período de caja.

    Args:
        movimientos:  Lista de MovimientoCaja del período.
        cuenta_caja:  Código contable de la cuenta de caja (lado fijo del asiento).
        df_cuentas:   (reservado) maestro de cuentas; no usado actualmente.

    Returns:
        Lista plana de FilaSiigo lista para exportar.
    """
    cuenta_caja = (cuenta_caja or "").strip()

    # Solo movimientos con valor (entrada o salida). Se ordenan cronológicamente
    # para que los consecutivos respeten el orden, igual que en Bancos/RADIAN.
    from datetime import date as _date
    con_valor = [
        m for m in movimientos
        if abs(m.inflow_amount) > 0 or abs(m.outflow_amount) > 0
    ]
    con_valor.sort(key=lambda m: (m.movement_date or _date.max, m.sequence))

    contadores: dict[str, int] = {}
    filas: list[FilaSiigo] = []

    for m in con_valor:
        es_ingreso = m.es_entrada
        abs_valor = abs(m.inflow_amount) if es_ingreso else abs(m.outflow_amount)
        if abs_valor <= 0:
            continue

        tipo_comp = m.comprobante_efectivo()

        # Consecutivo yyyymmNN por (tipo_comprobante, año-mes).
        mes = m.movement_date.strftime("%Y%m") if m.movement_date else "000000"
        clave = f"{tipo_comp}_{mes}"
        contadores[clave] = contadores.get(clave, 0) + 1
        consecutivo = int(f"{mes}{contadores[clave]:02d}")

        fecha_str = m.movement_date.strftime("%d/%m/%Y") if m.movement_date else ""
        nit = m.third_party_nit
        descripcion = m.concept
        partes_obs = [p for p in (m.cost_center, m.observations) if p]
        observaciones = " | ".join(["Caja " + cuenta_caja, *partes_obs]) if cuenta_caja \
            else " | ".join(partes_obs)

        contrapartida = (m.contrapartida or "").strip()
        es_pendiente = not contrapartida
        monto = float(abs_valor)

        # Línea de la caja (lado fijo).
        filas.append(FilaSiigo(
            tipo_comprobante=tipo_comp,
            consecutivo_comprobante=consecutivo,
            fecha=fecha_str,
            codigo_cuenta=cuenta_caja,
            nit_tercero=nit,
            descripcion=descripcion,
            observaciones=observaciones,
            centro_costo=m.cost_center,
            debito=monto if es_ingreso else 0.0,
            credito=0.0 if es_ingreso else monto,
        ))
        # Línea de la contrapartida (lado opuesto).
        filas.append(FilaSiigo(
            tipo_comprobante=tipo_comp,
            consecutivo_comprobante=consecutivo,
            fecha=fecha_str,
            codigo_cuenta=contrapartida,
            nit_tercero=nit,
            descripcion=("[PENDIENTE] " + descripcion) if es_pendiente else descripcion,
            observaciones=observaciones,
            centro_costo=m.cost_center,
            debito=0.0 if es_ingreso else monto,
            credito=monto if es_ingreso else 0.0,
            es_pendiente=es_pendiente,
        ))

    return filas
