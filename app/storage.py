"""
Módulo de almacenamiento para 1ContaBot.

Proporciona una capa de abstracción para operaciones de archivos:
- Cuando AZURE_STORAGE_CONNECTION_STRING está configurada → Azure Blob Storage.
- Cuando no está configurada → sistema de archivos local (comportamiento original).

Uso:
    from app.storage import save_file, load_file, get_download_bytes, is_cloud

    ref = save_file(data_bytes, "uploads", "RADIAN.xlsx")
    local_path = load_file(ref)          # descarga a temp si es blob
    content = get_download_bytes(ref)     # bytes para enviar al cliente
"""

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.config import AZURE_STORAGE_CONNECTION_STRING, AZURE_STORAGE_CONTAINER

logger = logging.getLogger(__name__)

# Raíz del proyecto: app/storage.py → 2 niveles arriba
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_cloud() -> bool:
    """Retorna True si el almacenamiento en la nube está configurado."""
    return bool(AZURE_STORAGE_CONNECTION_STRING)


def _get_blob_service():
    """Retorna el BlobServiceClient de Azure (import lazy)."""
    from azure.storage.blob import BlobServiceClient
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


def _get_container_client():
    """Retorna el ContainerClient, creando el contenedor si no existe."""
    service = _get_blob_service()
    container = service.get_container_client(AZURE_STORAGE_CONTAINER)
    try:
        container.get_container_properties()
    except Exception:
        container.create_container()
    return container


# ═══════════════════════════════════════════════════════════════════════════
# API pública
# ═══════════════════════════════════════════════════════════════════════════

def save_file(data: bytes, category: str, filename: str) -> str:
    """
    Guarda un archivo y retorna una referencia (ruta local o blob name).

    Args:
        data:     Contenido del archivo en bytes.
        category: Subcarpeta/categoría (e.g. 'uploads', 'output', 'data').
        filename: Nombre del archivo.

    Returns:
        Referencia al archivo guardado:
        - Local: ruta absoluta del archivo.
        - Cloud: 'blob://<category>/<filename>'.
    """
    if is_cloud():
        blob_name = f"{category}/{filename}"
        container = _get_container_client()
        container.upload_blob(blob_name, data, overwrite=True)
        logger.info("Archivo subido a Blob: %s", blob_name)
        return f"blob://{blob_name}"
    else:
        folder = _PROJECT_ROOT / category
        folder.mkdir(parents=True, exist_ok=True)
        filepath = folder / filename
        filepath.write_bytes(data)
        logger.info("Archivo guardado localmente: %s", filepath)
        return str(filepath)


def save_local_file(local_path: str, category: str, filename: Optional[str] = None) -> str:
    """
    Sube un archivo local existente al almacenamiento.

    Args:
        local_path: Ruta al archivo local existente.
        category:   Subcarpeta/categoría.
        filename:   Nombre destino (usa el nombre original si es None).

    Returns:
        Referencia al archivo guardado.
    """
    fname = filename or Path(local_path).name
    data = Path(local_path).read_bytes()
    return save_file(data, category, fname)


def load_file(reference: str) -> str:
    """
    Retorna una ruta local al archivo. Si es un blob, lo descarga a un
    archivo temporal.

    Args:
        reference: Referencia retornada por save_file().

    Returns:
        Ruta absoluta a un archivo local legible.
    """
    if reference.startswith("blob://"):
        blob_name = reference[7:]  # quitar 'blob://'
        container = _get_container_client()
        blob_data = container.download_blob(blob_name).readall()

        # Guardar en temp con la extensión correcta
        ext = Path(blob_name).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.write(blob_data)
        tmp.close()
        logger.debug("Blob descargado a temp: %s → %s", blob_name, tmp.name)
        return tmp.name
    else:
        # Referencia local — es una ruta directa
        return reference


def get_download_bytes(reference: str) -> bytes:
    """
    Retorna el contenido del archivo como bytes (para enviar al cliente).

    Args:
        reference: Referencia retornada por save_file().

    Returns:
        Contenido del archivo en bytes.
    """
    if reference.startswith("blob://"):
        blob_name = reference[7:]
        container = _get_container_client()
        return container.download_blob(blob_name).readall()
    else:
        return Path(reference).read_bytes()


def delete_file(reference: str) -> None:
    """Elimina un archivo del almacenamiento (ignora errores si no existe)."""
    try:
        if reference.startswith("blob://"):
            blob_name = reference[7:]
            container = _get_container_client()
            container.delete_blob(blob_name)
        else:
            Path(reference).unlink(missing_ok=True)
    except Exception:
        logger.debug("No se pudo eliminar la referencia: %s", reference)


def file_exists(reference: str) -> bool:
    """Verifica si un archivo existe en el almacenamiento."""
    if reference.startswith("blob://"):
        blob_name = reference[7:]
        container = _get_container_client()
        blob = container.get_blob_client(blob_name)
        try:
            blob.get_blob_properties()
            return True
        except Exception:
            return False
    else:
        return Path(reference).exists()


def get_local_data_path(filename: str, category: str = "data") -> str:
    """
    Retorna la ruta a un archivo de datos maestros.

    En modo local: ruta directa en <category>/ (por defecto data/).
    En modo cloud: descarga desde blob '<category>/<filename>' a temp.

    El parámetro category permite carpetas por empresa, p. ej. 'data/acme'.
    """
    if is_cloud():
        ref = f"blob://{category}/{filename}"
        if file_exists(ref):
            return load_file(ref)
        else:
            # Fallback: intentar ruta local
            local = str(_PROJECT_ROOT / category / filename)
            if Path(local).exists():
                return local
            raise FileNotFoundError(
                f"Archivo maestro '{filename}' no encontrado en Blob Storage ni localmente."
            )
    else:
        return str(_PROJECT_ROOT / category / filename)
