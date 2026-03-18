"""
Tests del módulo impuestos.

Verifica la separación de impuestos con múltiples impuestos,
sin impuestos y con valores parciales.
"""

import pandas as pd
import pytest

from app.impuestos import separar_impuestos, calcular_base_gravable, procesar_impuestos_lote


class TestSepararImpuestos:
    """Pruebas de separar_impuestos con distintos escenarios."""

    def _fila(self, **kwargs) -> pd.Series:
        """Crea una fila de prueba con valores por defecto en cero."""
        defaults = {
            "IVA": 0.0, "ICA": 0.0, "IC": 0.0, "INC": 0.0, "Timbre": 0.0,
            "INC Bolsas": 0.0, "IN Carbono": 0.0, "IN Combustibles": 0.0,
            "IC Datos": 0.0, "ICL": 0.0, "INPP": 0.0, "IBUA": 0.0, "ICUI": 0.0,
            "Rete IVA": 0.0, "Rete Renta": 0.0, "Rete ICA": 0.0,
            "Total": 1000000.0,
            "clasificacion": "FACTURA_COMPRA",
        }
        defaults.update(kwargs)
        return pd.Series(defaults)

    def test_sin_impuestos_retorna_lista_vacia(self):
        row = self._fila()
        resultado = separar_impuestos(row)
        assert resultado == []

    def test_iva_detectado(self):
        row = self._fila(IVA=190000.0, Total=1190000.0)
        resultado = separar_impuestos(row)
        assert len(resultado) == 1
        assert resultado[0]["nombre_impuesto"] == "IVA"
        assert resultado[0]["valor"] == 190000.0

    def test_multiples_impuestos(self):
        row = self._fila(IVA=190000.0, Rete_Renta=25000.0,
                         **{"Rete Renta": 25000.0}, Total=1190000.0)
        row["Rete Renta"] = 25000.0
        resultado = separar_impuestos(row)
        nombres = [r["nombre_impuesto"] for r in resultado]
        assert "IVA" in nombres
        assert "Rete Renta" in nombres

    def test_retencion_marcada_como_tal(self):
        row = self._fila(**{"Rete Renta": 25000.0})
        resultado = separar_impuestos(row)
        assert resultado[0]["es_retencion"] is True

    def test_iva_no_es_retencion(self):
        row = self._fila(IVA=190000.0)
        resultado = separar_impuestos(row)
        assert resultado[0]["es_retencion"] is False

    def test_cuenta_sugerida_compra_iva(self):
        row = self._fila(IVA=190000.0, clasificacion="FACTURA_COMPRA")
        resultado = separar_impuestos(row, clasificacion="FACTURA_COMPRA")
        assert resultado[0]["cuenta_sugerida"] == "24081001"

    def test_cuenta_sugerida_venta_iva(self):
        row = self._fila(IVA=190000.0, clasificacion="FACTURA_VENTA")
        resultado = separar_impuestos(row, clasificacion="FACTURA_VENTA")
        assert resultado[0]["cuenta_sugerida"] == "24080501"

    def test_impuesto_cero_no_aparece(self):
        row = self._fila(IVA=0.0, ICA=0.0)
        assert separar_impuestos(row) == []


class TestCalcularBaseGravable:
    """Pruebas del cálculo de base gravable."""

    def test_base_sin_impuestos_igual_total(self):
        base = calcular_base_gravable(1000000.0, [])
        assert base == 1000000.0

    def test_base_con_iva(self):
        impuestos = [{"valor": 190000.0}]
        base = calcular_base_gravable(1190000.0, impuestos)
        assert base == 1000000.0

    def test_base_con_multiples_impuestos(self):
        impuestos = [{"valor": 190000.0}, {"valor": 25000.0}, {"valor": 14250.0}]
        base = calcular_base_gravable(1229250.0, impuestos)
        assert abs(base - 1000000.0) < 0.01

    def test_base_negativa_posible(self):
        """Casos extremos donde retenciones superan el total."""
        impuestos = [{"valor": 500000.0}]
        base = calcular_base_gravable(100000.0, impuestos)
        assert base < 0


class TestProcesarImpuestosLote:
    """Pruebas del procesamiento masivo."""

    def test_agrega_columnas(self, df_radian_basico):
        from app.clasificador import clasificar_lote
        df = clasificar_lote(df_radian_basico)
        resultado = procesar_impuestos_lote(df)
        assert "_impuestos" in resultado.columns
        assert "_base_gravable" in resultado.columns

    def test_base_gravable_es_float(self, df_radian_basico):
        from app.clasificador import clasificar_lote
        df = clasificar_lote(df_radian_basico)
        resultado = procesar_impuestos_lote(df)
        for val in resultado["_base_gravable"]:
            assert isinstance(val, float)
