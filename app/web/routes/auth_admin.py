"""Autenticación (login/logout/health) y administración (usuarios, auditoría)."""

import logging

from flask import (
    flash, redirect, render_template,
    request, session, url_for,
)

from app.empresas import (
    listar_empresas,
)
from app import authn, audit
from app.authz import require_permission
from app.web import session_store

from .base import (
    bp, KEY_RESULTADO, KEY_BANCO, KEY_EMPRESA,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Autenticación — login / logout / health
# ---------------------------------------------------------------------------

@bp.route("/health")
def health():
    """Endpoint de salud (sin autenticación) para sondas/monitoreo."""
    return {"status": "ok"}, 200


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Inicio de sesión.

    En modo Entra (Fase 4) la identidad la provee App Service Authentication:
    esta página ofrece el botón «Continuar con Microsoft» (/.auth/login/aad) y,
    si la cuenta Entra existe pero no tiene acceso (desactivada o de un tenant
    no permitido), lo explica sin entrar en un bucle de redirecciones. En modo
    dev es un stub: permite elegir el usuario para probar roles.
    """
    from app.config import AUTH_MODE
    # Sanea `next` para evitar open-redirect: solo rutas internas (una sola '/').
    raw_next = request.values.get("next") or ""
    destino = raw_next if raw_next.startswith("/") and not raw_next.startswith("//") \
        else url_for("web.index")

    # Si ya hay sesión válida, no mostrar el login.
    if authn.usuario_actual() is not None and request.method == "GET":
        return redirect(destino)

    if AUTH_MODE == "entra":
        # Identidad Entra presente pero sin acceso a la app (cuenta desactivada,
        # sin provisionar o tenant rechazado): se muestra el aviso en la página.
        principal = authn.principal_entra() if request.method == "GET" else None
        entra_email = (principal or {}).get("email", "")
        if entra_email:
            audit.registrar("login", detalle=f"email={entra_email}",
                            resultado="denegado")
        return render_template(
            "login.html", usuarios=[], modo=AUTH_MODE, next=destino,
            entra_email=entra_email,
            url_login_entra=authn.url_login_entra(destino),
            url_logout_entra=authn.url_logout_entra(),
        )

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        usuario = authn.iniciar_sesion(email)
        if usuario is None:
            audit.registrar("login", detalle=f"email={email}", resultado="denegado")
            flash("Usuario no encontrado o inactivo.", "error")
            return redirect(url_for("web.login", next=destino))
        audit.registrar("login", detalle=usuario["email"])
        flash(f"Bienvenido, {usuario['nombre'] or usuario['email']}.", "success")
        return redirect(destino)

    from app.database import listar_usuarios
    from app.config import SYSTEM_DB_PATH
    # Asegura que el esquema/seed existan antes de listar.
    authn._asegurar_auth()
    usuarios = listar_usuarios(SYSTEM_DB_PATH)
    return render_template(
        "login.html", usuarios=usuarios, modo=AUTH_MODE, next=destino,
    )


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Cierra la sesión del usuario."""
    from app.config import AUTH_MODE

    audit.registrar("logout")
    authn.cerrar_sesion()
    # Limpiar el contexto de trabajo de la sesión.
    session.pop(KEY_EMPRESA, None)
    session_store.eliminar(KEY_RESULTADO)
    session_store.eliminar(KEY_BANCO)
    if AUTH_MODE == "entra":
        # Cerrar también la sesión de App Service Authentication; de lo
        # contrario la siguiente petición volvería a entrar autenticada.
        return redirect(authn.url_logout_entra())
    flash("Sesión cerrada.", "success")
    return redirect(url_for("web.login"))


# ---------------------------------------------------------------------------
# GET /  — Dashboard
# ---------------------------------------------------------------------------


def _sysdb() -> str:
    """Ruta (dinámica) de la BD de sistema; respeta overrides de tests."""
    from app import config as _cfg
    return _cfg.SYSTEM_DB_PATH


@bp.route("/usuarios")
@require_permission("usuarios.gestionar")
def usuarios():
    """Administración de usuarios: lista, roles asignados y formularios."""
    from app.database import listar_usuarios, roles_de_usuario
    from app.authz import ROLES

    authn._asegurar_auth()
    sysdb = _sysdb()
    filas = []
    for u in listar_usuarios(sysdb):
        filas.append({**u, "roles": roles_de_usuario(u["id"], db_path=sysdb)})

    return render_template(
        "usuarios.html",
        usuarios=filas,
        roles=list(ROLES.keys()),
        empresas=listar_empresas(),
    )


@bp.route("/usuarios/crear", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_crear():
    """Crea un usuario nuevo (sin roles; se asignan después)."""
    from app.database import obtener_usuario_por_email, crear_usuario

    email = request.form.get("email", "").strip().lower()
    nombre = request.form.get("nombre", "").strip()
    if not email:
        flash("El correo es obligatorio.", "error")
        return redirect(url_for("web.usuarios"))

    sysdb = _sysdb()
    if obtener_usuario_por_email(email, db_path=sysdb):
        flash("Ya existe un usuario con ese correo.", "error")
        return redirect(url_for("web.usuarios"))

    crear_usuario(email, nombre=nombre, db_path=sysdb)
    audit.registrar("usuario.crear", detalle=email)
    flash(f"✓ Usuario {email} creado. Asígnale uno o más roles.", "success")
    return redirect(url_for("web.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/asignar", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_asignar(usuario_id):
    """Asigna un rol al usuario (global o acotado a una empresa)."""
    from app.database import (
        obtener_o_crear_rol, asignar_rol_global, asignar_rol_empresa,
    )
    from app.authz import ROLES

    rol = request.form.get("rol", "").strip()
    ambito = request.form.get("ambito", "").strip()          # 'global' | 'empresa'
    empresa_id = request.form.get("empresa_id", "").strip()

    if rol not in ROLES:
        flash("Rol desconocido.", "error")
        return redirect(url_for("web.usuarios"))

    sysdb = _sysdb()
    rid = obtener_o_crear_rol(rol, db_path=sysdb)
    if ambito == "global":
        asignar_rol_global(usuario_id, rid, db_path=sysdb)
        audit.registrar("usuario.rol_global", detalle=f"uid={usuario_id} rol={rol}")
        flash(f"✓ Rol global '{rol}' asignado.", "success")
    elif ambito == "empresa" and empresa_id:
        asignar_rol_empresa(usuario_id, empresa_id, rid, db_path=sysdb)
        audit.registrar("usuario.rol_empresa", empresa_id=empresa_id,
                        detalle=f"uid={usuario_id} rol={rol}")
        flash(f"✓ Rol '{rol}' asignado en la empresa {empresa_id}.", "success")
    else:
        flash("Indica el ámbito (global o una empresa).", "error")
    return redirect(url_for("web.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/revocar", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_revocar(usuario_id):
    """Revoca un rol del usuario (global o de empresa)."""
    from app.database import (
        obtener_o_crear_rol, revocar_rol_global, revocar_rol_empresa,
    )

    rol = request.form.get("rol", "").strip()
    ambito = request.form.get("ambito", "").strip()
    empresa_id = request.form.get("empresa_id", "").strip()

    sysdb = _sysdb()
    rid = obtener_o_crear_rol(rol, db_path=sysdb)
    if ambito == "global":
        revocar_rol_global(usuario_id, rid, db_path=sysdb)
    elif ambito == "empresa" and empresa_id:
        revocar_rol_empresa(usuario_id, empresa_id, rid, db_path=sysdb)
    audit.registrar("usuario.revocar",
                    empresa_id=(empresa_id or None),
                    detalle=f"uid={usuario_id} rol={rol} ambito={ambito}")
    flash("Rol revocado.", "success")
    return redirect(url_for("web.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/estado", methods=["POST"])
@require_permission("usuarios.gestionar")
def usuarios_estado(usuario_id):
    """Activa o desactiva un usuario."""
    from app.database import actualizar_usuario

    activo = request.form.get("activo") == "1"
    actualizar_usuario(usuario_id, activo=activo, db_path=_sysdb())
    audit.registrar("usuario.estado",
                    detalle=f"uid={usuario_id} activo={activo}")
    flash("Estado del usuario actualizado.", "success")
    return redirect(url_for("web.usuarios"))


# ---------------------------------------------------------------------------
# GET /auditoria — Bitácora de acciones
# ---------------------------------------------------------------------------


@bp.route("/auditoria")
@require_permission("auditoria.ver")
def auditoria():
    """Muestra los eventos de auditoría más recientes."""
    eventos = audit.listar(limite=300)
    for e in eventos:
        e["fecha_fmt"] = (e.get("timestamp") or "")[:19].replace("T", " ")
    return render_template("auditoria.html", eventos=eventos)
