"""Sincronización del presupuesto ejecutado desde la contabilidad.

Flujo:
1. Obtiene los movimientos contables del mes vía el conector de la empresa
   (Siigo, Alegra o CSV).
2. Cruza cada movimiento contra los mapeos de cuentas de las líneas
   presupuestales (por prefijo PUC: el mapeo "4135" captura 413501, 413524...).
3. Escribe/actualiza los valores EJECUTADOS del mes y deja log de auditoría.
"""
import json

from sqlalchemy.orm import Session

from ..connectors import crear_conector
from ..connectors.base import MovimientoContable
from ..models import (
    Empresa, FuenteDato, LogSincronizacion, Presupuesto, TipoValor, ValorMensual,
)
from ..schemas import ResultadoSync
from .motor import obtener_presupuesto


def _cruzar_movimientos(pres: Presupuesto, movimientos: list[MovimientoContable]) -> dict[int, dict]:
    """Devuelve {linea_id: {"valor": suma, "cuentas": [...]}}, cruzando por
    prefijo de cuenta. El mapeo más largo (más específico) gana."""
    mapeos = []  # (prefijo, invertir, linea_id)
    for cat in pres.categorias:
        for linea in cat.lineas:
            for m in linea.mapeos:
                mapeos.append((m.codigo_cuenta.strip(), m.invertir_signo, linea.id))
    mapeos.sort(key=lambda t: len(t[0]), reverse=True)  # específico primero

    resultado: dict[int, dict] = {}
    for mov in movimientos:
        for prefijo, invertir, linea_id in mapeos:
            if mov.codigo_cuenta.startswith(prefijo):
                valor = -mov.valor if invertir else mov.valor
                item = resultado.setdefault(linea_id, {"valor": 0.0, "cuentas": []})
                item["valor"] += valor
                item["cuentas"].append(mov.codigo_cuenta)
                break  # solo el mapeo más específico
    return resultado


def sincronizar_ejecutado(
    db: Session,
    presupuesto_id: int,
    mes: int,
    movimientos: list[MovimientoContable] | None = None,
) -> ResultadoSync:
    """Sincroniza el ejecutado de un mes. Si `movimientos` es None, los
    obtiene del conector configurado en la empresa."""
    pres = obtener_presupuesto(db, presupuesto_id)
    if pres is None:
        raise ValueError(f"Presupuesto {presupuesto_id} no existe")

    empresa: Empresa = db.get(Empresa, pres.empresa_id)
    fuente = empresa.conector

    try:
        if movimientos is None:
            if fuente == FuenteDato.MANUAL:
                raise ValueError(
                    "La empresa tiene conector 'manual': registre el ejecutado "
                    "por la API o configure Siigo/Alegra/CSV."
                )
            config = json.loads(empresa.conector_config or "{}")
            conector = crear_conector(fuente, config)
            movimientos = conector.obtener_movimientos(pres.anio, mes)
        else:
            fuente = FuenteDato.CSV if fuente == FuenteDato.MANUAL else fuente

        cruzado = _cruzar_movimientos(pres, movimientos)

        detalle = []
        for linea_id, info in cruzado.items():
            valor = round(info["valor"], 2)
            existente = (
                db.query(ValorMensual)
                .filter_by(linea_id=linea_id, mes=mes, tipo=TipoValor.EJECUTADO)
                .first()
            )
            if existente:
                existente.valor = valor
                existente.fuente = fuente
            else:
                db.add(ValorMensual(
                    linea_id=linea_id, mes=mes, tipo=TipoValor.EJECUTADO,
                    valor=valor, fuente=fuente,
                ))
            detalle.append({
                "linea_id": linea_id, "valor": valor,
                "cuentas_origen": info["cuentas"],
            })

        n = len(cruzado)
        sin_mapeo = [
            m.codigo_cuenta for m in movimientos
            if not any(m.codigo_cuenta in d["cuentas_origen"] for d in detalle)
        ]
        mensaje = f"{n} líneas actualizadas desde {fuente.value}."
        if sin_mapeo:
            mensaje += f" Cuentas sin mapear: {', '.join(sorted(set(sin_mapeo))[:10])}."

        db.add(LogSincronizacion(
            empresa_id=empresa.id, presupuesto_id=pres.id, fuente=fuente,
            anio=pres.anio, mes=mes, exito=True,
            lineas_actualizadas=n, mensaje=mensaje,
        ))
        db.commit()
        return ResultadoSync(
            exito=True, fuente=fuente, anio=pres.anio, mes=mes,
            lineas_actualizadas=n, mensaje=mensaje, detalle=detalle,
        )

    except Exception as e:  # noqa: BLE001
        db.rollback()
        db.add(LogSincronizacion(
            empresa_id=empresa.id, presupuesto_id=pres.id, fuente=fuente,
            anio=pres.anio, mes=mes, exito=False, mensaje=str(e),
        ))
        db.commit()
        return ResultadoSync(
            exito=False, fuente=fuente, anio=pres.anio, mes=mes,
            lineas_actualizadas=0, mensaje=f"Error: {e}",
        )
