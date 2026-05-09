"""
Mapeador de movimientos bancarios al formato SIIGO.

Genera dos FilaSiigo por cada movimiento principal:
  - Ingreso (valor > 0):
      Línea 1: banco  → DÉBITO  abs(valor)
      Línea 2: contra → CRÉDITO abs(valor)

  - Egreso (valor < 0):
      Línea 1: banco  → CRÉDITO abs(valor)
      Línea 2: contra → DÉBITO  abs(valor)

Para los movimientos 4x1000 enlazados a un egreso padre se agregan
dos líneas adicionales dentro del MISMO consecutivo:
      Línea 3: banco     → CRÉDITO abs(valor_4x1000)
      Línea 4: 53152001  → DÉBITO  abs(valor_4x1000)

Tipo de comprobante por defecto:
  - 111 (Recibo de caja)      → ingreso
  - 112 (Recibo de pago/egreso) → egreso
  - 110 (Traslado de fondos)  → seleccionado manualmente por el usuario

Consecutivo: formato yyyymmNN independiente por (tipo_comprobante, año-mes),
igual que en el mapeador RADIAN.
"""

from __future__ import annotations

from app.banco.importador_banco import MovimientoBanco
from app.config import (
    BANCO_CUENTA_DEFAULT,
    BANCO_CUENTA_4X1000,
    SIIGO_COMP_BANCO_INGRESO,
    SIIGO_COMP_BANCO_EGRESO,
)
from app.siigo.mapeador import FilaSiigo


def mapear_banco_a_siigo(
    movimientos: list[MovimientoBanco],
    cuenta_banco: str,
    asignaciones: list[dict],
    nit_banco: str = "",
    df_cuentas=None,
) -> list[FilaSiigo]:
    """
    Genera las FilaSiigo para los movimientos bancarios.

    Los movimientos 4x1000 (codigo 3339) se agregan automáticamente como
    líneas dentro del mismo asiento del egreso padre. No requieren asignación
    manual del usuario.

    Los movimientos con es_bancario=True (intereses, cuota de manejo, GMF,
    etc.) usan siempre nit_banco como tercero, independientemente de lo que
    el usuario haya ingresado en la asignación (que queda como fallback).

    Args:
        movimientos:   Lista ordenada de MovimientoBanco.
        cuenta_banco:  Código contable de la cuenta bancaria.
        asignaciones:  Lista de dicts con las contrapartidas asignadas por el usuario.
        nit_banco:     NIT del banco (auto-aplicado a 4x1000 y movimientos bancarios).
        df_cuentas:    (opcional) maestro de cuentas - no usado actualmente.

    Returns:
        Lista plana de FilaSiigo.
    """
    cuenta_banco = cuenta_banco or BANCO_CUENTA_DEFAULT

    # Indexar asignaciones por idx de movimiento
    asig_map: dict[int, dict] = {int(a["idx"]): a for a in asignaciones}

    # Mapa de 4x1000 agrupados por su padre
    impuestos_por_padre: dict[int, list[MovimientoBanco]] = {}
    for m in movimientos:
        if m.es_4x1000 and m.idx_padre is not None:
            impuestos_por_padre.setdefault(m.idx_padre, []).append(m)

    # Movimientos principales: no son 4x1000 agrupados (sí aparecen los huérfanos)
    principales = [
        m for m in movimientos
        if not m.es_4x1000 or m.idx_padre is None
    ]

    # Contador de consecutivos por clave (tipo_comprobante_yyyymm)
    contadores: dict[str, int] = {}
    filas: list[FilaSiigo] = []

    for m in principales:
        asig = asig_map.get(m.idx, {})

        # ── NIT tercero ──────────────────────────────────────────────────────────────────
        # Los movimientos marcados como bancarios (intereses, GMF, cuota de
        # manejo, etc.) siempre usan el NIT del banco. El NIT ingresado por
        # el usuario en la asignación es el fallback si nit_banco está vacío.
        asig_nit = asig.get("nit_tercero", "").strip()
        if m.es_bancario and nit_banco:
            nit_tercero = nit_banco
        else:
            nit_tercero = asig_nit

        # ── Tipo de comprobante ──────────────────────────────────────────
        tipo_manual = asig.get("tipo_comprobante")
        if tipo_manual and str(tipo_manual).strip():
            tipo_comp = int(tipo_manual)
        elif m.valor > 0:
            tipo_comp = SIIGO_COMP_BANCO_INGRESO   # 111
        else:
            tipo_comp = SIIGO_COMP_BANCO_EGRESO    # 112

        # ── Consecutivo ──────────────────────────────────────────────────
        mes   = m.fecha.strftime("%Y%m")
        clave = f"{tipo_comp}_{mes}"
        contadores[clave] = contadores.get(clave, 0) + 1
        consecutivo = int(f"{mes}{contadores[clave]:02d}")

        # ── Datos comunes ────────────────────────────────────────────────
        fecha_str            = m.fecha.strftime("%d/%m/%Y")
        abs_valor            = abs(m.valor)
        cuenta_contrapartida = asig.get("cuenta_contrapartida", "").strip()
        descripcion          = m.descripcion
        observaciones        = (
            f"Banco {m.cuenta_banco_num} | Cód.{m.codigo_banco} | {m.descripcion}"
        )
        es_pendiente         = not cuenta_contrapartida

        # ── Líneas del movimiento principal ──────────────────────────────
        if m.valor > 0:
            # Ingreso: débito banco / crédito contrapartida
            filas.append(FilaSiigo(
                tipo_comprobante=tipo_comp,
                consecutivo_comprobante=consecutivo,
                fecha=fecha_str,
                codigo_cuenta=cuenta_banco,
                nit_tercero=nit_tercero,
                descripcion=descripcion,
                observaciones=observaciones,
                debito=float(abs_valor),
                credito=0.0,
                es_pendiente=es_pendiente,
            ))
            filas.append(FilaSiigo(
                tipo_comprobante=tipo_comp,
                consecutivo_comprobante=consecutivo,
                fecha=fecha_str,
                codigo_cuenta=cuenta_contrapartida,
                nit_tercero=nit_tercero,
                descripcion=descripcion,
                observaciones=observaciones,
                debito=0.0,
                credito=float(abs_valor),
                es_pendiente=es_pendiente,
            ))
        else:
            # Egreso: crédito banco / débito contrapartida
            filas.append(FilaSiigo(
                tipo_comprobante=tipo_comp,
                consecutivo_comprobante=consecutivo,
                fecha=fecha_str,
                codigo_cuenta=cuenta_banco,
                nit_tercero=nit_tercero,
                descripcion=descripcion,
                observaciones=observaciones,
                debito=0.0,
                credito=float(abs_valor),
                es_pendiente=es_pendiente,
            ))
            filas.append(FilaSiigo(
                tipo_comprobante=tipo_comp,
                consecutivo_comprobante=consecutivo,
                fecha=fecha_str,
                codigo_cuenta=cuenta_contrapartida,
                nit_tercero=nit_tercero,
                descripcion=descripcion,
                observaciones=observaciones,
                debito=float(abs_valor),
                credito=0.0,
                es_pendiente=es_pendiente,
            ))

        # ── Líneas 4x1000 vinculadas al mismo consecutivo ───────────────────────────────
        # El 4x1000 siempre queda a nombre del banco (nit_banco), no del tercero.
        for imp in impuestos_por_padre.get(m.idx, []):
            abs_imp  = abs(imp.valor)
            desc_imp = imp.descripcion
            obs_imp  = f"4x1000 | {observaciones}"
            nit_4x1000 = nit_banco if nit_banco else nit_tercero

            # Crédito banco (el impuesto sale del banco)
            filas.append(FilaSiigo(
                tipo_comprobante=tipo_comp,
                consecutivo_comprobante=consecutivo,
                fecha=fecha_str,
                codigo_cuenta=cuenta_banco,
                nit_tercero=nit_4x1000,
                descripcion=desc_imp,
                observaciones=obs_imp,
                debito=0.0,
                credito=float(abs_imp),
            ))
            # Débito gasto 4x1000
            filas.append(FilaSiigo(
                tipo_comprobante=tipo_comp,
                consecutivo_comprobante=consecutivo,
                fecha=fecha_str,
                codigo_cuenta=BANCO_CUENTA_4X1000,
                nit_tercero=nit_4x1000,
                descripcion=desc_imp,
                observaciones=obs_imp,
                debito=float(abs_imp),
                credito=0.0,
            ))

    return filas
