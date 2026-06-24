"""
Tests de la importación automática de RADIAN desde la DIAN.

Cubren las piezas deterministas y verificables del flujo (las únicas que no
dependen del portal real de la DIAN):

- El dígito de verificación del NIT (algoritmo oficial de la DIAN).
- La construcción y el parseo del enlace de acceso `AuthToken`.
- La extracción del enlace desde el cuerpo de un correo.
- La espera/sondeo del correo del token.
- La configuración por empresa (`DianConfig`) y su persistencia.
- La lógica de programación del scheduler.
"""

from datetime import datetime, timezone

import pytest

import app.database as db
from app.radian_auto import dian_client as dc
from app.radian_auto import email_token as et
from app.radian_auto.config_dian import DianConfig


# ---------------------------------------------------------------------------
# Dígito de verificación del NIT
# ---------------------------------------------------------------------------

class TestDigitoVerificacion:
    def test_nit_1invest(self):
        # 1 INVEST SAS — NIT 901.331.657-7
        assert dc.calcular_digito_verificacion("901331657") == 7

    def test_acepta_formato_con_puntos_y_guion(self):
        assert dc.calcular_digito_verificacion("901.331.657") == 7

    def test_nit_con_dv_concatena(self):
        assert dc.nit_con_dv("901331657") == "9013316577"

    def test_resultado_en_rango_valido(self):
        for nit in ("800197268", "830053105", "12345678", "9"):
            dv = dc.calcular_digito_verificacion(nit)
            assert 0 <= dv <= 9

    def test_nit_vacio_lanza(self):
        with pytest.raises(ValueError):
            dc.calcular_digito_verificacion("abc")


# ---------------------------------------------------------------------------
# Enlace de acceso AuthToken
# ---------------------------------------------------------------------------

class TestAuthUrl:
    def test_construir_codifica_pipe(self):
        url = dc.construir_auth_url(
            token="338b95a6-277e-41b0-8d46-3f33e1df9118",
            nit_representante="10910094",
            nit_empresa="901331657",
        )
        assert "/User/AuthToken?" in url
        assert "%7C" in url            # la tubería va codificada
        assert "rk=9013316577" in url  # NIT + DV
        assert url.startswith("https://catalogo-vpfe.dian.gov.co")

    def test_parsear_extrae_parametros(self):
        url = (
            "https://catalogo-vpfe.dian.gov.co/User/AuthToken"
            "?pk=10910094%7C8356245&rk=901331657&token=abc-123"
        )
        datos = dc.parsear_auth_url(url)
        assert datos["pk"] == "10910094|8356245"  # parse_qs decodifica %7C
        assert datos["rk"] == "901331657"
        assert datos["token"] == "abc-123"

    def test_roundtrip(self):
        url = dc.construir_auth_url("tok-9", "10910094", "901331657")
        datos = dc.parsear_auth_url(url)
        assert datos["token"] == "tok-9"
        assert datos["pk"] == "10910094|901331657"

    def test_parsear_url_sin_parametros(self):
        assert dc.parsear_auth_url("https://x.com/User/AuthToken") == {}


class TestContentDisposition:
    def test_filename_simple(self):
        assert dc._nombre_desde_content_disposition(
            'attachment; filename="RADIAN.xlsx"'
        ) == "RADIAN.xlsx"

    def test_filename_extendido(self):
        assert dc._nombre_desde_content_disposition(
            "attachment; filename*=UTF-8''Reporte%20RADIAN.xlsx"
        ) == "Reporte RADIAN.xlsx"

    def test_sin_filename(self):
        assert dc._nombre_desde_content_disposition("inline") == ""


class TestDescargaSinSesion:
    def test_requiere_autenticacion(self):
        cli = dc.DianClient()
        with pytest.raises(dc.DianAuthError):
            cli.descargar_reporte("2026-06-01", "2026-06-24")


# ---------------------------------------------------------------------------
# Extracción del enlace desde el correo
# ---------------------------------------------------------------------------

class TestExtraerEnlace:
    def test_extrae_de_html_con_entidades(self):
        cuerpo = (
            '<p>Su token:</p>'
            '<a href="https://catalogo-vpfe.dian.gov.co/User/AuthToken'
            '?pk=10910094%7C8356245&amp;rk=901331657&amp;token=338b95a6">Ingrese aquí</a>'
        )
        enlace = et.extraer_enlace_token(cuerpo)
        assert enlace is not None
        # Las entidades &amp; quedan resueltas a & para que el enlace sea usable.
        assert "&amp;" not in enlace
        datos = dc.parsear_auth_url(enlace)
        assert datos["token"] == "338b95a6"
        assert datos["rk"] == "901331657"

    def test_extrae_de_texto_plano(self):
        cuerpo = "Acceda aquí: https://catalogo-vpfe.dian.gov.co/User/AuthToken?token=xyz vigencia 60 min"
        enlace = et.extraer_enlace_token(cuerpo)
        assert enlace == "https://catalogo-vpfe.dian.gov.co/User/AuthToken?token=xyz"

    def test_sin_enlace(self):
        assert et.extraer_enlace_token("Correo sin enlace") is None
        assert et.extraer_enlace_token("") is None


class TestEsperarEnlace:
    def test_encuentra_tras_reintentos(self, monkeypatch):
        llamadas = {"n": 0}

        def fake(cfg, no_antes_de=None):
            llamadas["n"] += 1
            return None if llamadas["n"] < 2 else "https://x/User/AuthToken?token=ok"

        monkeypatch.setattr(et, "obtener_enlace_token", fake)
        enlace = et.esperar_enlace_token(
            et.ImapConfig(usuario="u", password="p"),
            espera_seg=10, intervalo_seg=0, _sleep=lambda s: None,
        )
        assert enlace.endswith("token=ok")
        assert llamadas["n"] == 2

    def test_timeout_lanza(self, monkeypatch):
        monkeypatch.setattr(et, "obtener_enlace_token", lambda cfg, no_antes_de=None: None)
        with pytest.raises(et.EmailTokenError):
            et.esperar_enlace_token(
                et.ImapConfig(usuario="u", password="p"),
                espera_seg=0, intervalo_seg=0, _sleep=lambda s: None,
            )

    def test_sin_credenciales_lanza(self):
        with pytest.raises(et.EmailTokenError):
            et.obtener_enlace_token(et.ImapConfig(usuario="", password=""))


# ---------------------------------------------------------------------------
# Configuración por empresa
# ---------------------------------------------------------------------------

class _EmpresaFake:
    nit = "901331657"


class TestDianConfig:
    def test_roundtrip_dict(self):
        cfg = DianConfig(
            habilitado=True, nit_representante="10910094",
            email_user="x@gmail.com", email_password="apppwd", hora="07:30",
        )
        otra = DianConfig.from_dict(cfg.to_dict())
        assert otra == cfg

    def test_from_dict_ignora_claves_desconocidas(self):
        cfg = DianConfig.from_dict({"habilitado": True, "desconocido": 1})
        assert cfg.habilitado is True

    def test_nit_empresa_efectivo_cae_a_empresa(self):
        cfg = DianConfig()
        assert cfg.nit_empresa_efectivo(_EmpresaFake()) == "901331657"
        cfg2 = DianConfig(nit_empresa="800")
        assert cfg2.nit_empresa_efectivo(_EmpresaFake()) == "800"

    def test_configurado_y_faltantes(self):
        assert DianConfig().configurado() is False
        assert "NIT del representante legal" in DianConfig().faltantes()
        ok = DianConfig(
            nit_representante="10910094",
            email_user="x@gmail.com", email_password="pwd",
        )
        assert ok.configurado() is True
        assert ok.faltantes() == []

    def test_password_env_tiene_prioridad(self, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "DIAN_EMAIL_PASSWORD", "env-pwd")
        cfg = DianConfig(email_user="x@gmail.com", email_password="db-pwd")
        assert cfg.imap_config().password == "env-pwd"

    def test_imap_config_usa_defaults(self, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "DIAN_EMAIL_PASSWORD", "")
        monkeypatch.setattr(config, "DIAN_EMAIL_USER", "")
        cfg = DianConfig(email_user="x@gmail.com", email_password="pwd")
        imap = cfg.imap_config()
        assert imap.host == config.DIAN_IMAP_HOST
        assert imap.usuario == "x@gmail.com"
        assert imap.carpeta == "INBOX"


# ---------------------------------------------------------------------------
# Persistencia de dian_config en la BD de empresas
# ---------------------------------------------------------------------------

def test_dian_config_persiste_en_bd(tmp_path):
    p = str(tmp_path / "sistema.db")
    db.inicializar_db_sistema(p)
    db.guardar_empresa_registro({
        "id": "acme", "nit": "900", "nombre": "ACME SAS",
        "dian_config": {"habilitado": True, "hora": "06:00", "nit_representante": "111"},
    }, p)
    reg = db.obtener_empresa_registro("acme", p)
    assert reg["dian_config"]["habilitado"] is True
    assert reg["dian_config"]["hora"] == "06:00"
    assert reg["dian_config"]["nit_representante"] == "111"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class TestScheduler:
    def _cfg_habilitada(self):
        return DianConfig(
            habilitado=True, hora="06:00",
            nit_representante="10910094",
            email_user="x@gmail.com", email_password="pwd",
        )

    def test_debe_correr(self):
        from app.radian_auto import scheduler as sch
        sch._ultima_ejecucion.clear()
        cfg = self._cfg_habilitada()
        assert sch._debe_correr(cfg, "06:00", "acme", "2026-06-24") is True
        # Hora distinta → no corre.
        assert sch._debe_correr(cfg, "07:00", "acme", "2026-06-24") is False

    def test_no_repite_en_el_dia(self):
        from app.radian_auto import scheduler as sch
        sch._ultima_ejecucion.clear()
        sch._ultima_ejecucion["acme"] = "2026-06-24"
        cfg = self._cfg_habilitada()
        assert sch._debe_correr(cfg, "06:00", "acme", "2026-06-24") is False
        # Día siguiente → vuelve a correr.
        assert sch._debe_correr(cfg, "06:00", "acme", "2026-06-25") is True

    def test_deshabilitada_no_corre(self):
        from app.radian_auto import scheduler as sch
        sch._ultima_ejecucion.clear()
        cfg = DianConfig(habilitado=False, hora="06:00")
        assert sch._debe_correr(cfg, "06:00", "acme", "2026-06-24") is False


# ---------------------------------------------------------------------------
# Endpoint de cron (con CSRF ACTIVO: verifica la exención y el token)
# ---------------------------------------------------------------------------

@pytest.fixture
def cron_client(tmp_path, monkeypatch):
    """Cliente con CSRF habilitado para probar el endpoint /radian/auto/cron."""
    from app import config, authn
    from app import empresas as emp_mod
    from app.web import create_app

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
    emp_mod._sistema_listo.clear()
    authn.reset_estado()
    app = create_app()
    app.config["TESTING"] = True
    # CSRF queda ACTIVO a propósito: el endpoint cron debe estar exento.
    with app.test_client() as c:
        yield c
    emp_mod._sistema_listo.clear()
    authn.reset_estado()


class TestCronEndpoint:
    def test_deshabilitado_sin_token_configurado(self, cron_client, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "RADIAN_CRON_TOKEN", "")
        resp = cron_client.post("/radian/auto/cron")
        assert resp.status_code == 404  # no 400 → CSRF no bloqueó (está exento)

    def test_token_invalido(self, cron_client, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "RADIAN_CRON_TOKEN", "secreto")
        resp = cron_client.post("/radian/auto/cron", headers={"X-Radian-Token": "malo"})
        assert resp.status_code == 403

    def test_token_valido_dispara(self, cron_client, monkeypatch):
        from app import config
        from app.radian_auto import auto_importador
        monkeypatch.setattr(config, "RADIAN_CRON_TOKEN", "secreto")
        llamado = {"n": 0}
        monkeypatch.setattr(
            auto_importador, "importar_todas",
            lambda **kw: llamado.__setitem__("n", llamado["n"] + 1) or [],
        )
        resp = cron_client.post("/radian/auto/cron?token=secreto")
        assert resp.status_code == 202
        assert resp.get_json()["status"] == "started"
