"""Historial durable de importaciones RADIAN (abrir/reprocesar/anular/descargar)."""

import io
import logging
from pathlib import Path

from flask import (
    flash, redirect, render_template,
    send_file, url_for,
)

from app import storage as store
from app import audit
from app.authz import require_permission
from app.web import session_store

from . import base, radian
from .base import (
    bp, KEY_RESULTADO,
)

logger = logging.getLogger(__name__)


_ESTADOS_IMPORTACION = {
    "procesando": ("Procesando", "pill"),
    "procesada":  ("Procesada",  "pill pill-ok"),
    "completada": ("Procesada",  "pill pill-ok"),   # legado
    "corregida":  ("Corregida",  "pill pill-info"),
    "exportada":  ("Exportada",  "pill pill-ok"),
    "error":      ("Error",      "pill pill-pendiente"),
    "anulada":    ("Anulada",    "pill pill-muted"),
}


@bp.route("/importaciones")
@require_permission("importaciones.ver")
def importaciones():
    """Lista las importaciones realizadas con su estado y acciones disponibles."""
    from app.database import inicializar_db, listar_importaciones

    db_path = base._empresa_actual().db_path
    inicializar_db(db_path)
    registros = listar_importaciones(db_path)

    for r in registros:
        r["fecha_fmt"] = (r.get("fecha") or "")[:19].replace("T", " ")
        r["archivo_disponible"] = bool(
            r.get("archivo_ref") and store.file_exists(r["archivo_ref"])
        )
        r["excel_disponible"] = bool(
            r.get("excel_ref") and store.file_exists(r["excel_ref"])
        )
        r["tiene_snapshot"] = bool(r.get("tiene_snapshot"))
        r["anulada"] = r.get("estado") == "anulada"
        etiqueta, clase = _ESTADOS_IMPORTACION.get(
            r.get("estado"), (r.get("estado") or "—", "pill")
        )
        r["estado_label"] = etiqueta
        r["estado_clase"] = clase

    return render_template("importaciones.html", importaciones=registros)


@bp.route("/importaciones/<int:imp_id>/abrir", methods=["POST"])
@require_permission("importaciones.gestionar")
def importacion_abrir(imp_id):
    """Abre una importación cargando su snapshot durable en la sesión de trabajo.

    A diferencia de «Regenerar» (que reprocesa el archivo y pierde las
    correcciones manuales), «Abrir» recupera exactamente el estado guardado para
    seguir editándolo y exportarlo.
    """
    from app.database import inicializar_db, obtener_snapshot_importacion

    emp = base._empresa_actual()
    db = emp.db_path
    inicializar_db(db)

    snap = obtener_snapshot_importacion(imp_id, db_path=db)
    if not snap:
        flash("Esta importación no tiene un estado guardado para abrir. "
              "Usa «Regenerar» para reprocesar el archivo original.", "error")
        return redirect(url_for("web.importaciones"))

    snap["importacion_id"] = imp_id
    session_store.guardar(KEY_RESULTADO, snap)
    audit.registrar("importacion.abrir", empresa_id=emp.id, detalle=f"importacion={imp_id}")
    flash(f"✓ Importación #{imp_id} abierta. Puedes seguir editándola y exportar.",
          "success")
    return redirect(url_for("web.resultado"))


@bp.route("/importaciones/<int:imp_id>/anular", methods=["POST"])
@require_permission("importaciones.gestionar")
def importacion_anular(imp_id):
    """Marca una importación como anulada (descartada). No borra el histórico."""
    from app.database import (
        inicializar_db, obtener_importacion, actualizar_importacion,
    )

    emp = base._empresa_actual()
    db = emp.db_path
    inicializar_db(db)

    imp = obtener_importacion(imp_id, db_path=db)
    if not imp:
        flash("La importación no existe.", "error")
        return redirect(url_for("web.importaciones"))

    actualizar_importacion(
        imp_id, estado="anulada",
        n_docs=int(imp.get("n_docs", 0) or 0),
        n_excepciones=int(imp.get("n_excepciones", 0) or 0),
        db_path=db,
    )
    audit.registrar("importacion.anular", empresa_id=emp.id, detalle=f"importacion={imp_id}")
    flash(f"Importación #{imp_id} anulada.", "info")
    return redirect(url_for("web.importaciones"))


@bp.route("/importaciones/<int:imp_id>/reprocesar", methods=["POST"])
@require_permission("importaciones.gestionar")
def importacion_reprocesar(imp_id):
    """Retoma una importación: re-ejecuta el pipeline con el RADIAN original.

    Sirve tanto para reintentar una importación fallida como para volver a
    generar el Excel de una importación completada. Los documentos ya
    registrados se incluyen de nuevo (no se duplican en la BD: el INSERT
    ignora CUFEs existentes).
    """
    from app.database import inicializar_db, obtener_importacion, actualizar_importacion

    emp = base._empresa_actual()
    db = emp.db_path
    inicializar_db(db)

    imp = obtener_importacion(imp_id, db_path=db)
    if not imp:
        flash("La importación no existe.", "error")
        return redirect(url_for("web.importaciones"))

    archivo_ref = imp.get("archivo_ref") or ""
    if not archivo_ref or not store.file_exists(archivo_ref):
        flash("El archivo RADIAN original ya no está disponible; "
              "vuelve a subirlo desde el dashboard.", "error")
        return redirect(url_for("web.importaciones"))

    radian_path = store.load_file(archivo_ref)
    terceros_path, cuentas_path, comprobantes_path = base._rutas_maestros_default(emp)

    try:
        # incluir_duplicados=True: los documentos de esta importación ya
        # están registrados en la BD y de lo contrario quedarían excluidos.
        resultado = radian._ejecutar_pipeline(
            radian_path, terceros_path, cuentas_path,
            comprobantes_path, db, incluir_duplicados=True, empresa=emp,
        )
        resultado["importacion_id"] = imp_id
        session_store.guardar(KEY_RESULTADO, resultado)
        radian._persistir_importacion(emp, resultado, "procesada")
        audit.registrar("importacion.reprocesar", empresa_id=emp.id,
                        detalle=f"importacion={imp_id} docs={resultado['n_docs']}")
        flash(f"✓ Importación #{imp_id} retomada: {resultado['n_docs']} documentos, "
              f"{resultado['n_excepciones']} con excepciones. Archivo regenerado.",
              "success")
        return redirect(url_for("web.resultado"))

    except Exception as exc:
        logger.exception("Error al retomar la importación %s", imp_id)
        actualizar_importacion(imp_id, estado="error", error=str(exc), db_path=db)
        flash(f"Error al retomar la importación: {exc}", "error")
        return redirect(url_for("web.importaciones"))


@bp.route("/importaciones/<int:imp_id>/descargar")
@require_permission("importaciones.ver")
def importacion_descargar(imp_id):
    """Descarga el Excel generado de una importación previa."""
    from app.database import inicializar_db, obtener_importacion

    db_path = base._empresa_actual().db_path
    inicializar_db(db_path)
    imp = obtener_importacion(imp_id, db_path=db_path)

    if not imp or not imp.get("excel_ref"):
        flash("Esta importación no tiene un Excel generado. "
              "Usa «Retomar» para generarlo.", "error")
        return redirect(url_for("web.importaciones"))

    excel_ref = imp["excel_ref"]
    if not store.file_exists(excel_ref):
        flash("El Excel ya no existe en el servidor. "
              "Usa «Retomar» para volver a generarlo.", "error")
        return redirect(url_for("web.importaciones"))

    content = store.get_download_bytes(excel_ref)
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=Path(excel_ref.replace("blob://", "")).name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/importaciones/<int:imp_id>/descargar-original")
@require_permission("importaciones.ver")
def importacion_descargar_original(imp_id):
    """Descarga el archivo RADIAN original que se importó."""
    from app.database import inicializar_db, obtener_importacion

    db_path = base._empresa_actual().db_path
    inicializar_db(db_path)
    imp = obtener_importacion(imp_id, db_path=db_path)

    archivo_ref = (imp or {}).get("archivo_ref") or ""
    if not imp or not archivo_ref or not store.file_exists(archivo_ref):
        flash("El archivo importado ya no está disponible en el servidor.", "error")
        return redirect(url_for("web.importaciones"))

    content = store.get_download_bytes(archivo_ref)
    download_name = imp.get("archivo_nombre") or Path(archivo_ref.replace("blob://", "")).name
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/importaciones/<int:imp_id>/descargar-siigo")
@require_permission("importaciones.ver")
def importacion_descargar_siigo(imp_id):
    """Regenera y descarga el archivo SIIGO de una importación desde su snapshot.

    Reutiliza el estado guardado (preasientos con las correcciones manuales) para
    producir el mismo Excel SIIGO que la pantalla de resultados, sin reprocesar.
    """
    from app.database import inicializar_db, obtener_snapshot_importacion

    emp = base._empresa_actual()
    db_path = emp.db_path
    inicializar_db(db_path)

    datos = obtener_snapshot_importacion(imp_id, db_path=db_path)
    if not datos or not datos.get("preasientos"):
        flash("Esta importación no tiene un estado guardado para exportar a SIIGO. "
              "Usa «Regenerar» para reprocesar el archivo original.", "error")
        return redirect(url_for("web.importaciones"))

    try:
        rutas = radian._generar_archivos_siigo(datos)
    except Exception as exc:
        logger.exception("Error generando archivo SIIGO de la importación %s", imp_id)
        flash(f"Error al generar el archivo SIIGO: {exc}", "error")
        return redirect(url_for("web.importaciones"))

    audit.registrar("importacion.descargar_siigo", empresa_id=emp.id,
                    detalle=f"importacion={imp_id} archivos={len(rutas)}")
    return base._enviar_archivos_siigo(rutas)


# ---------------------------------------------------------------------------
# POST /exportar-siigo — Generar archivo(s) SIIGO
# ---------------------------------------------------------------------------
