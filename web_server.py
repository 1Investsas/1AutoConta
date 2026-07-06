"""
Punto de entrada del servidor web 1ContaBot.

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
@click.option("--debug/--no-debug", default=False, show_default=True,
              help="Modo debug con hot-reload. NO usar en producción: "
                   "el debugger de Werkzeug permite ejecutar código remoto.")
def serve(host, port, debug):
    """Arranca el servidor web de 1ContaBot."""
    app = create_app()
    print(f"\n  1ContaBot  — Interfaz Web")
    print(f"  Corriendo en  http://{host}:{port}")
    print(f"  Ctrl+C para detener\n")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    serve()
