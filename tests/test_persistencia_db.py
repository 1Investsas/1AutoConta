"""
Tests de la persistencia de la BD SQLite en Blob Storage.

Verifican que, cuando hay Blob configurado (modo cloud), la BD se respalda y se
restaura, de modo que la app NO "empieza desde cero" tras reiniciarse el
contenedor (disco efímero).

Se simula el Blob con un diccionario en memoria monkeypatcheando las funciones
de app.storage.
"""

from pathlib import Path

import app.storage as store
import app.database as db


def _fake_cloud(monkeypatch) -> dict:
    """Configura un Blob Storage falso en memoria. Retorna el dict de blobs."""
    blobs: dict[str, bytes] = {}

    def save_file(data, category, filename):
        ref = f"blob://{category}/{filename}"
        blobs[ref] = bytes(data)
        return ref

    def file_exists(ref):
        if ref.startswith("blob://"):
            return ref in blobs
        return Path(ref).exists()

    def get_download_bytes(ref):
        if ref.startswith("blob://"):
            return blobs[ref]
        return Path(ref).read_bytes()

    monkeypatch.setattr(store, "is_cloud", lambda: True)
    monkeypatch.setattr(store, "save_file", save_file)
    monkeypatch.setattr(store, "file_exists", file_exists)
    monkeypatch.setattr(store, "get_download_bytes", get_download_bytes)
    return blobs


def test_respaldo_en_blob_tras_commit(tmp_path, monkeypatch):
    blobs = _fake_cloud(monkeypatch)
    db_path = str(tmp_path / "contable.db")

    db.inicializar_db(db_path)
    db.registrar_proceso_banco("x.csv", n_movimientos=3, db_path=db_path)

    # Forzar la subida pendiente sin esperar el debounce.
    db._flush_todos_los_respaldos()

    assert "blob://db/contable.db" in blobs
    assert len(blobs["blob://db/contable.db"]) > 0


def test_restaura_desde_blob_si_falta_local(tmp_path, monkeypatch):
    """Simula un contenedor nuevo: el .db local no existe pero sí hay respaldo."""
    blobs = _fake_cloud(monkeypatch)
    db_path = str(tmp_path / "contable.db")

    db.inicializar_db(db_path)
    db.registrar_proceso_banco("hist.csv", n_movimientos=5,
                               cuenta_banco="11100501", db_path=db_path)
    db._flush_todos_los_respaldos()
    assert "blob://db/contable.db" in blobs

    # Contenedor nuevo: se pierde el disco local y la marca de restauración.
    Path(db_path).unlink()
    for sufijo in ("-wal", "-shm"):
        Path(db_path + sufijo).unlink(missing_ok=True)
    db._db_restauradas.discard(db_path)

    # La siguiente lectura debe restaurar la BD desde Blob (no empezar de cero).
    procesos = db.listar_procesos_banco(db_path)
    assert len(procesos) == 1
    assert procesos[0]["archivo_nombre"] == "hist.csv"
    assert procesos[0]["n_movimientos"] == 5


def test_no_respalda_sin_blob(tmp_path, monkeypatch):
    """Sin Blob configurado (modo local) no se intenta ninguna subida."""
    monkeypatch.setattr(store, "is_cloud", lambda: False)
    db_path = str(tmp_path / "contable.db")

    db.inicializar_db(db_path)
    db.registrar_proceso_banco("local.csv", db_path=db_path)
    # No debe haber timers de respaldo agendados.
    db._flush_todos_los_respaldos()
    assert db_path not in db._db_timers


def test_restaura_no_pisa_bd_local_existente(tmp_path, monkeypatch):
    """Si ya hay una BD local con datos, no se sobrescribe con el respaldo."""
    blobs = _fake_cloud(monkeypatch)
    db_path = str(tmp_path / "contable.db")

    # Respaldo "viejo" en Blob con 1 proceso.
    db.inicializar_db(db_path)
    db.registrar_proceso_banco("viejo.csv", db_path=db_path)
    db._flush_todos_los_respaldos()

    # La BD local sigue presente y se le agrega un proceso nuevo.
    db.registrar_proceso_banco("nuevo.csv", db_path=db_path)
    db._db_restauradas.discard(db_path)  # forzar reevaluación de restauración

    procesos = db.listar_procesos_banco(db_path)
    # Debe conservar la BD local (2 procesos), no restaurar el respaldo viejo (1).
    assert len(procesos) == 2
