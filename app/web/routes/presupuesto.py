"""Módulo Sistema Presupuestal — Finanzas.

Presupuesto anual de flujo de caja por empresa: estructura de categorías y
líneas con mapeo de cuentas PUC, valores proyectados/ejecutados mes a mes
(manual, CSV o conector Siigo/Alegra) y análisis comparativo con semáforos.

La lógica de negocio vive en ``app/presupuesto`` (motor, análisis,
sincronización y conectores); aquí solo están las vistas Flask. El módulo usa
su propia BD (``presupuesto.db`` en DB_DIR, tablas con prefijo ``pres_``) y se
aísla por empresa vinculando cada empresa presupuestal con la empresa activa
de la sesión (columna ``ref_externa``).
"""

import json
import logging
from pathlib import Path

from flask import (
    abort, flash, redirect, render_template, request, send_file, url_for,
)

from app import audit
from app.authz import require_permission

from . import base
from .base import bp

logger = logging.getLogger(__name__)

_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
          "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# Estructura sugerida al crear un presupuesto desde cero (editable después).
_PLANTILLA_ESTANDAR = [
    ("Ingresos operacionales", "ingreso",
     ["Ventas de contado", "Recaudo de cartera"]),
    ("Otros ingresos", "ingreso", ["Otros ingresos"]),
    ("Gastos de administración", "egreso",
     ["Nómina", "Arriendo", "Servicios públicos", "Honorarios"]),
    ("Gastos de ventas", "egreso", ["Comisiones", "Publicidad"]),
    ("Impuestos", "egreso", ["Impuestos"]),
    ("Inversión y financiación", "egreso",
     ["Compra de activos (CAPEX)", "Pago de créditos"]),
]

_db_inicializada = False


def _sesion():
    """Sesión de la BD presupuestal, inicializando el esquema una sola vez."""
    global _db_inicializada
    from app.presupuesto.database import SessionLocal, init_db
    if not _db_inicializada:
        init_db()
        _db_inicializada = True
    return SessionLocal()


def _empresa_pres(db, emp):
    """Empresa presupuestal vinculada a la empresa activa (get-or-create).

    El vínculo es por ``ref_externa`` = id de la empresa en el registro
    principal; nombre y NIT se mantienen sincronizados en cada acceso.
    """
    from app.presupuesto.models import Empresa

    registro = db.query(Empresa).filter_by(ref_externa=emp.id).first()
    if registro is None:
        registro = Empresa(nombre=emp.nombre, nit=emp.nit, ref_externa=emp.id)
        db.add(registro)
        db.commit()
    elif registro.nombre != emp.nombre or registro.nit != emp.nit:
        registro.nombre = emp.nombre
        registro.nit = emp.nit
        db.commit()
    return registro


def _presupuesto_de(db, empresa_pres, presupuesto_id: int):
    """Presupuesto activo de la empresa actual, o None (evita cruzar empresas)."""
    from app.presupuesto.services import motor

    pres = motor.obtener_presupuesto(db, presupuesto_id)
    if pres is None or pres.empresa_id != empresa_pres.id or not pres.activo:
        return None
    return pres


def _a_float(crudo: str) -> float | None:
    """Parsea un monto aceptando formatos '1.234.567,89' y '1,234,567.89'."""
    txt = (crudo or "").strip().replace("$", "").replace(" ", "")
    if not txt:
        return None
    if "," in txt and "." in txt:
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif "," in txt:
        txt = txt.replace(",", ".") if txt.count(",") == 1 else txt.replace(",", "")
    elif txt.count(".") > 1:
        txt = txt.replace(".", "")  # solo puntos de miles: 5.000.000
    try:
        return float(txt)
    except ValueError:
        return None


def _mes_del_form(campo: str = "mes") -> int | None:
    try:
        mes = int(request.form.get(campo, ""))
    except (TypeError, ValueError):
        return None
    return mes if 1 <= mes <= 12 else None


def _config_conector(conector: str, previa: dict) -> str | None:
    """Arma el JSON de credenciales del conector desde el formulario.

    Los campos secretos (access_key / token) conservan el valor anterior si
    llegan vacíos, para poder editar el resto sin re-digitar la credencial.
    """
    if conector == "siigo":
        cfg = {
            "username": request.form.get("siigo_username", "").strip(),
            "access_key": request.form.get("siigo_access_key", "").strip()
                          or previa.get("access_key", ""),
            "partner_id": request.form.get("siigo_partner_id", "").strip() or "1ContaBot",
        }
    elif conector == "alegra":
        cfg = {
            "email": request.form.get("alegra_email", "").strip(),
            "token": request.form.get("alegra_token", "").strip()
                     or previa.get("token", ""),
        }
    elif conector == "csv":
        cfg = {"ruta": request.form.get("csv_ruta", "").strip()}
    else:  # manual
        return None
    return json.dumps(cfg)


# ---------------------------------------------------------------------------
# GET /presupuesto — Página inicial: presupuestos de la empresa + conector
# ---------------------------------------------------------------------------


@bp.route("/presupuesto")
@require_permission("presupuesto.ver")
def presupuesto():
    """Lista los presupuestos de la empresa activa y la configuración del conector."""
    from datetime import date

    from app.presupuesto.models import Presupuesto

    emp = base._empresa_actual()
    db = _sesion()
    try:
        emp_pres = _empresa_pres(db, emp)
        presupuestos = (
            db.query(Presupuesto)
            .filter_by(empresa_id=emp_pres.id, activo=True)
            .order_by(Presupuesto.anio.desc())
            .all()
        )
        conector = emp_pres.conector.value
        try:
            cfg = json.loads(emp_pres.conector_config or "{}")
        except ValueError:
            cfg = {}
        return render_template(
            "presupuesto.html",
            presupuestos=presupuestos,
            conector=conector,
            conector_cfg=cfg,
            anio_actual=date.today().year,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /presupuesto/crear — Crear presupuesto anual
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/crear", methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_crear():
    """Crea un presupuesto anual (opcionalmente con la estructura estándar)."""
    from app.presupuesto.models import (
        CategoriaPresupuesto, LineaPresupuesto, Presupuesto, TipoFlujo,
    )

    emp = base._empresa_actual()
    nombre = request.form.get("nombre", "").strip()
    try:
        anio = int(request.form.get("anio", ""))
    except (TypeError, ValueError):
        anio = 0
    if not nombre or not 2000 <= anio <= 2100:
        flash("Indica un nombre y un año válido para el presupuesto.", "error")
        return redirect(url_for("web.presupuesto"))

    saldo_inicial = _a_float(request.form.get("saldo_inicial", "")) or 0.0
    umbral_alerta = _a_float(request.form.get("umbral_alerta", "")) or 5.0
    umbral_critico = _a_float(request.form.get("umbral_critico", "")) or 15.0

    db = _sesion()
    try:
        emp_pres = _empresa_pres(db, emp)
        existente = (
            db.query(Presupuesto)
            .filter_by(empresa_id=emp_pres.id, anio=anio)
            .first()
        )
        if existente:
            if existente.activo:
                flash(f"Ya existe un presupuesto para el año {anio} en esta empresa.", "error")
                return redirect(url_for("web.presupuesto"))
            # Reutilizar el registro desactivado (la restricción única es empresa+año).
            db.delete(existente)
            db.flush()

        pres = Presupuesto(
            empresa_id=emp_pres.id, anio=anio, nombre=nombre,
            saldo_inicial_caja=saldo_inicial,
            umbral_alerta=umbral_alerta, umbral_critico=umbral_critico,
        )
        if request.form.get("plantilla_estandar"):
            for orden_c, (cat_nombre, tipo, lineas) in enumerate(_PLANTILLA_ESTANDAR):
                cat = CategoriaPresupuesto(
                    nombre=cat_nombre, tipo=TipoFlujo(tipo), orden=orden_c,
                )
                for orden_l, linea_nombre in enumerate(lineas):
                    cat.lineas.append(
                        LineaPresupuesto(nombre=linea_nombre, orden=orden_l)
                    )
                pres.categorias.append(cat)
        db.add(pres)
        db.commit()
        audit.registrar("presupuesto.crear", empresa_id=emp.id,
                        detalle=f"presupuesto={pres.id} {nombre} ({anio})")
        flash(f"Presupuesto «{nombre}» ({anio}) creado. Define la estructura y "
              f"carga el proyectado de los 12 meses.", "success")
        return redirect(url_for("web.presupuesto_detalle", presupuesto_id=pres.id))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /presupuesto/conector — Configurar el conector contable de la empresa
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/conector", methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_conector():
    """Guarda el conector (manual/siigo/alegra/csv) y sus credenciales."""
    from app.presupuesto.models import FuenteDato

    emp = base._empresa_actual()
    conector = request.form.get("conector", "manual").strip().lower()
    if conector not in {f.value for f in FuenteDato}:
        flash("Conector no válido.", "error")
        return redirect(url_for("web.presupuesto"))

    db = _sesion()
    try:
        emp_pres = _empresa_pres(db, emp)
        try:
            previa = json.loads(emp_pres.conector_config or "{}")
        except ValueError:
            previa = {}
        emp_pres.conector = FuenteDato(conector)
        emp_pres.conector_config = _config_conector(conector, previa)
        db.commit()
        audit.registrar("presupuesto.conector", empresa_id=emp.id,
                        detalle=f"conector={conector}")
        flash(f"Conector «{conector}» guardado para {emp.nombre}.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto"))


@bp.route("/presupuesto/probar-conexion", methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_probar_conexion():
    """Prueba las credenciales del conector configurado."""
    from app.presupuesto.connectors import crear_conector
    from app.presupuesto.models import FuenteDato

    emp = base._empresa_actual()
    db = _sesion()
    try:
        emp_pres = _empresa_pres(db, emp)
        if emp_pres.conector == FuenteDato.MANUAL:
            flash("La empresa no tiene conector automático configurado.", "error")
            return redirect(url_for("web.presupuesto"))
        try:
            conector = crear_conector(
                emp_pres.conector, json.loads(emp_pres.conector_config or "{}")
            )
            exito, mensaje = conector.probar_conexion()
        except Exception as exc:  # credenciales malformadas, red, etc.
            exito, mensaje = False, str(exc)
        flash(f"Conexión {emp_pres.conector.value}: {mensaje}",
              "success" if exito else "error")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto"))


# ---------------------------------------------------------------------------
# GET /presupuesto/plantilla-csv — Plantilla de ejemplo del ejecutado
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/plantilla-csv")
@require_permission("presupuesto.ver")
def presupuesto_plantilla_csv():
    """Descarga la plantilla CSV de ejemplo para importar el ejecutado."""
    ruta = Path(__file__).resolve().parents[2] / "presupuesto" / "plantillas" / "ejecutado_ejemplo.csv"
    return send_file(ruta, as_attachment=True,
                     download_name="ejecutado_ejemplo.csv", mimetype="text/csv")


# ---------------------------------------------------------------------------
# GET /presupuesto/<id> — Detalle: dashboard, valores, estructura y sync
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/<int:presupuesto_id>")
@require_permission("presupuesto.ver")
def presupuesto_detalle(presupuesto_id):
    """Dashboard del presupuesto: análisis, matriz, valores y estructura."""
    from app.presupuesto.models import LogSincronizacion
    from app.presupuesto.services import analisis as srv_analisis
    from app.presupuesto.services import motor as srv_motor

    emp = base._empresa_actual()
    mes = request.args.get("mes", type=int)
    if mes is not None and not 1 <= mes <= 12:
        mes = None

    db = _sesion()
    try:
        emp_pres = _empresa_pres(db, emp)
        pres = _presupuesto_de(db, emp_pres, presupuesto_id)
        if pres is None:
            flash("El presupuesto no existe o pertenece a otra empresa.", "error")
            return redirect(url_for("web.presupuesto"))

        flujo = srv_motor.construir_flujo_caja(db, pres.id)
        analisis = srv_analisis.analizar(db, pres.id, mes)
        logs = (
            db.query(LogSincronizacion)
            .filter_by(presupuesto_id=pres.id)
            .order_by(LogSincronizacion.ejecutado_en.desc())
            .limit(20)
            .all()
        )
        return render_template(
            "presupuesto_detalle.html",
            pres=pres,
            emp_pres=emp_pres,
            flujo=flujo,
            analisis=analisis,
            mes=mes,
            meses=_MESES,
            logs=logs,
            flujo_json=json.dumps({
                "meses": flujo.meses,
                "neto_p": flujo.flujo_neto_proyectado,
                "neto_e": flujo.flujo_neto_ejecutado,
                "saldo_p": flujo.saldo_acumulado_proyectado,
                "saldo_e": flujo.saldo_acumulado_ejecutado,
                "ultimo_mes": analisis.resumen.get("ultimo_mes_ejecutado", 0),
            }),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /presupuesto/<id>/eliminar — Desactivar presupuesto
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/<int:presupuesto_id>/eliminar", methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_eliminar(presupuesto_id):
    """Desactiva el presupuesto (conserva los datos, deja de listarse)."""
    emp = base._empresa_actual()
    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        if pres is None:
            flash("El presupuesto no existe o pertenece a otra empresa.", "error")
        else:
            pres.activo = False
            db.commit()
            audit.registrar("presupuesto.eliminar", empresa_id=emp.id,
                            detalle=f"presupuesto={pres.id} {pres.nombre}")
            flash(f"Presupuesto «{pres.nombre}» ({pres.anio}) eliminado.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto"))


# ---------------------------------------------------------------------------
# Estructura: categorías y líneas
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/<int:presupuesto_id>/categoria", methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_categoria_crear(presupuesto_id):
    """Agrega una categoría (ingreso/egreso) al presupuesto."""
    from app.presupuesto.models import CategoriaPresupuesto, TipoFlujo

    emp = base._empresa_actual()
    nombre = request.form.get("nombre", "").strip()
    tipo = request.form.get("tipo", "").strip().lower()
    if not nombre or tipo not in ("ingreso", "egreso"):
        flash("Indica el nombre y el tipo (ingreso/egreso) de la categoría.", "error")
        return redirect(url_for("web.presupuesto_detalle",
                                presupuesto_id=presupuesto_id) + "#estructura")

    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        if pres is None:
            flash("El presupuesto no existe o pertenece a otra empresa.", "error")
            return redirect(url_for("web.presupuesto"))
        orden = max((c.orden for c in pres.categorias), default=-1) + 1
        db.add(CategoriaPresupuesto(
            presupuesto_id=pres.id, nombre=nombre,
            tipo=TipoFlujo(tipo), orden=orden,
        ))
        db.commit()
        audit.registrar("presupuesto.categoria_crear", empresa_id=emp.id,
                        detalle=f"presupuesto={pres.id} categoria={nombre} ({tipo})")
        flash(f"Categoría «{nombre}» agregada.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto_detalle",
                            presupuesto_id=presupuesto_id) + "#estructura")


@bp.route("/presupuesto/<int:presupuesto_id>/categoria/<int:categoria_id>/eliminar",
          methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_categoria_eliminar(presupuesto_id, categoria_id):
    """Elimina una categoría con sus líneas, valores y mapeos."""
    emp = base._empresa_actual()
    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        cat = next((c for c in (pres.categorias if pres else []) if c.id == categoria_id), None)
        if cat is None:
            flash("La categoría no existe en este presupuesto.", "error")
        else:
            db.delete(cat)
            db.commit()
            audit.registrar("presupuesto.categoria_eliminar", empresa_id=emp.id,
                            detalle=f"presupuesto={presupuesto_id} categoria={cat.nombre}")
            flash(f"Categoría «{cat.nombre}» eliminada.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto_detalle",
                            presupuesto_id=presupuesto_id) + "#estructura")


def _parsear_cuentas(crudo: str) -> list[str]:
    """'4135, 4210;4250' → ['4135', '4210', '4250'] (prefijos PUC)."""
    partes = crudo.replace(";", ",").split(",")
    return [p.strip() for p in partes if p.strip()]


@bp.route("/presupuesto/<int:presupuesto_id>/linea", methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_linea_crear(presupuesto_id):
    """Agrega una línea presupuestal a una categoría, con sus cuentas PUC."""
    from app.presupuesto.models import LineaPresupuesto, MapeoCuenta

    emp = base._empresa_actual()
    nombre = request.form.get("nombre", "").strip()
    categoria_id = request.form.get("categoria_id", type=int)
    if not nombre or not categoria_id:
        flash("Indica la categoría y el nombre de la línea.", "error")
        return redirect(url_for("web.presupuesto_detalle",
                                presupuesto_id=presupuesto_id) + "#estructura")

    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        cat = next((c for c in (pres.categorias if pres else []) if c.id == categoria_id), None)
        if cat is None:
            flash("La categoría no existe en este presupuesto.", "error")
            return redirect(url_for("web.presupuesto_detalle",
                                    presupuesto_id=presupuesto_id) + "#estructura")
        linea = LineaPresupuesto(
            nombre=nombre,
            orden=max((l.orden for l in cat.lineas), default=-1) + 1,
        )
        for cuenta in _parsear_cuentas(request.form.get("cuentas", "")):
            linea.mapeos.append(MapeoCuenta(codigo_cuenta=cuenta))
        cat.lineas.append(linea)
        db.commit()
        audit.registrar("presupuesto.linea_crear", empresa_id=emp.id,
                        detalle=f"presupuesto={pres.id} linea={nombre}")
        flash(f"Línea «{nombre}» agregada a {cat.nombre}.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto_detalle",
                            presupuesto_id=presupuesto_id) + "#estructura")


def _linea_de(pres, linea_id: int):
    for cat in (pres.categorias if pres else []):
        for linea in cat.lineas:
            if linea.id == linea_id:
                return linea
    return None


@bp.route("/presupuesto/<int:presupuesto_id>/linea/<int:linea_id>/cuentas",
          methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_linea_cuentas(presupuesto_id, linea_id):
    """Reemplaza los prefijos de cuentas PUC mapeados a una línea."""
    from app.presupuesto.models import MapeoCuenta

    emp = base._empresa_actual()
    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        linea = _linea_de(pres, linea_id)
        if linea is None:
            flash("La línea no existe en este presupuesto.", "error")
        else:
            for m in list(linea.mapeos):
                db.delete(m)
            for cuenta in _parsear_cuentas(request.form.get("cuentas", "")):
                linea.mapeos.append(MapeoCuenta(codigo_cuenta=cuenta))
            db.commit()
            audit.registrar("presupuesto.linea_cuentas", empresa_id=emp.id,
                            detalle=f"presupuesto={presupuesto_id} linea={linea.nombre}")
            flash(f"Cuentas de «{linea.nombre}» actualizadas.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto_detalle",
                            presupuesto_id=presupuesto_id) + "#estructura")


@bp.route("/presupuesto/<int:presupuesto_id>/linea/<int:linea_id>/eliminar",
          methods=["POST"])
@require_permission("presupuesto.gestionar")
def presupuesto_linea_eliminar(presupuesto_id, linea_id):
    """Elimina una línea con sus valores y mapeos."""
    emp = base._empresa_actual()
    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        linea = _linea_de(pres, linea_id)
        if linea is None:
            flash("La línea no existe en este presupuesto.", "error")
        else:
            db.delete(linea)
            db.commit()
            audit.registrar("presupuesto.linea_eliminar", empresa_id=emp.id,
                            detalle=f"presupuesto={presupuesto_id} linea={linea.nombre}")
            flash(f"Línea «{linea.nombre}» eliminada.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto_detalle",
                            presupuesto_id=presupuesto_id) + "#estructura")


# ---------------------------------------------------------------------------
# POST /presupuesto/<id>/valores — Guardar la matriz (proyectado o ejecutado)
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/<int:presupuesto_id>/valores", methods=["POST"])
@require_permission("presupuesto.procesar")
def presupuesto_valores(presupuesto_id):
    """Guarda los valores mensuales digitados en la matriz.

    Los inputs llegan como ``v_<linea_id>_<mes>``; los vacíos no se tocan
    (para borrar un valor se digita 0).
    """
    from app.presupuesto.models import FuenteDato, TipoValor
    from app.presupuesto.schemas import ValorItem
    from app.presupuesto.services import motor as srv_motor

    tipo = request.form.get("tipo", "")
    if tipo not in (TipoValor.PROYECTADO.value, TipoValor.EJECUTADO.value):
        abort(400)

    emp = base._empresa_actual()
    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        if pres is None:
            flash("El presupuesto no existe o pertenece a otra empresa.", "error")
            return redirect(url_for("web.presupuesto"))

        lineas_validas = {
            l.id for c in pres.categorias for l in c.lineas
        }
        items = []
        for campo, crudo in request.form.items():
            if not campo.startswith("v_"):
                continue
            try:
                _, linea_id, mes = campo.split("_")
                linea_id, mes = int(linea_id), int(mes)
            except ValueError:
                continue
            valor = _a_float(crudo)
            if valor is None or linea_id not in lineas_validas or not 1 <= mes <= 12:
                continue
            items.append(ValorItem(linea_id=linea_id, mes=mes, valor=valor))

        n = srv_motor.guardar_valores(db, TipoValor(tipo), FuenteDato.MANUAL, items)
        audit.registrar("presupuesto.valores", empresa_id=emp.id,
                        detalle=f"presupuesto={pres.id} tipo={tipo} valores={n}")
        flash(f"{n} valores {tipo}s guardados.", "success")
    finally:
        db.close()
    return redirect(url_for("web.presupuesto_detalle",
                            presupuesto_id=presupuesto_id) + "#valores")


# ---------------------------------------------------------------------------
# Ejecutado: importación CSV y sincronización con el software contable
# ---------------------------------------------------------------------------


@bp.route("/presupuesto/<int:presupuesto_id>/importar-csv", methods=["POST"])
@require_permission("presupuesto.procesar")
def presupuesto_importar_csv(presupuesto_id):
    """Importa el ejecutado de un mes desde un balance de prueba/auxiliar CSV."""
    from app.presupuesto.connectors.csv_file import parsear_contenido
    from app.presupuesto.services.sincronizacion import sincronizar_ejecutado

    emp = base._empresa_actual()
    destino = url_for("web.presupuesto_detalle",
                      presupuesto_id=presupuesto_id) + "#sync"

    mes = _mes_del_form()
    if mes is None:
        flash("Selecciona el mes del ejecutado a importar.", "error")
        return redirect(destino)
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        flash("Selecciona el archivo CSV (columnas: codigo_cuenta, "
              "nombre_cuenta, valor).", "error")
        return redirect(destino)
    if not base._allowed_csv(archivo.filename):
        flash("El archivo debe ser un CSV (.csv o .txt).", "error")
        return redirect(destino)

    try:
        movimientos = parsear_contenido(archivo.read().decode("utf-8-sig"))
    except Exception:
        logger.exception("Error leyendo el CSV del ejecutado")
        movimientos = []
    if not movimientos:
        flash("El CSV no contiene movimientos válidos (columnas: "
              "codigo_cuenta, nombre_cuenta, valor).", "error")
        return redirect(destino)

    db = _sesion()
    try:
        pres = _presupuesto_de(db, _empresa_pres(db, emp), presupuesto_id)
        if pres is None:
            flash("El presupuesto no existe o pertenece a otra empresa.", "error")
            return redirect(url_for("web.presupuesto"))
        r = sincronizar_ejecutado(db, pres.id, mes, movimientos=movimientos)
        audit.registrar("presupuesto.importar_csv", empresa_id=emp.id,
                        resultado="ok" if r.exito else "error",
                        detalle=f"presupuesto={pres.id} mes={mes} lineas={r.lineas_actualizadas}")
        flash(r.mensaje, "success" if r.exito else "error")
    finally:
        db.close()
    return redirect(destino)


@bp.route("/presupuesto/<int:presupuesto_id>/sincronizar", methods=["POST"])
@require_permission("presupuesto.procesar")
def presupuesto_sincronizar(presupuesto_id):
    """Trae el ejecutado del mes desde el conector configurado (Siigo/Alegra/CSV)."""
    from app.presupuesto.models import FuenteDato
    from app.presupuesto.services.sincronizacion import sincronizar_ejecutado

    emp = base._empresa_actual()
    destino = url_for("web.presupuesto_detalle",
                      presupuesto_id=presupuesto_id) + "#sync"

    mes = _mes_del_form()
    if mes is None:
        flash("Selecciona el mes a sincronizar.", "error")
        return redirect(destino)

    db = _sesion()
    try:
        emp_pres = _empresa_pres(db, emp)
        pres = _presupuesto_de(db, emp_pres, presupuesto_id)
        if pres is None:
            flash("El presupuesto no existe o pertenece a otra empresa.", "error")
            return redirect(url_for("web.presupuesto"))
        if emp_pres.conector == FuenteDato.MANUAL:
            flash("La empresa tiene conector «manual»: configura Siigo, Alegra "
                  "o CSV en la página del módulo, o importa el ejecutado por CSV.",
                  "error")
            return redirect(destino)
        r = sincronizar_ejecutado(db, pres.id, mes)
        audit.registrar("presupuesto.sincronizar", empresa_id=emp.id,
                        resultado="ok" if r.exito else "error",
                        detalle=f"presupuesto={pres.id} mes={mes} lineas={r.lineas_actualizadas}")
        flash(r.mensaje, "success" if r.exito else "error")
    finally:
        db.close()
    return redirect(destino)
