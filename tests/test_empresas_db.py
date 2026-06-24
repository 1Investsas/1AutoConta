"""
Tests del registro de empresas en la BD de sistema (tabla `empresas`).

Verifican el CRUD (UPSERT por id, lectura, borrado) sobre SQLite y, mediante una
conexión simulada, que en modo Azure SQL el UPSERT use un MERGE con el número
correcto de parámetros (no se puede ejecutar T-SQL real en este entorno).
"""

import pytest

import app.database as db


def _p(tmp_path):
    path = str(tmp_path / "sistema.db")
    db.inicializar_db_sistema(path)
    return path


def test_listar_vacio(tmp_path):
    p = _p(tmp_path)
    assert db.listar_empresas_registro(p) == {}
    assert db.contar_empresas_registro(p) == 0


def test_guardar_y_obtener_con_json(tmp_path):
    p = _p(tmp_path)
    db.guardar_empresa_registro({
        "id": "acme", "nit": "900", "nombre": "ACME SAS", "sigla": "ACM",
        "cuenta_banco_default": "11100501", "nit_banco": "860",
        "cuentas_contraparte": {"FACTURA_COMPRA": "22059999"},
        "cuentas_impuestos": {"IVA": {"compra": "24080001"}},
        "cuentas_banco": [{"cuenta": "11100501", "etiqueta": "Ahorros"}],
        "bancos": [{"nit": "860", "nombre": "Bancolombia"}],
        "formato_banco": {"delimitador": ";"},
    }, p)

    reg = db.obtener_empresa_registro("acme", p)
    assert reg["nit"] == "900"
    assert reg["nombre"] == "ACME SAS"
    assert reg["sigla"] == "ACM"
    assert reg["cuenta_banco_default"] == "11100501"
    assert reg["cuentas_contraparte"] == {"FACTURA_COMPRA": "22059999"}
    assert reg["cuentas_impuestos"] == {"IVA": {"compra": "24080001"}}
    assert reg["cuentas_banco"] == [{"cuenta": "11100501", "etiqueta": "Ahorros"}]
    assert reg["bancos"][0]["nombre"] == "Bancolombia"
    assert reg["formato_banco"]["delimitador"] == ";"
    assert db.contar_empresas_registro(p) == 1


def test_json_vacio_se_guarda_nulo(tmp_path):
    p = _p(tmp_path)
    db.guardar_empresa_registro({"id": "x", "nit": "1", "nombre": "Uno"}, p)
    reg = db.obtener_empresa_registro("x", p)
    # Sin dicts/listas, las columnas JSON quedan None (el llamador las normaliza).
    assert reg["cuentas_contraparte"] is None
    assert reg["cuentas_banco"] is None


def test_upsert_actualiza_sin_duplicar(tmp_path):
    p = _p(tmp_path)
    db.guardar_empresa_registro({"id": "x", "nit": "1", "nombre": "Uno"}, p)
    db.guardar_empresa_registro({"id": "x", "nit": "2", "nombre": "Dos"}, p)
    assert db.contar_empresas_registro(p) == 1
    assert db.obtener_empresa_registro("x", p)["nombre"] == "Dos"
    assert db.obtener_empresa_registro("x", p)["nit"] == "2"


def test_obtener_inexistente(tmp_path):
    p = _p(tmp_path)
    assert db.obtener_empresa_registro("nope", p) is None


def test_eliminar(tmp_path):
    p = _p(tmp_path)
    db.guardar_empresa_registro({"id": "x", "nit": "1", "nombre": "Uno"}, p)
    db.eliminar_empresa_registro("x", p)
    assert db.contar_empresas_registro(p) == 0
    assert db.obtener_empresa_registro("x", p) is None


def test_guardar_sin_id_falla(tmp_path):
    p = _p(tmp_path)
    with pytest.raises(ValueError):
        db.guardar_empresa_registro({"nit": "1", "nombre": "Sin id"}, p)


def test_listar_multiple(tmp_path):
    p = _p(tmp_path)
    db.guardar_empresa_registro({"id": "a", "nombre": "A"}, p)
    db.guardar_empresa_registro({"id": "b", "nombre": "B"}, p)
    reg = db.listar_empresas_registro(p)
    assert set(reg.keys()) == {"a", "b"}


# ---------------------------------------------------------------------------
# Azure SQL (T-SQL): el UPSERT usa MERGE con el conteo de parámetros correcto
# ---------------------------------------------------------------------------

class _RecConn:
    """Conexión simulada que registra el SQL y params ejecutados."""

    def __init__(self, is_sqlite=False):
        self.is_sqlite = is_sqlite
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

        class _R:
            def fetchone(self_):
                return None

            def fetchall(self_):
                return []

        return _R()

    def commit(self):
        pass

    def close(self):
        pass


def test_guardar_mssql_merge_param_count(monkeypatch):
    conn = _RecConn(is_sqlite=False)
    monkeypatch.setattr(db, "get_connection", lambda p=None: conn)
    db.guardar_empresa_registro({
        "id": "acme", "nit": "900", "nombre": "ACME", "sigla": "ACM",
        "cuentas_contraparte": {"a": "1"},
    }, "ignorado")
    sql, params = conn.calls[0]
    assert "MERGE empresas" in sql
    # USING(1) + UPDATE(12) + INSERT(14) = 27 parámetros enlazados.
    # (12/14 incluyen la columna dian_config añadida para RADIAN automático.)
    assert len(params) == 27
    assert params[0] == "acme"


def test_listar_mssql_sin_filtro_empresa(monkeypatch):
    """La tabla `empresas` es el catálogo: NO se filtra por empresa_id."""
    conn = _RecConn(is_sqlite=False)
    monkeypatch.setattr(db, "get_connection", lambda p=None: conn)
    db.listar_empresas_registro("ignorado")
    sql, _ = conn.calls[0]
    assert "empresa_id" not in sql
    assert "FROM empresas" in sql
