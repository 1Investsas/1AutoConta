"""
Blueprint compartido y helpers comunes de la interfaz web.

Aquí vive el Blueprint ``web`` (único para toda la app: los endpoints se
llaman ``web.<funcion>`` sin importar el módulo), las claves de sesión y los
helpers que comparten los módulos de rutas: empresa activa, guardado de
uploads, rutas de maestros, caché de maestros Excel, cookie de descarga y
formateo de actividad.
"""

import io
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, request, send_file,
)
from werkzeug.utils import secure_filename

from app import storage as store
from app import authn, tenancy

logger = logging.getLogger(__name__)
bp = Blueprint("web", __name__)

ALLOWED_EXT     = {"xlsx", "xls"}
ALLOWED_EXT_CSV = {"csv", "txt"}

# Claves de sesión: solo guardan una referencia pequeña; los datos completos
# viven server-side (ver app/web/session_store.py).
KEY_RESULTADO = "resultado_ref"
KEY_BANCO     = "banco_ref"
KEY_EMPRESA   = tenancy.KEY_EMPRESA

_MESES_ABR = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
              "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


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
    """Retorna la ruta raíz del proyecto (1ContaBot/)."""
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


# Maestros de una empresa: (tipo / clave de formulario, nombre de archivo destino).
# Se comparte entre la subida, la descarga y la resolución de rutas por defecto.
MAESTROS_EMPRESA = (
    ("terceros",     "Listado_de_Terceros.xlsx"),
    ("cuentas",      "Listado_de_Cuentas_Contables.xlsx"),
    ("comprobantes", "Tipos_de_comprobante_contable.xlsx"),
)


def _rutas_maestros_default(emp) -> tuple:
    """Resuelve las rutas de los 3 maestros de la empresa (sin uploads nuevos)."""
    rutas = []
    for _tipo, default_name in MAESTROS_EMPRESA:
        try:
            path = emp.ruta_maestro(default_name)
        except FileNotFoundError:
            path = str(Path(_project_root()) / emp.data_category / default_name)
        rutas.append(path)
    return tuple(rutas)


def _ref_maestro(emp, filename: str) -> str:
    """Referencia de almacenamiento (local o blob) a un maestro de la empresa.

    Coincide con la referencia que produce `store.save_file` al subirlo, de modo
    que sirve para `file_exists` / `get_download_bytes` sin descargar a temp.
    """
    if store.is_cloud():
        return f"blob://{emp.data_category}/{filename}"
    return str(Path(_project_root()) / emp.data_category / filename)


def _maestros_disponibles(empresas) -> dict:
    """Mapa {empresa_id: [tipos con maestro cargado]} para la UI de descarga."""
    return {
        emp.id: [
            tipo for tipo, filename in MAESTROS_EMPRESA
            if store.file_exists(_ref_maestro(emp, filename))
        ]
        for emp in empresas
    }


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


def _columnas_cuentas(df):
    """Retorna (cod_col, nom_col) del plan de cuentas: las 2 primeras reales.

    Replica el criterio de `/api/cuentas`: las primeras columnas no-'Unnamed'
    son el código y el nombre de la cuenta.
    """
    valid_cols = [c for c in df.columns if not str(c).startswith("Unnamed")]
    cod_col = valid_cols[0] if valid_cols else df.columns[0]
    nom_col = valid_cols[1] if len(valid_cols) > 1 else None
    return cod_col, nom_col


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


def _estado_actividad(estado: str | None) -> str:
    return _ESTADO_ACTIVIDAD.get(estado or "", "procesando")
