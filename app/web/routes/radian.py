"""Módulo RADIAN: pipeline, resultado editable y exportación a SIIGO."""

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
    bp, KEY_RESULTADO,
)

logger = logging.getLogger(__name__)


@bp.route("/radian")
@require_permission("radian.ver")
def radian():
    """Página inicial del módulo RADIAN (misma estructura visual que Bancos).

    Presenta qué hace el módulo, el formulario de carga del reporte RADIAN
    (que reutiliza el pipeline de /procesar) y la actividad reciente del módulo.
    """
    emp = base._empresa_actual()
    return render_template("radian_upload.html", actividad=_actividad_radian(emp))


@bp.route("/procesar", methods=["POST"])
@require_permission("radian.procesar")
def procesar():
    """Recibe archivos, corre el pipeline y guarda el resultado server-side."""
    # Validar archivo RADIAN obligatorio
    if "radian" not in request.files or request.files["radian"].filename == "":
        flash("Debes seleccionar un archivo RADIAN.", "error")
        return redirect(url_for("web.index"))

    radian_file = request.files["radian"]
    if not base._allowed(radian_file.filename):
        flash("El archivo RADIAN debe ser .xlsx o .xls", "error")
        return redirect(url_for("web.index"))

    # Empresa activa (validada): aísla uploads, maestros y BD por empresa.
    emp = base._empresa_actual()

    # Leer bytes en memoria para evitar problemas de ruta en Windows
    radian_bytes = radian_file.read()
    radian_ref = base._save_upload(radian_bytes, radian_file.filename, emp)
    radian_path = store.load_file(radian_ref)  # ruta local para el pipeline

    # Archivos maestros opcionales (propios de la empresa seleccionada)
    terceros_path = cuentas_path = comprobantes_path = None
    for key, default_name in [
        ("terceros",     "Listado_de_Terceros.xlsx"),
        ("cuentas",      "Listado_de_Cuentas_Contables.xlsx"),
        ("comprobantes", "Tipos_de_comprobante_contable.xlsx"),
    ]:
        f = request.files.get(key)
        if f and f.filename and base._allowed(f.filename):
            from app.maestros import validar_maestro
            file_bytes = f.read()
            error = validar_maestro(key, file_bytes)
            if error:
                # Archivo en la casilla equivocada: no se guarda; se usa el maestro
                # ya configurado de la empresa y se avisa al usuario.
                flash(error, "error")
                try:
                    path = emp.ruta_maestro(default_name)
                except FileNotFoundError:
                    path = str(Path(base._project_root()) / emp.data_category / default_name)
            else:
                ref = store.save_file(file_bytes, emp.data_category, default_name)
                path = store.load_file(ref)
        else:
            try:
                path = emp.ruta_maestro(default_name)
            except FileNotFoundError:
                path = str(Path(base._project_root()) / emp.data_category / default_name)
        if key == "terceros":
            terceros_path = path
        elif key == "cuentas":
            cuentas_path = path
        else:
            comprobantes_path = path

    db = emp.db_path
    incluir_dup = request.form.get("incluir_duplicados") == "on"

    # Registrar la importación antes de procesar: si el pipeline falla, el
    # archivo RADIAN queda guardado y la importación puede retomarse después.
    from app.database import inicializar_db, registrar_importacion, actualizar_importacion
    inicializar_db(db)
    imp_id = registrar_importacion(
        archivo_nombre=secure_filename(radian_file.filename),
        archivo_ref=radian_ref,
        db_path=db,
    )

    try:
        resultado = _ejecutar_pipeline(
            radian_path, terceros_path, cuentas_path,
            comprobantes_path, db, incluir_dup, empresa=emp,
        )
        resultado["importacion_id"] = imp_id
        session_store.guardar(KEY_RESULTADO, resultado)
        # Persistir el snapshot durable (estado 'procesada'): permite retomar la
        # importación más tarde conservando lo trabajado, sin reprocesar.
        _persistir_importacion(emp, resultado, "procesada")
        audit.registrar(
            "radian.procesar", empresa_id=emp.id,
            detalle=f"importacion={imp_id} docs={resultado['n_docs']} "
                    f"excepciones={resultado['n_excepciones']}",
        )
        flash(f"✓ Procesados {resultado['n_docs']} documentos. "
              f"{resultado['n_excepciones']} con excepciones.", "success")
        return redirect(url_for("web.resultado"))

    except Exception as exc:
        logger.exception("Error en pipeline web")
        actualizar_importacion(imp_id, estado="error", error=str(exc), db_path=db)
        flash(f"Error al procesar: {exc}. El archivo quedó guardado: puedes "
              f"retomar esta importación desde la página de Importaciones.", "error")
        return redirect(url_for("web.importaciones"))


def _ejecutar_pipeline(
    radian_path, terceros_path, cuentas_path,
    comprobantes_path, db, incluir_duplicados, empresa=None,
) -> dict:
    """Ejecuta el pipeline completo y retorna un dict con los resultados.

    Envoltorio web de `app.pipeline.ejecutar_pipeline`: resuelve la empresa
    activa y la carpeta de salida del proyecto. La lógica vive en
    `app/pipeline.py` para que la importación automática (CLI/scheduler) la
    reutilice sin depender de Flask.
    """
    from app.pipeline import ejecutar_pipeline

    if empresa is None:
        empresa = base._empresa_actual()

    return ejecutar_pipeline(
        radian_path, terceros_path, cuentas_path, comprobantes_path,
        db, incluir_duplicados, empresa,
        output_dir=os.path.join(base._project_root(), "output"),
    )


# ---------------------------------------------------------------------------
# GET /resultado — Tabla de preasientos
# ---------------------------------------------------------------------------


@bp.route("/resultado")
@require_permission("radian.ver")
def resultado():
    """Muestra los preasientos y excepciones del último proceso."""
    datos = session_store.cargar(KEY_RESULTADO)
    if not datos:
        flash("No hay resultados. Procesa primero un archivo RADIAN.", "info")
        return redirect(url_for("web.index"))

    return render_template("resultado.html", datos=datos)


# ---------------------------------------------------------------------------
# POST /confirmar — Registrar cuenta en historial
# ---------------------------------------------------------------------------


@bp.route("/confirmar", methods=["POST"])
@require_permission("radian.editar")
def confirmar():
    """Registra una cuenta en el historial del motor de sugerencias."""
    from app.sugerencias import registrar_confirmacion

    clasificacion = request.form.get("clasificacion", "")
    nit_tercero   = request.form.get("nit_tercero", "")
    tipo_linea    = request.form.get("tipo_linea", "")
    cuenta        = request.form.get("cuenta", "").strip()
    cufe_full     = request.form.get("cufe_full", "")
    numero_linea  = request.form.get("numero_linea", "")

    if not all([clasificacion, nit_tercero, tipo_linea, cuenta]):
        flash("Datos incompletos para confirmar la cuenta.", "error")
    else:
        registrar_confirmacion(clasificacion, nit_tercero, tipo_linea, cuenta,
                               base._empresa_actual().db_path)

        # Actualizar la cuenta en el resultado guardado para reflejarla en pantalla
        resultado = session_store.cargar(KEY_RESULTADO)
        if resultado and cufe_full and numero_linea:
            try:
                num = int(numero_linea)
                for p in resultado.get("preasientos", []):
                    if p.get("cufe_full") == cufe_full:
                        for linea in p.get("lineas", []):
                            if linea.get("numero_linea") == num:
                                linea["cuenta"] = cuenta
                                linea["es_pendiente"] = False
                                break
                        # Alimentar también el motor de aprendizaje generalizado
                        # (predice por texto para terceros nuevos).
                        try:
                            from app import aprendizaje
                            aprendizaje.aprender(
                                "radian", f"cuenta_{tipo_linea}",
                                f"{clasificacion} {p.get('tercero_nombre', '')}",
                                cuenta, base._empresa_actual().db_path,
                            )
                        except Exception:
                            logger.exception("No se pudo aprender la confirmación.")
                        break
                session_store.guardar(KEY_RESULTADO, resultado)
                _persistir_importacion(base._empresa_actual(), resultado, "corregida")
            except (ValueError, TypeError):
                pass

        flash(f"✓ Cuenta {cuenta} confirmada para {clasificacion} / NIT {nit_tercero}.", "success")

    return redirect(url_for("web.resultado"))


# ---------------------------------------------------------------------------
# POST /corregir-tercero — Editar el tercero de un preasiento
# ---------------------------------------------------------------------------


def _resolver_tercero(emp, nit: str, nombre_form: str) -> tuple[str, bool]:
    """Resuelve el nombre y la presencia en maestro de un tercero por su NIT.

    Si el NIT existe en el maestro de la empresa, retorna su nombre oficial y
    encontrado=True; de lo contrario usa el nombre que envió el formulario y
    encontrado=False.
    """
    from app.importador import cargar_maestro_terceros
    from app.terceros import cruzar_tercero

    try:
        terceros_path = emp.ruta_maestro("Listado_de_Terceros.xlsx")
        df = base._cargar_maestro_cacheado(cargar_maestro_terceros, terceros_path)
        cruce = cruzar_tercero(nit, df)
    except Exception:
        cruce = None

    if cruce:
        nombre = cruce.get("Nombre tercero", "") or nombre_form
        return nombre, True
    return nombre_form, False


@bp.route("/corregir-tercero", methods=["POST"])
@require_permission("radian.editar")
def corregir_tercero():
    """Corrige el tercero de un preasiento y aprende la corrección.

    Actualiza el resultado guardado en sesión (para reflejarlo en pantalla y en
    la exportación SIIGO) y registra la corrección original→corregido en la BD
    para trazabilidad y para reaplicarla automáticamente en futuras importaciones.
    """
    from app.database import inicializar_db, registrar_correccion_tercero

    cufe_full     = request.form.get("cufe_full", "").strip()
    nit_nuevo     = request.form.get("nit_tercero", "").strip()
    nombre_form   = request.form.get("nombre_tercero", "").strip()
    nit_original  = request.form.get("nit_original", "").strip()
    nombre_orig   = request.form.get("nombre_original", "").strip()
    clasificacion = request.form.get("clasificacion", "").strip()

    if not cufe_full or not nit_nuevo:
        flash("Datos incompletos para corregir el tercero.", "error")
        return redirect(url_for("web.resultado"))

    emp = base._empresa_actual()
    nombre_nuevo, encontrado = _resolver_tercero(emp, nit_nuevo, nombre_form)

    # Actualizar el resultado en sesión para reflejar el cambio en pantalla.
    resultado = session_store.cargar(KEY_RESULTADO)
    actualizado = False
    if resultado:
        for p in resultado.get("preasientos", []):
            if p.get("cufe_full") == cufe_full:
                if not nit_original:
                    nit_original = p.get("tercero_nit_original") or p.get("tercero_nit", "")
                    nombre_orig = nombre_orig or p.get("tercero_nombre", "")
                p["tercero_nit"]       = nit_nuevo
                p["tercero_nombre"]    = nombre_nuevo
                p["tercero_encontrado"] = encontrado
                p["tercero_corregido"] = True
                actualizado = True
                break
        if actualizado:
            session_store.guardar(KEY_RESULTADO, resultado)
            _persistir_importacion(emp, resultado, "corregida")

    if not actualizado:
        flash("No se encontró el documento a corregir en la sesión.", "error")
        return redirect(url_for("web.resultado"))

    # Registrar la corrección solo si realmente cambió algo respecto al original.
    cambio = nit_original and (nit_nuevo != nit_original or nombre_nuevo != nombre_orig)
    if cambio:
        inicializar_db(emp.db_path)
        registrar_correccion_tercero(
            nit_original=nit_original,
            nombre_original=nombre_orig,
            nit_corregido=nit_nuevo,
            nombre_corregido=nombre_nuevo,
            clasificacion=clasificacion,
            db_path=emp.db_path,
        )

    audit.registrar(
        "radian.corregir_tercero", empresa_id=emp.id,
        detalle=f"cufe={cufe_full} {nit_original or '?'}→{nit_nuevo}",
    )
    flash(f"✓ Tercero actualizado a {nombre_nuevo or nit_nuevo} (NIT {nit_nuevo}).", "success")
    return redirect(url_for("web.resultado"))


# ---------------------------------------------------------------------------
# POST /dividir-linea — Partir una línea de un preasiento en varias cuentas
# ---------------------------------------------------------------------------


def _recalcular_preasiento(p: dict) -> None:
    """Recalcula `cuadra` y `excepciones` de un preasiento serializado en sesión.

    Replica la lógica de `app.preasiento.generar_preasiento`: el preasiento
    cuadra si Σ débitos = Σ créditos (tolerancia $0.01) y se listan como
    excepciones el descuadre y las líneas con cuenta [PENDIENTE].
    """
    lineas = p.get("lineas", [])
    total_d = sum(float(l.get("debito", 0) or 0) for l in lineas)
    total_c = sum(float(l.get("credito", 0) or 0) for l in lineas)
    cuadra = abs(total_d - total_c) < 0.01
    p["cuadra"] = cuadra

    exc = []
    if not cuadra:
        exc.append(f"No cuadra: débitos={total_d:.2f}, créditos={total_c:.2f}")
    n_pend = sum(1 for l in lineas if l.get("es_pendiente"))
    if n_pend:
        exc.append(f"{n_pend} línea(s) con cuenta [PENDIENTE]")
    p["excepciones"] = exc


@bp.route("/dividir-linea", methods=["POST"])
@require_permission("radian.editar")
def dividir_linea():
    """Divide una línea de un preasiento en varias partes (cuentas distintas).

    Cada parte conserva el mismo lado contable (débito o crédito) de la línea
    original; la suma de las partes debe igualar el monto original para
    preservar el cuadre. Sirve para separar, por ejemplo, un pago en capital +
    intereses, o repartir una base/gasto entre varias cuentas. El resultado se
    actualiza en sesión y se refleja en pantalla y en la exportación SIIGO.
    """
    cufe_full    = request.form.get("cufe_full", "").strip()
    numero_linea = request.form.get("numero_linea", "").strip()

    cuentas   = request.form.getlist("parte_cuenta")
    montos    = request.form.getlist("parte_monto")
    conceptos = request.form.getlist("parte_concepto")

    if not cufe_full or not numero_linea:
        flash("Datos incompletos para dividir la línea.", "error")
        return redirect(url_for("web.resultado"))

    datos = session_store.cargar(KEY_RESULTADO)
    if not datos:
        flash("No hay resultados en sesión. Procesa primero un archivo RADIAN.", "error")
        return redirect(url_for("web.index"))

    try:
        num = int(numero_linea)
    except ValueError:
        flash("Número de línea inválido.", "error")
        return redirect(url_for("web.resultado"))

    # Localizar preasiento y línea a dividir
    preasiento = next(
        (p for p in datos.get("preasientos", []) if p.get("cufe_full") == cufe_full),
        None,
    )
    if not preasiento:
        flash("No se encontró el documento a dividir en la sesión.", "error")
        return redirect(url_for("web.resultado"))

    lineas = preasiento.get("lineas", [])
    pos = next((i for i, l in enumerate(lineas) if l.get("numero_linea") == num), None)
    if pos is None:
        flash("No se encontró la línea a dividir.", "error")
        return redirect(url_for("web.resultado"))

    original = lineas[pos]
    es_debito = float(original.get("debito", 0) or 0) > 0
    monto_original = float(original.get("debito", 0) or 0) if es_debito \
        else float(original.get("credito", 0) or 0)

    # Construir las partes a partir del formulario (ignorando filas vacías)
    partes = []
    for i, cta in enumerate(cuentas):
        cta = (cta or "").strip()
        monto_raw = (montos[i] if i < len(montos) else "").strip()
        if not cta and not monto_raw:
            continue
        if not cta:
            flash("Cada parte debe tener una cuenta contable.", "error")
            return redirect(url_for("web.resultado"))
        try:
            monto = round(float(monto_raw), 2)
        except ValueError:
            flash(f"Monto inválido en una de las partes: '{monto_raw}'.", "error")
            return redirect(url_for("web.resultado"))
        if monto <= 0:
            flash("Cada parte debe tener un monto mayor que cero.", "error")
            return redirect(url_for("web.resultado"))
        concepto = (conceptos[i].strip() if i < len(conceptos) and conceptos[i] else
                    original.get("concepto", ""))
        partes.append({"cuenta": cta, "monto": monto, "concepto": concepto})

    if len(partes) < 2:
        flash("Indica al menos dos partes para dividir la línea.", "error")
        return redirect(url_for("web.resultado"))

    suma = round(sum(p["monto"] for p in partes), 2)
    if abs(suma - round(monto_original, 2)) >= 0.01:
        flash(
            f"La suma de las partes (${suma:,.2f}) debe igualar el monto "
            f"original (${monto_original:,.2f}).",
            "error",
        )
        return redirect(url_for("web.resultado"))

    # Reemplazar la línea original por las partes (mismo lado contable)
    nuevas = []
    for parte in partes:
        nuevas.append({
            "numero_linea": 0,  # se renumera abajo
            "cuenta": parte["cuenta"],
            "descripcion_cuenta": original.get("descripcion_cuenta", ""),
            "debito": parte["monto"] if es_debito else 0.0,
            "credito": 0.0 if es_debito else parte["monto"],
            "concepto": parte["concepto"],
            "es_pendiente": False,
            "es_sugerida": False,
        })
    lineas[pos:pos + 1] = nuevas

    # Renumerar líneas de forma consecutiva (numero_linea debe ser único)
    for i, l in enumerate(lineas, start=1):
        l["numero_linea"] = i

    _recalcular_preasiento(preasiento)
    session_store.guardar(KEY_RESULTADO, datos)
    emp = base._empresa_actual()
    _persistir_importacion(emp, datos, "corregida")

    audit.registrar(
        "radian.dividir_linea", empresa_id=emp.id if emp else None,
        detalle=f"cufe={cufe_full} linea={num} partes={len(partes)}",
    )
    flash(f"✓ Línea dividida en {len(partes)} cuentas.", "success")
    return redirect(url_for("web.resultado"))


# ---------------------------------------------------------------------------
# GET /historial — Motor de sugerencias
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /descargar — Descargar Excel
# ---------------------------------------------------------------------------

@bp.route("/descargar")
@require_permission("radian.exportar")
def descargar():
    """Envía el archivo Excel generado como descarga."""
    datos = session_store.cargar(KEY_RESULTADO)
    if not datos or not datos.get("excel_path"):
        flash("No hay archivo Excel disponible.", "error")
        return redirect(url_for("web.index"))

    excel_ref = datos["excel_path"]
    if not store.file_exists(excel_ref):
        flash("El archivo Excel ya no existe en el servidor.", "error")
        return redirect(url_for("web.index"))

    content = store.get_download_bytes(excel_ref)
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=Path(excel_ref.replace('blob://', '')).name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Importaciones — historial persistente, retomar y regenerar archivos
# ---------------------------------------------------------------------------


def _persistir_importacion(emp, datos: dict, estado: str) -> None:
    """Guarda el snapshot editable durable de una importación RADIAN.

    Es la copia durable (en BD) del resultado que vive en la sesión de trabajo:
    así "Abrir" una importación recupera lo trabajado (correcciones de tercero,
    divisiones, cuentas confirmadas) sin reprocesar el archivo. Best-effort: si
    falla la persistencia durable no se rompe la edición (ya guardada en sesión).
    """
    import json as _json
    from app.database import actualizar_importacion

    imp_id = datos.get("importacion_id")
    if not imp_id:
        return
    try:
        actualizar_importacion(
            imp_id,
            estado=estado,
            n_docs=int(datos.get("n_docs", 0) or 0),
            n_excepciones=int(datos.get("n_excepciones", 0) or 0),
            excel_ref=datos.get("excel_path") or None,
            preasientos_json=_json.dumps(datos, ensure_ascii=False),
            db_path=emp.db_path,
        )
    except Exception:
        logger.exception("No se pudo persistir el snapshot de la importación %s", imp_id)


def _generar_archivos_siigo(datos: dict, incluir_pendientes: bool = False) -> list:
    """Genera el/los Excel en formato SIIGO a partir de un resultado.

    `datos` es el dict del resultado (de la sesión de trabajo o de un snapshot
    durable de importación). Reconstruye los PreasientoContable serializados y
    delega en el exportador SIIGO. Retorna la lista de rutas generadas.
    """
    from app.siigo.exportador_siigo import exportar_siigo as _exportar
    from app.importador import cargar_maestro_cuentas

    preasientos = _deserializar_preasientos(datos.get("preasientos", []))

    # Cargar plan de cuentas para determinar columnas de vencimiento (cols 13-16)
    try:
        _cuentas_path = base._empresa_actual().ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
        df_cuentas_siigo = cargar_maestro_cuentas(_cuentas_path)
    except Exception:
        df_cuentas_siigo = None

    return _exportar(
        preasientos,
        output_path=os.path.join(base._project_root(), "output"),
        incluir_pendientes=incluir_pendientes,
        df_cuentas=df_cuentas_siigo,
    )


@bp.route("/exportar-siigo", methods=["POST"])
@require_permission("radian.exportar")
def exportar_siigo():
    """Genera el Excel en formato SIIGO y lo envía como descarga (o ZIP si hay varios)."""
    datos = session_store.cargar(KEY_RESULTADO)
    if not datos or not datos.get("preasientos"):
        flash("No hay resultados para exportar. Procesa primero un archivo RADIAN.", "error")
        return redirect(url_for("web.index"))

    incluir_pendientes = request.form.get("incluir_pendientes") == "on"

    try:
        rutas = _generar_archivos_siigo(datos, incluir_pendientes)
    except Exception as exc:
        logger.exception("Error generando archivo SIIGO")
        flash(f"Error al generar archivo SIIGO: {exc}", "error")
        return redirect(url_for("web.resultado"))

    # Marcar la importación como exportada (trazabilidad del ciclo de vida).
    emp_exp = base._empresa_actual()
    _persistir_importacion(emp_exp, datos, "exportada")
    audit.registrar(
        "radian.exportar_siigo", empresa_id=emp_exp.id if emp_exp else None,
        detalle=f"archivos={len(rutas)} pendientes={'si' if incluir_pendientes else 'no'}",
    )

    return base._responder_descarga(base._enviar_archivos_siigo(rutas))


# ---------------------------------------------------------------------------
# GET /analytics — Dashboard de reportería (Fase 4)
# ---------------------------------------------------------------------------


@bp.route("/test-procesar")
@require_permission("radian.procesar")
def test_procesar():
    """Procesa el archivo RADIAN de input/ sin necesidad de subida. Solo para pruebas."""
    import flask
    if not flask.current_app.debug:
        return "Solo disponible en modo DEBUG.", 403

    root = base._project_root()
    input_dir = os.path.join(root, "input")

    # Buscar el primer RADIAN disponible (preferir "RADIAN.xlsx" exacto)
    radian_path = None
    candidates = sorted(
        [f for f in os.listdir(input_dir)
         if f.lower().endswith((".xlsx", ".xls")) and f != ".gitkeep"],
        # Primero archivos sin espacios (más simples y típicamente los válidos)
        key=lambda f: (1 if " " in f else 0, f)
    )
    for fname in candidates:
        radian_path = os.path.join(input_dir, fname)
        break

    if not radian_path:
        flash("No se encontró un archivo RADIAN en input/.", "error")
        return redirect(url_for("web.index"))

    emp = base._empresa_actual()
    db = emp.db_path
    data_dir = os.path.join(root, *emp.data_category.split("/"))

    def _p(name):
        path = os.path.join(data_dir, name)
        return path if os.path.exists(path) else None

    terceros_path     = _p("Listado_de_Terceros.xlsx")
    cuentas_path      = _p("Listado_de_Cuentas_Contables.xlsx")
    comprobantes_path = _p("Tipos_de_comprobante_contable.xlsx")

    try:
        resultado = _ejecutar_pipeline(
            radian_path, terceros_path, cuentas_path,
            comprobantes_path, db, incluir_duplicados=True, empresa=emp,
        )
        session_store.guardar(KEY_RESULTADO, resultado)
        flash(
            f"✓ (TEST) Procesados {resultado['n_docs']} documentos desde "
            f"{os.path.basename(radian_path)}. "
            f"{resultado['n_excepciones']} con excepciones.",
            "success",
        )
        return redirect(url_for("web.resultado"))
    except Exception as exc:
        logger.exception("Error en test-procesar")
        flash(f"Error en test-procesar: {exc}", "error")
        return redirect(url_for("web.index"))


def _deserializar_preasientos(preasientos_data: list[dict]):

    """Reconstruye objetos PreasientoContable desde los datos serializados en sesión."""
    from app.models import PreasientoContable, LineaContable
    from datetime import datetime

    resultado = []
    for p in preasientos_data:
        fecha = None
        if p.get("fecha_emision"):
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    fecha = datetime.strptime(p["fecha_emision"], fmt)
                    break
                except ValueError:
                    pass

        lineas = []
        for l in p.get("lineas", []):
            lineas.append(LineaContable(
                cufe=p.get("cufe_full", p.get("cufe", "")),
                numero_linea=l["numero_linea"],
                cuenta=l["cuenta"],
                descripcion_cuenta=l.get("descripcion_cuenta", ""),
                debito=float(l.get("debito", 0)),
                credito=float(l.get("credito", 0)),
                concepto=l.get("concepto", ""),
                tercero_nit=p.get("tercero_nit", ""),
                tercero_nombre=p.get("tercero_nombre", ""),
                es_pendiente=bool(l.get("es_pendiente", False)),
                es_sugerida=bool(l.get("es_sugerida", False)),
            ))

        resultado.append(PreasientoContable(
            cufe=p.get("cufe_full", p.get("cufe", "")),
            tipo_documento=p.get("tipo_documento", ""),
            clasificacion=p.get("clasificacion", ""),
            codigo_comprobante=p.get("codigo_comprobante", ""),
            titulo_comprobante=p.get("titulo_comprobante", ""),
            fecha_emision=fecha,
            folio=p.get("folio", ""),
            prefijo=p.get("prefijo", ""),
            tercero_nit=p.get("tercero_nit", ""),
            tercero_nombre=p.get("tercero_nombre", ""),
            tercero_encontrado=bool(p.get("tercero_encontrado", False)),
            total=float(p.get("total", 0)),
            base_gravable=0.0,
            lineas=lineas,
            cuadra=bool(p.get("cuadra", False)),
            excepciones=p.get("excepciones", []),
            tercero_nit_original=p.get("tercero_nit_original", "") or p.get("tercero_nit", ""),
            tercero_corregido=bool(p.get("tercero_corregido", False)),
        ))
    return resultado


# ---------------------------------------------------------------------------
# GET /banco — Formulario de upload del extracto bancario
# ---------------------------------------------------------------------------


def _actividad_radian(emp, limite: int = 6) -> list[dict]:
    """Histórico reciente del módulo RADIAN para la página inicial del módulo.

    Reutiliza la tabla `importaciones` y la presenta con la misma forma que la
    actividad de Bancos (claves archivo/estado/fecha/count/unidad/ext), de modo
    que ambos módulos comparten el partial `_actividad_items.html`.
    """
    from app.database import inicializar_db, listar_importaciones

    inicializar_db(emp.db_path)
    registros = listar_importaciones(emp.db_path, limite=limite)
    return [
        {
            "archivo": r.get("archivo_nombre") or "RADIAN.xlsx",
            "estado": base._estado_actividad(r.get("estado")),
            "fecha": base._fmt_fecha_banco(r.get("fecha")),
            "count": r.get("n_docs") or 0,
            "unidad": "documentos",
            "ext": "XLSX",
        }
        for r in registros
    ]
