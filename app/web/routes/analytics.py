"""Vistas de analítica y del historial de cuentas aprendidas."""

import logging

from flask import (
    render_template,
)

from app.authz import require_permission

from . import base
from .base import (
    bp,
)

logger = logging.getLogger(__name__)


@bp.route("/historial")
@require_permission("ml.ver")
def historial():
    """Muestra las cuentas aprendidas por el motor de sugerencias."""
    from app.database import inicializar_db, listar_historial_cuentas

    db_path = base._empresa_actual().db_path
    inicializar_db(db_path)
    entradas, total = listar_historial_cuentas(db_path, limite=200)

    return render_template("historial.html", entradas=entradas, total=total)


@bp.route("/analytics")
@require_permission("analitica.ver")
def analytics():
    """Dashboard de reportería y analytics contable."""
    from app.database import (
        obtener_kpis, obtener_evolucion_mensual,
        obtener_distribucion_clasificacion,
        obtener_top_terceros, obtener_actividad_reciente,
    )

    from app.database import inicializar_db
    db_path = base._empresa_actual().db_path
    inicializar_db(db_path)

    kpis          = obtener_kpis(db_path)
    evolucion     = obtener_evolucion_mensual(db_path, meses=12)
    distribucion  = obtener_distribucion_clasificacion(db_path)
    top_proveed   = obtener_top_terceros(db_path, limite=10, tipo="compra")
    top_clientes  = obtener_top_terceros(db_path, limite=10, tipo="venta")
    actividad     = obtener_actividad_reciente(db_path, limite=30)

    # Serializar para Chart.js
    charts = {
        "evolucion": {
            "labels":         [r["mes"] for r in evolucion],
            "ventas_monto":   [round(r["ventas_monto"],  2) for r in evolucion],
            "compras_monto":  [round(r["compras_monto"], 2) for r in evolucion],
            "otros_monto":    [round(r["otros_monto"],   2) for r in evolucion],
            "ventas_count":   [r["ventas_count"]  for r in evolucion],
            "compras_count":  [r["compras_count"] for r in evolucion],
        },
        "distribucion": {
            # clasificacion es nullable: documentos sin clasificar caen en "Sin clasificar".
            "labels": [(r["clasificacion"] or "Sin clasificar").replace("_", " ") for r in distribucion],
            "counts": [r["count"] for r in distribucion],
            "montos": [round(r["monto"], 2) for r in distribucion],
        },
        "top_proveed": {
            # nombre puede venir vacío aunque el NIT exista: usar el NIT como respaldo.
            "labels": [(r["nombre"] or r["nit"] or "—")[:25] for r in top_proveed],
            "montos": [round(r["monto"], 2) for r in top_proveed],
            "counts": [r["count"] for r in top_proveed],
        },
        "top_clientes": {
            "labels": [(r["nombre"] or r["nit"] or "—")[:25] for r in top_clientes],
            "montos": [round(r["monto"], 2) for r in top_clientes],
            "counts": [r["count"] for r in top_clientes],
        },
    }

    return render_template(
        "analytics.html",
        kpis=kpis,
        actividad=actividad,
        charts=charts,
    )
