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
    from app.config import DB_PATH
    from app.database import inicializar_db

    app = Flask(__name__, template_folder="templates", static_folder="static")

    app.secret_key = _resolver_secret_key()
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_MB * 1024 * 1024

    # Protección CSRF para todos los formularios POST
    csrf.init_app(app)

    # Crear carpeta de uploads si no existe
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    # Inicializar la BD una sola vez al arrancar (no en cada request)
    inicializar_db(DB_PATH)

    from app.web.routes import bp
    app.register_blueprint(bp)

    # Compuerta de autenticación (Fase 3): exige sesión iniciada en todas las
    # rutas salvo login/logout y estáticos. Se registra después del blueprint
    # para que los endpoints existan al resolver los exentos.
    from app import authn
    authn.registrar(app)

    _registrar_manejadores_error(app)

    return app


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
