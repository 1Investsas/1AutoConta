"""Módulo Terceros: maestro, importación de RUT y cuentas bancarias."""

import io
import logging
import os

from flask import (
    flash, redirect, render_template,
    request, send_file, url_for,
)
from werkzeug.utils import secure_filename

from app import storage as store
from app import audit
from app.authz import require_permission

from . import base
from .base import (
    bp,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Terceros — actualización del maestro importando el RUT de la DIAN
# ---------------------------------------------------------------------------

ALLOWED_EXT_PDF = {"pdf"}


def _allowed_pdf(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT_PDF


def _maestro_terceros_bytes(emp) -> bytes | None:
    """Devuelve los bytes del maestro de terceros de la empresa, o None.

    Resuelve la ruta local (descargándola del blob en modo cloud) y la lee. Si
    el archivo aún no existe, retorna None para que se cree uno nuevo.
    """
    try:
        path = emp.ruta_maestro("Listado_de_Terceros.xlsx")
    except FileNotFoundError:
        return None
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def _info_maestro_terceros(emp) -> dict:
    """Resumen del maestro de terceros actual (existencia y nº de registros).

    Si el archivo guardado en la casilla de Terceros resulta ser otro maestro
    (p. ej. el Plan de Cuentas), lo señala con ``tipo_invalido`` y una
    ``advertencia`` para que el usuario lo corrija.
    """
    from app.importador import cargar_maestro_terceros
    from app.maestros import clasificar_maestro, ETIQUETA_MAESTRO

    try:
        path = emp.ruta_maestro("Listado_de_Terceros.xlsx")
    except FileNotFoundError:
        return {"existe": False, "total": 0, "tipo_invalido": False}
    if not os.path.exists(path):
        return {"existe": False, "total": 0, "tipo_invalido": False}

    # Verificar que el archivo almacenado sea de verdad un maestro de terceros.
    try:
        with open(path, "rb") as fh:
            clase = clasificar_maestro(fh.read())
    except Exception:
        clase = "terceros"  # no bloquear por un fallo de lectura puntual
    if clase in ("cuentas", "comprobantes"):
        return {
            "existe": True, "total": 0, "tipo_invalido": True,
            "advertencia": (
                f"El archivo guardado como «Listado de Terceros» parece ser el "
                f"«{ETIQUETA_MAESTRO.get(clase, clase)}». Súbelo de nuevo en "
                f"Configuraciones → Empresas → Maestros, en la casilla de Terceros."
            ),
        }

    try:
        df = base._cargar_maestro_cacheado(cargar_maestro_terceros, path)
        return {"existe": True, "total": int(len(df)), "tipo_invalido": False}
    except Exception:
        return {"existe": False, "total": 0, "tipo_invalido": False}


def _actividad_terceros(emp, limite: int = 6) -> list[dict]:
    """Últimas importaciones de RUT registradas en auditoría para la empresa."""
    eventos = [
        e for e in audit.listar(limite=200)
        if e.get("accion") == "terceros.importar"
        and (e.get("empresa_id") in (None, emp.id))
    ]
    out = []
    for e in eventos[:limite]:
        out.append({
            "fecha": (e.get("timestamp") or "")[:19].replace("T", " "),
            "detalle": e.get("detalle") or "",
            "usuario": e.get("usuario_email") or "",
        })
    return out


def _listar_cuentas_bancarias(emp) -> list[dict]:
    """Lista las cuentas bancarias de terceros registradas en la empresa."""
    from app.database import inicializar_db, listar_cuentas_bancarias_tercero
    try:
        inicializar_db(emp.db_path)
        return listar_cuentas_bancarias_tercero(emp.db_path)
    except Exception:
        logger.exception("No se pudieron listar las cuentas bancarias de terceros.")
        return []


@bp.route("/terceros")
@require_permission("terceros.ver")
def terceros():
    """Página del módulo Terceros: estado del maestro + importación de RUT."""
    emp = base._empresa_actual()
    return render_template(
        "terceros.html",
        info=_info_maestro_terceros(emp),
        actividad=_actividad_terceros(emp),
        cuentas_bancarias=_listar_cuentas_bancarias(emp),
        resultado=None,
    )


@bp.route("/terceros/importar", methods=["POST"])
@require_permission("terceros.gestionar")
def terceros_importar():
    """Lee uno o varios PDF del RUT de la DIAN y actualiza el maestro de terceros."""
    import tempfile
    from app.rut import parsear_rut_pdf, RUTParseError
    from app.terceros_rut import mapear_rut_a_tercero, actualizar_maestro_terceros

    archivos = [f for f in request.files.getlist("rut") if f and f.filename]
    if not archivos:
        flash("Debes seleccionar al menos un PDF del RUT.", "error")
        return redirect(url_for("web.terceros"))

    emp = base._empresa_actual()
    parseados: list[dict] = []   # terceros canónicos para el upsert
    leidos: list[dict] = []      # info legible para la vista de resultados
    errores: list[str] = []

    for f in archivos:
        if not _allowed_pdf(f.filename):
            errores.append(f"{f.filename}: el archivo debe ser un PDF.")
            continue
        tmp_path = None
        try:
            data = f.read()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            rut = parsear_rut_pdf(tmp_path)
            tercero = mapear_rut_a_tercero(rut)
            parseados.append(tercero)
            leidos.append({
                "archivo": secure_filename(f.filename),
                "nit": f"{rut.get('nit', '')}-{rut.get('dv', '')}",
                "tipo_persona": rut.get("tipo_persona", ""),
                "nombre": rut.get("nombre", ""),
                "tipo_identificacion": rut.get("tipo_identificacion", ""),
                "ciudad": rut.get("ciudad", ""),
                "direccion": rut.get("direccion", ""),
                "correo": rut.get("correo", ""),
                "telefono": rut.get("telefono", ""),
            })
        except RUTParseError as exc:
            errores.append(f"{f.filename}: {exc}")
        except Exception:
            logger.exception("Error inesperado leyendo el RUT %s", f.filename)
            errores.append(f"{f.filename}: error inesperado al leer el RUT.")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if not parseados:
        flash("No se pudo leer ningún RUT. " + " ".join(errores), "error")
        return redirect(url_for("web.terceros"))

    try:
        from app.maestros import clasificar_maestro
        contenido = _maestro_terceros_bytes(emp)
        # Si el archivo guardado en la casilla de Terceros no es un maestro de
        # terceros (p. ej. quedó el Plan de Cuentas), no lo usamos como base:
        # creamos uno nuevo con los RUT y avisamos para que lo revisen.
        if contenido and clasificar_maestro(contenido) in ("cuentas", "comprobantes"):
            flash("El archivo que estaba en la casilla de Terceros no era un "
                  "maestro de terceros; se creó uno nuevo con los RUT importados. "
                  "Revisa en Empresas → Maestros que cada archivo esté en su casilla.",
                  "info")
            contenido = None
        nuevos_bytes, resumen = actualizar_maestro_terceros(parseados, contenido)
        store.save_file(nuevos_bytes, emp.data_category, "Listado_de_Terceros.xlsx")
    except ValueError as exc:
        # Error de validación esperado (p. ej. el archivo guardado no es el
        # maestro de terceros): mensaje claro, sin traza de error.
        flash(str(exc), "error")
        return redirect(url_for("web.terceros"))
    except Exception as exc:
        logger.exception("Error actualizando el maestro de terceros")
        flash(f"Error al actualizar el maestro de terceros: {exc}", "error")
        return redirect(url_for("web.terceros"))

    audit.registrar(
        "terceros.importar", empresa_id=emp.id,
        detalle=f"archivos={len(archivos)} agregados={resumen['agregados']} "
                f"actualizados={resumen['actualizados']}",
    )

    msg = (f"✓ Maestro de terceros actualizado: {resumen['agregados']} agregados, "
           f"{resumen['actualizados']} actualizados.")
    if resumen.get("creado"):
        msg += " Se creó el archivo Listado_de_Terceros.xlsx."
    if errores:
        msg += f" {len(errores)} archivo(s) con error."
    flash(msg, "success" if not errores else "info")

    resultado = {
        "leidos": leidos,
        "errores": errores,
        "resumen": resumen,
    }
    return render_template(
        "terceros.html",
        info=_info_maestro_terceros(emp),
        actividad=_actividad_terceros(emp),
        cuentas_bancarias=_listar_cuentas_bancarias(emp),
        resultado=resultado,
    )


@bp.route("/terceros/descargar")
@require_permission("terceros.ver")
def terceros_descargar():
    """Descarga el maestro de terceros actual de la empresa."""
    emp = base._empresa_actual()
    contenido = _maestro_terceros_bytes(emp)
    if contenido is None:
        flash("Aún no hay un maestro de terceros. Importa un RUT para crearlo.", "error")
        return redirect(url_for("web.terceros"))
    return send_file(
        io.BytesIO(contenido),
        as_attachment=True,
        download_name="Listado_de_Terceros.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Cuentas bancarias de terceros — importación desde el certificado bancario
# ---------------------------------------------------------------------------


@bp.route("/terceros/cuentas-bancarias/importar", methods=["POST"])
@require_permission("terceros.gestionar")
def terceros_cuentas_importar():
    """Lee uno o varios certificados bancarios (PDF) y registra las cuentas.

    Cada certificado pertenece a un tercero (persona jurídica o natural) e
    informa una o más cuentas. Se guardan en la tabla `cuentas_bancarias_tercero`
    de la empresa, asociadas por la identificación del tercero (NIT/cédula).
    """
    import tempfile
    from app.certificado_bancario import (
        parsear_certificado_pdf, CertificadoBancarioError,
    )
    from app.database import inicializar_db, registrar_cuenta_bancaria_tercero

    archivos = [f for f in request.files.getlist("certificado") if f and f.filename]
    if not archivos:
        flash("Debes seleccionar al menos un certificado bancario en PDF.", "error")
        return redirect(url_for("web.terceros"))

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)

    leidas: list[dict] = []   # info legible para la vista de resultados
    errores: list[str] = []
    n_registradas = 0

    for f in archivos:
        if not _allowed_pdf(f.filename):
            errores.append(f"{f.filename}: el archivo debe ser un PDF.")
            continue
        nombre_archivo = secure_filename(f.filename)
        tmp_path = None
        try:
            data = f.read()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            cert = parsear_certificado_pdf(tmp_path)
            nit = cert.get("numero_documento", "")
            if not nit:
                errores.append(f"{f.filename}: no se identificó el documento del titular.")
                continue
            for cuenta in cert.get("cuentas", []):
                registrar_cuenta_bancaria_tercero(
                    nit_tercero=nit,
                    numero_cuenta=cuenta.get("numero_cuenta", ""),
                    nombre_tercero=cert.get("titular", ""),
                    tipo_documento=cert.get("tipo_documento", ""),
                    banco=cert.get("banco", ""),
                    tipo_producto=cuenta.get("tipo_producto", ""),
                    fecha_apertura=cuenta.get("fecha_apertura", ""),
                    estado=cuenta.get("estado", ""),
                    archivo_origen=nombre_archivo,
                    db_path=emp.db_path,
                )
                n_registradas += 1
                leidas.append({
                    "archivo": nombre_archivo,
                    "titular": cert.get("titular", ""),
                    "tipo_persona": cert.get("tipo_persona", ""),
                    "tipo_documento": cert.get("tipo_documento", ""),
                    "numero_documento": nit,
                    "banco": cert.get("banco", ""),
                    "tipo_producto": cuenta.get("tipo_producto", ""),
                    "numero_cuenta": cuenta.get("numero_cuenta", ""),
                    "estado": cuenta.get("estado", ""),
                })
        except CertificadoBancarioError as exc:
            errores.append(f"{f.filename}: {exc}")
        except Exception:
            logger.exception("Error inesperado leyendo el certificado %s", f.filename)
            errores.append(f"{f.filename}: error inesperado al leer el certificado.")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if n_registradas:
        audit.registrar(
            "terceros.cuentas_importar", empresa_id=emp.id,
            detalle=f"archivos={len(archivos)} cuentas={n_registradas}",
        )
        msg = f"✓ {n_registradas} cuenta(s) bancaria(s) registrada(s)."
        if errores:
            msg += f" {len(errores)} archivo(s) con error."
        flash(msg, "success" if not errores else "info")
    else:
        flash("No se pudo leer ningún certificado bancario. " + " ".join(errores),
              "error")

    return render_template(
        "terceros.html",
        info=_info_maestro_terceros(emp),
        actividad=_actividad_terceros(emp),
        cuentas_bancarias=_listar_cuentas_bancarias(emp),
        resultado=None,
        resultado_cuentas={"cuentas_leidas": leidas, "errores": errores},
    )


@bp.route("/terceros/cuentas-bancarias/<int:cuenta_id>/eliminar", methods=["POST"])
@require_permission("terceros.gestionar")
def terceros_cuenta_eliminar(cuenta_id):
    """Elimina una cuenta bancaria de tercero registrada."""
    from app.database import inicializar_db, eliminar_cuenta_bancaria_tercero

    emp = base._empresa_actual()
    inicializar_db(emp.db_path)
    eliminar_cuenta_bancaria_tercero(cuenta_id, db_path=emp.db_path)
    audit.registrar(
        "terceros.cuenta_eliminar", empresa_id=emp.id,
        detalle=f"cuenta_id={cuenta_id}",
    )
    flash("Cuenta bancaria eliminada.", "info")
    return redirect(url_for("web.terceros"))


# ---------------------------------------------------------------------------
# GET /test-procesar — Prueba end-to-end sin file dialog (solo DEBUG)
# ---------------------------------------------------------------------------
