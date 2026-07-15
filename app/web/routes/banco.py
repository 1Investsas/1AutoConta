"""Módulo Bancos: extracto CSV, resultado editable, histórico y SIIGO."""

import io
import logging
import os
from pathlib import Path

from flask import (
    flash, redirect, render_template,
    request, send_file, url_for,
)
from werkzeug.utils import secure_filename

from app import storage as store
from app import audit
from app.authz import require_permission
from app.web import session_store

from . import base
from .base import (
    bp, KEY_BANCO,
)

logger = logging.getLogger(__name__)


# Estados durables del proceso de banco → etiqueta y clase de pill para el
# histórico (el módulo marca 'completada' al generar el archivo SIIGO).
_ESTADOS_PROCESO_BANCO = {
    "procesando": ("Procesando", "pill"),
    "completada": ("Exportada",  "pill pill-ok"),
    "error":      ("Error",      "pill pill-pendiente"),
    "anulada":    ("Anulada",    "pill pill-muted"),
}


@bp.route("/banco")
@require_permission("banco.ver")
def banco():
    """Formulario para subir el CSV del banco."""
    from app.config import SIIGO_COMP_BANCO_INGRESO, SIIGO_COMP_BANCO_EGRESO, SIIGO_COMP_BANCO_TRASLADO
    emp = base._empresa_actual()
    cuentas_banco = emp.cuentas_banco_efectivas()  # siempre ≥ 1
    bancos = emp.bancos_efectivos()                # puede estar vacía
    return render_template(
        "banco_upload.html",
        cuentas_banco=cuentas_banco,
        bancos=bancos,
        cuenta_default=cuentas_banco[0]["cuenta"],
        nit_banco_default=bancos[0]["nit"] if bancos else "",
        comp_ingreso=SIIGO_COMP_BANCO_INGRESO,
        comp_egreso=SIIGO_COMP_BANCO_EGRESO,
        comp_traslado=SIIGO_COMP_BANCO_TRASLADO,
        actividad=_actividad_banco(emp),
    )


def _actividad_banco(emp, limite: int = 6) -> list[dict]:
    """
    Histórico reciente del módulo de Bancos para la empresa actual.

    Lee la tabla `procesos_banco` de la BD de la empresa. Cada elemento:
    {"archivo", "estado" ("completada"|"procesando"|"error"), "fecha",
    "movimientos"}. La plantilla soporta lista vacía.
    """
    from app.database import inicializar_db, listar_procesos_banco

    inicializar_db(emp.db_path)
    procesos = listar_procesos_banco(emp.db_path, limite=limite)
    return [
        {
            "archivo": p.get("archivo_nombre") or "extracto.csv",
            "estado": p.get("estado") or "procesando",
            "fecha": base._fmt_fecha_banco(p.get("fecha")),
            "count": p.get("n_movimientos") or 0,
            "unidad": "movimientos",
            "ext": "CSV",
        }
        for p in procesos
    ]


def _render_banco_resultado(movimientos, cuenta_banco, nit_banco, asignaciones=None,
                            db_path=None):
    """Construye la vista editable de movimientos bancarios (banco_resultado.html).

    Centraliza la preparación de las filas «principales» (defaults de cuenta, NIT
    y tipo de comprobante, y agrupación de 4x1000). Cuando se pasan `asignaciones`
    guardadas (al Retomar/Corregir un proceso), se sobreponen para conservar lo
    que el usuario ya había trabajado. Si se pasa `db_path`, la cuenta
    contrapartida y el NIT que sigan vacíos se prediligencian con el motor de
    aprendizaje (por la descripción del movimiento) y se marcan como sugeridos.
    """
    from app.banco.importador_banco import a_dict
    from app.config import (
        SIIGO_COMP_BANCO_INGRESO, SIIGO_COMP_BANCO_EGRESO,
        SIIGO_COMP_BANCO_TRASLADO, BANCO_CUENTA_4X1000,
    )

    asig_por_idx = {a["idx"]: a for a in (asignaciones or [])}

    impuestos_por_padre: dict[int, list] = {}
    for m in movimientos:
        if m.es_4x1000 and m.idx_padre is not None:
            impuestos_por_padre.setdefault(m.idx_padre, []).append(a_dict(m))

    principales = []
    subdivisiones_js: dict[int, list] = {}
    for m in movimientos:
        if m.es_4x1000 and m.idx_padre is not None:
            continue  # agrupado bajo su padre, no aparece como fila propia
        d = a_dict(m)
        d["impuestos_4x1000"] = impuestos_por_padre.get(m.idx, [])
        # Tipo comprobante, cuenta y NIT por defecto
        if m.es_4x1000 and m.idx_padre is None:
            d["tipo_comp_default"] = SIIGO_COMP_BANCO_EGRESO
            d["cuenta_auto"] = BANCO_CUENTA_4X1000
            d["nit_auto"]    = nit_banco   # 4x1000 huérfano también usa NIT banco
        elif m.es_bancario:
            # Intereses, cuota de manejo, GMF: siempre NIT del banco
            d["nit_auto"]    = nit_banco
            d["cuenta_auto"] = ""
            d["tipo_comp_default"] = SIIGO_COMP_BANCO_INGRESO if m.valor > 0 else SIIGO_COMP_BANCO_EGRESO
        elif m.valor > 0:
            d["tipo_comp_default"] = SIIGO_COMP_BANCO_INGRESO
            d["cuenta_auto"] = ""
            d["nit_auto"]    = ""
        else:
            d["tipo_comp_default"] = SIIGO_COMP_BANCO_EGRESO
            d["cuenta_auto"] = ""
            d["nit_auto"]    = ""

        # Sobreponer las asignaciones guardadas (Retomar / Corregir).
        d["contrapartidas_guardadas"] = []
        asig = asig_por_idx.get(m.idx)
        if asig:
            if asig.get("cuenta_contrapartida"):
                d["cuenta_auto"] = asig["cuenta_contrapartida"]
            if asig.get("nit_tercero"):
                d["nit_auto"] = asig["nit_tercero"]
            if asig.get("tipo_comprobante"):
                d["tipo_comp_default"] = asig["tipo_comprobante"]
            if asig.get("contrapartidas"):
                d["contrapartidas_guardadas"] = asig["contrapartidas"]
                # Semilla para el editor de subdivisión (claves del JS: nit).
                subdivisiones_js[m.idx] = [
                    {"cuenta": c.get("cuenta", ""), "monto": c.get("monto", 0),
                     "nit": c.get("nit_tercero", ""), "concepto": c.get("concepto", "")}
                    for c in asig["contrapartidas"]
                ]

        # Prediligenciar con el motor de aprendizaje lo que siga vacío.
        d["cuenta_pred"] = None
        d["nit_pred"] = None
        if db_path and not d["contrapartidas_guardadas"] and not m.es_4x1000:
            try:
                from app import aprendizaje
                if not d["cuenta_auto"]:
                    pred = aprendizaje.predecir("banco", "cuenta",
                                                m.descripcion, db_path)
                    if pred:
                        d["cuenta_auto"] = pred.valor
                        d["cuenta_pred"] = pred.a_dict()
                if not d["nit_auto"] and not m.es_bancario:
                    pred = aprendizaje.predecir("banco", "nit_tercero",
                                                m.descripcion, db_path)
                    if pred:
                        d["nit_auto"] = pred.valor
                        d["nit_pred"] = pred.a_dict()
            except Exception:
                logger.exception("Fallo prediligenciando el movimiento %s", m.idx)

        principales.append(d)

    return render_template(
        "banco_resultado.html",
        movimientos=principales,
        cuenta_banco=cuenta_banco,
        nit_banco=nit_banco,
        n_total=len(movimientos),
        n_principales=len(principales),
        cuenta_4x1000=BANCO_CUENTA_4X1000,
        comp_ingreso=SIIGO_COMP_BANCO_INGRESO,
        comp_egreso=SIIGO_COMP_BANCO_EGRESO,
        comp_traslado=SIIGO_COMP_BANCO_TRASLADO,
        subdivisiones_guardadas=subdivisiones_js,
    )


def _persistir_proceso_banco(emp, datos: dict, estado: str) -> None:
    """Guarda el snapshot editable durable de un proceso de banco.

    Copia durable (en BD) del estado de trabajo que vive en la sesión: así
    «Retomar»/«Corregir» recupera los movimientos y las asignaciones sin volver a
    subir el CSV. Best-effort: si falla no se rompe el flujo (ya está en sesión).
    """
    import json as _json
    from app.database import actualizar_proceso_banco

    proceso_id = datos.get("proceso_id")
    if not proceso_id:
        return
    try:
        actualizar_proceso_banco(
            proceso_id,
            estado=estado,
            n_movimientos=len(datos.get("movimientos", [])) or None,
            snapshot_json=_json.dumps(datos, ensure_ascii=False),
            db_path=emp.db_path,
        )
    except Exception:
        logger.exception("No se pudo persistir el snapshot del proceso de banco %s",
                         proceso_id)


def _generar_archivos_banco_siigo(datos: dict) -> list:
    """Genera el/los Excel SIIGO del módulo Bancos a partir de un snapshot/sesión."""
    from app.banco.importador_banco import desde_dict
    from app.banco.exportador_banco import exportar_banco_siigo

    movimientos = [desde_dict(d) for d in datos.get("movimientos", [])]
    return exportar_banco_siigo(
        movimientos=movimientos,
        cuenta_banco=datos.get("cuenta_banco", ""),
        asignaciones=datos.get("asignaciones", []),
        nit_banco=datos.get("nit_banco", ""),
        output_path=os.path.join(base._project_root(), "output"),
    )


# ---------------------------------------------------------------------------
# POST /banco/previsualizar — Parsea el CSV y muestra la tabla editable
# ---------------------------------------------------------------------------


@bp.route("/banco/previsualizar", methods=["POST"])
@require_permission("banco.procesar")
def banco_previsualizar():
    """Recibe el CSV, lo parsea, agrupa 4x1000 y guarda en sesión."""
    from app.banco.importador_banco import leer_csv_banco, a_dict

    emp = base._empresa_actual()
    BANCO_CUENTA_DEFAULT = emp.cuenta_banco_efectiva()

    if "csv_banco" not in request.files or request.files["csv_banco"].filename == "":
        flash("Debes seleccionar el archivo CSV del banco.", "error")
        return redirect(url_for("web.banco"))

    csv_file = request.files["csv_banco"]

    cuenta_banco = request.form.get("cuenta_banco", BANCO_CUENTA_DEFAULT).strip()
    if not cuenta_banco:
        cuenta_banco = BANCO_CUENTA_DEFAULT

    nit_banco = request.form.get("nit_banco", "").strip() or emp.nit_banco

    csv_path = base._save_upload(csv_file.read(), csv_file.filename, emp)
    csv_local_path = store.load_file(csv_path)

    try:
        movimientos = leer_csv_banco(csv_local_path, formato=emp.formato_banco_efectivo())
    except Exception as exc:
        logger.exception("Error al leer CSV del banco")
        flash(f"Error al leer el CSV: {exc}", "error")
        return redirect(url_for("web.banco"))

    if not movimientos:
        flash("El archivo CSV no contiene movimientos válidos.", "error")
        return redirect(url_for("web.banco"))

    # Registrar el proceso en el histórico del módulo (estado 'procesando';
    # pasará a 'completada' al generar el archivo SIIGO). Guarda el CSV original
    # (archivo_ref) para poder descargarlo o retomar el proceso después.
    from app.database import inicializar_db, registrar_proceso_banco
    inicializar_db(emp.db_path)
    proceso_id = registrar_proceso_banco(
        archivo_nombre=secure_filename(csv_file.filename),
        n_movimientos=len(movimientos),
        cuenta_banco=cuenta_banco,
        nit_banco=nit_banco,
        archivo_ref=csv_path,
        db_path=emp.db_path,
    )

    datos_banco = {
        "movimientos":  [a_dict(m) for m in movimientos],
        "cuenta_banco": cuenta_banco,
        "nit_banco":    nit_banco,
        "proceso_id":   proceso_id,
        "asignaciones": [],
    }
    session_store.guardar(KEY_BANCO, datos_banco)
    # Snapshot durable inicial: permite "Retomar" el proceso aunque el usuario
    # cierre la sesión antes de exportar a SIIGO.
    _persistir_proceso_banco(emp, datos_banco, "procesando")

    return _render_banco_resultado(movimientos, cuenta_banco, nit_banco,
                                   db_path=emp.db_path)


# ---------------------------------------------------------------------------
# POST /banco/exportar — Genera el Excel SIIGO con las asignaciones del usuario
# ---------------------------------------------------------------------------


def _recolectar_contrapartidas(idx: int) -> list[dict]:
    """Lee del formulario las partes de la contrapartida subdividida de un movimiento.

    Cada movimiento subdividido envía arreglos paralelos `sub_<idx>_cuenta`,
    `sub_<idx>_monto`, `sub_<idx>_nit` y `sub_<idx>_concepto`. Se ignoran las
    filas totalmente vacías (sin cuenta ni monto). Devuelve [] si el movimiento
    no fue subdividido.
    """
    cuentas   = request.form.getlist(f"sub_{idx}_cuenta")
    if not cuentas:
        return []
    montos    = request.form.getlist(f"sub_{idx}_monto")
    nits      = request.form.getlist(f"sub_{idx}_nit")
    conceptos = request.form.getlist(f"sub_{idx}_concepto")

    partes: list[dict] = []
    for i, cta in enumerate(cuentas):
        cta = (cta or "").strip()
        monto_raw = (montos[i] if i < len(montos) else "").strip()
        if not cta and not monto_raw:
            continue
        try:
            monto = round(float(monto_raw), 2)
        except ValueError:
            monto = 0.0
        partes.append({
            "cuenta":      cta,
            "monto":       monto,
            "nit_tercero": (nits[i].strip() if i < len(nits) else ""),
            "concepto":    (conceptos[i].strip() if i < len(conceptos) else ""),
        })
    return partes


@bp.route("/banco/exportar", methods=["POST"])
@require_permission("banco.exportar")
def banco_exportar():
    """Recibe las asignaciones, genera el Excel SIIGO y lo envía como descarga."""
    from app.banco.importador_banco import desde_dict

    emp = base._empresa_actual()
    BANCO_CUENTA_DEFAULT = emp.cuenta_banco_efectiva()

    datos_banco = session_store.cargar(KEY_BANCO)
    if not datos_banco or not datos_banco.get("movimientos"):
        flash("No hay movimientos en sesión. Sube el CSV primero.", "error")
        return redirect(url_for("web.banco"))

    movimientos = [desde_dict(d) for d in datos_banco["movimientos"]]
    cuenta_banco = request.form.get("cuenta_banco", BANCO_CUENTA_DEFAULT).strip() or BANCO_CUENTA_DEFAULT
    nit_banco    = request.form.get("nit_banco",    "").strip()

    # Recolectar asignaciones del formulario
    asignaciones = []
    for m in movimientos:
        # Solo los principales (no-4x1000 agrupados) tienen inputs en el form
        if m.es_4x1000 and m.idx_padre is not None:
            continue
        asig = {
            "idx":                m.idx,
            "cuenta_contrapartida": request.form.get(f"cuenta_{m.idx}", "").strip(),
            "nit_tercero":         request.form.get(f"nit_{m.idx}", "").strip(),
            "tipo_comprobante":    request.form.get(f"tipo_comp_{m.idx}", "").strip(),
        }

        # Subdivisión de la contrapartida (opcional): el movimiento bancario
        # permanece por un solo valor, pero la contrapartida puede repartirse
        # en varias cuentas por importes distintos que sumen el valor total.
        contrapartidas = _recolectar_contrapartidas(m.idx)
        if contrapartidas:
            suma = round(sum(c["monto"] for c in contrapartidas), 2)
            total = round(abs(float(m.valor)), 2)
            if abs(suma - total) >= 0.01:
                flash(
                    f"La suma de la contrapartida subdividida (${suma:,.2f}) debe "
                    f"igualar el valor del movimiento (${total:,.2f}).",
                    "error",
                )
                return redirect(url_for("web.banco"))
            asig["contrapartidas"] = contrapartidas

        asignaciones.append(asig)

    # Persistir lo trabajado en el snapshot durable ANTES de exportar, para que un
    # fallo no pierda las asignaciones y el proceso pueda retomarse.
    datos_banco["cuenta_banco"] = cuenta_banco
    datos_banco["nit_banco"]    = nit_banco
    datos_banco["asignaciones"] = asignaciones
    session_store.guardar(KEY_BANCO, datos_banco)
    _persistir_proceso_banco(emp, datos_banco, "procesando")

    try:
        rutas = _generar_archivos_banco_siigo(datos_banco)
    except Exception as exc:
        logger.exception("Error generando Excel banco SIIGO")
        proceso_id = datos_banco.get("proceso_id")
        if proceso_id:
            from app.database import actualizar_proceso_banco
            actualizar_proceso_banco(proceso_id, estado="error",
                                     error=str(exc), db_path=emp.db_path)
        flash(f"Error al generar SIIGO: {exc}", "error")
        return redirect(url_for("web.banco"))

    # Marcar el proceso como completado (exportado a SIIGO) en el histórico.
    _persistir_proceso_banco(emp, datos_banco, "completada")
    audit.registrar("banco.exportar_siigo", empresa_id=emp.id,
                    detalle=f"proceso={datos_banco.get('proceso_id')} archivos={len(rutas)}")

    # Actualizar la Cartera y Cuentas por Pagar con los pagos confirmados: los
    # ingresos con tercero abonan la cartera (CxC) y los egresos las cuentas
    # por pagar (CxP). Idempotente por proceso (reexportar no duplica abonos)
    # y best-effort (un fallo no bloquea la descarga).
    try:
        from app.database import aplicar_pagos_flujos_directos

        movs_por_idx = {m.idx: m for m in movimientos}
        pagos = []
        for asig in asignaciones:
            m = movs_por_idx.get(asig["idx"])
            if m is None or m.es_bancario or m.es_4x1000:
                continue  # intereses/GMF/cuota de manejo: no son pagos a terceros
            sentido = "ingreso" if m.valor > 0 else "egreso"
            fecha = m.fecha.isoformat() if m.fecha else ""
            partes = asig.get("contrapartidas") or []
            if partes:
                for p in partes:
                    if p.get("nit_tercero"):
                        pagos.append({
                            "nit": p["nit_tercero"], "valor": p["monto"],
                            "sentido": sentido, "fecha": fecha,
                            "detalle": p.get("concepto") or m.descripcion,
                        })
            elif asig.get("nit_tercero"):
                pagos.append({
                    "nit": asig["nit_tercero"], "valor": abs(float(m.valor)),
                    "sentido": sentido, "fecha": fecha, "detalle": m.descripcion,
                })
        if pagos:
            res = aplicar_pagos_flujos_directos(
                pagos, "banco", f"banco:{datos_banco.get('proceso_id')}",
                emp.db_path,
            )
            if res["n_pagos"]:
                audit.registrar(
                    "cartera.pagos_aplicados", empresa_id=emp.id,
                    detalle=f"banco:{datos_banco.get('proceso_id')} "
                            f"obligaciones={res['n_pagos']} valor={res['aplicado']}",
                )
    except Exception:
        logger.exception("No se pudo actualizar la cartera desde el banco.")

    # Aprender de las asignaciones confirmadas (descripción → cuenta / NIT):
    # el usuario revisó la tabla antes de exportar, así que cada valor cuenta
    # como una confirmación para el motor de aprendizaje. Best-effort.
    try:
        from app import aprendizaje
        desc_por_idx = {m.idx: m.descripcion for m in movimientos}
        es_bancario_por_idx = {m.idx: m.es_bancario for m in movimientos}
        observaciones = []
        for asig in asignaciones:
            desc = desc_por_idx.get(asig["idx"], "")
            if not desc or asig.get("contrapartidas"):
                continue  # subdivididos: la relación descripción→cuenta es ambigua
            if asig.get("cuenta_contrapartida"):
                observaciones.append({
                    "modulo": "banco", "campo": "cuenta",
                    "texto": desc, "valor": asig["cuenta_contrapartida"],
                })
            if asig.get("nit_tercero") and not es_bancario_por_idx.get(asig["idx"]):
                observaciones.append({
                    "modulo": "banco", "campo": "nit_tercero",
                    "texto": desc, "valor": asig["nit_tercero"],
                })
        aprendizaje.aprender_lote(observaciones, emp.db_path)
    except Exception:
        logger.exception("No se pudo aprender de las asignaciones del banco.")

    return base._responder_descarga(base._enviar_archivos_siigo(rutas, zip_name="siigo_banco.zip"))


# ---------------------------------------------------------------------------
# GET /banco/historial — Histórico completo de procesos del módulo Bancos
# ---------------------------------------------------------------------------


@bp.route("/banco/historial")
@require_permission("banco.ver")
def banco_historial():
    """Lista todos los procesos del módulo Bancos con su estado y acciones."""
    from app.database import inicializar_db, listar_procesos_banco

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)
    procesos = listar_procesos_banco(emp.db_path, limite=200)

    for p in procesos:
        p["fecha_fmt"] = base._fmt_fecha_banco(p.get("fecha"))
        p["archivo_disponible"] = bool(
            p.get("archivo_ref") and store.file_exists(p["archivo_ref"])
        )
        p["tiene_snapshot"] = bool(p.get("tiene_snapshot"))
        p["anulada"] = p.get("estado") == "anulada"
        etiqueta, clase = _ESTADOS_PROCESO_BANCO.get(
            p.get("estado"), (p.get("estado") or "—", "pill")
        )
        p["estado_label"] = etiqueta
        p["estado_clase"] = clase

    return render_template("banco_historial.html", procesos=procesos)


@bp.route("/banco/historial/<int:proceso_id>/abrir", methods=["POST"])
@require_permission("banco.procesar")
def proceso_banco_abrir(proceso_id):
    """Retoma/corrige un proceso de banco cargando su snapshot en la sesión.

    Recupera los movimientos y las asignaciones guardadas para seguir editando y
    volver a exportar a SIIGO, sin tener que subir el CSV de nuevo.
    """
    from app.database import inicializar_db, obtener_snapshot_proceso_banco
    from app.banco.importador_banco import desde_dict

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    snap = obtener_snapshot_proceso_banco(proceso_id, db_path=emp.db_path)
    if not snap or not snap.get("movimientos"):
        flash("Este proceso no tiene un estado guardado para abrir.", "error")
        return redirect(url_for("web.banco_historial"))

    snap["proceso_id"] = proceso_id
    session_store.guardar(KEY_BANCO, snap)
    audit.registrar("banco.abrir", empresa_id=emp.id, detalle=f"proceso={proceso_id}")

    movimientos = [desde_dict(d) for d in snap["movimientos"]]
    return _render_banco_resultado(
        movimientos,
        snap.get("cuenta_banco", ""),
        snap.get("nit_banco", ""),
        asignaciones=snap.get("asignaciones"),
        db_path=emp.db_path,
    )


@bp.route("/banco/historial/<int:proceso_id>/descargar-original")
@require_permission("banco.ver")
def proceso_banco_descargar_original(proceso_id):
    """Descarga el CSV original que se importó para este proceso de banco."""
    from app.database import inicializar_db, obtener_proceso_banco

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)
    proc = obtener_proceso_banco(proceso_id, db_path=emp.db_path)

    archivo_ref = (proc or {}).get("archivo_ref") or ""
    if not proc or not archivo_ref or not store.file_exists(archivo_ref):
        flash("El archivo importado ya no está disponible en el servidor.", "error")
        return redirect(url_for("web.banco_historial"))

    content = store.get_download_bytes(archivo_ref)
    download_name = proc.get("archivo_nombre") or Path(archivo_ref.replace("blob://", "")).name
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=download_name,
        mimetype="text/csv",
    )


@bp.route("/banco/historial/<int:proceso_id>/descargar-siigo")
@require_permission("banco.exportar")
def proceso_banco_descargar_siigo(proceso_id):
    """Regenera y descarga el archivo SIIGO de un proceso de banco desde su snapshot."""
    from app.database import inicializar_db, obtener_snapshot_proceso_banco

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    snap = obtener_snapshot_proceso_banco(proceso_id, db_path=emp.db_path)
    if not snap or not snap.get("movimientos"):
        flash("Este proceso no tiene un estado guardado para exportar a SIIGO.", "error")
        return redirect(url_for("web.banco_historial"))

    try:
        rutas = _generar_archivos_banco_siigo(snap)
    except Exception as exc:
        logger.exception("Error generando SIIGO del proceso de banco %s", proceso_id)
        flash(f"Error al generar el archivo SIIGO: {exc}", "error")
        return redirect(url_for("web.banco_historial"))

    audit.registrar("banco.descargar_siigo", empresa_id=emp.id,
                    detalle=f"proceso={proceso_id} archivos={len(rutas)}")
    return base._enviar_archivos_siigo(rutas, zip_name="siigo_banco.zip")


@bp.route("/banco/historial/<int:proceso_id>/anular", methods=["POST"])
@require_permission("banco.procesar")
def proceso_banco_anular(proceso_id):
    """Marca un proceso de banco como anulado (descartado). No borra el histórico."""
    from app.database import (
        inicializar_db, obtener_proceso_banco, actualizar_proceso_banco,
    )

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    proc = obtener_proceso_banco(proceso_id, db_path=emp.db_path)
    if not proc:
        flash("El proceso no existe.", "error")
        return redirect(url_for("web.banco_historial"))

    actualizar_proceso_banco(
        proceso_id, estado="anulada",
        n_movimientos=int(proc.get("n_movimientos", 0) or 0),
        db_path=emp.db_path,
    )
    audit.registrar("banco.anular", empresa_id=emp.id, detalle=f"proceso={proceso_id}")
    flash(f"Proceso de banco #{proceso_id} anulado.", "info")
    return redirect(url_for("web.banco_historial"))
