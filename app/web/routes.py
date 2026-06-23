"""
Rutas de la interfaz web — Fase 2.

Blueprint con 6 rutas que reutilizan exactamente el mismo pipeline que el CLI.
"""

import io
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, abort, flash, redirect, render_template,
    request, send_file, session, url_for,
)
from werkzeug.utils import secure_filename

from app.config import DATA_DIR, DB_PATH
from app import storage as store
from app.empresas import (
    listar_empresas, obtener_empresa, crear_empresa, actualizar_empresa,
    eliminar_empresa, FORMATO_BANCO_DEFAULT,
)
from app import authn, audit, tenancy
from app.authz import require_permission
from app.web import session_store
from app.web.navegacion import CATEGORIAS

logger = logging.getLogger(__name__)
bp = Blueprint("web", __name__)

ALLOWED_EXT     = {"xlsx", "xls"}
ALLOWED_EXT_CSV = {"csv", "txt"}

# Claves de sesión: solo guardan una referencia pequeña; los datos completos
# viven server-side (ver app/web/session_store.py).
KEY_RESULTADO = "resultado_ref"
KEY_BANCO     = "banco_ref"
KEY_EMPRESA   = tenancy.KEY_EMPRESA


def _empresa_actual():
    """Retorna la Empresa activa, validada contra el acceso del usuario.

    Delega en `tenancy.empresa_actual`, que comprueba que el usuario pueda
    operar la empresa seleccionada (arreglo del bloqueante #1) y corrige la
    sesión si la selección no es accesible.
    """
    return tenancy.empresa_actual()


@bp.app_context_processor
def _inyectar_empresas():
    """Hace disponibles la empresa actual, las accesibles y el usuario en los templates."""
    usuario = authn.usuario_actual()
    emp = _empresa_actual()
    return {
        "empresa_actual": emp,
        "empresas_disponibles": tenancy.empresas_accesibles(usuario),
        "empresa": emp.nombre if emp else "",
        "empresa_sigla": emp.sigla_efectiva if emp else "",
        "nit": emp.nit if emp else "",
        "usuario_actual": usuario,
    }


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def _allowed_csv(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT_CSV


def _project_root() -> str:
    """Retorna la ruta raíz del proyecto (contable-auto/)."""
    # routes.py vive en &lt;root&gt;/app/web/routes.py → 3 niveles arriba
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _save_upload(file_bytes: bytes, filename: str, emp=None) -> str:
    """Guarda los bytes de un archivo subido y retorna la referencia.

    En modo cloud sube a Azure Blob Storage; en modo local guarda en disco.
    El nombre lleva un prefijo único para que dos usuarios concurrentes
    no se sobreescriban los archivos entre sí. Si se pasa la empresa, el archivo
    queda aislado por empresa (`empresas/<id>/uploads`).
    """
    fname = secure_filename(filename)
    categoria = emp.upload_category if emp is not None else "uploads"
    return store.save_file(file_bytes, categoria, f"{uuid.uuid4().hex[:8]}_{fname}")


def _rutas_maestros_default(emp) -> tuple:
    """Resuelve las rutas de los 3 maestros de la empresa (sin uploads nuevos)."""
    rutas = []
    for default_name in [
        "Listado_de_Terceros.xlsx",
        "Listado_de_Cuentas_Contables.xlsx",
        "Tipos_de_comprobante_contable.xlsx",
    ]:
        try:
            path = emp.ruta_maestro(default_name)
        except FileNotFoundError:
            path = str(Path(_project_root()) / emp.data_category / default_name)
        rutas.append(path)
    return tuple(rutas)


# Cache en memoria de los maestros Excel para los endpoints de autocompletar,
# invalidado por fecha de modificación del archivo.
_MAESTROS_CACHE: dict[str, tuple[float, object]] = {}


def _cargar_maestro_cacheado(loader, path: str):
    mtime = os.path.getmtime(path)
    key = f"{loader.__name__}:{path}"
    hit = _MAESTROS_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    df = loader(path)
    _MAESTROS_CACHE[key] = (mtime, df)
    return df


# ---------------------------------------------------------------------------
# Autenticación — login / logout / health
# ---------------------------------------------------------------------------

@bp.route("/health")
def health():
    """Endpoint de salud (sin autenticación) para sondas/monitoreo."""
    return {"status": "ok"}, 200


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Inicio de sesión.

    En modo Entra (Fase 4) la identidad la provee App Service Authentication y
    esta página solo redirige. En modo dev es un stub: permite elegir el usuario
    para probar roles.
    """
    from app.config import AUTH_MODE
    # Sanea `next` para evitar open-redirect: solo rutas internas (una sola '/').
    raw_next = request.values.get("next") or ""
    destino = raw_next if raw_next.startswith("/") and not raw_next.startswith("//") \
        else url_for("web.index")

    # Si ya hay sesión válida, no mostrar el login.
    if authn.usuario_actual() is not None and request.method == "GET":
        return redirect(destino)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        usuario = authn.iniciar_sesion(email)
        if usuario is None:
            audit.registrar("login", detalle=f"email={email}", resultado="denegado")
            flash("Usuario no encontrado o inactivo.", "error")
            return redirect(url_for("web.login", next=destino))
        audit.registrar("login", detalle=usuario["email"])
        flash(f"Bienvenido, {usuario['nombre'] or usuario['email']}.", "success")
        return redirect(destino)

    usuarios = []
    if AUTH_MODE != "entra":
        from app.database import listar_usuarios
        from app.config import SYSTEM_DB_PATH
        # Asegura que el esquema/seed existan antes de listar.
        authn._asegurar_auth()
        usuarios = listar_usuarios(SYSTEM_DB_PATH)
    return render_template(
        "login.html", usuarios=usuarios, modo=AUTH_MODE, next=destino,
    )


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Cierra la sesión del usuario."""
    audit.registrar("logout")
    authn.cerrar_sesion()
    # Limpiar el contexto de trabajo de la sesión.
    session.pop(KEY_EMPRESA, None)
    session_store.eliminar(KEY_RESULTADO)
    session_store.eliminar(KEY_BANCO)
    flash("Sesión cerrada.", "success")
    return redirect(url_for("web.login"))


# ---------------------------------------------------------------------------
# GET /  — Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
@require_permission("dashboard.ver")
def index():
    """Dashboard principal: estadísticas de la BD + formulario de upload."""
    from app.database import inicializar_db, obtener_resumen_dashboard

    emp = _empresa_actual()
    inicializar_db(emp.db_path)
    resumen = obtener_resumen_dashboard(emp.db_path)

    stats = {
        "total_docs": resumen["total_docs"],
        "ultimas": resumen["ultimas"],
        "ultima_fecha": (resumen["ultima_fecha"] or "")[:19].replace("T", " "),
        "total_historial": resumen["total_historial"],
    }

    return render_template("index.html", stats=stats)


# ---------------------------------------------------------------------------
# GET /modulos/<slug> — Página de categoría (submenú del sidebar)
# ---------------------------------------------------------------------------

@bp.route("/modulos/<slug>")
@require_permission("dashboard.ver")
def categoria(slug):
    """Página de una categoría: muestra como botones los módulos del submenú.

    El menú lateral llega solo hasta la categoría (Flujos directos, Empresas, …);
    esta página agrupa los módulos que la componen (Bancos, Caja general, …) y
    enlaza a cada uno. Cada módulo aplica su propio permiso al entrar.
    """
    cat = CATEGORIAS.get(slug)
    if not cat:
        abort(404)
    return render_template("categoria.html", categoria=cat)


# ---------------------------------------------------------------------------
# GET /radian — Página inicial del módulo RADIAN
# ---------------------------------------------------------------------------

@bp.route("/radian")
@require_permission("radian.ver")
def radian():
    """Página inicial del módulo RADIAN (misma estructura visual que Bancos).

    Presenta qué hace el módulo, el formulario de carga del reporte RADIAN
    (que reutiliza el pipeline de /procesar) y la actividad reciente del módulo.
    """
    emp = _empresa_actual()
    return render_template("radian_upload.html", actividad=_actividad_radian(emp))


# ---------------------------------------------------------------------------
# POST /procesar — Ejecuta el pipeline completo
# ---------------------------------------------------------------------------

@bp.route("/procesar", methods=["POST"])
@require_permission("radian.procesar")
def procesar():
    """Recibe archivos, corre el pipeline y guarda el resultado server-side."""
    # Validar archivo RADIAN obligatorio
    if "radian" not in request.files or request.files["radian"].filename == "":
        flash("Debes seleccionar un archivo RADIAN.", "error")
        return redirect(url_for("web.index"))

    radian_file = request.files["radian"]
    if not _allowed(radian_file.filename):
        flash("El archivo RADIAN debe ser .xlsx o .xls", "error")
        return redirect(url_for("web.index"))

    # Empresa activa (validada): aísla uploads, maestros y BD por empresa.
    emp = _empresa_actual()

    # Leer bytes en memoria para evitar problemas de ruta en Windows
    radian_bytes = radian_file.read()
    radian_ref = _save_upload(radian_bytes, radian_file.filename, emp)
    radian_path = store.load_file(radian_ref)  # ruta local para el pipeline

    # Archivos maestros opcionales (propios de la empresa seleccionada)
    terceros_path = cuentas_path = comprobantes_path = None
    for key, default_name in [
        ("terceros",     "Listado_de_Terceros.xlsx"),
        ("cuentas",      "Listado_de_Cuentas_Contables.xlsx"),
        ("comprobantes", "Tipos_de_comprobante_contable.xlsx"),
    ]:
        f = request.files.get(key)
        if f and f.filename and _allowed(f.filename):
            file_bytes = f.read()
            ref = store.save_file(file_bytes, emp.data_category, default_name)
            path = store.load_file(ref)
        else:
            try:
                path = emp.ruta_maestro(default_name)
            except FileNotFoundError:
                path = str(Path(_project_root()) / emp.data_category / default_name)
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
    """Ejecuta el pipeline completo y retorna un dict con los resultados."""
    import pandas as pd
    from app.database import inicializar_db, registrar_documento
    from app import bitacora as bita
    from app.importador import (
        importar_radian, cargar_maestro_terceros,
        cargar_maestro_cuentas, cargar_maestro_comprobantes,
    )
    from app.clasificador import clasificar_lote
    from app.terceros import procesar_terceros_lote, aplicar_correcciones_lote
    from app.comprobantes import asignar_comprobantes_lote
    from app.impuestos import procesar_impuestos_lote
    from app.preasiento import generar_lote
    from app.validaciones import validar_preasiento_completo
    from app.exportador import exportar_excel
    from app.sugerencias import registrar_lote_confirmaciones

    if empresa is None:
        empresa = _empresa_actual()

    inicializar_db(db)
    bita.limpiar_sesion()

    # 1. Importar
    df = importar_radian(radian_path, db_path=db)
    if not incluir_duplicados:
        df = df[~df["_duplicado"]].copy()
    if df.empty:
        raise ValueError("No hay documentos nuevos para procesar.")

    # 2-4. Maestros opcionales
    def _carga(fn, path):
        try:
            return fn(path)
        except Exception:
            return None

    df_terceros     = _carga(cargar_maestro_terceros, terceros_path)
    df_cuentas      = _carga(cargar_maestro_cuentas, cuentas_path)
    df_comprobantes = _carga(cargar_maestro_comprobantes, comprobantes_path)

    # 5-8. Pipeline (con NIT y cuentas propias de la empresa)
    df = clasificar_lote(df, nit_empresa=empresa.nit)
    df = procesar_terceros_lote(df, df_terceros if df_terceros is not None else pd.DataFrame())
    # Reaplicar correcciones de tercero aprendidas de procesamientos previos.
    df = aplicar_correcciones_lote(df, df_terceros, db)
    df = asignar_comprobantes_lote(df, df_comprobantes)
    df = procesar_impuestos_lote(df, cuentas_impuestos=empresa.cuentas_impuestos_efectivas())
    preasientos = generar_lote(
        df, df_comprobantes, db_path=db,
        cuentas_contraparte=empresa.cuentas_contraparte_efectivas(),
    )

    # 9. Validar
    excepciones = []
    for p in preasientos:
        errs = validar_preasiento_completo(p, df_cuentas, db)
        if errs:
            excepciones.append({
                "cufe": p.cufe,
                "tipo_documento": p.tipo_documento,
                "clasificacion": p.clasificacion,
                "tercero_nit": p.tercero_nit,
                "total": p.total,
                "errores": errs,
            })

    # 10. Registrar en BD
    for _, row in df.iterrows():
        try:
            registrar_documento(
                cufe=str(row.get("CUFE/CUDE", "")),
                tipo_documento=str(row.get("Tipo de documento", "")),
                clasificacion=str(row.get("clasificacion", "")),
                folio=str(row.get("Folio", "")),
                prefijo=str(row.get("Prefijo", "")),
                nit_emisor=str(row.get("NIT Emisor", "")),
                nombre_emisor=str(row.get("Nombre Emisor", "")),
                nit_receptor=str(row.get("NIT Receptor", "")),
                nombre_receptor=str(row.get("Nombre Receptor", "")),
                total=float(row.get("Total", 0.0) or 0.0),
                fecha_emision=row.get("Fecha Emisión"),
                archivo_origen=radian_path,
                db_path=db,
            )
        except Exception:
            logger.exception(
                "No se pudo registrar el documento CUFE=%s en la BD",
                row.get("CUFE/CUDE", ""),
            )

    # 11. Alimentar historial
    registrar_lote_confirmaciones(preasientos, db_path=db)

    # 12. Exportar Excel — ruta absoluta para que funcione desde cualquier CWD.
    # En modo cloud, exportar_excel ya sube el archivo al storage y retorna una
    # referencia 'blob://output/...' (el disco local es efímero), de modo que la
    # importación pueda descargarse/retomarse más adelante. No re-subir aquí: la
    # referencia blob no es una ruta local y volver a subirla fallaría.
    ruta_excel = exportar_excel(
        preasientos=preasientos,
        excepciones=excepciones,
        bitacora=bita.obtener_registros_sesion(),
        output_path=os.path.join(_project_root(), "output"),
        archivo_origen=radian_path,
    )

    # Serializar preasientos para la sesión (sólo datos necesarios para la vista)
    preasientos_data = []
    for p in preasientos:
        lineas = []
        for l in p.lineas:
            lineas.append({
                "numero_linea": l.numero_linea,
                "cuenta": l.cuenta,
                "descripcion_cuenta": l.descripcion_cuenta,
                "debito": l.debito,
                "credito": l.credito,
                "concepto": l.concepto,
                "es_pendiente": l.es_pendiente,
                "es_sugerida": getattr(l, "es_sugerida", False),
            })
        preasientos_data.append({
            "cufe": p.cufe[:30] + "…" if len(p.cufe) > 30 else p.cufe,
            "cufe_full": p.cufe,
            "clasificacion": p.clasificacion,
            "tipo_documento": p.tipo_documento,
            "codigo_comprobante": p.codigo_comprobante,
            "titulo_comprobante": p.titulo_comprobante,
            "base_gravable": p.base_gravable,
            "fecha_emision": p.fecha_emision.strftime("%d/%m/%Y") if p.fecha_emision else "",
            "folio": p.folio,
            "prefijo": p.prefijo,
            "tercero_nit": p.tercero_nit,
            "tercero_nombre": p.tercero_nombre,
            "tercero_encontrado": p.tercero_encontrado,
            "tercero_nit_original": getattr(p, "tercero_nit_original", "") or p.tercero_nit,
            "tercero_corregido": getattr(p, "tercero_corregido", False),
            "total": p.total,
            "cuadra": p.cuadra,
            "excepciones": p.excepciones,
            "lineas": lineas,
        })

    return {
        "n_docs": len(preasientos),
        "n_excepciones": len(excepciones),
        "preasientos": preasientos_data,
        "excepciones": excepciones,
        "excel_path": ruta_excel,
        "archivo_origen": radian_path,
    }


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
                               _empresa_actual().db_path)

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
                        break
                session_store.guardar(KEY_RESULTADO, resultado)
                _persistir_importacion(_empresa_actual(), resultado, "corregida")
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
        df = _cargar_maestro_cacheado(cargar_maestro_terceros, terceros_path)
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

    emp = _empresa_actual()
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
    emp = _empresa_actual()
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

@bp.route("/historial")
@require_permission("ml.ver")
def historial():
    """Muestra las cuentas aprendidas por el motor de sugerencias."""
    from app.database import inicializar_db, listar_historial_cuentas

    db_path = _empresa_actual().db_path
    inicializar_db(db_path)
    entradas, total = listar_historial_cuentas(db_path, limite=200)

    return render_template("historial.html", entradas=entradas, total=total)


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

    db_path = _empresa_actual().db_path
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

    emp = _empresa_actual()
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

    emp = _empresa_actual()
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

    emp = _empresa_actual()
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
    terceros_path, cuentas_path, comprobantes_path = _rutas_maestros_default(emp)

    try:
        # incluir_duplicados=True: los documentos de esta importación ya
        # están registrados en la BD y de lo contrario quedarían excluidos.
        resultado = _ejecutar_pipeline(
            radian_path, terceros_path, cuentas_path,
            comprobantes_path, db, incluir_duplicados=True, empresa=emp,
        )
        resultado["importacion_id"] = imp_id
        session_store.guardar(KEY_RESULTADO, resultado)
        _persistir_importacion(emp, resultado, "procesada")
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

    db_path = _empresa_actual().db_path
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

    db_path = _empresa_actual().db_path
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

    emp = _empresa_actual()
    db_path = emp.db_path
    inicializar_db(db_path)

    datos = obtener_snapshot_importacion(imp_id, db_path=db_path)
    if not datos or not datos.get("preasientos"):
        flash("Esta importación no tiene un estado guardado para exportar a SIIGO. "
              "Usa «Regenerar» para reprocesar el archivo original.", "error")
        return redirect(url_for("web.importaciones"))

    try:
        rutas = _generar_archivos_siigo(datos)
    except Exception as exc:
        logger.exception("Error generando archivo SIIGO de la importación %s", imp_id)
        flash(f"Error al generar el archivo SIIGO: {exc}", "error")
        return redirect(url_for("web.importaciones"))

    audit.registrar("importacion.descargar_siigo", empresa_id=emp.id,
                    detalle=f"importacion={imp_id} archivos={len(rutas)}")
    return _enviar_archivos_siigo(rutas)


# ---------------------------------------------------------------------------
# POST /exportar-siigo — Generar archivo(s) SIIGO
# ---------------------------------------------------------------------------

def _responder_descarga(resp):
    """Adjunta la cookie de señal de descarga al `Response` de un archivo.

    El frontend envía un `download_token` oculto al exportar; el servidor lo
    devuelve como cookie `descargaSiigo`. Así el navegador, al iniciar la
    descarga (sin navegar de página), puede ocultar el overlay de carga y no
    dejar la pantalla bloqueada en "Generando archivo SIIGO…".
    """
    token = request.form.get("download_token", "").strip()
    if token:
        resp.set_cookie("descargaSiigo", token, max_age=60, path="/", samesite="Lax")
    return resp


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
        _cuentas_path = _empresa_actual().ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
        df_cuentas_siigo = cargar_maestro_cuentas(_cuentas_path)
    except Exception:
        df_cuentas_siigo = None

    return _exportar(
        preasientos,
        output_path=os.path.join(_project_root(), "output"),
        incluir_pendientes=incluir_pendientes,
        df_cuentas=df_cuentas_siigo,
    )


def _enviar_archivos_siigo(rutas: list, zip_name: str = "siigo_comprobantes.zip"):
    """Envía como descarga un único Excel SIIGO, o un ZIP si hay varios."""
    import zipfile

    if len(rutas) == 1:
        return send_file(
            rutas[0],
            as_attachment=True,
            download_name=Path(rutas[0]).name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ruta in rutas:
            zf.write(ruta, Path(ruta).name)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=zip_name,
                     mimetype="application/zip")


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
    emp_exp = _empresa_actual()
    _persistir_importacion(emp_exp, datos, "exportada")
    audit.registrar(
        "radian.exportar_siigo", empresa_id=emp_exp.id if emp_exp else None,
        detalle=f"archivos={len(rutas)} pendientes={'si' if incluir_pendientes else 'no'}",
    )

    return _responder_descarga(_enviar_archivos_siigo(rutas))


# ---------------------------------------------------------------------------
# GET /analytics — Dashboard de reportería (Fase 4)
# ---------------------------------------------------------------------------

@bp.route("/analytics")
@require_permission("analitica.ver")
def analytics():
    """Dashboard de reportería y analytics contable."""
    from app.database import (
        obtener_kpis, obtener_evolucion_mensual,
        obtener_distribucion_clasificacion,
        obtener_top_terceros, obtener_actividad_reciente,
    )

    from app.database import inicializar_db
    db_path = _empresa_actual().db_path
    inicializar_db(db_path)

    kpis          = obtener_kpis(db_path)
    evolucion     = obtener_evolucion_mensual(db_path, meses=12)
    distribucion  = obtener_distribucion_clasificacion(db_path)
    top_proveed   = obtener_top_terceros(db_path, limite=10, tipo="compra")
    top_clientes  = obtener_top_terceros(db_path, limite=10, tipo="venta")
    actividad     = obtener_actividad_reciente(db_path, limite=30)

    # Serializar para Chart.js
    charts = {
        "evolucion": {
            "labels":         [r["mes"] for r in evolucion],
            "ventas_monto":   [round(r["ventas_monto"],  2) for r in evolucion],
            "compras_monto":  [round(r["compras_monto"], 2) for r in evolucion],
            "otros_monto":    [round(r["otros_monto"],   2) for r in evolucion],
            "ventas_count":   [r["ventas_count"]  for r in evolucion],
            "compras_count":  [r["compras_count"] for r in evolucion],
        },
        "distribucion": {
            # clasificacion es nullable: documentos sin clasificar caen en "Sin clasificar".
            "labels": [(r["clasificacion"] or "Sin clasificar").replace("_", " ") for r in distribucion],
            "counts": [r["count"] for r in distribucion],
            "montos": [round(r["monto"], 2) for r in distribucion],
        },
        "top_proveed": {
            # nombre puede venir vacío aunque el NIT exista: usar el NIT como respaldo.
            "labels": [(r["nombre"] or r["nit"] or "—")[:25] for r in top_proveed],
            "montos": [round(r["monto"], 2) for r in top_proveed],
            "counts": [r["count"] for r in top_proveed],
        },
        "top_clientes": {
            "labels": [(r["nombre"] or r["nit"] or "—")[:25] for r in top_clientes],
            "montos": [round(r["monto"], 2) for r in top_clientes],
            "counts": [r["count"] for r in top_clientes],
        },
    }

    return render_template(
        "analytics.html",
        kpis=kpis,
        actividad=actividad,
        charts=charts,
    )


# ---------------------------------------------------------------------------
# GET /cuentas — Consulta del plan de cuentas (buscador)
# ---------------------------------------------------------------------------

def _columnas_cuentas(df):
    """Retorna (cod_col, nom_col) del plan de cuentas: las 2 primeras reales.

    Replica el criterio de `/api/cuentas`: las primeras columnas no-'Unnamed'
    son el código y el nombre de la cuenta.
    """
    valid_cols = [c for c in df.columns if not str(c).startswith("Unnamed")]
    cod_col = valid_cols[0] if valid_cols else df.columns[0]
    nom_col = valid_cols[1] if len(valid_cols) > 1 else None
    return cod_col, nom_col


def _listar_cuentas(emp) -> list[dict]:
    """Lista todas las cuentas transaccionales activas de la empresa.

    Retorna una lista de ``{"codigo", "nombre"}`` ordenada por código, lista
    para mostrarla en el buscador del plan de cuentas. Usa el maestro cacheado.
    """
    from app.importador import cargar_maestro_cuentas

    cuentas_path = emp.ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
    df = _cargar_maestro_cacheado(cargar_maestro_cuentas, cuentas_path)
    cod_col, nom_col = _columnas_cuentas(df)

    out = []
    for _, row in df.iterrows():
        codigo = str(row[cod_col]).strip()
        if not codigo or codigo.lower() == "nan":
            continue
        nombre = str(row[nom_col]).strip() if nom_col else ""
        if nombre.lower() == "nan":
            nombre = ""
        out.append({"codigo": codigo, "nombre": nombre})

    out.sort(key=lambda c: c["codigo"])
    return out


@bp.route("/cuentas")
@require_permission("radian.ver")
def cuentas():
    """Página/ventana para consultar y buscar en el plan de cuentas.

    Sirve para encontrar fácilmente el código que se debe digitar en una casilla.
    Con ``?popup=1`` se renderiza una versión compacta (sin menú lateral) pensada
    para abrirse en una ventana emergente desde las pantallas de asignación.
    """
    emp = _empresa_actual()
    error = None
    try:
        items = _listar_cuentas(emp)
    except FileNotFoundError:
        items = []
        error = ("Esta empresa no tiene cargado el plan de cuentas "
                 "(Listado_de_Cuentas_Contables.xlsx). Cárgalo en Empresas → Maestros.")
    except Exception:
        logger.exception("Error listando el plan de cuentas")
        items = []
        error = "No se pudo leer el plan de cuentas de la empresa."

    popup = request.args.get("popup", "") in ("1", "true", "yes", "on")
    return render_template("cuentas.html", cuentas=items, error=error, popup=popup)


# ---------------------------------------------------------------------------
# GET /api/cuentas — Autocompletar cuentas contables
# ---------------------------------------------------------------------------

@bp.route("/api/cuentas")
@require_permission("radian.ver")
def api_cuentas():
    """Retorna cuentas que coincidan con el query por código o nombre. Máx 15."""
    from flask import jsonify
    from app.importador import cargar_maestro_cuentas

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    try:
        cuentas_path = _empresa_actual().ruta_maestro("Listado_de_Cuentas_Contables.xlsx")
        df = _cargar_maestro_cacheado(cargar_maestro_cuentas, cuentas_path)

        q_lower = q.lower()

        # Las primeras 2 columnas no-Unnamed son: código y nombre
        cod_col, nom_col = _columnas_cuentas(df)

        codigos = df[cod_col].astype(str).str.strip()
        mask = codigos.str.lower().str.startswith(q_lower)
        if nom_col:
            mask |= df[nom_col].astype(str).str.lower().str.contains(q_lower, regex=False)

        cols = [cod_col, nom_col] if nom_col else [cod_col]
        resultados = df[mask][cols].head(15)

        out = [
            {
                "codigo": str(row[cod_col]).strip(),
                "nombre": str(row[nom_col]).strip() if nom_col else "",
            }
            for _, row in resultados.iterrows()
        ]
        return jsonify(out)
    except Exception as exc:
        logger.exception("Error en /api/cuentas")
        return jsonify([])



# ---------------------------------------------------------------------------
# GET /api/terceros — Autocompletar terceros por NIT o nombre
# ---------------------------------------------------------------------------


@bp.route("/api/terceros")
@require_permission("radian.ver")
def api_terceros():
    """Retorna terceros que coincidan con el query por NIT o nombre. Máx 15."""
    from flask import jsonify
    from app.importador import cargar_maestro_terceros

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    try:
        terceros_path = _empresa_actual().ruta_maestro("Listado_de_Terceros.xlsx")
        df = _cargar_maestro_cacheado(cargar_maestro_terceros, terceros_path)
    except Exception:
        return jsonify([])

    q_lower    = q.lower()
    col_nit    = "Identificación"
    col_nombre = "Nombre tercero"

    if col_nit not in df.columns:
        return jsonify([])

    nits = df[col_nit].astype(str).str.strip()
    mask = nits.str.lower().str.startswith(q_lower)

    if col_nombre in df.columns:
        mask |= df[col_nombre].astype(str).str.lower().str.contains(q_lower, regex=False)

    cols = [col_nit, col_nombre] if col_nombre in df.columns else [col_nit]
    resultados = df[mask][cols].head(15)

    out = [
        {
            "nit":    str(row[col_nit]).strip(),
            "nombre": str(row[col_nombre]).strip() if col_nombre in df.columns else "",
        }
        for _, row in resultados.iterrows()
    ]
    return jsonify(out)


# ---------------------------------------------------------------------------
# GET /test-procesar — Prueba end-to-end sin file dialog (solo DEBUG)
# ---------------------------------------------------------------------------

@bp.route("/test-procesar")
@require_permission("radian.procesar")
def test_procesar():
    """Procesa el archivo RADIAN de input/ sin necesidad de subida. Solo para pruebas."""
    import flask
    if not flask.current_app.debug:
        return "Solo disponible en modo DEBUG.", 403

    root = _project_root()
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

    emp = _empresa_actual()
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

@bp.route("/banco")
@require_permission("banco.ver")
def banco():
    """Formulario para subir el CSV del banco."""
    from app.config import SIIGO_COMP_BANCO_INGRESO, SIIGO_COMP_BANCO_EGRESO, SIIGO_COMP_BANCO_TRASLADO
    emp = _empresa_actual()
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


_MESES_ABR = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
              "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _fmt_fecha_banco(iso: str) -> str:
    """Formatea una fecha ISO como 'DD Mmm YYYY, HH:MM AM/PM' (mes en español)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return str(iso)[:16].replace("T", " ")
    hora12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.day:02d} {_MESES_ABR[dt.month - 1]} {dt.year}, {hora12:02d}:{dt.minute:02d} {ampm}"


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
            "fecha": _fmt_fecha_banco(p.get("fecha")),
            "count": p.get("n_movimientos") or 0,
            "unidad": "movimientos",
            "ext": "CSV",
        }
        for p in procesos
    ]


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
            "estado": _estado_actividad(r.get("estado")),
            "fecha": _fmt_fecha_banco(r.get("fecha")),
            "count": r.get("n_docs") or 0,
            "unidad": "documentos",
            "ext": "XLSX",
        }
        for r in registros
    ]


# Estados durables de importación → vocabulario del partial de actividad
# (`_actividad_items.html`: completada / error / anulada / procesando).
_ESTADO_ACTIVIDAD = {
    "procesada": "completada",
    "corregida": "completada",
    "exportada": "completada",
    "completada": "completada",
    "error": "error",
    "anulada": "anulada",
}


def _estado_actividad(estado: str | None) -> str:
    return _ESTADO_ACTIVIDAD.get(estado or "", "procesando")


# Estados durables del proceso de banco → etiqueta y clase de pill para el
# histórico (el módulo marca 'completada' al generar el archivo SIIGO).
_ESTADOS_PROCESO_BANCO = {
    "procesando": ("Procesando", "pill"),
    "completada": ("Exportada",  "pill pill-ok"),
    "error":      ("Error",      "pill pill-pendiente"),
    "anulada":    ("Anulada",    "pill pill-muted"),
}


def _render_banco_resultado(movimientos, cuenta_banco, nit_banco, asignaciones=None):
    """Construye la vista editable de movimientos bancarios (banco_resultado.html).

    Centraliza la preparación de las filas «principales» (defaults de cuenta, NIT
    y tipo de comprobante, y agrupación de 4x1000). Cuando se pasan `asignaciones`
    guardadas (al Retomar/Corregir un proceso), se sobreponen para conservar lo
    que el usuario ya había trabajado.
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
        output_path=os.path.join(_project_root(), "output"),
    )


# ---------------------------------------------------------------------------
# POST /banco/previsualizar — Parsea el CSV y muestra la tabla editable
# ---------------------------------------------------------------------------

@bp.route("/banco/previsualizar", methods=["POST"])
@require_permission("banco.procesar")
def banco_previsualizar():
    """Recibe el CSV, lo parsea, agrupa 4x1000 y guarda en sesión."""
    from app.banco.importador_banco import leer_csv_banco, a_dict
    from app.config import SIIGO_COMP_BANCO_INGRESO, SIIGO_COMP_BANCO_EGRESO, SIIGO_COMP_BANCO_TRASLADO, BANCO_CUENTA_4X1000

    emp = _empresa_actual()
    BANCO_CUENTA_DEFAULT = emp.cuenta_banco_efectiva()

    if "csv_banco" not in request.files or request.files["csv_banco"].filename == "":
        flash("Debes seleccionar el archivo CSV del banco.", "error")
        return redirect(url_for("web.banco"))

    csv_file = request.files["csv_banco"]

    cuenta_banco = request.form.get("cuenta_banco", BANCO_CUENTA_DEFAULT).strip()
    if not cuenta_banco:
        cuenta_banco = BANCO_CUENTA_DEFAULT

    nit_banco = request.form.get("nit_banco", "").strip() or emp.nit_banco

    csv_path = _save_upload(csv_file.read(), csv_file.filename, emp)
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

    return _render_banco_resultado(movimientos, cuenta_banco, nit_banco)


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

    emp = _empresa_actual()
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

    return _responder_descarga(_enviar_archivos_siigo(rutas, zip_name="siigo_banco.zip"))


# ---------------------------------------------------------------------------
# GET /banco/historial — Histórico completo de procesos del módulo Bancos
# ---------------------------------------------------------------------------

@bp.route("/banco/historial")
@require_permission("banco.ver")
def banco_historial():
    """Lista todos los procesos del módulo Bancos con su estado y acciones."""
    from app.database import inicializar_db, listar_procesos_banco

    emp = _empresa_actual()
    inicializar_db(emp.db_path)
    procesos = listar_procesos_banco(emp.db_path, limite=200)

    for p in procesos:
        p["fecha_fmt"] = _fmt_fecha_banco(p.get("fecha"))
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

    emp = _empresa_actual()
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
    )


@bp.route("/banco/historial/<int:proceso_id>/descargar-original")
@require_permission("banco.ver")
def proceso_banco_descargar_original(proceso_id):
    """Descarga el CSV original que se importó para este proceso de banco."""
    from app.database import inicializar_db, obtener_proceso_banco

    emp = _empresa_actual()
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

    emp = _empresa_actual()
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
    return _enviar_archivos_siigo(rutas, zip_name="siigo_banco.zip")


@bp.route("/banco/historial/<int:proceso_id>/anular", methods=["POST"])
@require_permission("banco.procesar")
def proceso_banco_anular(proceso_id):
    """Marca un proceso de banco como anulado (descartado). No borra el histórico."""
    from app.database import (
        inicializar_db, obtener_proceso_banco, actualizar_proceso_banco,
    )

    emp = _empresa_actual()
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


# ---------------------------------------------------------------------------
# Empresas — selección y administración (multi-empresa)
# ---------------------------------------------------------------------------

@bp.route("/empresas")
@require_permission("empresas.ver")
def empresas():
    """Página de administración de empresas."""
    return render_template(
        "empresas.html",
        formato_default=FORMATO_BANCO_DEFAULT,
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
    return render_template(
        "empresas.html",
        formato_default=FORMATO_BANCO_DEFAULT,
        empresa_editar=emp,
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
    """Sube/reemplaza los archivos maestros de la empresa indicada."""
    emp = obtener_empresa(request.form.get("empresa_id", "").strip())
    subidos = []
    for key, default_name in [
        ("terceros",     "Listado_de_Terceros.xlsx"),
        ("cuentas",      "Listado_de_Cuentas_Contables.xlsx"),
        ("comprobantes", "Tipos_de_comprobante_contable.xlsx"),
    ]:
        f = request.files.get(key)
        if f and f.filename and _allowed(f.filename):
            store.save_file(f.read(), emp.data_category, default_name)
            subidos.append(default_name)

    if subidos:
        audit.registrar("empresa.maestros", empresa_id=emp.id,
                        detalle=", ".join(subidos))
        flash(f"✓ Maestros actualizados para {emp.nombre}: {', '.join(subidos)}", "success")
    else:
        flash("No se subió ningún archivo maestro.", "info")
    return redirect(url_for("web.empresas"))


# ---------------------------------------------------------------------------
# Usuarios y roles — administración (RBAC · Fase 3)
# ---------------------------------------------------------------------------

def _sysdb() -> str:
    """Ruta (dinámica) de la BD de sistema; respeta overrides de tests."""
    from app import config as _cfg
    return _cfg.SYSTEM_DB_PATH


@bp.route("/usuarios")
@require_permission("usuarios.gestionar")
def usuarios():
    """Administración de usuarios: lista, roles asignados y formularios."""
    from app.database import listar_usuarios, roles_de_usuario
    from app.authz import ROLES

    authn._asegurar_auth()
    sysdb = _sysdb()
    filas = []
    for u in listar_usuarios(sysdb):
        filas.append({**u, "roles": roles_de_usuario(u["id"], db_path=sysdb)})

    return render_template(
        "usuarios.html",
        usuarios=filas,
        roles=list(ROLES.keys()),
        empresas=listar_empresas(),
    )


@bp.route("/usuarios/crear", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_crear():
    """Crea un usuario nuevo (sin roles; se asignan después)."""
    from app.database import obtener_usuario_por_email, crear_usuario

    email = request.form.get("email", "").strip().lower()
    nombre = request.form.get("nombre", "").strip()
    if not email:
        flash("El correo es obligatorio.", "error")
        return redirect(url_for("web.usuarios"))

    sysdb = _sysdb()
    if obtener_usuario_por_email(email, db_path=sysdb):
        flash("Ya existe un usuario con ese correo.", "error")
        return redirect(url_for("web.usuarios"))

    crear_usuario(email, nombre=nombre, db_path=sysdb)
    audit.registrar("usuario.crear", detalle=email)
    flash(f"✓ Usuario {email} creado. Asígnale uno o más roles.", "success")
    return redirect(url_for("web.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/asignar", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_asignar(usuario_id):
    """Asigna un rol al usuario (global o acotado a una empresa)."""
    from app.database import (
        obtener_o_crear_rol, asignar_rol_global, asignar_rol_empresa,
    )
    from app.authz import ROLES

    rol = request.form.get("rol", "").strip()
    ambito = request.form.get("ambito", "").strip()          # 'global' | 'empresa'
    empresa_id = request.form.get("empresa_id", "").strip()

    if rol not in ROLES:
        flash("Rol desconocido.", "error")
        return redirect(url_for("web.usuarios"))

    sysdb = _sysdb()
    rid = obtener_o_crear_rol(rol, db_path=sysdb)
    if ambito == "global":
        asignar_rol_global(usuario_id, rid, db_path=sysdb)
        audit.registrar("usuario.rol_global", detalle=f"uid={usuario_id} rol={rol}")
        flash(f"✓ Rol global '{rol}' asignado.", "success")
    elif ambito == "empresa" and empresa_id:
        asignar_rol_empresa(usuario_id, empresa_id, rid, db_path=sysdb)
        audit.registrar("usuario.rol_empresa", empresa_id=empresa_id,
                        detalle=f"uid={usuario_id} rol={rol}")
        flash(f"✓ Rol '{rol}' asignado en la empresa {empresa_id}.", "success")
    else:
        flash("Indica el ámbito (global o una empresa).", "error")
    return redirect(url_for("web.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/revocar", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_revocar(usuario_id):
    """Revoca un rol del usuario (global o de empresa)."""
    from app.database import (
        obtener_o_crear_rol, revocar_rol_global, revocar_rol_empresa,
    )

    rol = request.form.get("rol", "").strip()
    ambito = request.form.get("ambito", "").strip()
    empresa_id = request.form.get("empresa_id", "").strip()

    sysdb = _sysdb()
    rid = obtener_o_crear_rol(rol, db_path=sysdb)
    if ambito == "global":
        revocar_rol_global(usuario_id, rid, db_path=sysdb)
    elif ambito == "empresa" and empresa_id:
        revocar_rol_empresa(usuario_id, empresa_id, rid, db_path=sysdb)
    audit.registrar("usuario.revocar",
                    empresa_id=(empresa_id or None),
                    detalle=f"uid={usuario_id} rol={rol} ambito={ambito}")
    flash("Rol revocado.", "success")
    return redirect(url_for("web.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/estado", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_estado(usuario_id):
    """Activa o desactiva un usuario."""
    from app.database import actualizar_usuario

    activo = request.form.get("activo") == "1"
    actualizar_usuario(usuario_id, activo=activo, db_path=_sysdb())
    audit.registrar("usuario.estado",
                    detalle=f"uid={usuario_id} activo={activo}")
    flash("Estado del usuario actualizado.", "success")
    return redirect(url_for("web.usuarios"))


# ---------------------------------------------------------------------------
# GET /auditoria — Bitácora de acciones
# ---------------------------------------------------------------------------

@bp.route("/auditoria")
@require_permission("auditoria.ver")
def auditoria():
    """Muestra los eventos de auditoría más recientes."""
    eventos = audit.listar(limite=300)
    for e in eventos:
        e["fecha_fmt"] = (e.get("timestamp") or "")[:19].replace("T", " ")
    return render_template("auditoria.html", eventos=eventos)
