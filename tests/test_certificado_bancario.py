"""
Tests del lector de certificados bancarios de Bancolombia.

Verifica la extracción del banco, el titular (persona jurídica y natural) y las
cuentas a partir del texto del PDF, además del manejo de errores.

El texto de los certificados de ejemplo se replica tal como lo entrega
``pdfplumber`` (con ``x_tolerance`` ajustado), de modo que se prueba la lógica
real sin depender de archivos binarios.
"""

import pytest

from app.certificado_bancario import (
    parsear_certificado_texto,
    CertificadoBancarioError,
)


# Texto de la primera página de un certificado de PERSONA JURÍDICA (CHICA BOTERO SAS).
TEXTO_JURIDICA = (
    "Producto No. Producto Fecha Apertura Estado\n"
    "CUENTA DE AHORROS 55116315903 2013/11/26 ACTIVA\n"
    "Jueves, 13 de abril de 2023\n"
    "Señor(a)\n"
    "A QUIEN PUEDA INTERESAR\n"
    "BANCOLOMBIA S.A. se permite informar que CHICA BOTERO SAS identificado(a) "
    "con NIT 900669897, a\n"
    "la fecha de expedición de esta certificación, tiene con el banco los "
    "siguientes productos:\n"
)

# Texto de la primera página de un certificado de PERSONA NATURAL (JUAN DAVID...).
# El número del documento queda en la línea siguiente al tipo (como en el PDF real).
TEXTO_NATURAL = (
    "Producto No. Producto Fecha Apertura Estado\n"
    "CUENTA DE AHORROS 33118436798 2021/02/11 ACTIVA\n"
    "Domingo, 8 de octubre de 2023\n"
    "Señor(a)\n"
    "A QUIEN PUEDA INTERESAR\n"
    "BANCOLOMBIA S.A. se permite informar que JUAN DAVID MUNERA LOPEZ "
    "identificado(a) con CC\n"
    "1000398865, a la fecha de expedición de esta certificación, tiene con el "
    "banco los siguientes productos:\n"
)


class TestPersonaJuridica:
    def test_banco(self):
        d = parsear_certificado_texto(TEXTO_JURIDICA)
        assert d["banco"] == "BANCOLOMBIA S.A."

    def test_titular_y_documento(self):
        d = parsear_certificado_texto(TEXTO_JURIDICA)
        assert d["titular"] == "CHICA BOTERO SAS"
        assert d["tipo_documento"] == "NIT"
        assert d["numero_documento"] == "900669897"
        assert d["tipo_persona"] == "juridica"

    def test_cuenta(self):
        d = parsear_certificado_texto(TEXTO_JURIDICA)
        assert len(d["cuentas"]) == 1
        c = d["cuentas"][0]
        assert c["tipo_producto"] == "CUENTA DE AHORROS"
        assert c["numero_cuenta"] == "55116315903"
        assert c["fecha_apertura"] == "2013/11/26"
        assert c["estado"] == "ACTIVA"


class TestPersonaNatural:
    def test_titular_y_documento(self):
        d = parsear_certificado_texto(TEXTO_NATURAL)
        assert d["titular"] == "JUAN DAVID MUNERA LOPEZ"
        assert d["tipo_documento"] == "CC"
        assert d["numero_documento"] == "1000398865"
        assert d["tipo_persona"] == "natural"

    def test_cuenta(self):
        d = parsear_certificado_texto(TEXTO_NATURAL)
        assert d["cuentas"][0]["numero_cuenta"] == "33118436798"
        assert d["cuentas"][0]["tipo_producto"] == "CUENTA DE AHORROS"


class TestVariantes:
    def test_varias_cuentas(self):
        texto = (
            "Producto No. Producto Fecha Apertura Estado\n"
            "CUENTA DE AHORROS 55116315903 2013/11/26 ACTIVA\n"
            "CUENTA CORRIENTE 12345678901 2018/05/04 ACTIVA\n"
            "BANCOLOMBIA S.A. se permite informar que EMPRESA XYZ SAS "
            "identificado(a) con NIT 901000000, a\n"
        )
        d = parsear_certificado_texto(texto)
        assert len(d["cuentas"]) == 2
        assert {c["tipo_producto"] for c in d["cuentas"]} == {
            "CUENTA DE AHORROS", "CUENTA CORRIENTE",
        }
        assert d["cuentas"][1]["numero_cuenta"] == "12345678901"

    def test_documento_con_separadores_se_normaliza(self):
        texto = (
            "Producto No. Producto Fecha Apertura Estado\n"
            "CUENTA DE AHORROS 55116315903 2013/11/26 ACTIVA\n"
            "BANCOLOMBIA S.A. se permite informar que CHICA BOTERO SAS "
            "identificado(a) con NIT 900.669.897, a\n"
        )
        d = parsear_certificado_texto(texto)
        assert d["numero_documento"] == "900669897"

    def test_estado_inactiva(self):
        texto = (
            "Producto No. Producto Fecha Apertura Estado\n"
            "CUENTA DE AHORROS 55116315903 2013/11/26 INACTIVA\n"
            "BANCOLOMBIA S.A. se permite informar que X SAS "
            "identificado(a) con NIT 900669897, a\n"
        )
        d = parsear_certificado_texto(texto)
        assert d["cuentas"][0]["estado"] == "INACTIVA"


class TestErrores:
    def test_sin_titular_lanza_error(self):
        texto = (
            "Producto No. Producto Fecha Apertura Estado\n"
            "CUENTA DE AHORROS 55116315903 2013/11/26 ACTIVA\n"
        )
        with pytest.raises(CertificadoBancarioError):
            parsear_certificado_texto(texto)

    def test_sin_cuentas_lanza_error(self):
        texto = (
            "BANCOLOMBIA S.A. se permite informar que CHICA BOTERO SAS "
            "identificado(a) con NIT 900669897, a\n"
            "la fecha de expedición de esta certificación.\n"
        )
        with pytest.raises(CertificadoBancarioError):
            parsear_certificado_texto(texto)

    def test_texto_vacio_lanza_error(self):
        with pytest.raises(CertificadoBancarioError):
            parsear_certificado_texto("")
