"""Módulo Cartera y Cuentas por Pagar — Finanzas.

Mantiene actualizada la cartera (CxC) y las cuentas por pagar (CxP) con
vencimientos, cuotas, valores, datos de contacto y cuentas bancarias para
programar pagos y cobros a tiempo:

- Los **valores y terceros** nacen de los módulos de Flujos Indirectos de
  Efectivo (documentos RADIAN ya procesados) vía «Sincronizar».
- Los **datos bancarios** vienen del módulo de Terceros (certificados
  bancarios importados); si un tercero por pagar no los tiene, se marca que
  se le debe solicitar el certificado bancario.
- Los **saldos** bajan con los pagos hechos en los módulos de Flujos Directos
  de Efectivo (Bancos al exportar; Caja y Flujos mixtos al cerrar) y con los
  abonos manuales.

La capa de datos vive en ``app/database/cartera.py``.
"""

import logging

from flask import flash, redirect, render_template, request, url_for

from app import audit
from app.authz import require_permission

from . import base
from .base import bp

logger = logging.getLogger(__name__)

_ETIQUETA_TIPO = {"cxc": "por cobrar", "cxp": "por pagar"}


def _a_float(crudo: str) -> float | None:
    """Parsea un monto aceptando formatos '1.234.567,89' y '1,234,567.89'.

    Un único punto o coma seguido de exactamente 3 dígitos se toma como
    separador de miles ('250.000' → 250000), que es lo habitual al digitar
    montos en pesos; con otra cantidad de decimales se toma como decimal.
    """
    txt = (crudo or "").strip().replace("$", "").replace(" ", "")
    if not txt:
        return None
    if "," in txt and "." in txt:
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif "," in txt:
        if txt.count(",") > 1 or len(txt.rsplit(",", 1)[1]) == 3:
            txt = txt.replace(",", "")   # miles: 250,000 / 1,234,567
        else:
            txt = txt.replace(",", ".")  # decimal: 250,5
    elif "." in txt:
        if txt.count(".") > 1 or len(txt.rsplit(".", 1)[1]) == 3:
            txt = txt.replace(".", "")   # miles: 250.000 / 5.000.000
    try:
        return float(txt)
    except ValueError:
        return None


def _contactos_maestro(emp) -> dict[str, dict]:
    """Datos de contacto por NIT desde el maestro de terceros (best-effort).

    Devuelve ``{nit: {"telefono", "correo", "contacto"}}``. Si el maestro no
    está cargado o no se puede leer, retorna un dict vacío (el módulo funciona
    igual; solo no prediligencia contactos).
    """
    try:
        from app.importador import cargar_maestro_terceros
        from app.terceros_schema import campo_de_encabezado

        terceros_path = emp.ruta_maestro("Listado_de_Terceros.xlsx")
        df = base._cargar_maestro_cacheado(cargar_maestro_terceros, terceros_path)
    except Exception:
        return {}

    # Columna real de cada campo del modelo Siigo presente en el archivo.
    col_de: dict[str, str] = {}
    for col in df.columns:
        campo = campo_de_encabezado(col)
        if campo and campo not in col_de:
            col_de[campo] = col

    def _valor(row, campo: str) -> str:
        col = col_de.get(campo)
        if not col:
            return ""
        v = str(row.get(col, "") or "").strip()
        return "" if v.lower() == "nan" else v

    contactos: dict[str, dict] = {}
    for _, row in df.iterrows():
        nit = str(row.get("Identificación", "") or "").strip()
        if not nit:
            continue
        nombres = " ".join(p for p in (
            _valor(row, "contacto_nombres"), _valor(row, "contacto_apellidos"),
        ) if p)
        contactos[nit] = {
            "telefono": _valor(row, "contacto_telefono") or _valor(row, "telefono"),
            "correo":   _valor(row, "correo"),
            "contacto": nombres,
        }
    return contactos


def _enriquecer_contactos(emp, obligaciones: list[dict]) -> None:
    """Rellena en BD los contactos vacíos de las obligaciones desde el maestro."""
    from app.database import actualizar_datos_obligacion

    contactos = _contactos_maestro(emp)
    if not contactos:
        return
    for o in obligaciones:
        c = contactos.get(o["nit_tercero"])
        if not c:
            continue
        cambios = {}
        if not o.get("contacto_nombre") and c["contacto"]:
            cambios["contacto_nombre"] = c["contacto"]
        if not o.get("contacto_telefono") and c["telefono"]:
            cambios["contacto_telefono"] = c["telefono"]
        if not o.get("contacto_correo") and c["correo"]:
            cambios["contacto_correo"] = c["correo"]
        if cambios:
            actualizar_datos_obligacion(o["id"], db_path=emp.db_path, **cambios)
            o.update(cambios)


def _bancos_por_tercero(emp) -> dict[str, list[dict]]:
    """Cuentas bancarias registradas (módulo Terceros) agrupadas por NIT."""
    from app.database import listar_cuentas_bancarias_tercero

    bancos: dict[str, list[dict]] = {}
    for c in listar_cuentas_bancarias_tercero(db_path=emp.db_path):
        bancos.setdefault(str(c["nit_tercero"]), []).append(c)
    return bancos


# ---------------------------------------------------------------------------
# GET /cartera — Tablero: CxC y CxP con vencimientos, contactos y bancos
# ---------------------------------------------------------------------------


@bp.route("/cartera")
@require_permission("cartera.ver")
def cartera():
    """Tablero del módulo: resumen, cartera (CxC) y cuentas por pagar (CxP)."""
    from app.database import inicializar_db, listar_obligaciones, resumen_cartera

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    obligaciones = listar_obligaciones(emp.db_path)
    bancos = _bancos_por_tercero(emp)
    for o in obligaciones:
        o["cuentas_bancarias"] = bancos.get(o["nit_tercero"], [])
        # A los terceros por pagar sin certificado bancario hay que pedírselo
        # para poder programar la dispersión del pago.
        o["falta_certificado"] = (
            o["tipo"] == "cxp" and not o["cuentas_bancarias"]
            and (o["saldo"] or 0) > 0.01
        )

    cxc = [o for o in obligaciones if o["tipo"] == "cxc"]
    cxp = [o for o in obligaciones if o["tipo"] == "cxp"]

    return render_template(
        "cartera.html",
        cxc=cxc,
        cxp=cxp,
        resumen=resumen_cartera(emp.db_path),
    )


# ---------------------------------------------------------------------------
# POST /cartera/sincronizar — Trae los documentos de Flujos Indirectos
# ---------------------------------------------------------------------------


@bp.route("/cartera/sincronizar", methods=["POST"])
@require_permission("cartera.procesar")
def cartera_sincronizar():
    """Crea las obligaciones que falten desde los documentos RADIAN procesados."""
    from app.database import (
        inicializar_db, listar_obligaciones, sincronizar_desde_documentos,
    )

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)
    resultado = sincronizar_desde_documentos(emp.db_path)
    # Prediligenciar contactos desde el maestro de terceros (best-effort).
    try:
        _enriquecer_contactos(emp, listar_obligaciones(emp.db_path))
    except Exception:
        logger.exception("No se pudieron enriquecer los contactos de la cartera.")

    audit.registrar("cartera.sincronizar", empresa_id=emp.id,
                    detalle=f"creadas={resultado['creadas']} "
                            f"revisadas={resultado['revisadas']}")
    if resultado["creadas"]:
        flash(f"Sincronización lista: {resultado['creadas']} obligación(es) nueva(s) "
              f"de {resultado['revisadas']} documento(s) revisados.", "success")
    else:
        flash(f"Cartera al día: los {resultado['revisadas']} documento(s) de los "
              "Flujos Indirectos ya estaban registrados.", "success")
    return redirect(url_for("web.cartera"))


# ---------------------------------------------------------------------------
# POST /cartera/nueva — Obligación manual (no viene de RADIAN)
# ---------------------------------------------------------------------------


@bp.route("/cartera/nueva", methods=["POST"])
@require_permission("cartera.gestionar")
def cartera_nueva():
    """Registra una obligación manual (contratos, préstamos, etc.)."""
    from app.database import inicializar_db, registrar_obligacion

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    tipo = request.form.get("tipo", "").strip()
    nit = "".join(ch for ch in request.form.get("nit_tercero", "") if ch.isdigit())
    valor = _a_float(request.form.get("valor_total", ""))
    if tipo not in ("cxc", "cxp") or not nit or not valor or valor <= 0:
        flash("Para crear la obligación indica el tipo, el NIT del tercero y "
              "un valor mayor que cero.", "error")
        return redirect(url_for("web.cartera"))

    nuevo = registrar_obligacion(
        tipo=tipo,
        nit_tercero=nit,
        valor_total=valor,
        nombre_tercero=request.form.get("nombre_tercero", "").strip(),
        documento=request.form.get("documento", "").strip(),
        fecha_emision=request.form.get("fecha_emision", "").strip(),
        fecha_vencimiento=request.form.get("fecha_vencimiento", "").strip(),
        origen="manual",
        db_path=emp.db_path,
    )
    audit.registrar("cartera.crear", empresa_id=emp.id,
                    detalle=f"obligacion={nuevo} tipo={tipo} nit={nit}")
    flash(f"Obligación {_ETIQUETA_TIPO[tipo]} registrada.", "success")
    return redirect(url_for("web.cartera_detalle", oblig_id=nuevo))


# ---------------------------------------------------------------------------
# GET /cartera/<id> — Detalle: cuotas, pagos, contacto y datos bancarios
# ---------------------------------------------------------------------------


def _obligacion_o_none(emp, oblig_id: int):
    from app.database import inicializar_db, obtener_obligacion

    inicializar_db(emp.db_path)
    oblig = obtener_obligacion(oblig_id, emp.db_path)
    if not oblig or oblig["estado"] == "anulada":
        return None
    return oblig


@bp.route("/cartera/<int:oblig_id>")
@require_permission("cartera.ver")
def cartera_detalle(oblig_id):
    """Hoja de la obligación: condiciones, cuotas, abonos y datos de pago."""
    from app.database import (
        listar_cuentas_bancarias_tercero, listar_cuotas, listar_pagos,
        listar_obligaciones,
    )

    emp = base._empresa_actual()
    oblig = _obligacion_o_none(emp, oblig_id)
    if not oblig:
        flash("La obligación no existe o fue anulada.", "error")
        return redirect(url_for("web.cartera"))

    # Campos calculados (vencida / próximo vencimiento) del listado.
    for o in listar_obligaciones(emp.db_path, tipo=oblig["tipo"]):
        if o["id"] == oblig_id:
            oblig = o
            break

    cuentas_banco = listar_cuentas_bancarias_tercero(
        db_path=emp.db_path, nit_tercero=oblig["nit_tercero"],
    )
    return render_template(
        "cartera_detalle.html",
        o=oblig,
        cuotas=listar_cuotas(oblig_id, emp.db_path),
        pagos=listar_pagos(oblig_id, emp.db_path),
        cuentas_banco=cuentas_banco,
        etiqueta_tipo=_ETIQUETA_TIPO.get(oblig["tipo"], oblig["tipo"]),
    )


# ---------------------------------------------------------------------------
# POST /cartera/<id>/condiciones — Vencimiento y cuotas (contado / crédito)
# ---------------------------------------------------------------------------


@bp.route("/cartera/<int:oblig_id>/condiciones", methods=["POST"])
@require_permission("cartera.gestionar")
def cartera_condiciones(oblig_id):
    """Define la condición de pago: contado (una fecha) o crédito (N cuotas,
    cada una con su propia fecha de vencimiento)."""
    from app.database import definir_condiciones_pago

    emp = base._empresa_actual()
    if not _obligacion_o_none(emp, oblig_id):
        flash("La obligación no existe o fue anulada.", "error")
        return redirect(url_for("web.cartera"))

    condicion = request.form.get("condicion_pago", "contado").strip()
    cuotas = []
    if condicion == "credito":
        fechas = request.form.getlist("cuota_fecha")
        valores = request.form.getlist("cuota_valor")
        for i, fecha in enumerate(fechas):
            fecha = (fecha or "").strip()
            valor = _a_float(valores[i] if i < len(valores) else "")
            if not fecha and not valor:
                continue  # fila vacía del formulario
            cuotas.append({"fecha_vencimiento": fecha, "valor": valor or 0})

    try:
        definir_condiciones_pago(
            oblig_id,
            condicion,
            fecha_vencimiento=request.form.get("fecha_vencimiento", "").strip(),
            cuotas=cuotas,
            db_path=emp.db_path,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.cartera_detalle", oblig_id=oblig_id))

    audit.registrar("cartera.condiciones", empresa_id=emp.id,
                    detalle=f"obligacion={oblig_id} {condicion} "
                            f"cuotas={len(cuotas) or 1}")
    flash("Condiciones de pago actualizadas.", "success")
    return redirect(url_for("web.cartera_detalle", oblig_id=oblig_id))


# ---------------------------------------------------------------------------
# POST /cartera/<id>/datos — Contacto, fuente de recursos y observaciones
# ---------------------------------------------------------------------------


@bp.route("/cartera/<int:oblig_id>/datos", methods=["POST"])
@require_permission("cartera.gestionar")
def cartera_datos(oblig_id):
    """Guarda los datos de gestión: a quién contactar y de dónde sale el pago."""
    from app.database import actualizar_datos_obligacion

    emp = base._empresa_actual()
    if not _obligacion_o_none(emp, oblig_id):
        flash("La obligación no existe o fue anulada.", "error")
        return redirect(url_for("web.cartera"))

    actualizar_datos_obligacion(
        oblig_id,
        db_path=emp.db_path,
        contacto_nombre=request.form.get("contacto_nombre", "").strip(),
        contacto_telefono=request.form.get("contacto_telefono", "").strip(),
        contacto_correo=request.form.get("contacto_correo", "").strip(),
        fuente_recursos=request.form.get("fuente_recursos", "").strip(),
        observaciones=request.form.get("observaciones", "").strip(),
    )
    flash("Datos de gestión guardados.", "success")
    return redirect(url_for("web.cartera_detalle", oblig_id=oblig_id))


# ---------------------------------------------------------------------------
# POST /cartera/<id>/pago — Abono manual
# ---------------------------------------------------------------------------


@bp.route("/cartera/<int:oblig_id>/pago", methods=["POST"])
@require_permission("cartera.procesar")
def cartera_pago(oblig_id):
    """Registra un abono manual (pago/cobro hecho fuera de los módulos)."""
    from app.database import registrar_pago

    emp = base._empresa_actual()
    if not _obligacion_o_none(emp, oblig_id):
        flash("La obligación no existe o fue anulada.", "error")
        return redirect(url_for("web.cartera"))

    valor = _a_float(request.form.get("valor", ""))
    if not valor or valor <= 0:
        flash("El valor del abono debe ser mayor que cero.", "error")
        return redirect(url_for("web.cartera_detalle", oblig_id=oblig_id))

    aplicado = registrar_pago(
        oblig_id,
        valor,
        fecha=request.form.get("fecha", "").strip(),
        origen="manual",
        detalle=request.form.get("detalle", "").strip(),
        db_path=emp.db_path,
    )
    audit.registrar("cartera.abono", empresa_id=emp.id,
                    detalle=f"obligacion={oblig_id} valor={aplicado}")
    if aplicado < valor:
        flash(f"Abono aplicado por ${aplicado:,.0f} (la obligación tenía un "
              "saldo menor al valor digitado).", "success")
    else:
        flash(f"Abono de ${aplicado:,.0f} registrado.", "success")
    return redirect(url_for("web.cartera_detalle", oblig_id=oblig_id))


# ---------------------------------------------------------------------------
# POST /cartera/<id>/anular — Descarta la obligación
# ---------------------------------------------------------------------------


@bp.route("/cartera/<int:oblig_id>/anular", methods=["POST"])
@require_permission("cartera.gestionar")
def cartera_anular(oblig_id):
    """Anula una obligación (deja de aparecer en el tablero)."""
    from app.database import anular_obligacion

    emp = base._empresa_actual()
    if not _obligacion_o_none(emp, oblig_id):
        flash("La obligación no existe o ya estaba anulada.", "error")
        return redirect(url_for("web.cartera"))

    anular_obligacion(oblig_id, emp.db_path)
    audit.registrar("cartera.anular", empresa_id=emp.id,
                    detalle=f"obligacion={oblig_id}")
    flash("Obligación anulada.", "success")
    return redirect(url_for("web.cartera"))
