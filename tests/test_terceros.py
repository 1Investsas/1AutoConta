"""
Tests del módulo terceros.

Verifica la identificación del tercero según clasificación y el cruce
contra el maestro, incluyendo NITs con formatos sucios.
"""

import pandas as pd
import pytest

from app.terceros import identificar_tercero, cruzar_tercero, procesar_terceros_lote
from app.config import NIT_EMPRESA

NIT_EMPRESA_TEST = NIT_EMPRESA
NIT_TERCERO = "800123456"


class TestIdentificarTercero:
    """Pruebas de qué NIT se usa según la clasificación."""

    def test_factura_venta_usa_receptor(self):
        r = identificar_tercero("FACTURA_VENTA", NIT_EMPRESA_TEST, "1INVEST",
                                NIT_TERCERO, "CLIENTE")
        assert r["nit"] == NIT_TERCERO
        assert r["nombre"] == "CLIENTE"

    def test_factura_compra_usa_emisor(self):
        r = identificar_tercero("FACTURA_COMPRA", NIT_TERCERO, "PROVEEDOR",
                                NIT_EMPRESA_TEST, "1INVEST")
        assert r["nit"] == NIT_TERCERO
        assert r["nombre"] == "PROVEEDOR"

    def test_documento_soporte_usa_receptor(self):
        r = identificar_tercero("DOCUMENTO_SOPORTE", NIT_EMPRESA_TEST, "1INVEST",
                                "12345678", "NO OBLIGADO")
        assert r["nit"] == "12345678"

    def test_nomina_usa_receptor(self):
        r = identificar_tercero("NOMINA", NIT_EMPRESA_TEST, "1INVEST",
                                "99887766", "EMPLEADO")
        assert r["nit"] == "99887766"

    def test_nota_credito_venta_usa_receptor(self):
        r = identificar_tercero("NOTA_CREDITO_VENTA", NIT_EMPRESA_TEST, "1INVEST",
                                NIT_TERCERO, "CLIENTE")
        assert r["nit"] == NIT_TERCERO

    def test_nota_credito_compra_usa_emisor(self):
        r = identificar_tercero("NOTA_CREDITO_COMPRA", NIT_TERCERO, "PROVEEDOR",
                                NIT_EMPRESA_TEST, "1INVEST")
        assert r["nit"] == NIT_TERCERO


class TestCruzarTercero:
    """Pruebas del cruce contra el maestro de terceros."""

    def test_tercero_encontrado(self, df_terceros):
        resultado = cruzar_tercero("800123456", df_terceros)
        assert resultado is not None
        assert resultado["Identificación"] == "800123456"

    def test_tercero_no_encontrado(self, df_terceros):
        resultado = cruzar_tercero("999999999", df_terceros)
        assert resultado is None

    def test_nit_vacio_retorna_none(self, df_terceros):
        assert cruzar_tercero("", df_terceros) is None

    def test_nit_con_formato_sucio_no_encuentra(self, df_terceros):
        """El importador limpia el NIT antes del cruce; aquí probamos el estado limpio."""
        resultado = cruzar_tercero("800.123.456", df_terceros)
        assert resultado is None  # El maestro ya tiene '800123456' limpio

    def test_nit_limpio_encuentra(self, df_terceros):
        resultado = cruzar_tercero("800123456", df_terceros)
        assert resultado is not None

    def test_maestro_vacio_retorna_none(self):
        df_vacio = pd.DataFrame(columns=["Identificación", "Nombre tercero", "Estado"])
        assert cruzar_tercero("800123456", df_vacio) is None

    def test_sin_columna_identificacion_retorna_none(self):
        df_sin_col = pd.DataFrame({"NombreRaro": ["800123456"]})
        assert cruzar_tercero("800123456", df_sin_col) is None


class TestProcesarTercerosLote:
    """Pruebas del procesamiento masivo de terceros."""

    def test_agrega_columnas_esperadas(self, df_radian_basico, df_terceros):
        from app.clasificador import clasificar_lote
        df = clasificar_lote(df_radian_basico)
        resultado = procesar_terceros_lote(df, df_terceros)
        for col in ["tercero_nit", "tercero_nombre", "tercero_encontrado", "tercero_estado"]:
            assert col in resultado.columns

    def test_tercero_encontrado_true_si_existe(self, df_radian_basico, df_terceros):
        from app.clasificador import clasificar_lote
        df = clasificar_lote(df_radian_basico)
        resultado = procesar_terceros_lote(df, df_terceros)
        # Todos los NITs del fixture de prueba están en el maestro
        assert resultado["tercero_encontrado"].any()
