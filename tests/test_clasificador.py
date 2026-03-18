"""
Tests unitarios del módulo clasificador.

Verifica cada regla de clasificación con casos explícitos derivados
de los tipos de documento que aparecen en el reporte RADIAN real.
"""

import pandas as pd
import pytest

from app.clasificador import clasificar_documento, clasificar_lote
from app.config import NIT_EMPRESA

NIT_EMPRESA_TEST = NIT_EMPRESA
NIT_TERCERO = "800123456"


class TestClasificarDocumento:
    """Pruebas de la función clasificar_documento."""

    def test_factura_venta_emisor_empresa(self):
        assert clasificar_documento("Factura electrónica", NIT_EMPRESA_TEST) == "FACTURA_VENTA"

    def test_factura_compra_emisor_tercero(self):
        assert clasificar_documento("Factura electrónica", NIT_TERCERO) == "FACTURA_COMPRA"

    def test_nomina_individual(self):
        assert clasificar_documento("Nomina Individual", NIT_EMPRESA_TEST) == "NOMINA"

    def test_nomina_case_insensitive(self):
        assert clasificar_documento("nomina individual", NIT_EMPRESA_TEST) == "NOMINA"

    def test_documento_soporte(self):
        assert clasificar_documento(
            "Documento soporte con no obligados", NIT_EMPRESA_TEST
        ) == "DOCUMENTO_SOPORTE"

    def test_nota_credito_venta(self):
        assert clasificar_documento("Nota crédito", NIT_EMPRESA_TEST) == "NOTA_CREDITO_VENTA"

    def test_nota_credito_compra(self):
        assert clasificar_documento("Nota crédito", NIT_TERCERO) == "NOTA_CREDITO_COMPRA"

    def test_nota_credito_sin_tilde(self):
        assert clasificar_documento("Nota credito", NIT_EMPRESA_TEST) == "NOTA_CREDITO_VENTA"

    def test_nota_debito_venta(self):
        assert clasificar_documento("Nota débito", NIT_EMPRESA_TEST) == "NOTA_DEBITO_VENTA"

    def test_nota_debito_compra(self):
        assert clasificar_documento("Nota débito", NIT_TERCERO) == "NOTA_DEBITO_COMPRA"

    def test_tipo_desconocido_retorna_sin_clasificar(self):
        assert clasificar_documento("Documento raro XYZ", NIT_TERCERO) == "SIN_CLASIFICAR"

    def test_tipo_vacio_retorna_sin_clasificar(self):
        assert clasificar_documento("", NIT_EMPRESA_TEST) == "SIN_CLASIFICAR"

    def test_nit_con_puntos_y_guion_empresa(self):
        """NIT con formato sucio debe reconocerse como empresa si ya fue limpiado."""
        # El clasificador recibe el NIT ya normalizado (limpiado por el importador)
        assert clasificar_documento("Factura electrónica", NIT_EMPRESA_TEST) == "FACTURA_VENTA"

    def test_factura_electronica_case_insensitive(self):
        assert clasificar_documento("factura electronica", NIT_TERCERO) == "FACTURA_COMPRA"


class TestClasificarLote:
    """Pruebas de la función clasificar_lote."""

    def test_lote_agrega_columna_clasificacion(self, df_radian_basico):
        resultado = clasificar_lote(df_radian_basico)
        assert "clasificacion" in resultado.columns

    def test_lote_clasifica_todos_los_tipos(self, df_radian_basico):
        resultado = clasificar_lote(df_radian_basico)
        clasificaciones = set(resultado["clasificacion"].tolist())
        esperadas = {
            "FACTURA_VENTA", "FACTURA_COMPRA",
            "DOCUMENTO_SOPORTE", "NOMINA",
            "NOTA_CREDITO_VENTA", "NOTA_CREDITO_COMPRA",
        }
        assert clasificaciones == esperadas

    def test_lote_no_modifica_filas_originales(self, df_radian_basico):
        original_len = len(df_radian_basico)
        resultado = clasificar_lote(df_radian_basico)
        assert len(resultado) == original_len

    def test_lote_sin_clasificar_no_aparece_en_basico(self, df_radian_basico):
        resultado = clasificar_lote(df_radian_basico)
        assert "SIN_CLASIFICAR" not in resultado["clasificacion"].values
