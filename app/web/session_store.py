"""
Almacenamiento server-side de resultados de proceso.

La cookie de sesión de Flask tiene un límite de ~4 KB en el navegador, por lo
que los resultados completos (preasientos, movimientos bancarios) no caben en
ella y se perderían en silencio. Este módulo guarda los datos como JSON en el
almacenamiento (disco local o Azure Blob según configuración) y deja en la
sesión únicamente una referencia pequeña.
"""

import json
import logging
import uuid

from flask import session

from app import storage as store

logger = logging.getLogger(__name__)

# Categoría/carpeta donde se guardan los JSON de resultados
_CATEGORY = "web_sessions"


def guardar(clave: str, data: dict | list) -> None:
    """Guarda `data` como JSON server-side y deja la referencia en session[clave].

    Si ya existía un resultado para esta clave, se sobreescribe el mismo
    archivo (la referencia no cambia) para no acumular archivos huérfanos.
    """
    ref = session.get(clave)
    if not ref or not isinstance(ref, str):
        ref = None

    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

    if ref:
        # Reutilizar el archivo existente: extraer el nombre y sobreescribir
        filename = ref.replace("blob://", "").rsplit("/", 1)[-1]
    else:
        filename = f"{uuid.uuid4().hex}.json"

    session[clave] = store.save_file(payload, _CATEGORY, filename)


def cargar(clave: str):
    """Retorna los datos guardados para session[clave], o None si no existen."""
    ref = session.get(clave)
    if not ref:
        return None
    try:
        if not store.file_exists(ref):
            return None
        return json.loads(store.get_download_bytes(ref).decode("utf-8"))
    except Exception:
        logger.exception("Error cargando resultado de sesión '%s'", clave)
        return None


def eliminar(clave: str) -> None:
    """Elimina el resultado guardado y la referencia de la sesión."""
    ref = session.pop(clave, None)
    if ref:
        store.delete_file(ref)
