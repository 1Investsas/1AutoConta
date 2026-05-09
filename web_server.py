"""
Punto de entrada del servidor web contable-auto.

Uso:
    python web_server.py
    python web_server.py --port 8080
    python web_server.py --host 0.0.0.0 --port 8080
"""

import click
from app.web import create_app


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Host del servidor.")
@click.option("--port", default=5000, show_default=True, help="Puerto del servidor.")
@click.option("--debug/--no-debug", default=True, show_default=True, help="Modo debug con hot-reload.")
def serve(host, port, debug):
    """Arranca el servidor web de contable-auto."""
    app = create_app()
    print(f"\n  contable-auto  — Interfaz Web")
    print(f"  Corriendo en  http://{host}:{port}")
    print(f"  Ctrl+C para detener\n")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    serve()
