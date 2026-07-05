"""RADIAN automático: descarga diaria desde el portal de la DIAN."""

import logging

from flask import (
    flash, redirect, render_template,
    request, url_for,
)

from app import storage as store
from app import audit
from app.authz import require_permission
from app.web import session_store, csrf

from . import base, radian
from .base import (
    bp, KEY_RESULTADO,
)

logger = logging.getLogger(__name__)


def _dian_form_to_config(form, actual: dict) -> dict:
    """Construye el dict ``dian_config`` desde el formulario.

    Conserva la contraseña guardada si el campo llega vacío (para no borrarla al
    editar el resto de los datos) y normaliza los enteros con defaults seguros.
    """
    def _int(nombre, default):
        try:
            return int(form.get(nombre) or default)
        except (TypeError, ValueError):
            return default

    pwd_form = form.get("email_password", "").strip()
    return {
        "habilitado": form.get("habilitado") == "on",
        "tipo_identificacion": form.get("tipo_identificacion", "").strip() or "13",
        "nit_representante": form.get("nit_representante", "").strip(),
        "nit_empresa": form.get("nit_empresa", "").strip(),
        "email_user": form.get("email_user", "").strip(),
        # Si el campo viene vacío, se conserva la contraseña ya almacenada.
        "email_password": pwd_form or (actual or {}).get("email_password", ""),
        "imap_host": form.get("imap_host", "").strip(),
        "imap_port": _int("imap_port", 0),
        "email_carpeta": form.get("email_carpeta", "").strip() or "INBOX",
        "hora": form.get("hora", "").strip(),
        "dias_atras": max(0, _int("dias_atras", 1)),
        "login_path": form.get("login_path", "").strip(),
        "descarga_path": form.get("descarga_path", "").strip(),
    }


@bp.route("/radian/auto")
@require_permission("radian.auto")
def radian_auto():
    """Página de configuración y estado de la importación automática de RADIAN."""
    from app.config import RADIAN_SCHEDULER_ENABLED, RADIAN_CRON_TOKEN
    from app.radian_auto.config_dian import TIPOS_IDENTIFICACION

    emp = base._empresa_actual()
    dcfg = emp.dian()
    return render_template(
        "radian_auto.html",
        dcfg=dcfg,
        tipos_identificacion=TIPOS_IDENTIFICACION,
        scheduler_activo=RADIAN_SCHEDULER_ENABLED,
        cron_activo=bool(RADIAN_CRON_TOKEN),
        nit_empresa_efectivo=dcfg.nit_empresa_efectivo(emp),
        faltantes=dcfg.faltantes(),
        actividad=radian._actividad_radian(emp),
    )


@bp.route("/radian/auto/guardar", methods=["POST"])
@require_permission("radian.auto")
def radian_auto_guardar():
    """Guarda la configuración de importación automática de la empresa."""
    from app.empresas import guardar_dian_config

    emp = base._empresa_actual()
    nueva = _dian_form_to_config(request.form, emp.dian_config)
    guardar_dian_config(emp.id, nueva)
    audit.registrar(
        "radian.auto.guardar", empresa_id=emp.id,
        detalle=f"habilitado={nueva['habilitado']} hora={nueva['hora'] or 'default'}",
    )
    flash("✓ Configuración de importación automática guardada.", "success")
    return redirect(url_for("web.radian_auto"))


@bp.route("/radian/auto/solicitar", methods=["POST"])
@require_permission("radian.auto")
def radian_auto_solicitar():
    """Pide a la DIAN que envíe el token al correo (flujo manual: paso 1)."""
    from app.radian_auto.auto_importador import solicitar_token
    from app.radian_auto.dian_client import DianError

    emp = base._empresa_actual()
    try:
        solicitar_token(emp)
    except DianError as exc:
        flash(f"No se pudo solicitar el token automáticamente: {exc} "
              "Puedes ingresar al portal de la DIAN y traer tú el enlace.", "error")
        return redirect(url_for("web.radian_auto"))

    audit.registrar("radian.auto.solicitar", empresa_id=emp.id)
    flash("✓ Token solicitado. Revisa el correo del representante legal y pega "
          "abajo el enlace de acceso (es válido 60 minutos).", "info")
    return redirect(url_for("web.radian_auto"))


@bp.route("/radian/auto/procesar-enlace", methods=["POST"])
@require_permission("radian.auto")
def radian_auto_procesar_enlace():
    """Descarga y procesa el reporte a partir del enlace de acceso pegado (paso 2).

    Reutiliza el mismo pipeline que la carga manual: deja el resultado en la
    sesión y abre la pantalla de resultados (editable y exportable a SIIGO).
    """
    from app.database import inicializar_db, registrar_importacion, actualizar_importacion
    from app.radian_auto.auto_importador import descargar_con_enlace
    from app.radian_auto.dian_client import DianError

    emp = base._empresa_actual()
    auth_url = request.form.get("auth_url", "").strip()
    incluir_dup = request.form.get("incluir_duplicados") == "on"
    if not auth_url:
        flash("Pega el enlace de acceso que la DIAN envió al correo.", "error")
        return redirect(url_for("web.radian_auto"))

    try:
        archivo_ref, nombre, _ = descargar_con_enlace(emp, auth_url)
    except DianError as exc:
        flash(f"No se pudo descargar el reporte de la DIAN: {exc}", "error")
        return redirect(url_for("web.radian_auto"))

    db = emp.db_path
    inicializar_db(db)
    imp_id = registrar_importacion(archivo_nombre=nombre, archivo_ref=archivo_ref, db_path=db)
    try:
        radian_path = store.load_file(archivo_ref)
        terceros, cuentas, comprobantes = base._rutas_maestros_default(emp)
        resultado = radian._ejecutar_pipeline(
            radian_path, terceros, cuentas, comprobantes,
            db, incluir_dup, empresa=emp,
        )
        resultado["importacion_id"] = imp_id
        session_store.guardar(KEY_RESULTADO, resultado)
        radian._persistir_importacion(emp, resultado, "procesada")
        audit.registrar(
            "radian.auto.enlace", empresa_id=emp.id,
            detalle=f"importacion={imp_id} docs={resultado['n_docs']}",
        )
        flash(f"✓ Procesados {resultado['n_docs']} documentos. "
              f"{resultado['n_excepciones']} con excepciones.", "success")
        return redirect(url_for("web.resultado"))
    except Exception as exc:
        logger.exception("Error procesando el reporte RADIAN por enlace")
        actualizar_importacion(imp_id, estado="error", error=str(exc), db_path=db)
        flash(f"Error al procesar: {exc}. El archivo quedó guardado: puedes "
              "retomar esta importación desde «Importaciones».", "error")
        return redirect(url_for("web.importaciones"))


@bp.route("/radian/auto/ejecutar", methods=["POST"])
@require_permission("radian.auto")
def radian_auto_ejecutar():
    """Dispara una importación automática inmediata (en segundo plano).

    El proceso puede tardar varios minutos (espera el correo del token de la
    DIAN), por lo que corre en un hilo y el resultado aparece en «Importaciones».
    """
    import threading

    emp = base._empresa_actual()
    dcfg = emp.dian()
    if not dcfg.configurado():
        flash("Faltan datos para la importación automática: "
              + ", ".join(dcfg.faltantes()) + ".", "error")
        return redirect(url_for("web.radian_auto"))

    def _correr(empresa):
        from app.radian_auto.auto_importador import importar_empresa
        try:
            importar_empresa(empresa)
        except Exception:
            logger.exception("Error en importación automática manual de %s", empresa.id)

    threading.Thread(target=_correr, args=(emp,), daemon=True).start()
    audit.registrar("radian.auto.ejecutar", empresa_id=emp.id)
    flash("⏳ Importación automática iniciada. La DIAN enviará el token al correo "
          "configurado; el resultado aparecerá en «Importaciones» en unos minutos.",
          "info")
    return redirect(url_for("web.importaciones"))


@bp.route("/radian/auto/cron", methods=["POST"])
@csrf.exempt
def radian_auto_cron():
    """Endpoint para un programador externo (Azure Scheduler, cron, GitHub Action).

    Protegido por el token compartido ``RADIAN_CRON_TOKEN`` (cabecera
    ``X-Radian-Token`` o parámetro ``token``). Lanza la importación de todas las
    empresas habilitadas en segundo plano y responde de inmediato.
    """
    from flask import jsonify
    from app.config import RADIAN_CRON_TOKEN

    if not RADIAN_CRON_TOKEN:
        return jsonify({"error": "cron deshabilitado"}), 404

    enviado = request.headers.get("X-Radian-Token") or request.values.get("token", "")
    if enviado != RADIAN_CRON_TOKEN:
        return jsonify({"error": "token inválido"}), 403

    import threading

    def _correr():
        from app.radian_auto.auto_importador import importar_todas
        try:
            importar_todas(solo_habilitadas=True)
        except Exception:
            logger.exception("Error en importación automática por cron")

    threading.Thread(target=_correr, name="radian-cron", daemon=True).start()
    audit.registrar("radian.auto.cron", detalle="disparo externo")
    return jsonify({"status": "started"}), 202


# ---------------------------------------------------------------------------
# POST /procesar — Ejecuta el pipeline completo
# ---------------------------------------------------------------------------
