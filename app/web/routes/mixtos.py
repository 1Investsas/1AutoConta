"""Módulo Flujos Mixtos: flujos de efectivo sin límite de período."""

import io
import logging
import os

from flask import (
    abort, flash, redirect, render_template,
    request, send_file, url_for,
)

from app import storage as store
from app import authn, audit
from app.authz import require_permission

from . import base, caja
from .base import (
    bp,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Flujos Mixtos — disponible en Flujos directos e indirectos de efectivo
# ═══════════════════════════════════════════════════════════════════════════
#
# Funciona igual que Caja General pero SIN límite de período mensual/anual: un
# «flujo» puede cubrir cualquier rango de fechas o correr de forma continua
# («de corrido»). Reutiliza el mismo modelo de dominio, la plantilla, el
# importador y el exportador SIIGO de Caja General; solo cambia el almacenamiento
# (tablas mixed_*) y que los movimientos no se validan contra un mes/año.

_TITULO_PLANTILLA_MIXTO = "FLUJOS MIXTOS — MOVIMIENTOS DE EFECTIVO"


def _resumen_flujo(emp, period: dict) -> dict:
    """Enriquece un flujo mixto con etiquetas legibles para las plantillas."""
    from app.caja.modelo_caja import ESTADOS_CAJA, ESTADOS_EDITABLES
    estado = period.get("status", "borrador")
    period = dict(period)
    period["estado_label"] = ESTADOS_CAJA.get(estado, estado)
    period["editable"] = estado in ESTADOS_EDITABLES
    # Rango legible del flujo (informativo; no restringe las fechas).
    ini, fin = period.get("start_date") or "", period.get("end_date") or ""
    if ini and fin:
        period["rango_texto"] = f"{ini} a {fin}"
    elif ini:
        period["rango_texto"] = f"desde {ini}"
    elif fin:
        period["rango_texto"] = f"hasta {fin}"
    else:
        period["rango_texto"] = "sin fechas (continuo)"
    return period


# ---------------------------------------------------------------------------
# GET /flujos-mixtos — Página inicial: cuentas + actividad
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos")
@require_permission("mixto.ver")
def flujos_mixtos():
    """Lista las cuentas de flujos mixtos de la empresa y los flujos recientes."""
    from app.database import listar_mixed_accounts, listar_mixed_periods

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    cuentas = listar_mixed_accounts(db_path, incluir_inactivas=True)

    recientes = []
    for c in cuentas:
        flujos = listar_mixed_periods(c["id"], db_path)
        c["n_periodos"] = len(flujos)
        for p in flujos[:3]:
            p = _resumen_flujo(emp, p)
            p["cuenta_nombre"] = c["name"]
            recientes.append(p)
    recientes = recientes[:8]

    return render_template(
        "mixto.html",
        cuentas=cuentas,
        recientes=recientes,
    )


# ---------------------------------------------------------------------------
# POST /flujos-mixtos/cuenta — Crear cuenta
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/cuenta", methods=["POST"])
@require_permission("mixto.gestionar")
def mixto_cuenta_crear():
    """Crea una cuenta de flujos mixtos, asociada a una cuenta contable."""
    from app.database import crear_mixed_account

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)

    nombre = request.form.get("name", "").strip()
    if not nombre:
        flash("El nombre de la cuenta es obligatorio.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    account_code = request.form.get("account_code", "").strip()
    if not account_code:
        flash("Debes asociar la cuenta a una cuenta contable del plan de cuentas.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    account_name, encontrada, maestro_ok = caja._resolver_cuenta_contable(emp, account_code)
    if maestro_ok and not encontrada:
        flash(f"La cuenta contable {account_code} no se encontró en el plan de "
              f"cuentas. Verifica el código en el maestro.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    acc_id = crear_mixed_account(
        name=nombre,
        description=request.form.get("description", "").strip(),
        currency=request.form.get("currency", "COP").strip() or "COP",
        responsible=request.form.get("responsible", "").strip(),
        account_code=account_code,
        account_name=account_name,
        db_path=db_path,
    )
    audit.registrar("mixto.cuenta_crear", empresa_id=emp.id,
                    detalle=f"cuenta={acc_id} nombre={nombre} contable={account_code}")
    flash(f"Cuenta de flujos mixtos «{nombre}» creada (cuenta contable {account_code}).",
          "success")
    return redirect(url_for("web.mixto_cuenta", account_id=acc_id))


# ---------------------------------------------------------------------------
# GET /flujos-mixtos/cuenta/<id> — Flujos de una cuenta
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/cuenta/<int:account_id>")
@require_permission("mixto.ver")
def mixto_cuenta(account_id):
    """Muestra los flujos (períodos libres) de una cuenta de flujos mixtos."""
    from app.database import obtener_mixed_account, listar_mixed_periods

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    cuenta = obtener_mixed_account(account_id, db_path)
    if not cuenta:
        flash("La cuenta de flujos mixtos no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    flujos = [_resumen_flujo(emp, p) for p in listar_mixed_periods(account_id, db_path)]
    saldo_sugerido = flujos[0]["closing_balance"] if flujos else "0"

    return render_template(
        "mixto_cuenta.html",
        cuenta=cuenta,
        flujos=flujos,
        saldo_sugerido=saldo_sugerido,
    )


# ---------------------------------------------------------------------------
# POST /flujos-mixtos/cuenta/<id>/flujo — Crear flujo (período libre)
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/cuenta/<int:account_id>/flujo", methods=["POST"])
@require_permission("mixto.procesar")
def mixto_flujo_crear(account_id):
    """Crea un flujo con nombre y rango de fechas opcional (sin límite de mes/año)."""
    from app.database import obtener_mixed_account, crear_mixed_period
    from app.caja.modelo_caja import a_decimal

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    cuenta = obtener_mixed_account(account_id, db_path)
    if not cuenta:
        flash("La cuenta de flujos mixtos no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    nombre = request.form.get("name", "").strip() or "Flujo continuo"
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    if start_date and end_date and end_date < start_date:
        flash("La fecha final no puede ser anterior a la inicial.", "error")
        return redirect(url_for("web.mixto_cuenta", account_id=account_id))

    saldo_inicial = str(a_decimal(request.form.get("opening_balance", "0")))
    responsable = request.form.get("responsible", "").strip() or cuenta.get("responsible", "")

    period_id = crear_mixed_period(
        mixed_account_id=account_id, name=nombre,
        start_date=start_date, end_date=end_date,
        opening_balance=saldo_inicial, responsible=responsable,
        created_by=caja._usuario_email(), db_path=db_path,
    )
    audit.registrar("mixto.flujo_crear", empresa_id=emp.id,
                    detalle=f"flujo={period_id} cuenta={account_id} {nombre}")
    flash("Flujo creado. Ya puedes registrar movimientos sin límite de período.",
          "success")
    return redirect(url_for("web.mixto_flujo", period_id=period_id))


# ---------------------------------------------------------------------------
# GET /flujos-mixtos/flujo/<id> — Hoja de trabajo del flujo
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/flujo/<int:period_id>")
@require_permission("mixto.ver")
def mixto_flujo(period_id):
    """Hoja de trabajo: encabezado + tabla editable de movimientos."""
    from app.database import (
        obtener_mixed_period, obtener_mixed_account, listar_mixed_movements,
    )

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    period = obtener_mixed_period(period_id, db_path)
    if not period:
        flash("El flujo no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    cuenta = obtener_mixed_account(period["mixed_account_id"], db_path)
    movimientos = listar_mixed_movements(period_id, db_path)
    period = _resumen_flujo(emp, period)

    from app.caja.modelo_caja import (
        COMPROBANTES_CAJA, COMP_INGRESO, COMP_EGRESO, comprobante_label,
    )
    comprobantes = [
        {"codigo": c, "label": comprobante_label(c)} for c in COMPROBANTES_CAJA
    ]

    return render_template(
        "mixto_flujo.html",
        period=period,
        cuenta=cuenta,
        movimientos=movimientos,
        comprobantes=comprobantes,
        comp_ingreso=COMP_INGRESO,
        comp_egreso=COMP_EGRESO,
    )


# ---------------------------------------------------------------------------
# POST /flujos-mixtos/flujo/<id>/guardar — Guardar movimientos (borrador)
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/flujo/<int:period_id>/guardar", methods=["POST"])
@require_permission("mixto.procesar")
def mixto_flujo_guardar(period_id):
    """Guarda la tabla completa de movimientos, recalculando el saldo."""
    import json
    from app.database import (
        obtener_mixed_period, reemplazar_mixed_movements,
        actualizar_mixed_period_saldos,
    )
    from app.caja import modelo_caja as mc

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    period = obtener_mixed_period(period_id, db_path)
    if not period:
        flash("El flujo no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    if period["status"] not in mc.ESTADOS_EDITABLES:
        flash("Este flujo está cerrado o aprobado. Solicita su reapertura para "
              "modificarlo.", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    try:
        crudos = json.loads(request.form.get("movimientos_json", "[]"))
    except (ValueError, TypeError):
        crudos = []

    saldo_inicial = mc.a_decimal(request.form.get("opening_balance", period["opening_balance"]))

    movimientos = [mc.desde_dict(d) for d in crudos]
    movimientos = [
        m for m in movimientos
        if (m.concept or m.inflow_amount or m.outflow_amount
            or m.movement_date or m.third_party_nit or m.third_party_name)
    ]

    ordenados = mc.recalcular_saldos(movimientos, saldo_inicial)
    mc.renumerar(ordenados)
    entradas, salidas = mc.totales(ordenados)
    cierre = mc.saldo_final(saldo_inicial, ordenados)

    reemplazar_mixed_movements(
        period_id, [mc.a_dict(m) for m in ordenados], db_path,
    )
    actualizar_mixed_period_saldos(
        period_id, str(saldo_inicial), str(entradas), str(salidas), str(cierre),
        db_path,
    )
    audit.registrar("mixto.guardar", empresa_id=emp.id,
                    detalle=f"flujo={period_id} movimientos={len(ordenados)}")
    caja._aprender_de_movimientos_caja(emp, ordenados)

    if any(m.running_balance < 0 for m in ordenados):
        flash("Advertencia: hay movimientos que generan saldo negativo. "
              "Verifica las salidas de efectivo.", "error")
    # Validación sin período: no se restringe la fecha a un mes/año.
    n_invalidos = sum(1 for m in ordenados if mc.validar_movimiento(m))
    if n_invalidos:
        flash(f"Se guardaron {len(ordenados)} movimientos. {n_invalidos} tienen "
              f"datos incompletos o inconsistentes (revisa las filas marcadas).",
              "error")
    else:
        flash(f"Avance guardado: {len(ordenados)} movimientos. "
              f"Saldo final {cierre:,.0f}.", "success")
    return redirect(url_for("web.mixto_flujo", period_id=period_id))


# ---------------------------------------------------------------------------
# POST /flujos-mixtos/flujo/<id>/estado/<accion> — Transiciones de estado
# ---------------------------------------------------------------------------

# accion → (permiso requerido, estados de origen permitidos, estado destino)
_TRANSICIONES_MIXTO = {
    "enviar-revision": ("mixto.procesar", ("borrador", "reabierto"), "en_revision"),
    "aprobar":         ("mixto.aprobar",  ("en_revision",),          "aprobado"),
    "devolver":        ("mixto.aprobar",  ("en_revision", "aprobado"), "borrador"),
    "cerrar":          ("mixto.cerrar",   ("borrador", "en_revision", "aprobado", "reabierto"), "cerrado"),
    "reabrir":         ("mixto.cerrar",   ("cerrado",),              "reabierto"),
}


@bp.route("/flujos-mixtos/flujo/<int:period_id>/estado/<accion>", methods=["POST"])
def mixto_flujo_estado(period_id, accion):
    """Cambia el estado de un flujo aplicando el permiso de la transición."""
    from app.database import (
        obtener_mixed_period, actualizar_mixed_period_estado,
        listar_mixed_movements, actualizar_mixed_period_saldos,
    )
    from app.caja import modelo_caja as mc
    from app.authz import tiene_permiso
    from datetime import datetime as _dt

    trans = _TRANSICIONES_MIXTO.get(accion)
    if not trans:
        abort(404)
    permiso, origenes, destino = trans

    usuario = authn.usuario_actual()
    if usuario is None:
        return authn.redirigir_login()

    emp = base._empresa_actual()
    if not tiene_permiso(usuario, emp.id, permiso):
        audit.registrar("permiso.denegado", empresa_id=emp.id,
                        detalle=f"{permiso} · flujos mixtos {accion}", resultado="denegado")
        abort(403)

    db_path = caja._caja_db(emp)
    period = obtener_mixed_period(period_id, db_path)
    if not period:
        flash("El flujo no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    if period["status"] not in origenes:
        flash("La acción no es válida para el estado actual del flujo.", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    extra = {}
    if destino == "aprobado":
        extra["approved_by"] = caja._usuario_email()
    elif destino == "cerrado":
        movs = [mc.desde_dict(m) for m in listar_mixed_movements(period_id, db_path)]
        entradas, salidas = mc.totales(movs)
        cierre = mc.saldo_final(period["opening_balance"], movs)
        actualizar_mixed_period_saldos(
            period_id, str(mc.a_decimal(period["opening_balance"])),
            str(entradas), str(salidas), str(cierre), db_path,
        )
        extra["closed_by"] = caja._usuario_email()
        extra["closed_at"] = _dt.now().isoformat()

    actualizar_mixed_period_estado(period_id, destino, db_path=db_path, **extra)
    audit.registrar(f"mixto.{accion}", empresa_id=emp.id,
                    detalle=f"flujo={period_id} → {destino}")

    etiquetas = {
        "en_revision": "enviado a revisión", "aprobado": "aprobado",
        "borrador": "devuelto a borrador", "cerrado": "cerrado",
        "reabierto": "reabierto",
    }
    flash(f"Flujo {etiquetas.get(destino, destino)}.", "success")
    return redirect(url_for("web.mixto_flujo", period_id=period_id))


# ---------------------------------------------------------------------------
# GET /flujos-mixtos/flujo/<id>/plantilla[-prediligenciada] — Descargas Excel
# ---------------------------------------------------------------------------


def _descargar_plantilla_mixto(period_id, prediligenciada: bool):
    """Genera y envía la plantilla Excel del flujo (vacía o prediligenciada)."""
    from app.database import (
        obtener_mixed_period, obtener_mixed_account, listar_mixed_movements,
    )
    from app.caja.plantilla_caja import generar_plantilla

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    period = obtener_mixed_period(period_id, db_path)
    if not period:
        flash("El flujo no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    cuenta = obtener_mixed_account(period["mixed_account_id"], db_path)
    movimientos = listar_mixed_movements(period_id, db_path) if prediligenciada else None

    # anio/mes van vacíos a propósito: el flujo no está atado a un mes/año.
    data = generar_plantilla(
        empresa=emp.nombre,
        cuenta_caja=(cuenta or {}).get("name", ""),
        cuenta_contable=(cuenta or {}).get("account_code", ""),
        cuenta_contable_nombre=(cuenta or {}).get("account_name", ""),
        anio=None, mes=None,
        saldo_inicial=period["opening_balance"],
        responsable=period.get("responsible", ""),
        movimientos=movimientos,
        terceros=caja._terceros_para_plantilla(emp),
        titulo=_TITULO_PLANTILLA_MIXTO,
    )
    sufijo = "prediligenciada" if prediligenciada else "vacia"
    nombre = f"flujos_mixtos_{period_id}_{sufijo}.xlsx"
    audit.registrar("mixto.descargar_plantilla", empresa_id=emp.id,
                    detalle=f"flujo={period_id} tipo={sufijo}")
    return send_file(
        io.BytesIO(data), as_attachment=True,
        download_name=nombre, mimetype=caja._MIME_XLSX,
    )


@bp.route("/flujos-mixtos/flujo/<int:period_id>/plantilla")
@require_permission("mixto.exportar")
def mixto_flujo_plantilla(period_id):
    """Descarga la plantilla Excel vacía para diligenciar a mano."""
    return _descargar_plantilla_mixto(period_id, prediligenciada=False)


@bp.route("/flujos-mixtos/flujo/<int:period_id>/plantilla-prediligenciada")
@require_permission("mixto.exportar")
def mixto_flujo_plantilla_pre(period_id):
    """Descarga la plantilla Excel prediligenciada con los movimientos registrados."""
    return _descargar_plantilla_mixto(period_id, prediligenciada=True)


# ---------------------------------------------------------------------------
# POST /flujos-mixtos/flujo/<id>/importar — Importar plantilla diligenciada
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/flujo/<int:period_id>/importar", methods=["POST"])
@require_permission("mixto.procesar")
def mixto_flujo_importar(period_id):
    """Importa una plantilla diligenciada: valida por fila y guarda si no hay errores."""
    from app.database import (
        obtener_mixed_period, reemplazar_mixed_movements,
        actualizar_mixed_period_saldos,
    )
    from app.caja import modelo_caja as mc
    from app.caja.importador_caja import importar_plantilla

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    period = obtener_mixed_period(period_id, db_path)
    if not period:
        flash("El flujo no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    if period["status"] not in mc.ESTADOS_EDITABLES:
        flash("Este flujo está cerrado o aprobado; no se puede importar.", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    if "archivo" not in request.files or request.files["archivo"].filename == "":
        flash("Selecciona el archivo Excel diligenciado.", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    archivo = request.files["archivo"]
    if not base._allowed(archivo.filename):
        flash("El archivo debe ser una plantilla de Excel (.xlsx).", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    ref = base._save_upload(archivo.read(), archivo.filename, emp)
    try:
        local = store.load_file(ref)
        res = importar_plantilla(local)
    except Exception as exc:
        logger.exception("Error leyendo la plantilla de flujos mixtos")
        flash(f"No se pudo leer el archivo: {exc}", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    if res.tiene_errores:
        for msg in res.errores_generales:
            flash(msg, "error")
        if res.errores_por_fila:
            detalle = "; ".join(
                f"fila {f}: {', '.join(errs)}"
                for f, errs in sorted(res.errores_por_fila.items())[:8]
            )
            flash(f"La plantilla tiene errores de validación. {detalle}", "error")
        audit.registrar("mixto.importar", empresa_id=emp.id, resultado="error",
                        detalle=f"flujo={period_id} errores={res.n_errores}")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    saldo_inicial = res.saldo_inicial
    ordenados = mc.recalcular_saldos(res.movimientos, saldo_inicial)
    mc.renumerar(ordenados)
    entradas, salidas = mc.totales(ordenados)
    cierre = mc.saldo_final(saldo_inicial, ordenados)

    reemplazar_mixed_movements(period_id, [mc.a_dict(m) for m in ordenados], db_path)
    actualizar_mixed_period_saldos(
        period_id, str(saldo_inicial), str(entradas), str(salidas), str(cierre),
        db_path,
    )
    audit.registrar("mixto.importar", empresa_id=emp.id,
                    detalle=f"flujo={period_id} movimientos={len(ordenados)}")
    caja._aprender_de_movimientos_caja(emp, ordenados)
    flash(f"Plantilla importada: {len(ordenados)} movimientos. "
          f"Saldo final {cierre:,.0f}.", "success")
    return redirect(url_for("web.mixto_flujo", period_id=period_id))


# ---------------------------------------------------------------------------
# POST /flujos-mixtos/flujo/<id>/exportar-siigo — Generar el Excel SIIGO
# ---------------------------------------------------------------------------


@bp.route("/flujos-mixtos/flujo/<int:period_id>/exportar-siigo", methods=["POST"])
@require_permission("mixto.exportar")
def mixto_flujo_exportar_siigo(period_id):
    """Genera el archivo Excel de importación SIIGO de los movimientos del flujo."""
    from app.database import (
        obtener_mixed_period, obtener_mixed_account, listar_mixed_movements,
    )
    from app.caja import modelo_caja as mc
    from app.caja.exportador_caja import exportar_caja_siigo
    from app.importador import cargar_maestro_cuentas

    emp = base._empresa_actual()
    db_path = caja._caja_db(emp)
    period = obtener_mixed_period(period_id, db_path)
    if not period:
        flash("El flujo no existe.", "error")
        return redirect(url_for("web.flujos_mixtos"))

    cuenta = obtener_mixed_account(period["mixed_account_id"], db_path)
    cuenta_caja = (cuenta or {}).get("account_code", "").strip()
    if not cuenta_caja:
        flash("La cuenta no tiene una cuenta contable asociada; no se puede "
              "generar el archivo SIIGO.", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    movimientos = [mc.desde_dict(m) for m in listar_mixed_movements(period_id, db_path)]
    if not any(abs(m.inflow_amount) > 0 or abs(m.outflow_amount) > 0 for m in movimientos):
        flash("No hay movimientos con valor para exportar a SIIGO.", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    try:
        df_cuentas = cargar_maestro_cuentas(
            emp.ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
        )
    except Exception:
        df_cuentas = None

    try:
        rutas = exportar_caja_siigo(
            movimientos, cuenta_caja,
            output_path=os.path.join(base._project_root(), "output"),
            df_cuentas=df_cuentas,
        )
    except Exception as exc:
        logger.exception("Error generando Excel SIIGO de flujos mixtos")
        flash(f"Error al generar el archivo SIIGO: {exc}", "error")
        return redirect(url_for("web.mixto_flujo", period_id=period_id))

    audit.registrar("mixto.exportar_siigo", empresa_id=emp.id,
                    detalle=f"flujo={period_id} archivos={len(rutas)}")
    return base._responder_descarga(
        base._enviar_archivos_siigo(rutas, zip_name="siigo_flujos_mixtos.zip")
    )
