"""Módulo Caja General: cuentas, períodos, movimientos y plantillas."""

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

from . import base
from .base import (
    bp,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Caja General — Flujos directos de efectivo
# ═══════════════════════════════════════════════════════════════════════════
#
# A diferencia de Bancos (que importa un extracto externo), Caja General
# estructura el formato en el que el usuario diligencia los movimientos de
# efectivo. El módulo gestiona cuentas de caja, períodos mensuales con saldo
# inicial, movimientos de entrada/salida con saldo acumulado automático, estados
# del período (borrador → revisión → aprobado → cerrado → reabierto) y la
# plantilla Excel (vacía, prediligenciada e importación).

_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _caja_db(emp):
    """Inicializa (una vez) y retorna la ruta de BD de la empresa para caja."""
    from app.database import inicializar_db
    inicializar_db(emp.db_path)
    return emp.db_path


def _aprender_de_movimientos_caja(emp, movimientos) -> None:
    """Alimenta el motor de aprendizaje con los movimientos de efectivo guardados.

    Por cada movimiento con concepto se aprende (concepto → contrapartida) y
    (concepto → NIT del tercero), de modo que la próxima vez que se digite un
    concepto igual o similar el sistema prediligencie esos campos. Comparte el
    módulo 'caja' entre Caja General y Flujos Mixtos (mismo dominio: efectivo).
    Best-effort: un fallo aquí nunca rompe el guardado.
    """
    try:
        from app import aprendizaje

        observaciones = []
        for m in movimientos:
            concepto = (m.concept or "").strip()
            if not concepto:
                continue
            if m.contrapartida and not m.contrapartidas:
                observaciones.append({
                    "modulo": "caja", "campo": "cuenta",
                    "texto": concepto, "valor": m.contrapartida,
                })
            if m.third_party_nit:
                observaciones.append({
                    "modulo": "caja", "campo": "nit_tercero",
                    "texto": concepto, "valor": m.third_party_nit,
                })
        aprendizaje.aprender_lote(observaciones, emp.db_path)
    except Exception:
        logger.exception("No se pudo aprender de los movimientos de efectivo.")


def _usuario_email() -> str:
    """Email del usuario actual (para trazabilidad), o '' si no hay sesión."""
    u = authn.usuario_actual()
    return (u or {}).get("email", "") if u else ""


def _aplicar_pagos_cartera(emp, movimientos, referencia: str) -> None:
    """Abona en la Cartera los movimientos de efectivo que tienen tercero.

    Se invoca al **cerrar** un período de caja o un flujo mixto (un evento
    único, no en cada guardado): los ingresos abonan la cartera (CxC) del
    tercero y los egresos sus cuentas por pagar (CxP). Idempotente por
    ``referencia`` (reabrir y volver a cerrar no duplica abonos) y
    best-effort (un fallo no bloquea el cierre). También lo usa el módulo de
    Flujos Mixtos.
    """
    from app.database import aplicar_pagos_flujos_directos

    try:
        pagos = []
        for m in movimientos:
            if not m.third_party_nit:
                continue
            fecha = m.movement_date.isoformat() if m.movement_date else ""
            if m.inflow_amount and abs(m.inflow_amount) > 0:
                pagos.append({"nit": m.third_party_nit,
                              "valor": float(abs(m.inflow_amount)),
                              "sentido": "ingreso", "fecha": fecha,
                              "detalle": m.concept})
            if m.outflow_amount and abs(m.outflow_amount) > 0:
                pagos.append({"nit": m.third_party_nit,
                              "valor": float(abs(m.outflow_amount)),
                              "sentido": "egreso", "fecha": fecha,
                              "detalle": m.concept})
        if not pagos:
            return
        origen = referencia.split(":", 1)[0]
        res = aplicar_pagos_flujos_directos(pagos, origen, referencia, emp.db_path)
        if res["n_pagos"]:
            audit.registrar("cartera.pagos_aplicados", empresa_id=emp.id,
                            detalle=f"{referencia} obligaciones={res['n_pagos']} "
                                    f"valor={res['aplicado']}")
    except Exception:
        logger.exception("No se pudo actualizar la cartera desde %s", referencia)


def _resolver_cuenta_contable(emp, codigo: str) -> tuple[str, bool, bool]:
    """Resuelve el nombre de una cuenta contable por su código en el maestro.

    Retorna ``(nombre, encontrada, maestro_disponible)``:
    - encontrada=True: el código existe en el plan de cuentas; ``nombre`` es el
      nombre oficial.
    - maestro_disponible=False: no se pudo leer el plan de cuentas de la empresa
      (no se puede validar); el llamador decide aceptar el código tal cual.
    """
    from app.importador import cargar_maestro_cuentas

    codigo = (codigo or "").strip()
    if not codigo:
        return "", False, True
    try:
        path = emp.ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
        df = base._cargar_maestro_cacheado(cargar_maestro_cuentas, path)
    except Exception:
        logger.debug("Plan de cuentas no disponible para validar %s", codigo)
        return "", False, False

    try:
        cod_col, nom_col = base._columnas_cuentas(df)
        coincide = df[df[cod_col].astype(str).str.strip() == codigo]
        if not coincide.empty:
            nombre = str(coincide.iloc[0][nom_col]).strip() if nom_col else ""
            return nombre, True, True
    except Exception:
        logger.debug("No se pudo resolver la cuenta contable %s", codigo)
        return "", False, False
    return "", False, True


def _terceros_para_plantilla(emp, limite: int = 2000) -> list[dict]:
    """Lista de {'nit','nombre'} del maestro de terceros para la hoja auxiliar."""
    from app.importador import cargar_maestro_terceros
    try:
        path = emp.ruta_maestro("Listado_de_Terceros.xlsx")
        df = base._cargar_maestro_cacheado(cargar_maestro_terceros, path)
    except Exception:
        return []
    col_nit, col_nom = "Identificación", "Nombre tercero"
    if col_nit not in df.columns:
        return []
    out = []
    for _, row in df.head(limite).iterrows():
        nit = str(row[col_nit]).strip()
        if not nit:
            continue
        out.append({
            "nit": nit,
            "nombre": str(row[col_nom]).strip() if col_nom in df.columns else "",
        })
    return out


def _cargar_movimientos_caja(period_id: int, db_path: str) -> list[dict]:
    """Lee los movimientos del período y parsea la subdivisión de contrapartida.

    La BD guarda las partes de la contrapartida serializadas en
    ``contrapartidas_json``; aquí se convierten a la lista ``contrapartidas`` que
    esperan el modelo, la hoja de trabajo y el exportador SIIGO.
    """
    import json
    from app.database import listar_cash_movements

    filas = listar_cash_movements(period_id, db_path)
    for m in filas:
        crudo = m.get("contrapartidas_json")
        try:
            m["contrapartidas"] = json.loads(crudo) if crudo else []
        except (ValueError, TypeError):
            m["contrapartidas"] = []
    return filas


def _resumen_periodo(emp, period: dict) -> dict:
    """Enriquece un período con etiquetas legibles para las plantillas."""
    from app.caja.modelo_caja import ESTADOS_CAJA, ESTADOS_EDITABLES, MESES_ES
    estado = period.get("status", "borrador")
    period = dict(period)
    mes = int(period.get("month") or 0)
    period["mes_nombre"] = MESES_ES[mes] if 1 <= mes <= 12 else ""
    period["estado_label"] = ESTADOS_CAJA.get(estado, estado)
    period["editable"] = estado in ESTADOS_EDITABLES
    return period


# ---------------------------------------------------------------------------
# GET /caja — Página inicial: cuentas de caja + actividad
# ---------------------------------------------------------------------------


@bp.route("/caja")
@require_permission("caja.ver")
def caja():
    """Lista las cuentas de caja de la empresa y los períodos recientes."""
    from app.database import listar_cash_accounts, listar_cash_periods

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    cuentas = listar_cash_accounts(db_path, incluir_inactivas=True)

    # Conteo de períodos por cuenta + actividad reciente (períodos más nuevos).
    recientes = []
    for c in cuentas:
        periodos = listar_cash_periods(c["id"], db_path)
        c["n_periodos"] = len(periodos)
        for p in periodos[:3]:
            p = _resumen_periodo(emp, p)
            p["cuenta_nombre"] = c["name"]
            recientes.append(p)
    recientes.sort(key=lambda p: (p.get("year", 0), p.get("month", 0)), reverse=True)

    return render_template(
        "caja.html",
        cuentas=cuentas,
        recientes=recientes[:8],
    )


# ---------------------------------------------------------------------------
# POST /caja/cuenta — Crear cuenta de caja
# ---------------------------------------------------------------------------


@bp.route("/caja/cuenta", methods=["POST"])
@require_permission("caja.gestionar")
def caja_cuenta_crear():
    """Crea una cuenta de caja (caja menor o caja general)."""
    from app.database import crear_cash_account

    emp = base._empresa_actual()
    db_path = _caja_db(emp)

    nombre = request.form.get("name", "").strip()
    if not nombre:
        flash("El nombre de la cuenta de caja es obligatorio.", "error")
        return redirect(url_for("web.caja"))

    # La caja debe quedar asociada a una cuenta contable del maestro.
    account_code = request.form.get("account_code", "").strip()
    if not account_code:
        flash("Debes asociar la caja a una cuenta contable del plan de cuentas.", "error")
        return redirect(url_for("web.caja"))

    account_name, encontrada, maestro_ok = _resolver_cuenta_contable(emp, account_code)
    if maestro_ok and not encontrada:
        flash(f"La cuenta contable {account_code} no se encontró en el plan de "
              f"cuentas. Verifica el código en el maestro.", "error")
        return redirect(url_for("web.caja"))

    acc_id = crear_cash_account(
        name=nombre,
        description=request.form.get("description", "").strip(),
        currency=request.form.get("currency", "COP").strip() or "COP",
        responsible=request.form.get("responsible", "").strip(),
        account_code=account_code,
        account_name=account_name,
        db_path=db_path,
    )
    audit.registrar("caja.cuenta_crear", empresa_id=emp.id,
                    detalle=f"cuenta={acc_id} nombre={nombre} contable={account_code}")
    flash(f"Cuenta de caja «{nombre}» creada (cuenta contable {account_code}).", "success")
    return redirect(url_for("web.caja_cuenta", account_id=acc_id))


# ---------------------------------------------------------------------------
# GET /caja/cuenta/<id> — Períodos de una cuenta de caja
# ---------------------------------------------------------------------------


@bp.route("/caja/cuenta/<int:account_id>")
@require_permission("caja.ver")
def caja_cuenta(account_id):
    """Muestra los períodos mensuales de una cuenta de caja."""
    from app.database import obtener_cash_account, listar_cash_periods

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    cuenta = obtener_cash_account(account_id, db_path)
    if not cuenta:
        flash("La cuenta de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    periodos = [_resumen_periodo(emp, p) for p in listar_cash_periods(account_id, db_path)]

    # Saldo inicial sugerido para un período nuevo = cierre del más reciente.
    saldo_sugerido = periodos[0]["closing_balance"] if periodos else "0"
    from datetime import date as _date
    hoy = _date.today()

    return render_template(
        "caja_cuenta.html",
        cuenta=cuenta,
        periodos=periodos,
        saldo_sugerido=saldo_sugerido,
        anio_actual=hoy.year,
        mes_actual=hoy.month,
    )


# ---------------------------------------------------------------------------
# POST /caja/cuenta/<id>/periodo — Crear período mensual
# ---------------------------------------------------------------------------


@bp.route("/caja/cuenta/<int:account_id>/periodo", methods=["POST"])
@require_permission("caja.procesar")
def caja_periodo_crear(account_id):
    """Crea un período mensual de caja con su saldo inicial."""
    from app.database import (
        obtener_cash_account, crear_cash_period, obtener_cash_period_por_mes,
    )
    from app.caja.modelo_caja import a_decimal

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    cuenta = obtener_cash_account(account_id, db_path)
    if not cuenta:
        flash("La cuenta de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    try:
        anio = int(request.form.get("year", ""))
        mes = int(request.form.get("month", ""))
    except (TypeError, ValueError):
        flash("Selecciona un mes y año válidos.", "error")
        return redirect(url_for("web.caja_cuenta", account_id=account_id))

    if not (1 <= mes <= 12) or not (2000 <= anio <= 2100):
        flash("El mes y año del período no son válidos.", "error")
        return redirect(url_for("web.caja_cuenta", account_id=account_id))

    if obtener_cash_period_por_mes(account_id, anio, mes, db_path):
        flash("Ya existe un período para ese mes y año en esta cuenta.", "error")
        return redirect(url_for("web.caja_cuenta", account_id=account_id))

    saldo_inicial = str(a_decimal(request.form.get("opening_balance", "0")))
    responsable = request.form.get("responsible", "").strip() or cuenta.get("responsible", "")

    period_id = crear_cash_period(
        cash_account_id=account_id, year=anio, month=mes,
        opening_balance=saldo_inicial, responsible=responsable,
        created_by=_usuario_email(), db_path=db_path,
    )
    audit.registrar("caja.periodo_crear", empresa_id=emp.id,
                    detalle=f"periodo={period_id} cuenta={account_id} {mes:02d}/{anio}")
    flash("Período de caja creado. Ya puedes registrar movimientos.", "success")
    return redirect(url_for("web.caja_periodo", period_id=period_id))


# ---------------------------------------------------------------------------
# GET /caja/periodo/<id> — Hoja de trabajo del período
# ---------------------------------------------------------------------------


@bp.route("/caja/periodo/<int:period_id>")
@require_permission("caja.ver")
def caja_periodo(period_id):
    """Hoja de trabajo: encabezado + tabla editable de movimientos."""
    from app.database import (
        obtener_cash_period, obtener_cash_account,
    )

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    period = obtener_cash_period(period_id, db_path)
    if not period:
        flash("El período de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    cuenta = obtener_cash_account(period["cash_account_id"], db_path)
    movimientos = _cargar_movimientos_caja(period_id, db_path)
    period = _resumen_periodo(emp, period)

    from app.caja.modelo_caja import (
        COMPROBANTES_CAJA, COMP_INGRESO, COMP_EGRESO, comprobante_label,
    )
    comprobantes = [
        {"codigo": c, "label": comprobante_label(c)} for c in COMPROBANTES_CAJA
    ]

    return render_template(
        "caja_periodo.html",
        period=period,
        cuenta=cuenta,
        movimientos=movimientos,
        comprobantes=comprobantes,
        comp_ingreso=COMP_INGRESO,
        comp_egreso=COMP_EGRESO,
    )


# ---------------------------------------------------------------------------
# POST /caja/periodo/<id>/guardar — Guardar movimientos (borrador)
# ---------------------------------------------------------------------------


@bp.route("/caja/periodo/<int:period_id>/guardar", methods=["POST"])
@require_permission("caja.procesar")
def caja_periodo_guardar(period_id):
    """Guarda la tabla completa de movimientos, recalculando el saldo."""
    import json
    from app.database import (
        obtener_cash_period, reemplazar_cash_movements,
        actualizar_cash_period_saldos,
    )
    from app.caja import modelo_caja as mc

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    period = obtener_cash_period(period_id, db_path)
    if not period:
        flash("El período de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    if period["status"] not in mc.ESTADOS_EDITABLES:
        flash("Este período de caja está cerrado o aprobado. Solicita su "
              "reapertura para modificarlo.", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    try:
        crudos = json.loads(request.form.get("movimientos_json", "[]"))
    except (ValueError, TypeError):
        crudos = []

    saldo_inicial = mc.a_decimal(request.form.get("opening_balance", period["opening_balance"]))

    movimientos = [mc.desde_dict(d) for d in crudos]
    # Descartar filas completamente vacías.
    movimientos = [
        m for m in movimientos
        if (m.concept or m.inflow_amount or m.outflow_amount
            or m.movement_date or m.third_party_nit or m.third_party_name)
    ]

    ordenados = mc.recalcular_saldos(movimientos, saldo_inicial)
    mc.renumerar(ordenados)
    entradas, salidas = mc.totales(ordenados)
    cierre = mc.saldo_final(saldo_inicial, ordenados)

    reemplazar_cash_movements(
        period_id, [mc.a_dict(m) for m in ordenados], db_path,
    )
    actualizar_cash_period_saldos(
        period_id, str(saldo_inicial), str(entradas), str(salidas), str(cierre),
        db_path,
    )
    audit.registrar("caja.guardar", empresa_id=emp.id,
                    detalle=f"periodo={period_id} movimientos={len(ordenados)}")
    _aprender_de_movimientos_caja(emp, ordenados)

    # Advertencias no bloqueantes: saldo negativo / errores de validación.
    if any(m.running_balance < 0 for m in ordenados):
        flash("Advertencia: hay movimientos que generan saldo negativo de caja. "
              "Verifica las salidas de efectivo.", "error")
    if any(mc.validar_contrapartidas(m) for m in ordenados):
        flash("Advertencia: hay contrapartidas divididas cuya suma no coincide con "
              "el valor del movimiento. Corrígelas antes de generar el SIIGO.", "error")
    n_invalidos = sum(
        1 for m in ordenados
        if mc.validar_movimiento(m, period["year"], period["month"])
    )
    if n_invalidos:
        flash(f"Se guardaron {len(ordenados)} movimientos. {n_invalidos} tienen "
              f"datos incompletos o inconsistentes (revisa las filas marcadas).",
              "error")
    else:
        flash(f"Avance guardado: {len(ordenados)} movimientos. "
              f"Saldo final {cierre:,.0f}.", "success")
    return redirect(url_for("web.caja_periodo", period_id=period_id))


# ---------------------------------------------------------------------------
# POST /caja/periodo/<id>/estado/<accion> — Transiciones de estado
# ---------------------------------------------------------------------------

# accion → (permiso requerido, estados de origen permitidos, estado destino)
_TRANSICIONES_CAJA = {
    "enviar-revision": ("caja.procesar", ("borrador", "reabierto"), "en_revision"),
    "aprobar":         ("caja.aprobar",  ("en_revision",),          "aprobado"),
    "devolver":        ("caja.aprobar",  ("en_revision", "aprobado"), "borrador"),
    "cerrar":          ("caja.cerrar",   ("borrador", "en_revision", "aprobado", "reabierto"), "cerrado"),
    "reabrir":         ("caja.cerrar",   ("cerrado",),              "reabierto"),
}


@bp.route("/caja/periodo/<int:period_id>/estado/<accion>", methods=["POST"])
def caja_periodo_estado(period_id, accion):
    """Cambia el estado de un período aplicando el permiso de la transición."""
    from app.database import (
        obtener_cash_period, actualizar_cash_period_estado,
        listar_cash_movements, actualizar_cash_period_saldos,
    )
    from app.caja import modelo_caja as mc
    from app.authz import tiene_permiso
    from datetime import datetime as _dt

    trans = _TRANSICIONES_CAJA.get(accion)
    if not trans:
        abort(404)
    permiso, origenes, destino = trans

    usuario = authn.usuario_actual()
    if usuario is None:
        return authn.redirigir_login()

    emp = base._empresa_actual()
    if not tiene_permiso(usuario, emp.id, permiso):
        audit.registrar("permiso.denegado", empresa_id=emp.id,
                        detalle=f"{permiso} · caja {accion}", resultado="denegado")
        abort(403)

    db_path = _caja_db(emp)
    period = obtener_cash_period(period_id, db_path)
    if not period:
        flash("El período de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    if period["status"] not in origenes:
        flash("La acción no es válida para el estado actual del período.", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    extra = {}
    if destino == "aprobado":
        extra["approved_by"] = _usuario_email()
    elif destino == "cerrado":
        # Al cerrar, fijar el saldo de cierre desde los movimientos actuales.
        movs = [mc.desde_dict(m) for m in listar_cash_movements(period_id, db_path)]
        entradas, salidas = mc.totales(movs)
        cierre = mc.saldo_final(period["opening_balance"], movs)
        actualizar_cash_period_saldos(
            period_id, str(mc.a_decimal(period["opening_balance"])),
            str(entradas), str(salidas), str(cierre), db_path,
        )
        extra["closed_by"] = _usuario_email()
        extra["closed_at"] = _dt.now().isoformat()
        # Al cerrar, abonar en la Cartera los pagos/recaudos con tercero.
        _aplicar_pagos_cartera(emp, movs, f"caja:{period_id}")

    actualizar_cash_period_estado(period_id, destino, db_path=db_path, **extra)
    audit.registrar(f"caja.{accion}", empresa_id=emp.id,
                    detalle=f"periodo={period_id} → {destino}")

    etiquetas = {
        "en_revision": "enviado a revisión", "aprobado": "aprobado",
        "borrador": "devuelto a borrador", "cerrado": "cerrado",
        "reabierto": "reabierto",
    }
    flash(f"Período {etiquetas.get(destino, destino)}.", "success")
    return redirect(url_for("web.caja_periodo", period_id=period_id))


# ---------------------------------------------------------------------------
# GET /caja/periodo/<id>/plantilla[-prediligenciada] — Descargas Excel
# ---------------------------------------------------------------------------


def _descargar_plantilla_caja(period_id, prediligenciada: bool):
    """Genera y envía la plantilla Excel del período (vacía o prediligenciada)."""
    from app.database import (
        obtener_cash_period, obtener_cash_account,
    )
    from app.caja.plantilla_caja import generar_plantilla

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    period = obtener_cash_period(period_id, db_path)
    if not period:
        flash("El período de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    cuenta = obtener_cash_account(period["cash_account_id"], db_path)
    movimientos = _cargar_movimientos_caja(period_id, db_path) if prediligenciada else None

    data = generar_plantilla(
        empresa=emp.nombre,
        cuenta_caja=(cuenta or {}).get("name", ""),
        cuenta_contable=(cuenta or {}).get("account_code", ""),
        cuenta_contable_nombre=(cuenta or {}).get("account_name", ""),
        anio=period["year"], mes=period["month"],
        saldo_inicial=period["opening_balance"],
        responsable=period.get("responsible", ""),
        movimientos=movimientos,
        terceros=_terceros_para_plantilla(emp),
    )
    sufijo = "prediligenciada" if prediligenciada else "vacia"
    nombre = f"caja_{period['year']}{period['month']:02d}_{sufijo}.xlsx"
    audit.registrar("caja.descargar_plantilla", empresa_id=emp.id,
                    detalle=f"periodo={period_id} tipo={sufijo}")
    return send_file(
        io.BytesIO(data), as_attachment=True,
        download_name=nombre, mimetype=_MIME_XLSX,
    )


@bp.route("/caja/periodo/<int:period_id>/plantilla")
@require_permission("caja.exportar")
def caja_periodo_plantilla(period_id):
    """Descarga la plantilla Excel vacía para diligenciar a mano."""
    return _descargar_plantilla_caja(period_id, prediligenciada=False)


@bp.route("/caja/periodo/<int:period_id>/plantilla-prediligenciada")
@require_permission("caja.exportar")
def caja_periodo_plantilla_pre(period_id):
    """Descarga la plantilla Excel prediligenciada con los movimientos registrados."""
    return _descargar_plantilla_caja(period_id, prediligenciada=True)


# ---------------------------------------------------------------------------
# POST /caja/periodo/<id>/importar — Importar plantilla diligenciada
# ---------------------------------------------------------------------------


@bp.route("/caja/periodo/<int:period_id>/importar", methods=["POST"])
@require_permission("caja.procesar")
def caja_periodo_importar(period_id):
    """Importa una plantilla diligenciada: valida por fila y guarda si no hay errores."""
    from app.database import (
        obtener_cash_period, reemplazar_cash_movements,
        actualizar_cash_period_saldos,
    )
    from app.caja import modelo_caja as mc
    from app.caja.importador_caja import importar_plantilla

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    period = obtener_cash_period(period_id, db_path)
    if not period:
        flash("El período de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    if period["status"] not in mc.ESTADOS_EDITABLES:
        flash("Este período está cerrado o aprobado; no se puede importar.", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    if "archivo" not in request.files or request.files["archivo"].filename == "":
        flash("Selecciona el archivo Excel diligenciado.", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    archivo = request.files["archivo"]
    if not base._allowed(archivo.filename):
        flash("El archivo debe ser una plantilla de Excel (.xlsx).", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    # Guardar el upload y parsearlo.
    ref = base._save_upload(archivo.read(), archivo.filename, emp)
    try:
        local = store.load_file(ref)
        res = importar_plantilla(local)
    except Exception as exc:
        logger.exception("Error leyendo la plantilla de caja")
        flash(f"No se pudo leer el archivo: {exc}", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    if res.tiene_errores:
        for msg in res.errores_generales:
            flash(msg, "error")
        if res.errores_por_fila:
            detalle = "; ".join(
                f"fila {f}: {', '.join(errs)}"
                for f, errs in sorted(res.errores_por_fila.items())[:8]
            )
            flash(f"La plantilla tiene errores de validación. {detalle}", "error")
        audit.registrar("caja.importar", empresa_id=emp.id, resultado="error",
                        detalle=f"periodo={period_id} errores={res.n_errores}")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    # Sin errores: el saldo inicial puede venir actualizado en la plantilla.
    saldo_inicial = res.saldo_inicial
    ordenados = mc.recalcular_saldos(res.movimientos, saldo_inicial)
    mc.renumerar(ordenados)
    entradas, salidas = mc.totales(ordenados)
    cierre = mc.saldo_final(saldo_inicial, ordenados)

    reemplazar_cash_movements(period_id, [mc.a_dict(m) for m in ordenados], db_path)
    actualizar_cash_period_saldos(
        period_id, str(saldo_inicial), str(entradas), str(salidas), str(cierre),
        db_path,
    )
    audit.registrar("caja.importar", empresa_id=emp.id,
                    detalle=f"periodo={period_id} movimientos={len(ordenados)}")
    _aprender_de_movimientos_caja(emp, ordenados)
    flash(f"Plantilla importada: {len(ordenados)} movimientos. "
          f"Saldo final {cierre:,.0f}.", "success")
    return redirect(url_for("web.caja_periodo", period_id=period_id))


# ---------------------------------------------------------------------------
# POST /caja/periodo/<id>/exportar-siigo — Generar el Excel de importación SIIGO
# ---------------------------------------------------------------------------


@bp.route("/caja/periodo/<int:period_id>/exportar-siigo", methods=["POST"])
@require_permission("caja.exportar")
def caja_periodo_exportar_siigo(period_id):
    """Genera el archivo Excel de importación SIIGO de los movimientos de caja.

    Cada movimiento produce un asiento de dos líneas: la cuenta contable de la
    caja (lado fijo) y la contrapartida, con el tipo de comprobante y el
    consecutivo asignados como en el módulo Bancos.
    """
    from app.database import (
        obtener_cash_period, obtener_cash_account,
    )
    from app.caja import modelo_caja as mc
    from app.caja.exportador_caja import exportar_caja_siigo
    from app.importador import cargar_maestro_cuentas

    emp = base._empresa_actual()
    db_path = _caja_db(emp)
    period = obtener_cash_period(period_id, db_path)
    if not period:
        flash("El período de caja no existe.", "error")
        return redirect(url_for("web.caja"))

    cuenta = obtener_cash_account(period["cash_account_id"], db_path)
    cuenta_caja = (cuenta or {}).get("account_code", "").strip()
    if not cuenta_caja:
        flash("La caja no tiene una cuenta contable asociada; no se puede "
              "generar el archivo SIIGO.", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    movimientos = [mc.desde_dict(m) for m in _cargar_movimientos_caja(period_id, db_path)]
    if not any(abs(m.inflow_amount) > 0 or abs(m.outflow_amount) > 0 for m in movimientos):
        flash("No hay movimientos con valor para exportar a SIIGO.", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    # Validar que las contrapartidas subdivididas sumen el valor del movimiento.
    errores_sub = []
    for m in movimientos:
        for err in mc.validar_contrapartidas(m):
            errores_sub.append(f"{m.movement_date or ''} {m.concept}: {err}")
    if errores_sub:
        flash("No se puede generar el SIIGO: " + " | ".join(errores_sub[:5]), "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

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
        logger.exception("Error generando Excel SIIGO de caja")
        flash(f"Error al generar el archivo SIIGO: {exc}", "error")
        return redirect(url_for("web.caja_periodo", period_id=period_id))

    audit.registrar("caja.exportar_siigo", empresa_id=emp.id,
                    detalle=f"periodo={period_id} archivos={len(rutas)}")
    return base._responder_descarga(
        base._enviar_archivos_siigo(rutas, zip_name="siigo_caja.zip")
    )
