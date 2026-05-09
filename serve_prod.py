"""
Servidor de producción — contable-auto.

Usa Waitress (WSGI server de producción para Windows) en lugar del servidor
de desarrollo de Flask.

Uso:
    python serve_prod.py                      # 0.0.0.0:5000 (toda la red local)
    python serve_prod.py --host 127.0.0.1     # solo localhost
    python serve_prod.py --port 8080          # otro puerto

Variables de entorno relevantes (se leen del archivo .env):
    FLASK_SECRET_KEY   — clave secreta para las sesiones Flask (OBLIGATORIA en prod)
    HOST               — dirección de escucha (default: 0.0.0.0)
    PORT               — puerto de escucha (default: 5000)
    LOG_LEVEL          — nivel de logging (default: INFO)
"""

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

# Cargar .env antes de importar la app (así config.py recoge los valores)
load_dotenv(Path(__file__).parent / ".env")


def _check_secret_key() -> None:
    """Avisa si se está usando la clave de desarrollo en producción."""
    key = os.getenv("FLASK_SECRET_KEY", "")
    if not key or "dev" in key.lower() or "cambiar" in key.lower():
        print(
            "\n  ⚠️  ADVERTENCIA: FLASK_SECRET_KEY no configurada o usa el valor de desarrollo.\n"
            "     Agrega una clave segura a tu archivo .env:\n"
            "     FLASK_SECRET_KEY=<cadena aleatoria larga>\n",
            file=sys.stderr,
        )


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.command()
@click.option("--host", default=lambda: os.getenv("HOST", "0.0.0.0"),
              show_default="0.0.0.0", help="Dirección de escucha.")
@click.option("--port", default=lambda: int(os.getenv("PORT", "5000")),
              show_default=5000, help="Puerto de escucha.", type=int)
@click.option("--threads", default=4, show_default=True,
              help="Número de hilos Waitress.")
def serve(host: str, port: int, threads: int) -> None:
    """Arranca contable-auto con Waitress (servidor de producción)."""
    from waitress import serve as waitress_serve
    from app.web import create_app

    log_level = os.getenv("LOG_LEVEL", "INFO")
    _setup_logging(log_level)
    _check_secret_key()

    app = create_app()

    print(f"\n  contable-auto — Servidor de PRODUCCIÓN (Waitress)")
    print(f"  Escuchando en  http://{host}:{port}")
    print(f"  Hilos          {threads}")
    print(f"  Ctrl+C para detener\n")

    waitress_serve(
        app,
        host=host,
        port=port,
        threads=threads,
        channel_timeout=120,
        connection_limit=100,
    )


if __name__ == "__main__":
    serve()
