"""
Punto de entrada para Azure App Service.

startup.sh arranca gunicorn con "application:app".

En Azure App Service las dependencias se empaquetan en la carpeta ./vendor
durante el despliegue (ver .github/workflows). Oryx no instala requirements.txt
en el servidor a menos que SCM_DO_BUILD_DURING_DEPLOYMENT esté activo, así que
añadimos ./vendor al path de Python para que las dependencias estén siempre
disponibles, independientemente de la build del servidor. En desarrollo local
la carpeta no existe y se usan las dependencias del entorno virtual normal.
"""
import os
import sys

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from app.web import create_app  # noqa: E402  (import tras ajustar sys.path)

app = create_app()
