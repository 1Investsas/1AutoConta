"""
Interfaz web del sistema contable-auto (FASE 2).

Punto de entrada — Flask application factory.
"""

import logging
import os
import secrets
from pathlib import Path

from flask import Flask, render_template
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError

logger = logging.getLogger(__name__)

# Raíz del proyecto: app/web/__init__.py → 3 niveles arriba
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Carpeta de uploads con ruta absoluta (funciona sin importar el CWD)
UPLOAD_FOLDER = _PROJECT_ROOT / "uploads"
MAX_CONTENT_MB = 50

csrf = CSRFProtect()


def _resolver_secret_key() -> str:
    """Retorna la clave secreta de sesiones.

    Si no hay una clave configurada (o es la de desarrollo), se genera una
    aleatoria y se persiste en db/.flask_secret_key para que todos los
    workers y reinicios compartan la misma clave. Nunca se usa una clave
    fija conocida públicamente (permitiría falsificar cookies de sesión).
    """
    secret = os.getenv("FLASK_SECRET_KEY", "")
    if secret and "dev" not in secret.lower() and "cambiar" not in secret.lower():
        return secret

    logger.warning(
        "FLASK_SECRET_KEY no configurada (o usa el valor de desarrollo). "
        "Se usará una clave aleatoria autogenerada; configura una clave fija "
        "en .env para producción."
    )

    # Guardar la clave junto a la BD (en Azure, almacenamiento persistente):
    # así sobrevive a reinicios y despliegues y las sesiones —incluida la
    # empresa seleccionada— no se invalidan en cada arranque.
    from app.config import DB_DIR
    db_dir = Path(DB_DIR) if os.path.isabs(DB_DIR) else _PROJECT_ROOT / DB_DIR
    key_file = db_dir / ".flask_secret_key"
    try:
        if key_file.exists():
            persisted = key_file.read_text().strip()
            if persisted:
                return persisted
        key_file.parent.mkdir(parents=True, exist_ok=True)
        nueva = secrets.token_hex(32)
        key_file.write_text(nueva)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        return nueva
    except OSError:
        # Sistema de archivos de solo lectura: clave aleatoria por proceso
        return secrets.token_hex(32)


def create_app() -> Flask:
    """Crea y configura la aplicación Flask."""
    from app import database
    from app.config import DB_PATH
    from app.database import inicializar_db

    app = Flask(__name__, template_folder="templates", static_folder="static")

    app.secret_key = _resolver_secret_key()
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_MB * 1024 * 1024

    # Protección CSRF para todos los formularios POST
    csrf.init_app(app)

    # Cierre de las conexiones de BD compartidas por-petición (ver database.py)
    database.init_app(app)

    # Caché agresivo de estáticos (CSS, logos): las URLs llevan un parámetro de
    # versión (?v=<mtime>), así que pueden cachearse por mucho tiempo sin riesgo
    # de servir versiones viejas tras un despliegue.
    _configurar_cache_estaticos(app)

    # Crear carpeta de uploads si no existe
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    # Inicializar la BD una sola vez al arrancar (no en cada request)
    inicializar_db(DB_PATH)

    # Scheduler interno de la importación automática de RADIAN (opt-in).
    _iniciar_scheduler_radian()

    from app.web.routes import bp
    app.register_blueprint(bp)

    # Compuerta de autenticación (Fase 3): exige sesión iniciada en todas las
    # rutas salvo login/logout y estáticos. Se registra después del blueprint
    # para que los endpoints existan al resolver los exentos.
    from app import authn
    authn.registrar(app)

    _registrar_manejadores_error(app)

    return app


def _configurar_cache_estaticos(app: Flask) -> None:
    """Permite al navegador cachear los archivos estáticos de forma segura.

    Sin esto el navegador revalida (o re-descarga) el CSS y los logos en CADA
    página, lo que hace sentir lenta toda la app. La estrategia es la clásica de
    "cache busting": cada URL de estático lleva ``?v=<mtime del archivo>``, de
    modo que puede cachearse por 30 días; cuando un despliegue cambia el
    archivo, cambia la URL y el navegador descarga la versión nueva.
    """
    static_dir = Path(app.static_folder)
    versiones: dict[str, int] = {}  # caché de mtimes (evita stat() repetidos)

    @app.url_defaults
    def _versionar_estaticos(endpoint, values):
        if endpoint != "static" or "v" in values:
            return
        filename = values.get("filename")
        if not filename:
            return
        v = versiones.get(filename)
        if v is None:
            try:
                v = int((static_dir / filename).stat().st_mtime)
            except OSError:
                v = 0
            versiones[filename] = v
        if v:
            values["v"] = v

    @app.after_request
    def _cache_control_estaticos(resp):
        from flask import request
        if request.endpoint == "static" and resp.status_code in (200, 304):
            # Flask marca los estáticos con "no-cache" cuando no hay
            # SEND_FILE_MAX_AGE_DEFAULT; hay que quitarlo o el navegador
            # revalidaría en cada página a pesar del max-age.
            resp.cache_control.no_cache = None
            resp.cache_control.public = True
            resp.cache_control.max_age = 60 * 60 * 24 * 30  # 30 días
            resp.cache_control.immutable = True
        return resp


def _iniciar_scheduler_radian() -> None:
    """Arranca el scheduler diario de RADIAN si está habilitado por configuración.

    Solo corre cuando ``RADIAN_SCHEDULER_ENABLED=true``. En despliegues con varias
    instancias conviene dejarlo desactivado y usar un cron externo contra
    ``/radian/auto/cron`` para no duplicar la importación.
    """
    from app.config import RADIAN_SCHEDULER_ENABLED
    if not RADIAN_SCHEDULER_ENABLED:
        return
    try:
        from app.radian_auto.scheduler import iniciar_scheduler
        if iniciar_scheduler():
            logger.info("Scheduler de importación automática RADIAN activado.")
    except Exception:
        logger.exception("No se pudo iniciar el scheduler de RADIAN.")


def _registrar_manejadores_error(app: Flask) -> None:
    """Páginas de error amigables en lugar de los errores crudos de Werkzeug."""
    from app.config import NOMBRE_EMPRESA, NIT_EMPRESA

    def _render(mensaje: str, codigo: int):
        return render_template(
            "error.html",
            error=mensaje,
            empresa=NOMBRE_EMPRESA,
            nit=NIT_EMPRESA,
        ), codigo

    @app.errorhandler(404)
    def _not_found(e):
        return _render("Página no encontrada", 404)

    @app.errorhandler(403)
    def _forbidden(e):
        return _render(
            "No tienes permiso para realizar esta acción con la empresa activa. "
            "Si crees que es un error, contacta al administrador.",
            403,
        )

    @app.errorhandler(413)
    def _too_large(e):
        return _render(
            f"El archivo supera el tamaño máximo permitido ({MAX_CONTENT_MB} MB)",
            413,
        )

    @app.errorhandler(CSRFError)
    def _csrf_error(e):
        return _render(
            "La sesión del formulario expiró. Vuelve a intentarlo.", 400
        )

    @app.errorhandler(500)
    def _server_error(e):
        logger.exception("Error interno no controlado")
        return _render("Error interno del servidor", 500)
