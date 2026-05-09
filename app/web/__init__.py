"""
Interfaz web del sistema contable-auto (FASE 2).

Punto de entrada — Flask application factory.
"""

import logging
import os
from pathlib import Path

from flask import Flask

logger = logging.getLogger(__name__)

# Raíz del proyecto: app/web/__init__.py → 3 niveles arriba
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Carpeta de uploads con ruta absoluta (funciona sin importar el CWD)
UPLOAD_FOLDER = _PROJECT_ROOT / "uploads"
MAX_CONTENT_MB = 50


def create_app() -> Flask:
    """Crea y configura la aplicación Flask."""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    secret = os.getenv("FLASK_SECRET_KEY", "contable-auto-dev-key-cambiar-en-prod")
    if "dev" in secret.lower() or "cambiar" in secret.lower():
        logger.warning(
            "FLASK_SECRET_KEY usa el valor de desarrollo. "
            "Configura una clave segura en .env para producción."
        )

    app.secret_key = secret
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_MB * 1024 * 1024

    # Crear carpeta de uploads si no existe
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    from app.web.routes import bp
    app.register_blueprint(bp)

    return app
