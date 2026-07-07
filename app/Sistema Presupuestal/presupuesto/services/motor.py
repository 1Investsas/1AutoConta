"""Motor de flujo de caja: construye la matriz mensual proyectado vs ejecutado
con subtotales por categoría, flujo neto y saldo acumulado de caja."""
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CategoriaPresupuesto, LineaPresupuesto, Presupuesto, TipoFlujo, TipoValor,
    ValorMensual,
)
from ..schemas import CategoriaFlujo, FlujoCaja, LineaFlujo


def _vector_valores(linea: LineaPresupuesto, tipo: TipoValor) -> list[float]:
    """Devuelve los 12 meses de una línea para un tipo de valor."""
    v = [0.0] * 12
    for val in linea.valores:
        if val.tipo == tipo and 1 <= val.mes <= 12:
            v[val.mes - 1] = val.valor
    return v


def _suma_vectores(vectores: list[list[float]]) -> list[float]:
    return [round(sum(vals), 2) for vals in zip(*vectores)] if vectores else [0.0] * 12


def obtener_presupuesto(db: Session, presupuesto_id: int) -> Presupuesto | None:
    return (
        db.query(Presupuesto)
        .options(
            selectinload(Presupuesto.categorias)
            .selectinload(CategoriaPresupuesto.lineas)
            .selectinload(LineaPresupuesto.valores)
        )
        .filter(Presupuesto.id == presupuesto_id)
        .first()
    )


def construir_flujo_caja(db: Session, presupuesto_id: int) -> FlujoCaja | None:
    pres = obtener_presupuesto(db, presupuesto_id)
    if pres is None:
        return None

    categorias_out: list[CategoriaFlujo] = []
    ingresos_p, ingresos_e = [[0.0] * 12], [[0.0] * 12]
    egresos_p, egresos_e = [[0.0] * 12], [[0.0] * 12]

    for cat in pres.categorias:
        lineas_out: list[LineaFlujo] = []
        for linea in cat.lineas:
            proy = _vector_valores(linea, TipoValor.PROYECTADO)
            ejec = _vector_valores(linea, TipoValor.EJECUTADO)
            lineas_out.append(LineaFlujo(
                linea_id=linea.id,
                nombre=linea.nombre,
                proyectado=proy,
                ejecutado=ejec,
                total_proyectado=round(sum(proy), 2),
                total_ejecutado=round(sum(ejec), 2),
            ))
        sub_p = _suma_vectores([l.proyectado for l in lineas_out])
        sub_e = _suma_vectores([l.ejecutado for l in lineas_out])
        categorias_out.append(CategoriaFlujo(
            categoria_id=cat.id, nombre=cat.nombre, tipo=cat.tipo,
            lineas=lineas_out,
            subtotal_proyectado=sub_p, subtotal_ejecutado=sub_e,
        ))
        if cat.tipo == TipoFlujo.INGRESO:
            ingresos_p.append(sub_p)
            ingresos_e.append(sub_e)
        else:
            egresos_p.append(sub_p)
            egresos_e.append(sub_e)

    ing_p, ing_e = _suma_vectores(ingresos_p), _suma_vectores(ingresos_e)
    egr_p, egr_e = _suma_vectores(egresos_p), _suma_vectores(egresos_e)
    neto_p = [round(i - e, 2) for i, e in zip(ing_p, egr_p)]
    neto_e = [round(i - e, 2) for i, e in zip(ing_e, egr_e)]

    def acumulado(neto: list[float]) -> list[float]:
        saldo, out = pres.saldo_inicial_caja, []
        for n in neto:
            saldo = round(saldo + n, 2)
            out.append(saldo)
        return out

    return FlujoCaja(
        presupuesto_id=pres.id,
        anio=pres.anio,
        saldo_inicial_caja=pres.saldo_inicial_caja,
        categorias=categorias_out,
        ingresos_proyectados=ing_p, ingresos_ejecutados=ing_e,
        egresos_proyectados=egr_p, egresos_ejecutados=egr_e,
        flujo_neto_proyectado=neto_p, flujo_neto_ejecutado=neto_e,
        saldo_acumulado_proyectado=acumulado(neto_p),
        saldo_acumulado_ejecutado=acumulado(neto_e),
    )


def guardar_valores(
    db: Session,
    tipo: TipoValor,
    fuente,
    valores: list,
) -> int:
    """Upsert masivo de valores mensuales. `valores` = [(linea_id, mes, valor)]."""
    n = 0
    for item in valores:
        linea_id, mes, valor = item.linea_id, item.mes, item.valor
        existente = (
            db.query(ValorMensual)
            .filter_by(linea_id=linea_id, mes=mes, tipo=tipo)
            .first()
        )
        if existente:
            existente.valor = valor
            existente.fuente = fuente
        else:
            db.add(ValorMensual(
                linea_id=linea_id, mes=mes, tipo=tipo, valor=valor, fuente=fuente,
            ))
        n += 1
    db.commit()
    return n
