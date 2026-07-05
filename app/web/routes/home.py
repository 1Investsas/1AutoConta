"""Dashboard principal y páginas de categoría del menú."""

import logging

from flask import (
    abort, render_template,
)

from app.authz import require_permission
from app.web.navegacion import CATEGORIAS

from . import base
from .base import (
    bp,
)

logger = logging.getLogger(__name__)


@bp.route("/")
@require_permission("dashboard.ver")
def index():
    """Dashboard principal: estadísticas de la BD + formulario de upload."""
    from app.database import inicializar_db, obtener_resumen_dashboard

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)
    resumen = obtener_resumen_dashboard(emp.db_path)

    stats = {
        "total_docs": resumen["total_docs"],
        "ultimas": resumen["ultimas"],
        "ultima_fecha": (resumen["ultima_fecha"] or "")[:19].replace("T", " "),
        "total_historial": resumen["total_historial"],
    }

    return render_template("index.html", stats=stats)


# ---------------------------------------------------------------------------
# GET /modulos/<slug> — Página de categoría (submenú del sidebar)
# ---------------------------------------------------------------------------


@bp.route("/modulos/<slug>")
@require_permission("dashboard.ver")
def categoria(slug):
    """Página de una categoría: muestra como botones los módulos del submenú.

    El menú lateral llega solo hasta la categoría (Flujos directos, Empresas, …);
    esta página agrupa los módulos que la componen (Bancos, Caja general, …) y
    enlaza a cada uno. Cada módulo aplica su propio permiso al entrar.
    """
    cat = CATEGORIAS.get(slug)
    if not cat:
        abort(404)
    return render_template("categoria.html", categoria=cat)


# ---------------------------------------------------------------------------
# GET /radian — Página inicial del módulo RADIAN
# ---------------------------------------------------------------------------
