"""Gestión de empresas: selección, CRUD y archivos maestros."""

import io
import logging

from flask import (
    flash, redirect, render_template,
    request, send_file, session, url_for,
)

from app import storage as store
from app.empresas import (
    obtener_empresa, crear_empresa, actualizar_empresa,
    eliminar_empresa, FORMATO_BANCO_DEFAULT,
)
from app import authn, audit, tenancy
from app.authz import require_permission
from app.web import session_store

from . import base
from .base import (
    bp, KEY_RESULTADO, KEY_BANCO, KEY_EMPRESA,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Empresas — selección y administración (multi-empresa)
# ---------------------------------------------------------------------------

@bp.route("/empresas")
@require_permission("empresas.ver")
def empresas():
    """Página de administración de empresas."""
    empresas_acc = tenancy.empresas_accesibles(authn.usuario_actual())
    return render_template(
        "empresas.html",
        formato_default=FORMATO_BANCO_DEFAULT,
        maestros_disponibles=base._maestros_disponibles(empresas_acc),
    )


@bp.route("/empresas/seleccionar", methods=["POST"])
@require_permission("empresas.ver")
def empresas_seleccionar():
    """Cambia la empresa activa de la sesión (solo a empresas accesibles)."""
    empresa_id = request.form.get("empresa_id", "").strip()
    emp = tenancy.seleccionar_empresa(empresa_id)
    if emp is None:
        # Selección de una empresa a la que el usuario no tiene acceso.
        audit.registrar("empresa.seleccionar", empresa_id=empresa_id,
                        detalle="acceso no autorizado", resultado="denegado")
        flash("No tienes acceso a esa empresa.", "error")
        return redirect(request.referrer or url_for("web.index"))

    # Los resultados en sesión pertenecen a la empresa anterior: descartarlos
    session_store.eliminar(KEY_RESULTADO)
    session_store.eliminar(KEY_BANCO)

    audit.registrar("empresa.seleccionar", empresa_id=emp.id)
    flash(f"Empresa activa: {emp.nombre} (NIT {emp.nit}).", "success")
    return redirect(request.referrer or url_for("web.index"))


def _parse_empresa_form() -> dict:
    """
    Lee y valida los campos del formulario de empresa (crear o editar).

    Retorna un dict con los campos listos para crear/actualizar una empresa.
    Lanza ValueError con un mensaje legible si algún dato es inválido.
    """
    import json as _json

    nombre = request.form.get("nombre", "").strip()
    nit    = request.form.get("nit", "").strip()
    sigla  = request.form.get("sigla", "").strip()
    if not nombre or not nit or not sigla:
        raise ValueError("Nombre, sigla y NIT son obligatorios.")

    # Formato del extracto bancario (solo guardar lo que difiere del default)
    formato_banco = {}
    for campo, default in FORMATO_BANCO_DEFAULT.items():
        valor = request.form.get(f"banco_{campo}", "").strip()
        if valor == "":
            continue
        if isinstance(default, int):
            try:
                valor = int(valor)
            except ValueError:
                raise ValueError(f"Valor inválido para {campo}: {valor}")
        if valor != default:
            formato_banco[campo] = valor

    # Cuentas contables de banco (lista; pueden ser varias)
    cuentas_banco = []
    codigos = request.form.getlist("cuenta_banco_cuenta")
    etiquetas = request.form.getlist("cuenta_banco_etiqueta")
    for i, codigo in enumerate(codigos):
        codigo = codigo.strip()
        if not codigo:
            continue
        etiqueta = etiquetas[i].strip() if i < len(etiquetas) else ""
        cuentas_banco.append({"cuenta": codigo, "etiqueta": etiqueta})

    # Bancos (lista; pueden ser varios)
    bancos = []
    nits_banco = request.form.getlist("banco_nit")
    nombres_banco = request.form.getlist("banco_nombre")
    for i, nit_b in enumerate(nits_banco):
        nit_b = nit_b.strip()
        if not nit_b:
            continue
        nombre_b = nombres_banco[i].strip() if i < len(nombres_banco) else ""
        bancos.append({"nit": nit_b, "nombre": nombre_b})

    # Overrides de cuentas contables (JSON opcional)
    def _json_dict(campo):
        raw = request.form.get(campo, "").strip()
        if not raw:
            return {}
        try:
            d = _json.loads(raw)
            if not isinstance(d, dict):
                raise ValueError
            return d
        except (ValueError, TypeError):
            raise ValueError(f"El campo {campo} debe ser un objeto JSON válido.")

    return {
        "nit": nit,
        "nombre": nombre,
        "sigla": sigla,
        # La cuenta/NIT único se derivan del primer elemento (compatibilidad).
        "cuenta_banco_default": cuentas_banco[0]["cuenta"] if cuentas_banco else "",
        "nit_banco": bancos[0]["nit"] if bancos else "",
        "cuentas_banco": cuentas_banco,
        "bancos": bancos,
        "formato_banco": formato_banco,
        "cuentas_contraparte": _json_dict("cuentas_contraparte"),
        "cuentas_impuestos": _json_dict("cuentas_impuestos"),
    }


@bp.route("/empresas/crear", methods=["POST"])
@require_permission("empresas.gestionar")
def empresas_crear():
    """Crea una empresa nueva con su configuración propia."""
    try:
        campos = _parse_empresa_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.empresas"))

    emp = crear_empresa(**campos)

    audit.registrar("empresa.crear", empresa_id=emp.id,
                    detalle=f"{emp.nombre} NIT={emp.nit}")
    flash(f"✓ Empresa '{emp.nombre}' ({emp.sigla_efectiva}) creada. "
          f"Sube sus archivos maestros en data/{emp.id}/ "
          f"o desde el formulario de procesamiento.", "success")
    return redirect(url_for("web.empresas"))


@bp.route("/empresas/<empresa_id>/editar")
@require_permission("empresas.gestionar")
def empresas_editar(empresa_id):
    """Muestra el formulario de edición pre-rellenado con la empresa indicada."""
    emp = obtener_empresa(empresa_id)
    empresas_acc = tenancy.empresas_accesibles(authn.usuario_actual())
    return render_template(
        "empresas.html",
        formato_default=FORMATO_BANCO_DEFAULT,
        empresa_editar=emp,
        maestros_disponibles=base._maestros_disponibles(empresas_acc),
    )


@bp.route("/empresas/<empresa_id>/actualizar", methods=["POST"])
@require_permission("empresas.gestionar")
def empresas_actualizar(empresa_id):
    """Guarda los cambios de datos y configuración de una empresa existente."""
    try:
        campos = _parse_empresa_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.empresas_editar", empresa_id=empresa_id))

    emp = actualizar_empresa(empresa_id, **campos)
    audit.registrar("empresa.actualizar", empresa_id=emp.id, detalle=emp.nombre)
    flash(f"✓ Empresa '{emp.nombre}' ({emp.sigla_efectiva}) actualizada.", "success")
    return redirect(url_for("web.empresas"))


@bp.route("/empresas/<empresa_id>/eliminar", methods=["POST"])
@require_permission("empresas.gestionar")
def empresas_eliminar(empresa_id):
    """Elimina una empresa del registro (la principal no se puede eliminar)."""
    try:
        eliminar_empresa(empresa_id)
        if session.get(KEY_EMPRESA) == empresa_id:
            session.pop(KEY_EMPRESA, None)
            session_store.eliminar(KEY_RESULTADO)
            session_store.eliminar(KEY_BANCO)
        audit.registrar("empresa.eliminar", empresa_id=empresa_id)
        flash("Empresa eliminada.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("web.empresas"))


@bp.route("/empresas/maestros", methods=["POST"])
@require_permission("empresas.gestionar")
def empresas_maestros():
    """Sube/reemplaza los archivos maestros de la empresa indicada.

    Antes de guardar, valida que cada archivo corresponda a su casilla (terceros,
    cuentas, comprobantes): si se subió uno en la casilla equivocada —p. ej. el
    Plan de Cuentas en «Terceros»— se rechaza con un mensaje claro y no se
    sobrescribe el maestro existente.
    """
    from app.maestros import validar_maestro

    emp = obtener_empresa(request.form.get("empresa_id", "").strip())
    subidos = []
    rechazados = []
    for key, default_name in base.MAESTROS_EMPRESA:
        f = request.files.get(key)
        if not (f and f.filename and base._allowed(f.filename)):
            continue
        contenido = f.read()
        error = validar_maestro(key, contenido)
        if error:
            rechazados.append(error)
            continue
        store.save_file(contenido, emp.data_category, default_name)
        subidos.append(default_name)

    if subidos:
        audit.registrar("empresa.maestros", empresa_id=emp.id,
                        detalle=", ".join(subidos))
        flash(f"✓ Maestros actualizados para {emp.nombre}: {', '.join(subidos)}", "success")
    for error in rechazados:
        flash(error, "error")
    if not subidos and not rechazados:
        flash("No se subió ningún archivo maestro.", "info")
    return redirect(url_for("web.empresas"))


@bp.route("/empresas/<empresa_id>/maestros/<tipo>/descargar")
@require_permission("empresas.gestionar")
def empresas_maestros_descargar(empresa_id, tipo):
    """Descarga el archivo maestro `tipo` de la empresa indicada."""
    filename = dict(base.MAESTROS_EMPRESA).get(tipo)
    if filename is None:
        flash("Tipo de archivo maestro no válido.", "error")
        return redirect(url_for("web.empresas"))

    emp = obtener_empresa(empresa_id)
    ref = base._ref_maestro(emp, filename)
    if not store.file_exists(ref):
        flash(f"{emp.nombre} no tiene cargado el archivo maestro solicitado.", "error")
        return redirect(url_for("web.empresas"))

    audit.registrar("empresa.maestros.descargar", empresa_id=emp.id, detalle=filename)
    content = store.get_download_bytes(ref)
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Usuarios y roles — administración (RBAC · Fase 3)
# ---------------------------------------------------------------------------
