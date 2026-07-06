"""
Tests de autenticación con Microsoft Entra ID (Fase 4).

En modo entra la identidad la inyecta App Service Authentication en las
cabeceras X-MS-CLIENT-PRINCIPAL* (Easy Auth las elimina de las peticiones
externas, por lo que dentro de App Service son de confianza). Se cubre:

- Decodificación del principal (claims base64): email, nombre, oid, tid.
- Autoprovisión del usuario (sin roles) y sincronización de nombre/oid.
- Bootstrap del primer admin (BOOTSTRAP_ADMIN_EMAIL).
- Validación opcional de tenant (ENTRA_TENANT_ID).
- Flujo web: gate → /login con botón a /.auth/login/aad, cuenta desactivada
  sin bucle de redirecciones, logout vía /.auth/logout.
- El modo dev IGNORA las cabeceras Entra (no se puede suplantar identidad).
"""

import base64
import json
import os

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

import app.database as db                       # noqa: E402
from app import config, authn, authz            # noqa: E402
from app import empresas as emp_mod             # noqa: E402
from app.web import create_app                  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Helpers y fixtures
# ═══════════════════════════════════════════════════════════════════════════

_CLAIM_OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
_CLAIM_TID = "http://schemas.microsoft.com/identity/claims/tenantid"


def _principal_b64(email, nombre="", oid="", tid="", email_claim="preferred_username"):
    """Codifica un X-MS-CLIENT-PRINCIPAL como lo entrega Easy Auth."""
    claims = [{"typ": email_claim, "val": email}]
    if nombre:
        claims.append({"typ": "name", "val": nombre})
    if oid:
        claims.append({"typ": _CLAIM_OID, "val": oid})
    if tid:
        claims.append({"typ": _CLAIM_TID, "val": tid})
    payload = {"auth_typ": "aad", "claims": claims,
               "name_typ": "preferred_username", "role_typ": "roles"}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _headers(email, **kwargs):
    return {"X-MS-CLIENT-PRINCIPAL": _principal_b64(email, **kwargs)}


@pytest.fixture
def client_entra(tmp_path, monkeypatch):
    """App en modo entra con BD de sistema aislada."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    monkeypatch.setattr(config, "AUTH_MODE", "entra")
    monkeypatch.setattr(config, "BOOTSTRAP_ADMIN_EMAIL", "")
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "")
    emp_mod._sistema_listo.clear()
    authn.reset_estado()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


@pytest.fixture
def client_dev(tmp_path, monkeypatch):
    """App en modo dev (para verificar que ignora las cabeceras Entra)."""
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


def _dar_rol(email, rol, ambito="global", empresa_id="principal"):
    """Asigna un rol a un usuario ya provisionado (como haría el admin)."""
    p = config.SYSTEM_DB_PATH
    u = db.obtener_usuario_por_email(email, db_path=p)
    rid = db.obtener_o_crear_rol(rol, db_path=p)
    if ambito == "global":
        db.asignar_rol_global(u["id"], rid, db_path=p)
    else:
        db.asignar_rol_empresa(u["id"], empresa_id, rid, db_path=p)


# ═══════════════════════════════════════════════════════════════════════════
# Identidad: decodificación del principal y provisión de usuarios
# ═══════════════════════════════════════════════════════════════════════════

def test_autoprovision_con_nombre_y_oid(client_entra):
    """El primer acceso crea el usuario con nombre real y oid, sin roles."""
    client_entra.get("/", headers=_headers(
        "Ana.Gomez@Corp.com", nombre="Ana Gómez", oid="oid-123"))
    u = db.obtener_usuario_por_email("ana.gomez@corp.com",
                                     db_path=config.SYSTEM_DB_PATH)
    assert u is not None
    assert u["email"] == "ana.gomez@corp.com"      # normalizado a minúsculas
    assert u["nombre"] == "Ana Gómez"
    assert u["entra_oid"] == "oid-123"
    assert u["ultimo_acceso"]                      # acceso registrado
    # Sin roles: no ve el dashboard (403), pero la identidad sí se resolvió.
    resp = client_entra.get("/", headers=_headers(
        "ana.gomez@corp.com", nombre="Ana Gómez", oid="oid-123"))
    assert resp.status_code == 403


def test_sincroniza_nombre_y_oid_de_usuario_existente(client_entra):
    """Un usuario precreado por el admin adquiere nombre/oid al iniciar sesión."""
    authn._asegurar_auth()
    p = config.SYSTEM_DB_PATH
    db.crear_usuario("luis@corp.com", db_path=p)   # precreado sin nombre ni oid
    client_entra.get("/", headers=_headers(
        "luis@corp.com", nombre="Luis Pérez", oid="oid-999"))
    u = db.obtener_usuario_por_email("luis@corp.com", db_path=p)
    assert u["nombre"] == "Luis Pérez"
    assert u["entra_oid"] == "oid-999"


def test_fallback_cabecera_name_sin_principal(client_entra):
    """Sin X-MS-CLIENT-PRINCIPAL se usa X-MS-CLIENT-PRINCIPAL-NAME/-ID."""
    client_entra.get("/", headers={
        "X-MS-CLIENT-PRINCIPAL-NAME": "Solo.Name@Corp.com",
        "X-MS-CLIENT-PRINCIPAL-ID": "oid-fallback",
    })
    u = db.obtener_usuario_por_email("solo.name@corp.com",
                                     db_path=config.SYSTEM_DB_PATH)
    assert u is not None
    assert u["entra_oid"] == "oid-fallback"
    assert u["nombre"] == ""                       # el GUID no se usa como nombre


def test_claim_emailaddress_largo(client_entra):
    """También se acepta el claim URI largo de emailaddress (WS-Fed)."""
    client_entra.get("/", headers=_headers(
        "uri@corp.com",
        email_claim="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    ))
    assert db.obtener_usuario_por_email(
        "uri@corp.com", db_path=config.SYSTEM_DB_PATH) is not None


def test_principal_ilegible_no_revienta(client_entra):
    """Un principal corrupto no rompe la petición: se trata como no autenticado."""
    resp = client_entra.get("/", headers={"X-MS-CLIENT-PRINCIPAL": "no-es-base64!!"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_bootstrap_admin_recibe_rol_global(client_entra, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_ADMIN_EMAIL", "jefe@corp.com")
    resp = client_entra.get("/", headers=_headers("jefe@corp.com", nombre="Jefe"))
    assert resp.status_code == 200                 # admin global ve el dashboard
    p = config.SYSTEM_DB_PATH
    u = db.obtener_usuario_por_email("jefe@corp.com", db_path=p)
    assert db.tiene_rol_global(u["id"], db_path=p) is True
    assert "usuarios.gestionar" in db.permisos_usuario(u["id"], "principal", db_path=p)


# ═══════════════════════════════════════════════════════════════════════════
# Validación de tenant
# ═══════════════════════════════════════════════════════════════════════════

def test_tenant_correcto_entra(client_entra, monkeypatch):
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "tenant-oficial")
    client_entra.get("/", headers=_headers("ok@corp.com", tid="tenant-oficial"))
    assert db.obtener_usuario_por_email(
        "ok@corp.com", db_path=config.SYSTEM_DB_PATH) is not None


def test_tenant_ajeno_rechazado(client_entra, monkeypatch):
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "tenant-oficial")
    resp = client_entra.get("/", headers=_headers("intruso@otro.com", tid="tenant-b"))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    # Ni siquiera se autoprovisiona.
    assert db.obtener_usuario_por_email(
        "intruso@otro.com", db_path=config.SYSTEM_DB_PATH) is None


def test_tenant_requerido_sin_claim_tid_rechazado(client_entra, monkeypatch):
    """Con tenant configurado, una identidad sin `tid` verificable no entra."""
    monkeypatch.setattr(config, "ENTRA_TENANT_ID", "tenant-oficial")
    resp = client_entra.get("/", headers=_headers("sin.tid@corp.com"))
    assert resp.status_code == 302
    assert db.obtener_usuario_por_email(
        "sin.tid@corp.com", db_path=config.SYSTEM_DB_PATH) is None


# ═══════════════════════════════════════════════════════════════════════════
# Flujo web: gate, login, cuenta desactivada, logout
# ═══════════════════════════════════════════════════════════════════════════

def test_sin_identidad_gate_redirige_y_login_ofrece_microsoft(client_entra):
    resp = client_entra.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    resp = client_entra.get("/login?next=/banco")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/.auth/login/aad?post_login_redirect_uri=%2Fbanco" in html
    assert "Continuar con Microsoft" in html


def test_login_con_sesion_valida_redirige_a_destino(client_entra):
    headers = _headers("valido@corp.com", nombre="Válido")
    client_entra.get("/", headers=headers)        # autoprovisiona
    _dar_rol("valido@corp.com", "consulta", "empresa", "principal")
    resp = client_entra.get("/login?next=/", headers=headers)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_usuario_desactivado_ve_aviso_sin_bucle(client_entra):
    headers = _headers("baja@corp.com", nombre="De Baja")
    client_entra.get("/", headers=headers)        # autoprovisiona
    p = config.SYSTEM_DB_PATH
    u = db.obtener_usuario_por_email("baja@corp.com", db_path=p)
    db.actualizar_usuario(u["id"], activo=False, db_path=p)

    # La compuerta lo manda al login…
    resp = client_entra.get("/", headers=headers)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    # …y el login RESPONDE 200 con el aviso (no redirige de nuevo a /.auth →
    # sin bucle) y ofrece cambiar de cuenta.
    resp = client_entra.get("/login?next=/", headers=headers)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "baja@corp.com" in html
    assert "/.auth/logout" in html
    # El intento denegado queda en auditoría.
    eventos = db.listar_auditoria(10, db_path=p)
    assert any(e["accion"] == "login" and e["resultado"] == "denegado"
               for e in eventos)


def test_logout_cierra_tambien_easy_auth(client_entra):
    headers = _headers("valido@corp.com")
    client_entra.get("/", headers=headers)
    resp = client_entra.get("/logout", headers=headers)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith(
        "/.auth/logout?post_logout_redirect_uri=")


def test_post_login_deshabilitado_en_entra(client_entra):
    """El formulario de login dev no permite elegir usuario en modo entra."""
    authn._asegurar_auth()
    p = config.SYSTEM_DB_PATH
    db.crear_usuario("victima@corp.com", db_path=p)
    resp = client_entra.post("/login", data={"email": "victima@corp.com", "next": "/"})
    assert resp.status_code == 200                # vuelve a la página de login
    # Y sin cabeceras Entra la sesión sigue sin usuario.
    assert client_entra.get("/").status_code == 302


def test_health_sigue_publico_en_entra(client_entra):
    assert client_entra.get("/health").status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# El modo dev no es suplantable con cabeceras Entra
# ═══════════════════════════════════════════════════════════════════════════

def test_modo_dev_ignora_cabeceras_entra(client_dev):
    resp = client_dev.get("/", headers=_headers("atacante@evil.com",
                                                nombre="Atacante"))
    assert resp.status_code == 200                # entra como el admin dev…
    p = config.SYSTEM_DB_PATH
    # …y la cabecera no provisionó ningún usuario.
    assert db.obtener_usuario_por_email("atacante@evil.com", db_path=p) is None
