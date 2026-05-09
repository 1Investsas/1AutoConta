"""
Punto de entrada para Azure App Service.

Azure detecta automáticamente 'app.py' y ejecuta 'gunicorn app:app'.
"""
from app.web import create_app

app = create_app()
