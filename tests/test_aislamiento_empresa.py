"""
Tests del aislamiento por empresa en la capa de datos.

Con SQLite cada empresa tiene su propio archivo .db, así que el aislamiento ya
es total (estos tests lo verifican). El discriminador `empresa_id` solo aplica
a Azure SQL (tablas compartidas); aquí se prueba la derivación del id y que la
condición de filtro queda vacía en SQLite (comportamiento idéntico al original).
"""

import app.database as db


class _FakeConn:
    def __init__(self, is_sqlite):
        self.is_sqlite = is_sqlite


class TestEmpresaIdDesdeDbPath:
    def test_principal(self):
        assert db._empresa_id_desde_db_path("db/contable.db") == "principal"

    def test_otra_empresa(self):
        assert db._empresa_id_desde_db_path("db/contable_acme.db") == "acme"

    def test_ruta_absoluta(self):
        assert db._empresa_id_desde_db_path("/home/data/db/contable_acme_2.db") == "acme_2"

    def test_none_cae_a_principal(self):
        assert db._empresa_id_desde_db_path(None) == "principal"

    def test_nombre_no_estandar_cae_a_principal(self):
        assert db._empresa_id_desde_db_path("/tmp/mi_base.db") == "principal"


class TestCondicionEmpresa:
    def test_sqlite_sin_condicion(self):
        cond, params = db._cond_empresa(_FakeConn(is_sqlite=True), "db/contable_acme.db")
        assert cond == ""
        assert params == ()

    def test_azure_con_condicion(self):
        cond, params = db._cond_empresa(_FakeConn(is_sqlite=False), "db/contable_acme.db")
        assert cond == "empresa_id = ?"
        assert params == ("acme",)

    def test_and_empresa_sqlite_vacio(self):
        frag, params = db._and_empresa(_FakeConn(is_sqlite=True), "db/contable.db")
        assert frag == ""
        assert params == ()

    def test_and_empresa_azure(self):
        frag, params = db._and_empresa(_FakeConn(is_sqlite=False), "db/contable.db")
        assert frag == " AND empresa_id = ?"
        assert params == ("principal",)

    def test_where_empresa_sqlite_vacio(self):
        frag, params = db._where_empresa(_FakeConn(is_sqlite=True), "db/contable.db")
        assert frag == ""
        assert params == ()

    def test_where_empresa_azure(self):
        frag, params = db._where_empresa(_FakeConn(is_sqlite=False), "db/contable_x.db")
        assert frag == " WHERE empresa_id = ?"
        assert params == ("x",)


class TestAislamientoPorArchivoSqlite:
    """Con SQLite cada empresa (cada archivo) ve solo sus propios datos."""

    def test_documentos_aislados_entre_empresas(self, tmp_path):
        db_a = str(tmp_path / "contable.db")
        db_b = str(tmp_path / "contable_otra.db")
        db.inicializar_db(db_a)
        db.inicializar_db(db_b)

        db.registrar_documento(
            "CUFE-A", "Factura", "FACTURA_COMPRA", "1", "",
            "900", "Prov A", "901", "1INVEST", 1000.0, None, "a.xlsx", db_path=db_a,
        )

        # El mismo CUFE NO se considera duplicado para la otra empresa.
        assert db.cufe_existe("CUFE-A", db_path=db_a) is True
        assert db.cufe_existe("CUFE-A", db_path=db_b) is False


class _Result:
    def __init__(self, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _RecConn:
    """Conexión simulada que registra el SQL y params ejecutados (Azure SQL)."""

    def __init__(self, is_sqlite=False, one=None, rows=None):
        self.is_sqlite = is_sqlite
        self.calls = []
        self._result = _Result(one=one, rows=rows)

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return self._result

    def commit(self):
        pass

    def close(self):
        pass


class TestSqlAzureScoping:
    """En modo Azure SQL las consultas deben filtrar/insertar por empresa_id."""

    def test_cufe_existe_filtra_por_empresa(self, monkeypatch):
        conn = _RecConn(is_sqlite=False, one=None)
        monkeypatch.setattr(db, "get_connection", lambda p=None: conn)
        db.cufe_existe("CUFE-X", db_path="db/contable_acme.db")
        sql, params = conn.calls[0]
        assert "WHERE cufe = ? AND empresa_id = ?" in sql
        assert params == ("CUFE-X", "acme")

    def test_listar_importaciones_filtra_por_empresa(self, monkeypatch):
        conn = _RecConn(is_sqlite=False, rows=[])
        monkeypatch.setattr(db, "get_connection", lambda p=None: conn)
        db.listar_importaciones(db_path="db/contable_acme.db")
        sql, params = conn.calls[0]
        assert "WHERE empresa_id = ?" in sql
        assert "ORDER BY id DESC" in sql
        assert params == ("acme",)

    def test_registrar_documento_incluye_empresa(self, monkeypatch):
        conn = _RecConn(is_sqlite=False)
        monkeypatch.setattr(db, "get_connection", lambda p=None: conn)
        db.registrar_documento(
            "CUFE-Y", "Factura", "FACTURA_COMPRA", "1", "",
            "900", "Prov", "901", "Cli", 100.0, None, "a.xlsx",
            db_path="db/contable_acme.db",
        )
        sql, params = conn.calls[0]
        assert "empresa_id" in sql
        assert "WHERE cufe = ? AND empresa_id = ?" in sql
        # (cufe, emp_id) para el IF NOT EXISTS + (emp_id, ...) para el INSERT
        assert params[0] == "CUFE-Y"
        assert params[1] == "acme"
        assert params[2] == "acme"


class TestFuncionesNuevas:
    """Las funciones que reemplazan consultas crudas de routes.py."""

    def test_resumen_dashboard(self, tmp_path):
        db_path = str(tmp_path / "contable.db")
        db.inicializar_db(db_path)
        db.registrar_documento(
            "CUFE-1", "Factura", "FACTURA_VENTA", "1", "",
            "900", "Emisor", "901", "Cliente", 500.0, None, "x.xlsx", db_path=db_path,
        )
        resumen = db.obtener_resumen_dashboard(db_path)
        assert resumen["total_docs"] == 1
        assert resumen["total_historial"] == 0
        assert any(u["clasificacion"] == "FACTURA_VENTA" for u in resumen["ultimas"])

    def test_resumen_dashboard_vacio(self, tmp_path):
        db_path = str(tmp_path / "contable.db")
        db.inicializar_db(db_path)
        resumen = db.obtener_resumen_dashboard(db_path)
        assert resumen["total_docs"] == 0
        assert resumen["ultimas"] == []
        assert resumen["total_historial"] == 0

    def test_listar_historial_cuentas(self, tmp_path):
        db_path = str(tmp_path / "contable.db")
        db.inicializar_db(db_path)
        db.actualizar_historial_cuenta("FACTURA_COMPRA", "900", "base", "51959501", db_path)
        db.actualizar_historial_cuenta("FACTURA_COMPRA", "900", "base", "51959501", db_path)
        db.actualizar_historial_cuenta("FACTURA_COMPRA", "800", "iva", "24081001", db_path)

        entradas, total = db.listar_historial_cuentas(db_path, limite=200)
        assert total == 2
        assert len(entradas) == 2
        # Ordenado por usos DESC: la cuenta con 2 usos va primero.
        assert entradas[0]["cuenta"] == "51959501"
        assert entradas[0]["usos"] == 2
        assert "ultima_fecha" in entradas[0]

    def test_listar_historial_cuentas_limite(self, tmp_path):
        db_path = str(tmp_path / "contable.db")
        db.inicializar_db(db_path)
        for i in range(5):
            db.actualizar_historial_cuenta("FACTURA_COMPRA", f"{i}", "base", "5195", db_path)
        entradas, total = db.listar_historial_cuentas(db_path, limite=3)
        assert total == 5
        assert len(entradas) == 3


class TestTenantAwareAzure:
    """En Azure SQL todas las tablas compartidas llevan empresa_id e índices."""

    def test_ddl_todas_las_tablas_tienen_empresa_id(self):
        conn = _RecConn(is_sqlite=False)
        db._create_tables_mssql(conn)
        creates = [sql for sql, _ in conn.calls if "CREATE TABLE" in sql]
        # documentos_importados, bitacora, historial_cuentas, importaciones,
        # procesos_banco, correcciones_tercero, cuentas_bancarias_tercero,
        # cash_accounts, cash_periods, cash_movements,
        # mixed_accounts, mixed_periods, mixed_movements,
        # patrones_aprendidos, tokens_aprendidos, importaciones_conocimiento
        # (la tabla `empresas` es el catálogo y se crea aparte, sin empresa_id).
        assert len(creates) == 16
        assert all("empresa_id" in sql for sql in creates)

    def test_asegurar_indices_idempotente_y_por_empresa(self):
        conn = _RecConn(is_sqlite=False)
        db._asegurar_indices_mssql(conn)
        sqls = [sql for sql, _ in conn.calls]
        assert len(sqls) == 11
        # Todos guardados por IF NOT EXISTS (idempotencia) y liderados por empresa_id.
        assert all("IF NOT EXISTS" in sql for sql in sqls)
        assert any("CREATE INDEX ix_importaciones_empresa ON importaciones (empresa_id, id)" in sql
                   for sql in sqls)
        assert any("ix_procesos_banco_empresa" in sql and "(empresa_id, id)" in sql
                   for sql in sqls)
        assert any("ix_documentos_empresa_clasif" in sql
                   and "(empresa_id, clasificacion)" in sql for sql in sqls)
        assert any("ix_cuentas_banco_tercero" in sql
                   and "(empresa_id, nit_tercero)" in sql for sql in sqls)
        assert any("ix_cash_accounts_empresa" in sql
                   and "(empresa_id, id)" in sql for sql in sqls)
        assert any("ix_cash_periods_cuenta" in sql
                   and "(empresa_id, cash_account_id)" in sql for sql in sqls)
        assert any("ix_cash_movements_periodo" in sql
                   and "(empresa_id, cash_period_id)" in sql for sql in sqls)
        assert any("ix_mixed_accounts_empresa" in sql
                   and "(empresa_id, id)" in sql for sql in sqls)
        assert any("ix_mixed_periods_cuenta" in sql
                   and "(empresa_id, mixed_account_id)" in sql for sql in sqls)
        assert any("ix_mixed_movements_periodo" in sql
                   and "(empresa_id, mixed_period_id)" in sql for sql in sqls)
        assert any("ix_import_conocimiento_emp" in sql
                   and "(empresa_id, id)" in sql for sql in sqls)

    def test_inicializar_db_azure_crea_indices(self, monkeypatch):
        conn = _RecConn(is_sqlite=False)
        monkeypatch.setattr(db, "get_connection", lambda p=None: conn)
        db.inicializar_db("db/contable_acme.db")
        sqls = [sql for sql, _ in conn.calls]
        # La inicialización en modo Azure debe emitir los CREATE INDEX tenant-aware.
        assert any("CREATE INDEX ix_importaciones_empresa" in sql for sql in sqls)
        assert any("CREATE INDEX ix_procesos_banco_empresa" in sql for sql in sqls)
        assert any("CREATE INDEX ix_documentos_empresa_clasif" in sql for sql in sqls)

    def test_sqlite_no_crea_indices_mssql(self, tmp_path):
        """En SQLite no hay empresa_id; inicializar_db no debe emitir índices Azure."""
        db_path = str(tmp_path / "contable.db")
        db.inicializar_db(db_path)  # no debe fallar
        import sqlite3
        nombres = [
            r[1] for r in sqlite3.connect(db_path)
            .execute("PRAGMA index_list(importaciones)").fetchall()
        ]
        assert "ix_importaciones_empresa" not in nombres
