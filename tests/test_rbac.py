"""
Tests de RBAC + autorización + multi-tenencia (Fase 3).

Cubren tres niveles:
- BD de sistema: esquema, seed de roles/permisos, CRUD de usuarios, asignación de
  roles (global y por empresa) y la unión de permisos efectivos.
- Lógica de autorización/tenencia (`authz`, `tenancy`): permisos efectivos,
  empresas accesibles y validación de acceso.
- Rutas web: la compuerta de login, el flujo de login/logout, la denegación por
  permiso (403), el aislamiento de empresa (no poder fijar una empresa ajena) y
  el registro en auditoría de los intentos denegados.
"""

import os

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import app.database as db                       # noqa: E402
from app import config, authn, authz, tenancy   # noqa: E402
from app import empresas as emp_mod             # noqa: E402
from app.web import create_app                  # noqa: E402
from app.authn import SESSION_EMAIL_KEY, SESSION_LOGOUT_KEY  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sysdb(tmp_path, monkeypatch):
    """BD de sistema aislada con el esquema RBAC sembrado."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    emp_mod._sistema_listo.clear()
    authn.reset_estado()
    p = str(tmp_path / "sistema.db")
    db.inicializar_db_sistema(p)
    db.inicializar_db_auth(p)
    authz.seed_rbac(p)
    yield p
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    monkeypatch.setattr(config, "AUTH_MODE", "dev")
    emp_mod._sistema_listo.clear()
    authn.reset_estado()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


def _crear_usuario_con_rol(p, email, rol, ambito="empresa", empresa_id="principal"):
    uid = db.crear_usuario(email, nombre=email.split("@")[0], db_path=p)
    rid = db.obtener_o_crear_rol(rol, db_path=p)
    if ambito == "global":
        db.asignar_rol_global(uid, rid, db_path=p)
    else:
        db.asignar_rol_empresa(uid, empresa_id, rid, db_path=p)
    return uid


def _login(client, email):
    with client.session_transaction() as sess:
        sess[SESSION_EMAIL_KEY] = email
        sess.pop(SESSION_LOGOUT_KEY, None)


# ═══════════════════════════════════════════════════════════════════════════
# Nivel BD
# ═══════════════════════════════════════════════════════════════════════════

def test_seed_crea_roles_y_permisos(sysdb):
    roles = {r["nombre"] for r in db.listar_roles(sysdb)}
    assert {"admin", "contador", "auxiliar", "consulta"} <= roles
    # El seed es idempotente: re-sembrar no duplica.
    authz.seed_rbac(sysdb)
    assert len(db.listar_roles(sysdb)) == len(authz.ROLES)


def test_crud_usuario_por_email_insensible_mayusculas(sysdb):
    uid = db.crear_usuario("Persona@Empresa.com", nombre="Persona", db_path=sysdb)
    u = db.obtener_usuario_por_email("persona@empresa.com", db_path=sysdb)
    assert u is not None and u["id"] == uid
    assert u["email"] == "persona@empresa.com"   # se normaliza a minúsculas
    assert u["activo"] is True


def test_permisos_usuario_union_global_y_empresa(sysdb):
    uid = _crear_usuario_con_rol(sysdb, "aux@local", "auxiliar", "empresa", "principal")
    perms_ppal = db.permisos_usuario(uid, "principal", db_path=sysdb)
    assert "radian.procesar" in perms_ppal
    assert "radian.editar" in perms_ppal
    # El auxiliar no exporta ni gestiona usuarios.
    assert "radian.exportar" not in perms_ppal
    assert "usuarios.gestionar" not in perms_ppal
    # El rol está acotado a 'principal': en otra empresa no hay permisos.
    assert db.permisos_usuario(uid, "acme", db_path=sysdb) == set()


def test_rol_global_aplica_en_cualquier_empresa(sysdb):
    uid = _crear_usuario_con_rol(sysdb, "boss@local", "admin", "global")
    assert db.tiene_rol_global(uid, db_path=sysdb) is True
    assert "usuarios.gestionar" in db.permisos_usuario(uid, "principal", db_path=sysdb)
    assert "usuarios.gestionar" in db.permisos_usuario(uid, "cualquiera", db_path=sysdb)


def test_empresas_de_usuario(sysdb):
    uid = _crear_usuario_con_rol(sysdb, "u@local", "consulta", "empresa", "acme")
    assert db.empresas_de_usuario(uid, db_path=sysdb) == {"acme"}
    rid = db.obtener_o_crear_rol("consulta", db_path=sysdb)
    db.asignar_rol_empresa(uid, "beta", rid, db_path=sysdb)
    assert db.empresas_de_usuario(uid, db_path=sysdb) == {"acme", "beta"}


def test_revocar_rol(sysdb):
    uid = _crear_usuario_con_rol(sysdb, "u@local", "consulta", "empresa", "acme")
    rid = db.obtener_o_crear_rol("consulta", db_path=sysdb)
    db.revocar_rol_empresa(uid, "acme", rid, db_path=sysdb)
    assert db.empresas_de_usuario(uid, db_path=sysdb) == set()


def test_auditoria_round_trip(sysdb):
    db.registrar_evento_auditoria(
        "permiso.denegado", usuario_email="x@local", empresa_id="principal",
        detalle="radian.procesar", resultado="denegado", db_path=sysdb,
    )
    eventos = db.listar_auditoria(10, db_path=sysdb)
    assert len(eventos) == 1
    assert eventos[0]["accion"] == "permiso.denegado"
    assert eventos[0]["resultado"] == "denegado"


# ═══════════════════════════════════════════════════════════════════════════
# Nivel authz / tenancy
# ═══════════════════════════════════════════════════════════════════════════

def test_tiene_permiso(sysdb):
    uid = _crear_usuario_con_rol(sysdb, "c@local", "consulta", "empresa", "principal")
    usuario = db.obtener_usuario_por_email("c@local", db_path=sysdb)
    assert authz.tiene_permiso(usuario, "principal", "dashboard.ver") is True
    assert authz.tiene_permiso(usuario, "principal", "radian.procesar") is False
    assert authz.tiene_permiso(None, "principal", "dashboard.ver") is False


def test_tenancy_puede_acceder(sysdb):
    uid = _crear_usuario_con_rol(sysdb, "u@local", "consulta", "empresa", "principal")
    usuario = db.obtener_usuario_por_email("u@local", db_path=sysdb)
    assert tenancy.puede_acceder_empresa(usuario, "principal") is True
    assert tenancy.puede_acceder_empresa(usuario, "acme") is False
    assert tenancy.puede_acceder_empresa(None, "principal") is False


# ═══════════════════════════════════════════════════════════════════════════
# Nivel rutas web
# ═══════════════════════════════════════════════════════════════════════════

def test_autologin_dev_admin_accede(client):
    """En modo dev (por defecto) el admin local entra sin login explícito."""
    resp = client.get("/")
    assert resp.status_code == 200


def test_gate_redirige_a_login_sin_sesion(client):
    """Con la sesión cerrada (sin autologin), las rutas redirigen al login."""
    with client.session_transaction() as sess:
        sess[SESSION_LOGOUT_KEY] = True
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_health_publico(client):
    with client.session_transaction() as sess:
        sess[SESSION_LOGOUT_KEY] = True
    resp = client.get("/health")
    assert resp.status_code == 200


def test_login_logout_flujo(client):
    # Provisiona el esquema y crea un usuario operativo.
    authn._asegurar_auth()
    p = config.SYSTEM_DB_PATH
    _crear_usuario_con_rol(p, "operario@local", "consulta", "empresa", "principal")

    resp = client.post("/login", data={"email": "operario@local", "next": "/"})
    assert resp.status_code == 302
    # Ahora la sesión es del operario.
    resp = client.get("/")
    assert resp.status_code == 200

    # Logout suprime el autologin → la siguiente petición redirige al login.
    resp = client.get("/logout")
    assert resp.status_code == 302
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_usuario_inexistente_rechazado(client):
    authn._asegurar_auth()
    resp = client.post("/login", data={"email": "nadie@local", "next": "/"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_permiso_denegado_da_403_y_audita(client):
    """Un usuario de solo lectura no puede procesar RADIAN (403) y se audita."""
    authn._asegurar_auth()
    p = config.SYSTEM_DB_PATH
    _crear_usuario_con_rol(p, "lector@local", "consulta", "empresa", "principal")
    _login(client, "lector@local")

    # Ve el dashboard (dashboard.ver) …
    assert client.get("/").status_code == 200
    # … pero no puede procesar (radian.procesar).
    resp = client.post("/procesar", data={})
    assert resp.status_code == 403

    eventos = db.listar_auditoria(20, db_path=p)
    assert any(e["accion"] == "permiso.denegado" and e["resultado"] == "denegado"
               for e in eventos)


def test_aislamiento_no_puede_fijar_empresa_ajena(client):
    """Fijar en sesión una empresa no autorizada no expone sus datos."""
    authn._asegurar_auth()
    p = config.SYSTEM_DB_PATH
    # Crea una segunda empresa real en el registro.
    otra = emp_mod.crear_empresa(nit="900999999", nombre="Otra SAS", sigla="OTRA")
    # Usuario con rol solo en 'principal'.
    _crear_usuario_con_rol(p, "u@local", "auxiliar", "empresa", "principal")
    _login(client, "u@local")

    # Intenta forzar la empresa ajena directamente en la sesión.
    with client.session_transaction() as sess:
        sess[tenancy.KEY_EMPRESA] = otra.id

    # Seleccionarla explícitamente también debe rechazarse.
    resp = client.post("/empresas/seleccionar", data={"empresa_id": otra.id})
    assert resp.status_code == 302
    # Se registró el intento denegado.
    eventos = db.listar_auditoria(20, db_path=p)
    assert any(e["accion"] == "empresa.seleccionar" and e["resultado"] == "denegado"
               for e in eventos)


def test_empresas_accesibles_filtra_por_usuario(client):
    authn._asegurar_auth()
    p = config.SYSTEM_DB_PATH
    otra = emp_mod.crear_empresa(nit="900999999", nombre="Otra SAS", sigla="OTRA")
    _crear_usuario_con_rol(p, "u@local", "consulta", "empresa", "principal")
    usuario = db.obtener_usuario_por_email("u@local", db_path=p)
    accesibles = {e.id for e in tenancy.empresas_accesibles(usuario)}
    assert "principal" in accesibles
    assert otra.id not in accesibles
