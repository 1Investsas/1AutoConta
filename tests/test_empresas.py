"""Tests del módulo multi-empresa."""

import json

import pytest

from app import config
from app import empresas as emp_mod
from app.empresas import (
    Empresa, EMPRESA_PRINCIPAL_ID, FORMATO_BANCO_DEFAULT,
    actualizar_empresa, crear_empresa, eliminar_empresa,
    listar_empresas, obtener_empresa,
)


@pytest.fixture(autouse=True)
def registro_temporal(tmp_path, monkeypatch):
    """Redirige el registro de empresas (BD de sistema) a un directorio temporal.

    La fuente de verdad es ahora la tabla SQL `empresas`; este fixture apunta la
    BD de sistema a un sqlite temporal y limpia la caché de inicialización para
    que cada test arranque con un registro vacío (sin tocar el db/ del proyecto).
    """
    db_sistema = str(tmp_path / "sistema.db")
    monkeypatch.setattr(config, "SYSTEM_DB_PATH", db_sistema)

    # `data/empresas.json` legado: apuntar a un directorio temporal vacío, de
    # modo que la migración inicial sea un no-op (no hay JSON que importar).
    def fake_get_local_data_path(filename, category="data"):
        return str(tmp_path / filename)

    monkeypatch.setattr(emp_mod.store, "get_local_data_path", fake_get_local_data_path)

    # Aislar la caché de "sistema listo" por test (rutas distintas por tmp_path).
    emp_mod._sistema_listo.clear()
    yield
    emp_mod._sistema_listo.clear()


class TestEmpresaPrincipal:
    def test_siempre_existe(self):
        empresas = listar_empresas()
        assert empresas[0].id == EMPRESA_PRINCIPAL_ID
        assert empresas[0].nit == config.NIT_EMPRESA

    def test_db_y_data_compatibles(self):
        emp = obtener_empresa(None)
        assert emp.db_path == config.DB_PATH
        assert emp.data_category == "data"

    def test_id_desconocido_retorna_principal(self):
        assert obtener_empresa("no_existe").id == EMPRESA_PRINCIPAL_ID


class TestCrearEmpresa:
    def test_crear_y_obtener(self):
        emp = crear_empresa("900123456", "ACME SAS")
        assert emp.id == "acme_sas"
        assert obtener_empresa("acme_sas").nit == "900123456"
        assert emp.db_path == "db/contable_acme_sas.db"
        assert emp.data_category == "data/acme_sas"

    def test_ids_no_colisionan(self):
        a = crear_empresa("1", "ACME SAS")
        b = crear_empresa("2", "ACME S.A.S")
        assert a.id != b.id

    def test_eliminar(self):
        emp = crear_empresa("900123456", "Temporal")
        eliminar_empresa(emp.id)
        assert obtener_empresa(emp.id).id == EMPRESA_PRINCIPAL_ID

    def test_principal_no_eliminable(self):
        with pytest.raises(ValueError):
            eliminar_empresa(EMPRESA_PRINCIPAL_ID)


class TestMigracionJsonLegacy:
    """La primera lectura migra el `empresas.json` legado a la BD de sistema."""

    def test_migra_empresas_json_a_db(self, tmp_path):
        legacy = {
            "acme_sas": {
                "id": "acme_sas", "nit": "900123456", "nombre": "ACME SAS",
                "sigla": "ACME", "cuenta_banco_default": "11100501",
                "nit_banco": "860034313",
                "cuentas_contraparte": {"FACTURA_COMPRA": "22059999"},
                "cuentas_impuestos": {},
                "cuentas_banco": [{"cuenta": "11100501", "etiqueta": "Ahorros"}],
                "bancos": [{"nit": "860034313", "nombre": "Bancolombia"}],
                "formato_banco": {"delimitador": ";"},
            }
        }
        (tmp_path / "empresas.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )
        emp_mod._sistema_listo.clear()  # forzar la migración en esta lectura

        ids = [e.id for e in listar_empresas()]
        assert "acme_sas" in ids

        re = obtener_empresa("acme_sas")
        assert re.nit == "900123456"
        assert re.nombre == "ACME SAS"
        assert re.cuentas_contraparte_efectivas()["FACTURA_COMPRA"] == "22059999"
        assert re.bancos_efectivos()[0]["nombre"] == "Bancolombia"
        assert re.formato_banco_efectivo()["delimitador"] == ";"

    def test_migracion_no_pisa_datos_existentes(self, tmp_path):
        # Ya hay una empresa en la BD: el JSON legado NO debe importarse.
        crear_empresa("900", "Existente", sigla="EX")
        (tmp_path / "empresas.json").write_text(
            json.dumps({"otra": {"id": "otra", "nit": "1", "nombre": "Otra"}}),
            encoding="utf-8",
        )
        emp_mod._sistema_listo.clear()

        ids = [e.id for e in listar_empresas()]
        assert "otra" not in ids
        assert "ex" in ids

    def test_sin_json_legado_arranca_vacio(self):
        # Sin empresas.json, solo existe la principal.
        ids = [e.id for e in listar_empresas()]
        assert ids == [EMPRESA_PRINCIPAL_ID]


class TestSigla:
    def test_sigla_efectiva_fallback_al_nombre(self):
        e = Empresa(id="x", nit="1", nombre="Mi Empresa SAS")
        assert e.sigla_efectiva == "Mi Empresa SAS"
        e.sigla = "MES"
        assert e.sigla_efectiva == "MES"

    def test_crear_con_sigla_deriva_id_de_sigla(self):
        emp = crear_empresa("900", "Comercializadora Internacional XYZ", sigla="CIXYZ")
        assert emp.id == "cixyz"
        assert emp.sigla == "CIXYZ"
        assert obtener_empresa("cixyz").nombre.startswith("Comercializadora")

    def test_sigla_se_persiste(self):
        crear_empresa("900", "ACME SAS", sigla="ACM")
        assert obtener_empresa("acm").sigla == "ACM"

    def test_principal_usa_sigla_de_config(self):
        # Sin override, la sigla de la principal cae al valor de config
        assert obtener_empresa(None).sigla == config.SIGLA_EMPRESA


class TestActualizarEmpresa:
    def test_actualizar_preserva_id(self):
        emp = crear_empresa("900", "ACME", sigla="ACM")
        upd = actualizar_empresa(
            emp.id, nit="901", nombre="ACME 2", sigla="ACM2",
            cuenta_banco_default="11200501", nit_banco="860",
            formato_banco={"delimitador": ";"},
            cuentas_contraparte={}, cuentas_impuestos={},
        )
        # El id no cambia aunque cambien nombre/sigla → conserva BD y maestros
        assert upd.id == emp.id
        re = obtener_empresa(emp.id)
        assert re.nit == "901"
        assert re.nombre == "ACME 2"
        assert re.sigla == "ACM2"
        assert re.nit_banco == "860"
        assert re.formato_banco_efectivo()["delimitador"] == ";"

    def test_actualizar_principal_persiste_override(self):
        upd = actualizar_empresa(
            EMPRESA_PRINCIPAL_ID, nit=config.NIT_EMPRESA, nombre="NUEVO NOMBRE",
            sigla="NN", cuenta_banco_default="", nit_banco="",
            formato_banco={}, cuentas_contraparte={}, cuentas_impuestos={},
        )
        assert upd.id == EMPRESA_PRINCIPAL_ID
        p = obtener_empresa(EMPRESA_PRINCIPAL_ID)
        assert p.nombre == "NUEVO NOMBRE"
        assert p.sigla == "NN"
        # La principal conserva su BD y carpeta de maestros
        assert p.db_path == config.DB_PATH
        assert p.data_category == "data"

    def test_principal_aparece_una_sola_vez_tras_editar(self):
        actualizar_empresa(
            EMPRESA_PRINCIPAL_ID, nit=config.NIT_EMPRESA, nombre="P", sigla="P",
            cuenta_banco_default="", nit_banco="",
            formato_banco={}, cuentas_contraparte={}, cuentas_impuestos={},
        )
        ids = [e.id for e in listar_empresas()]
        assert ids.count(EMPRESA_PRINCIPAL_ID) == 1


class TestOverrides:
    def test_cuentas_contraparte_efectivas(self):
        emp = Empresa(id="x", nit="1", nombre="X",
                      cuentas_contraparte={"FACTURA_VENTA": "13050599"})
        efectivas = emp.cuentas_contraparte_efectivas()
        assert efectivas["FACTURA_VENTA"] == "13050599"
        # Las demás heredan del default
        assert efectivas["FACTURA_COMPRA"] == config.CUENTAS_CONTRAPARTE["FACTURA_COMPRA"]

    def test_cuentas_impuestos_efectivas(self):
        emp = Empresa(id="x", nit="1", nombre="X",
                      cuentas_impuestos={"IVA": {"compra": "24089999"}})
        efectivas = emp.cuentas_impuestos_efectivas()
        assert efectivas["IVA"]["compra"] == "24089999"
        assert efectivas["IVA"]["venta"] == config.CUENTAS_IMPUESTOS["IVA"]["venta"]
        assert efectivas["Rete Renta"] == config.CUENTAS_IMPUESTOS["Rete Renta"]

    def test_formato_banco_efectivo(self):
        emp = Empresa(id="x", nit="1", nombre="X",
                      formato_banco={"delimitador": ";", "col_fecha": 1})
        fmt = emp.formato_banco_efectivo()
        assert fmt["delimitador"] == ";"
        assert fmt["col_fecha"] == 1
        assert fmt["col_valor"] == FORMATO_BANCO_DEFAULT["col_valor"]

    def test_cuenta_banco_default(self):
        emp = Empresa(id="x", nit="1", nombre="X")
        assert emp.cuenta_banco_efectiva() == config.BANCO_CUENTA_DEFAULT
        emp.cuenta_banco_default = "11200501"
        assert emp.cuenta_banco_efectiva() == "11200501"


class TestCuentasYBancosMultiples:
    def test_cuentas_banco_efectivas_fallback(self):
        """Sin lista configurada, cae a una sola cuenta (el default global)."""
        emp = Empresa(id="x", nit="1", nombre="X")
        cuentas = emp.cuentas_banco_efectivas()
        assert len(cuentas) == 1
        assert cuentas[0]["cuenta"] == config.BANCO_CUENTA_DEFAULT

    def test_cuentas_banco_efectivas_lista(self):
        emp = Empresa(
            id="x", nit="1", nombre="X",
            cuentas_banco=[
                {"cuenta": "11100501", "etiqueta": "Ahorros"},
                {"cuenta": "11100502", "etiqueta": "Corriente"},
            ],
        )
        cuentas = emp.cuentas_banco_efectivas()
        assert [c["cuenta"] for c in cuentas] == ["11100501", "11100502"]
        # cuenta_banco_efectiva() = primera cuenta de la lista
        assert emp.cuenta_banco_efectiva() == "11100501"

    def test_cuentas_banco_ignora_vacias(self):
        emp = Empresa(
            id="x", nit="1", nombre="X",
            cuentas_banco=[{"cuenta": "", "etiqueta": "vacía"},
                           {"cuenta": "11100501", "etiqueta": ""}],
        )
        cuentas = emp.cuentas_banco_efectivas()
        assert [c["cuenta"] for c in cuentas] == ["11100501"]

    def test_bancos_efectivos_vacio(self):
        emp = Empresa(id="x", nit="1", nombre="X")
        assert emp.bancos_efectivos() == []

    def test_bancos_efectivos_compat_nit_unico(self):
        emp = Empresa(id="x", nit="1", nombre="X", nit_banco="860034313")
        assert emp.bancos_efectivos() == [{"nit": "860034313", "nombre": ""}]

    def test_bancos_efectivos_lista(self):
        emp = Empresa(
            id="x", nit="1", nombre="X",
            bancos=[
                {"nit": "860034313", "nombre": "Bancolombia"},
                {"nit": "860035827", "nombre": "Davivienda"},
            ],
        )
        bancos = emp.bancos_efectivos()
        assert [b["nit"] for b in bancos] == ["860034313", "860035827"]
        assert bancos[0]["nombre"] == "Bancolombia"

    def test_actualizar_persiste_listas(self):
        emp = crear_empresa("900", "ACME", sigla="ACM")
        actualizar_empresa(
            emp.id, nit="900", nombre="ACME", sigla="ACM",
            cuentas_banco=[
                {"cuenta": "11100501", "etiqueta": "Ahorros"},
                {"cuenta": "11100502", "etiqueta": "Corriente"},
            ],
            bancos=[{"nit": "860034313", "nombre": "Bancolombia"}],
        )
        re = obtener_empresa(emp.id)
        assert len(re.cuentas_banco_efectivas()) == 2
        assert re.cuenta_banco_efectiva() == "11100501"
        assert re.bancos_efectivos()[0]["nit"] == "860034313"


class TestFormatoBancoImportador:
    def test_formato_personalizado(self, tmp_path):
        """CSV con encabezado, ';', fecha dd/mm/yyyy y decimal con coma."""
        from app.banco.importador_banco import leer_csv_banco

        csv = tmp_path / "extracto.csv"
        csv.write_text(
            "cuenta;cod;fecha;valor;detalle;descripcion\n"
            "551-000068-95;551;31/01/2026;-1.000.000,50;2999;PAGO PROVEEDOR\n"
            "551-000068-95;551;31/01/2026;500.000,00;2999;CONSIGNACION\n"
        )
        fmt = {
            "delimitador": ";",
            "filas_encabezado": 1,
            "col_cuenta": 0,
            "col_codigo_banco": 1,
            "col_fecha": 2,
            "col_valor": 3,
            "col_codigo_detalle": 4,
            "col_descripcion": 5,
            "formato_fecha": "%d/%m/%Y",
            "separador_decimal": ",",
            "separador_miles": ".",
        }
        movs = leer_csv_banco(csv, formato=fmt)
        assert len(movs) == 2
        valores = sorted(float(m.valor) for m in movs)
        assert valores == [-1000000.50, 500000.00]
        assert movs[0].fecha.isoformat() == "2026-01-31"

    def test_formato_default_sigue_funcionando(self, tmp_path):
        from app.banco.importador_banco import leer_csv_banco

        csv = tmp_path / "extracto.csv"
        csv.write_text(
            "551-000068-95,551,,20260131,,-250000.00,2999,PAGO X,0\n"
        )
        movs = leer_csv_banco(csv)
        assert len(movs) == 1
        assert float(movs[0].valor) == -250000.00

    def test_delimitador_mal_configurado_cae_al_detectado(self, tmp_path):
        """Un delimitador '.' parte los decimales y "S.A" de forma inconsistente
        (ParserError "Expected 2 fields... saw 3"); debe detectarse la coma."""
        from app.banco.importador_banco import leer_csv_banco

        csv = tmp_path / "extracto.csv"
        csv.write_text(
            "551-000068-95, 551, , 20260430, , 1.77, 2999, ABONO INTERESES AHORROS, 0,\n"
            "551-000068-95, 551, , 20260416, , -526500.00, 7513, PAGO PSE ENLACE OPERATIVO S.A, 0,\n"
        )
        movs = leer_csv_banco(csv, formato={"delimitador": "."})
        assert len(movs) == 2
        assert sorted(float(m.valor) for m in movs) == [-526500.00, 1.77]

    def test_delimitador_sin_columnas_suficientes_cae_al_detectado(self, tmp_path):
        """Un delimitador ';' sobre un CSV de comas deja una sola columna;
        debe reintentarse con los delimitadores habituales."""
        from app.banco.importador_banco import leer_csv_banco

        csv = tmp_path / "extracto.csv"
        csv.write_text(
            "551-000068-95,551,,20260131,,-250000.00,2999,PAGO X,0\n"
        )
        movs = leer_csv_banco(csv, formato={"delimitador": ";"})
        assert len(movs) == 1
        assert float(movs[0].valor) == -250000.00

    def test_csv_ilegible_da_error_claro(self, tmp_path):
        """Si ningún delimitador produce columnas suficientes el error debe
        orientar al usuario a revisar el formato configurado."""
        from app.banco.importador_banco import leer_csv_banco

        csv = tmp_path / "extracto.csv"
        csv.write_text("esto no es un extracto\ncon dos lineas\n")
        with pytest.raises(ValueError, match="Delimitador"):
            leer_csv_banco(csv)


class TestValidacionDelimitadorFormulario:
    """El formulario de empresas debe rechazar delimitadores que rompen la
    importación del extracto (p. ej. '.', que aparece en decimales y "S.A")."""

    def _parse(self, extra: dict):
        import os
        os.environ.setdefault("USE_SQLITE", "true")
        os.environ.setdefault("FLASK_SECRET_KEY",
                              "test-secret-fixed-key-no-dev-1234567890")
        from app.web import create_app
        from app.web.routes.empresas import _parse_empresa_form

        app = create_app()
        datos = {"nombre": "Empresa X", "nit": "900123456", "sigla": "EX",
                 **extra}
        with app.test_request_context("/empresas/crear", method="POST",
                                      data=datos):
            return _parse_empresa_form()

    def test_delimitador_punto_rechazado(self):
        with pytest.raises(ValueError, match="Delimitador inválido"):
            self._parse({"banco_delimitador": "."})

    def test_delimitador_alfanumerico_rechazado(self):
        with pytest.raises(ValueError, match="Delimitador inválido"):
            self._parse({"banco_delimitador": "0"})

    def test_delimitador_igual_a_separador_decimal_rechazado(self):
        with pytest.raises(ValueError, match="separador"):
            self._parse({"banco_delimitador": ";",
                         "banco_separador_decimal": ";"})

    def test_delimitador_valido_aceptado(self):
        campos = self._parse({"banco_delimitador": ";"})
        assert campos["formato_banco"]["delimitador"] == ";"


class TestClasificadorMultiEmpresa:
    def test_nit_empresa_parametrizable(self):
        from app.clasificador import clasificar_documento

        otro_nit = "999999999"
        assert clasificar_documento("Factura electrónica", otro_nit, otro_nit) == "FACTURA_VENTA"
        assert clasificar_documento("Factura electrónica", config.NIT_EMPRESA, otro_nit) == "FACTURA_COMPRA"


class TestPreasientoMultiEmpresa:
    def test_cuenta_contraparte_override(self):
        from app.preasiento import generar_preasiento

        doc = {"CUFE/CUDE": "abc", "Total": 1000.0, "Folio": "1", "Prefijo": ""}
        p = generar_preasiento(
            documento=doc,
            tercero={"nit": "123", "nombre": "Prov"},
            impuestos=[],
            base_gravable=1000.0,
            clasificacion="FACTURA_COMPRA",
            cuentas_contraparte={"FACTURA_COMPRA": "22059999"},
        )
        assert p.lineas[0].cuenta == "22059999"

    def test_sin_override_usa_default(self):
        from app.preasiento import generar_preasiento

        doc = {"CUFE/CUDE": "abc", "Total": 1000.0, "Folio": "1", "Prefijo": ""}
        p = generar_preasiento(
            documento=doc,
            tercero={"nit": "123", "nombre": "Prov"},
            impuestos=[],
            base_gravable=1000.0,
            clasificacion="FACTURA_COMPRA",
        )
        assert p.lineas[0].cuenta == config.CUENTAS_CONTRAPARTE["FACTURA_COMPRA"]


class TestImpuestosMultiEmpresa:
    def test_cuentas_impuestos_override(self):
        import pandas as pd
        from app.impuestos import separar_impuestos

        row = pd.Series({"IVA": 190.0, "Total": 1190.0})
        cuentas = {"IVA": {"compra": "24080001", "venta": "24080002"}}
        imps = separar_impuestos(row, "FACTURA_COMPRA", cuentas)
        assert imps[0]["cuenta_sugerida"] == "24080001"
