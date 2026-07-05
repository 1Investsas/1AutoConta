"""Autocompletado: plan de cuentas y terceros (endpoints JSON)."""

import logging

from flask import (
    render_template,
    request,
)

from app.authz import require_permission

from . import base
from .base import (
    bp,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GET /cuentas — Consulta del plan de cuentas (buscador)
# ---------------------------------------------------------------------------

def _listar_cuentas(emp) -> list[dict]:
    """Lista todas las cuentas transaccionales activas de la empresa.

    Retorna una lista de ``{"codigo", "nombre"}`` ordenada por código, lista
    para mostrarla en el buscador del plan de cuentas. Usa el maestro cacheado.
    """
    from app.importador import cargar_maestro_cuentas

    cuentas_path = emp.ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
    df = base._cargar_maestro_cacheado(cargar_maestro_cuentas, cuentas_path)
    cod_col, nom_col = base._columnas_cuentas(df)

    out = []
    for _, row in df.iterrows():
        codigo = str(row[cod_col]).strip()
        if not codigo or codigo.lower() == "nan":
            continue
        nombre = str(row[nom_col]).strip() if nom_col else ""
        if nombre.lower() == "nan":
            nombre = ""
        out.append({"codigo": codigo, "nombre": nombre})

    out.sort(key=lambda c: c["codigo"])
    return out


@bp.route("/cuentas")
@require_permission("radian.ver")
def cuentas():
    """Página/ventana para consultar y buscar en el plan de cuentas.

    Sirve para encontrar fácilmente el código que se debe digitar en una casilla.
    Con ``?popup=1`` se renderiza una versión compacta (sin menú lateral) pensada
    para abrirse en una ventana emergente desde las pantallas de asignación.
    """
    emp = base._empresa_actual()
    error = None
    try:
        items = _listar_cuentas(emp)
    except FileNotFoundError:
        items = []
        error = ("Esta empresa no tiene cargado el plan de cuentas "
                 "(Listado_de_Cuentas_Contables.xlsx). Cárgalo en Empresas → Maestros.")
    except Exception:
        logger.exception("Error listando el plan de cuentas")
        items = []
        error = "No se pudo leer el plan de cuentas de la empresa."

    popup = request.args.get("popup", "") in ("1", "true", "yes", "on")
    return render_template("cuentas.html", cuentas=items, error=error, popup=popup)


# ---------------------------------------------------------------------------
# GET /api/cuentas — Autocompletar cuentas contables
# ---------------------------------------------------------------------------


@bp.route("/api/cuentas")
@require_permission("radian.ver")
def api_cuentas():
    """Retorna cuentas que coincidan con el query por código o nombre. Máx 15."""
    from flask import jsonify
    from app.importador import cargar_maestro_cuentas

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    try:
        cuentas_path = base._empresa_actual().ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
        df = base._cargar_maestro_cacheado(cargar_maestro_cuentas, cuentas_path)

        q_lower = q.lower()

        # Las primeras 2 columnas no-Unnamed son: código y nombre
        cod_col, nom_col = base._columnas_cuentas(df)

        codigos = df[cod_col].astype(str).str.strip()
        mask = codigos.str.lower().str.startswith(q_lower)
        if nom_col:
            mask |= df[nom_col].astype(str).str.lower().str.contains(q_lower, regex=False)

        cols = [cod_col, nom_col] if nom_col else [cod_col]
        resultados = df[mask][cols].head(15)

        out = [
            {
                "codigo": str(row[cod_col]).strip(),
                "nombre": str(row[nom_col]).strip() if nom_col else "",
            }
            for _, row in resultados.iterrows()
        ]
        return jsonify(out)
    except Exception as exc:
        logger.exception("Error en /api/cuentas")
        return jsonify([])



# ---------------------------------------------------------------------------
# GET /api/terceros — Autocompletar terceros por NIT o nombre
# ---------------------------------------------------------------------------


@bp.route("/api/terceros")
@require_permission("radian.ver")
def api_terceros():
    """Retorna terceros que coincidan con el query por NIT o nombre. Máx 15."""
    from flask import jsonify
    from app.importador import cargar_maestro_terceros

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    try:
        terceros_path = base._empresa_actual().ruta_maestro("Listado_de_Terceros.xlsx")
        df = base._cargar_maestro_cacheado(cargar_maestro_terceros, terceros_path)
    except Exception:
        return jsonify([])

    q_lower    = q.lower()
    col_nit    = "Identificación"
    col_nombre = "Nombre tercero"

    if col_nit not in df.columns:
        return jsonify([])

    nits = df[col_nit].astype(str).str.strip()
    mask = nits.str.lower().str.startswith(q_lower)

    if col_nombre in df.columns:
        mask |= df[col_nombre].astype(str).str.lower().str.contains(q_lower, regex=False)

    cols = [col_nit, col_nombre] if col_nombre in df.columns else [col_nit]
    resultados = df[mask][cols].head(15)

    out = [
        {
            "nit":    str(row[col_nit]).strip(),
            "nombre": str(row[col_nombre]).strip() if col_nombre in df.columns else "",
        }
        for _, row in resultados.iterrows()
    ]
    return jsonify(out)
