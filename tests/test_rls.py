"""
Tests del Row-Level Security en Azure SQL (Fase 4 — defensa en profundidad).

Dos piezas:
- Contexto de sesión: `_cond_empresa` anota la empresa en la conexión y
  `DbConnection.execute` emite `sp_set_session_context` justo antes de la
  consulta que lo necesita (solo cuando la empresa cambia).
- Política RLS: `_asegurar_rls_mssql` crea de forma idempotente la función de
  predicado y la SECURITY POLICY sobre las tablas de datos compartidas.

Nada de esto aplica a SQLite (cada empresa tiene su propio archivo .db).
"""

import app.database as db
from app.database.core import DbConnection
from app.database.schema import _TABLAS_RLS, _asegurar_rls_mssql


class _FakeCursor:
    """Cursor pyodbc simulado que registra lo ejecutado en la conexión."""

    def __init__(self, conn):
        self._conn = conn
        self.description = None

    def execute(self, sql, params=None):
        self._conn.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._conn.one

    def fetchall(self):
        return []


class _FakePyodbc:
    """Conexión pyodbc simulada (cada cursor registra en `calls`)."""

    def __init__(self, one=None):
        self.calls = []
        self.one = one

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        pass

    def close(self):
        pass


def _conexion_azure(one=None):
    return DbConnection(_FakePyodbc(one=one), is_sqlite=False)


class TestContextoSesionRLS:
    """SESSION_CONTEXT('empresa_id') se fija por conexión, solo al cambiar."""

    def test_cond_empresa_anota_la_empresa_pendiente(self):
        conn = _conexion_azure()
        db._cond_empresa(conn, "db/contable_acme.db")
        assert conn._rls_empresa_pendiente == "acme"

    def test_execute_fija_contexto_antes_de_la_consulta(self):
        conn = _conexion_azure()
        cond, params = db._cond_empresa(conn, "db/contable_acme.db")
        conn.execute(f"SELECT * FROM importaciones WHERE {cond}", params)
        llamadas = conn._conn.calls
        assert len(llamadas) == 2
        assert "sp_set_session_context" in llamadas[0][0]
        assert llamadas[0][1] == ("acme",)
        assert llamadas[1][0].startswith("SELECT * FROM importaciones")

    def test_contexto_no_se_repite_si_la_empresa_no_cambia(self):
        conn = _conexion_azure()
        cond, params = db._cond_empresa(conn, "db/contable_acme.db")
        conn.execute(f"SELECT 1 FROM importaciones WHERE {cond}", params)
        db._cond_empresa(conn, "db/contable_acme.db")
        conn.execute(f"SELECT 2 FROM procesos_banco WHERE {cond}", params)
        contextos = [c for c, _ in conn._conn.calls if "sp_set_session_context" in c]
        assert len(contextos) == 1

    def test_contexto_cambia_al_cambiar_de_empresa(self):
        # La conexión se comparte por petición y puede tocar varias empresas.
        conn = _conexion_azure()
        cond, params = db._cond_empresa(conn, "db/contable_acme.db")
        conn.execute(f"SELECT 1 FROM importaciones WHERE {cond}", params)
        cond, params = db._cond_empresa(conn, "db/contable_beta.db")
        conn.execute(f"SELECT 1 FROM importaciones WHERE {cond}", params)
        contextos = [p for c, p in conn._conn.calls if "sp_set_session_context" in c]
        assert contextos == [("acme",), ("beta",)]

    def test_sin_cond_empresa_no_hay_contexto(self):
        # Consultas de sistema (usuarios, roles, empresas) no fijan contexto.
        conn = _conexion_azure()
        conn.execute("SELECT * FROM usuarios")
        assert len(conn._conn.calls) == 1

    def test_sqlite_no_emite_contexto(self, tmp_path):
        db_path = str(tmp_path / "contable_acme.db")
        db.inicializar_db(db_path)
        conn = db.get_connection(db_path)
        try:
            cond, params = db._cond_empresa(conn, db_path)
            assert cond == "" and params == ()
            conn.execute("SELECT COUNT(*) FROM importaciones")  # no debe fallar
        finally:
            conn.close()


class _RecConn:
    """DbConnection simulada para el DDL de la política (registra el SQL)."""

    is_sqlite = False

    def __init__(self, one=None):
        self.calls = []
        self._one = one

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return self

    def fetchone(self):
        return self._one


class TestPoliticaRLS:
    def test_crea_esquema_funcion_y_politica(self):
        conn = _RecConn(one=None)  # nada existe aún
        _asegurar_rls_mssql(conn)
        sqls = [s for s, _ in conn.calls]
        assert any("CREATE SCHEMA rls" in s for s in sqls)
        assert any("CREATE FUNCTION rls.fn_filtro_empresa" in s for s in sqls)
        assert any("SESSION_CONTEXT" in s for s in sqls)
        assert any("CREATE SECURITY POLICY rls.politica_empresa" in s for s in sqls)

    def test_cubre_todas_las_tablas_de_datos(self):
        conn = _RecConn(one=None)
        _asegurar_rls_mssql(conn)
        sqls = [s for s, _ in conn.calls]
        for tabla in _TABLAS_RLS:
            assert any(
                f"ON dbo.{tabla}" in s and "FILTER PREDICATE" in s for s in sqls
            ), f"falta predicado RLS para {tabla}"

    def test_no_incluye_tablas_de_sistema(self):
        # El catálogo y las tablas de control de acceso no llevan RLS: se
        # consultan antes de conocer la empresa activa / entre empresas.
        for tabla in ("empresas", "usuarios", "usuario_empresa_roles", "audit_log"):
            assert tabla not in _TABLAS_RLS

    def test_idempotente_si_ya_existe(self):
        conn = _RecConn(one=(1,))  # la política y los predicados ya existen
        _asegurar_rls_mssql(conn)
        sqls = [s for s, _ in conn.calls]
        assert not any("CREATE SECURITY POLICY" in s for s in sqls)
        assert not any("ALTER SECURITY POLICY" in s for s in sqls)

    def test_error_no_tumba_la_inicializacion(self):
        class _Explota:
            is_sqlite = False

            def execute(self, sql, params=None):
                raise RuntimeError("sin permiso ALTER ANY SECURITY POLICY")

        _asegurar_rls_mssql(_Explota())  # no debe propagar

    def test_inicializar_db_azure_aplica_rls(self, monkeypatch):
        conn = _RecConn(one=None)
        monkeypatch.setattr(db.core, "get_connection", lambda p=None: conn)

        def _commit():
            pass

        conn.commit = _commit
        conn.close = lambda: None
        db.reset_inicializacion_db()
        try:
            db.inicializar_db("db/contable_rls.db")
        finally:
            db.reset_inicializacion_db()
        sqls = [s for s, _ in conn.calls]
        assert any("CREATE SECURITY POLICY rls.politica_empresa" in s for s in sqls)
