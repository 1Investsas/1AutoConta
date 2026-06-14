"""
Pruebas de la capa de almacenamiento y del exportador en modo cloud.

Regresión: el bug «[Errno 2] No such file or directory: 'blob:/output/...'»
ocurría porque el pipeline subía el Excel dos veces — exportar_excel ya sube
a Blob Storage y retorna una referencia 'blob://output/...', y luego el código
volvía a llamar a save_local_file() sobre esa referencia. Path('blob://...')
colapsa el '//' a '/' y read_bytes() falla buscando una ruta local inexistente.
"""

from datetime import datetime

import pytest

from app import storage
from app.exportador import exportar_excel
from app.models import LineaContable, PreasientoContable


class _FakeDownload:
    def __init__(self, data: bytes):
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobClient:
    def __init__(self, store: dict, name: str):
        self._store = store
        self._name = name

    def get_blob_properties(self):
        if self._name not in self._store:
            raise FileNotFoundError(self._name)
        return {"name": self._name}


class _FakeContainer:
    """Contenedor Azure en memoria para pruebas."""

    def __init__(self, store: dict):
        self._store = store

    def upload_blob(self, name, data, overwrite=False):
        self._store[name] = bytes(data)

    def download_blob(self, name):
        return _FakeDownload(self._store[name])

    def get_blob_client(self, name):
        return _FakeBlobClient(self._store, name)

    def delete_blob(self, name):
        self._store.pop(name, None)


@pytest.fixture
def fake_cloud(monkeypatch):
    """Activa el modo cloud con un contenedor de blobs en memoria."""
    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "is_cloud", lambda: True)
    monkeypatch.setattr(storage, "_get_container_client", lambda: _FakeContainer(blobs))
    return blobs


@pytest.fixture
def preasiento_minimo():
    """Un preasiento balanceado mínimo para exportar."""
    return PreasientoContable(
        cufe="CUFE-TEST-001",
        tipo_documento="Factura electrónica",
        clasificacion="COMPRA",
        codigo_comprobante="50",
        titulo_comprobante="Facturas de compra",
        fecha_emision=datetime(2026, 6, 14),
        folio="1001",
        prefijo="FC",
        tercero_nit="800123456",
        tercero_nombre="PROVEEDOR SA",
        tercero_encontrado=True,
        total=119000.0,
        base_gravable=100000.0,
        lineas=[
            LineaContable("CUFE-TEST-001", 1, "13050501", "Cuenta",
                          100000.0, 0.0, "Base", "800123456", "PROVEEDOR SA"),
            LineaContable("CUFE-TEST-001", 2, "22050501", "Cuenta",
                          0.0, 100000.0, "Contraparte", "800123456", "PROVEEDOR SA"),
        ],
        cuadra=True,
    )


def test_exportar_excel_cloud_sube_una_sola_vez(fake_cloud, preasiento_minimo, tmp_path):
    """En modo cloud, exportar_excel retorna una referencia blob descargable."""
    ref = exportar_excel(
        preasientos=[preasiento_minimo],
        excepciones=[],
        bitacora=[],
        output_path=str(tmp_path),
    )

    # Debe devolver una referencia de blob, no una ruta local.
    assert ref.startswith("blob://output/")
    assert storage.file_exists(ref)

    # El contenido debe ser un .xlsx válido (firma ZIP "PK").
    data = storage.get_download_bytes(ref)
    assert data[:2] == b"PK"


def test_save_local_file_rechaza_referencia_blob(fake_cloud):
    """save_local_file espera una ruta local; una referencia blob no es válida.

    Documenta la causa raíz del bug: re-subir la referencia que ya devolvió
    exportar_excel rompía con FileNotFoundError sobre 'blob:/output/...'.
    """
    with pytest.raises(FileNotFoundError):
        storage.save_local_file("blob://output/preasientos_x.xlsx", "output")
