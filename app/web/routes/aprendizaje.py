"""Machine learning: entrenamiento y sugerencias del motor de aprendizaje."""

import logging

from flask import (
    flash, redirect, render_template,
    request, url_for,
)
from werkzeug.utils import secure_filename

from app import storage as store
from app import authn, audit
from app.authz import require_permission

from . import base
from .base import (
    bp, ALLOWED_EXT, ALLOWED_EXT_CSV,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Machine learning — motor de aprendizaje generalizado
# ---------------------------------------------------------------------------

# Módulos que aceptan conocimiento externo (destino del entrenamiento).
_MODULOS_APRENDIZAJE = {
    "general": "General (todos los módulos)",
    "banco":   "Bancos",
    "caja":    "Caja / Flujos mixtos",
    "radian":  "RADIAN",
}


# Nombres legibles de los campos aprendidos (para la página).
_CAMPOS_APRENDIZAJE = {
    "cuenta":      "Cuenta contable",
    "nit_tercero": "NIT tercero",
}


@bp.route("/aprendizaje")
@require_permission("ml.ver")
def aprendizaje():
    """Centro de Machine learning: conocimiento aprendido y entrenamiento."""
    from app.database import (
        inicializar_db, estadisticas_aprendizaje, listar_patrones_aprendidos,
        listar_importaciones_conocimiento,
    )

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    modulo = request.args.get("modulo", "").strip() or None
    q = request.args.get("q", "").strip()

    stats = estadisticas_aprendizaje(emp.db_path)
    patrones = listar_patrones_aprendidos(emp.db_path, modulo=modulo,
                                          q=q, limite=200)
    entrenamientos = listar_importaciones_conocimiento(emp.db_path, limite=10)
    for e in entrenamientos:
        e["fecha_fmt"] = (e.get("fecha") or "")[:19].replace("T", " ")

    from app.authz import tiene_permiso
    puede_entrenar = tiene_permiso(authn.usuario_actual(), emp.id, "ml.entrenar")

    return render_template(
        "aprendizaje.html",
        stats=stats,
        patrones=patrones,
        entrenamientos=entrenamientos,
        modulos=_MODULOS_APRENDIZAJE,
        campos=_CAMPOS_APRENDIZAJE,
        filtro_modulo=modulo or "",
        filtro_q=q,
        puede_entrenar=puede_entrenar,
    )


@bp.route("/aprendizaje/entrenar", methods=["POST"])
@require_permission("ml.entrenar")
def aprendizaje_entrenar():
    """Entrena el motor con un archivo externo (SIIGO u otra fuente)."""
    from app.aprendizaje_importador import importar_conocimiento
    from app.database import inicializar_db, registrar_importacion_conocimiento

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    archivo = request.files.get("archivo")
    if not archivo or archivo.filename == "":
        flash("Selecciona el archivo con el que quieres entrenar.", "error")
        return redirect(url_for("web.aprendizaje"))

    nombre = archivo.filename
    permitidas = ALLOWED_EXT | ALLOWED_EXT_CSV
    if not ("." in nombre and nombre.rsplit(".", 1)[1].lower() in permitidas):
        flash("El archivo debe ser Excel (.xlsx, .xls) o CSV.", "error")
        return redirect(url_for("web.aprendizaje"))

    modulo = request.form.get("modulo", "general").strip()
    if modulo not in _MODULOS_APRENDIZAJE:
        modulo = "general"

    ref = base._save_upload(archivo.read(), nombre, emp)
    try:
        local = store.load_file(ref)
        resumen = importar_conocimiento(local, emp.db_path, modulo)
    except Exception as exc:
        logger.exception("Error entrenando con archivo externo")
        registrar_importacion_conocimiento(
            secure_filename(nombre), modulo, 0, 0,
            estado="error", detalle=str(exc), db_path=emp.db_path,
        )
        flash(f"No se pudo entrenar con el archivo: {exc}", "error")
        return redirect(url_for("web.aprendizaje"))

    registrar_importacion_conocimiento(
        secure_filename(nombre), modulo, resumen["filas"], resumen["aprendidos"],
        detalle=resumen["mensaje"], db_path=emp.db_path,
    )
    audit.registrar("ml.entrenar", empresa_id=emp.id,
                    detalle=f"archivo={secure_filename(nombre)} "
                            f"modulo={modulo} aprendidos={resumen['aprendidos']}")
    flash(f"✓ Entrenamiento completado. {resumen['mensaje']}", "success")
    return redirect(url_for("web.aprendizaje"))


@bp.route("/aprendizaje/patron/<int:patron_id>/eliminar", methods=["POST"])
@require_permission("ml.entrenar")
def aprendizaje_patron_eliminar(patron_id):
    """Elimina un patrón aprendido incorrecto."""
    from app.database import eliminar_patron_aprendido

    emp = base._empresa_actual()
    eliminar_patron_aprendido(patron_id, emp.db_path)
    audit.registrar("ml.eliminar_patron", empresa_id=emp.id,
                    detalle=f"patron={patron_id}")
    flash("Patrón eliminado. El sistema dejará de sugerir ese valor exacto.",
          "success")
    return redirect(url_for("web.aprendizaje"))


@bp.route("/api/aprendizaje/sugerir")
@require_permission("ml.ver")
def api_aprendizaje_sugerir():
    """Predice campos para un texto digitado (prediligenciamiento en vivo).

    Parámetros GET: `modulo` (banco/caja/radian/general), `texto` (lo digitado)
    y `campos` (lista separada por comas; por defecto 'cuenta,nit_tercero').
    Responde {campo: {valor, confianza, origen, modulo, usos}}.
    """
    from flask import jsonify
    from app import aprendizaje as motor
    from app.database import inicializar_db

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    modulo = request.args.get("modulo", "general").strip() or "general"
    texto = request.args.get("texto", "").strip()
    campos = [c.strip() for c in
              request.args.get("campos", "cuenta,nit_tercero").split(",")
              if c.strip()]
    if not texto or len(texto) < 3:
        return jsonify({})

    predicciones = motor.predecir_campos(modulo, texto, campos, emp.db_path)
    return jsonify({campo: pred.a_dict() for campo, pred in predicciones.items()})
