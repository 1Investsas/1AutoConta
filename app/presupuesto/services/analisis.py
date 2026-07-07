"""Análisis comparativo presupuesto vs ejecución.

Calcula variaciones por línea (mensual o acumulado YTD), clasifica con
semáforo según los umbrales del presupuesto y genera alertas en lenguaje
natural listas para mostrar en el dashboard o enviar por correo.
"""
from sqlalchemy.orm import Session

from ..models import TipoFlujo, TipoValor
from ..schemas import AnalisisComparativo, VariacionLinea
from .motor import obtener_presupuesto, _vector_valores

MESES_LARGO = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _semaforo(var_pct: float | None, umbral_alerta: float, umbral_critico: float) -> str:
    if var_pct is None:
        return "sin_dato"
    v = abs(var_pct)
    if v >= umbral_critico:
        return "rojo"
    if v >= umbral_alerta:
        return "amarillo"
    return "verde"


def _favorable(tipo: TipoFlujo, variacion: float) -> bool:
    """Una variación positiva es favorable en ingresos y desfavorable en egresos."""
    return variacion >= 0 if tipo == TipoFlujo.INGRESO else variacion <= 0


def analizar(
    db: Session,
    presupuesto_id: int,
    mes: int | None = None,
) -> AnalisisComparativo | None:
    """Si `mes` se indica → análisis de ese mes. Si es None → acumulado
    de enero hasta el último mes con ejecución registrada (YTD)."""
    pres = obtener_presupuesto(db, presupuesto_id)
    if pres is None:
        return None

    # Último mes con ejecutado (para YTD)
    ultimo_mes = 0
    for cat in pres.categorias:
        for linea in cat.lineas:
            for v in linea.valores:
                if v.tipo == TipoValor.EJECUTADO and v.valor != 0:
                    ultimo_mes = max(ultimo_mes, v.mes)

    if mes is not None:
        idx_ini, idx_fin, alcance = mes - 1, mes, "mes"
    else:
        idx_ini, idx_fin, alcance = 0, max(ultimo_mes, 1), "acumulado"

    lineas_out: list[VariacionLinea] = []
    alertas: list[str] = []
    tot = {"ing_p": 0.0, "ing_e": 0.0, "egr_p": 0.0, "egr_e": 0.0}

    for cat in pres.categorias:
        for linea in cat.lineas:
            proy = sum(_vector_valores(linea, TipoValor.PROYECTADO)[idx_ini:idx_fin])
            ejec = sum(_vector_valores(linea, TipoValor.EJECUTADO)[idx_ini:idx_fin])
            variacion = round(ejec - proy, 2)
            var_pct = round(variacion / proy * 100, 2) if proy else None
            cumplimiento = round(ejec / proy * 100, 2) if proy else None
            sem = _semaforo(var_pct, pres.umbral_alerta, pres.umbral_critico)
            fav = _favorable(cat.tipo, variacion) if (proy or ejec) else None

            lineas_out.append(VariacionLinea(
                linea_id=linea.id, categoria=cat.nombre, nombre=linea.nombre,
                tipo=cat.tipo, proyectado=round(proy, 2), ejecutado=round(ejec, 2),
                variacion_absoluta=variacion, variacion_pct=var_pct,
                cumplimiento_pct=cumplimiento, semaforo=sem, favorable=fav,
            ))

            if cat.tipo == TipoFlujo.INGRESO:
                tot["ing_p"] += proy
                tot["ing_e"] += ejec
            else:
                tot["egr_p"] += proy
                tot["egr_e"] += ejec

            # Alertas de líneas críticas desfavorables
            if sem == "rojo" and fav is False:
                periodo = (
                    MESES_LARGO[mes - 1] if mes else f"acumulado a {MESES_LARGO[idx_fin - 1]}"
                )
                if cat.tipo == TipoFlujo.INGRESO:
                    alertas.append(
                        f"'{linea.nombre}' ({periodo}): ingresos {abs(var_pct):.1f}% "
                        f"por debajo de lo presupuestado "
                        f"(${ejec:,.0f} vs ${proy:,.0f})."
                    )
                else:
                    alertas.append(
                        f"'{linea.nombre}' ({periodo}): gasto {abs(var_pct):.1f}% "
                        f"por encima de lo presupuestado "
                        f"(${ejec:,.0f} vs ${proy:,.0f})."
                    )

    neto_p = tot["ing_p"] - tot["egr_p"]
    neto_e = tot["ing_e"] - tot["egr_e"]
    resumen = {
        "ingresos_proyectados": round(tot["ing_p"], 2),
        "ingresos_ejecutados": round(tot["ing_e"], 2),
        "cumplimiento_ingresos_pct": round(tot["ing_e"] / tot["ing_p"] * 100, 2) if tot["ing_p"] else None,
        "egresos_proyectados": round(tot["egr_p"], 2),
        "egresos_ejecutados": round(tot["egr_e"], 2),
        "ejecucion_egresos_pct": round(tot["egr_e"] / tot["egr_p"] * 100, 2) if tot["egr_p"] else None,
        "flujo_neto_proyectado": round(neto_p, 2),
        "flujo_neto_ejecutado": round(neto_e, 2),
        "desviacion_flujo_neto": round(neto_e - neto_p, 2),
        "ultimo_mes_ejecutado": ultimo_mes,
    }

    if neto_e < 0 <= neto_p:
        alertas.insert(0, (
            "El flujo de caja ejecutado del periodo es NEGATIVO "
            f"(${neto_e:,.0f}) cuando se proyectaba positivo (${neto_p:,.0f})."
        ))

    return AnalisisComparativo(
        presupuesto_id=pres.id, anio=pres.anio, mes=mes, alcance=alcance,
        lineas=lineas_out, resumen=resumen, alertas=alertas,
    )
