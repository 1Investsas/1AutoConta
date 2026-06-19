"""
Tests de las correcciones de tercero (trazabilidad y aprendizaje, Fase 1).

Cubre:
- Las funciones CRUD de la BD (registrar/obtener/listar) con UPSERT por NIT.
- La reaplicación automática de correcciones en el lote (aprendizaje).
"""

import pandas as pd
import pytest

from app.database import (
    inicializar_db,
    registrar_correccion_tercero,
    obtener_correccion_tercero,
    listar_correcciones_tercero,
)
from app.terceros import procesar_terceros_lote, aplicar_correcciones_lote


@pytest.fixture
def db_tmp(tmp_path):
    """Base de datos SQLite temporal en disco."""
    db_path = str(tmp_path / "test_correcciones.db")
    inicializar_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# CRUD de correcciones
# ---------------------------------------------------------------------------

class TestRegistrarCorreccionTercero:
    def test_sin_historial_retorna_none(self, db_tmp):
        assert obtener_correccion_tercero("900111", db_tmp) is None

    def test_registrar_y_obtener(self, db_tmp):
        registrar_correccion_tercero(
            "900111", "NOMBRE MALO", "900222", "NOMBRE BUENO SAS",
            clasificacion="FACTURA_COMPRA", db_path=db_tmp,
        )
        corr = obtener_correccion_tercero("900111", db_tmp)
        assert corr is not None
        assert corr["nit_corregido"] == "900222"
        assert corr["nombre_corregido"] == "NOMBRE BUENO SAS"

    def test_nit_original_vacio_no_registra(self, db_tmp):
        registrar_correccion_tercero("", "x", "900222", "y", db_path=db_tmp)
        assert listar_correcciones_tercero(db_tmp) == []

    def test_nit_corregido_vacio_no_registra(self, db_tmp):
        registrar_correccion_tercero("900111", "x", "", "y", db_path=db_tmp)
        assert obtener_correccion_tercero("900111", db_tmp) is None

    def test_upsert_incrementa_usos_y_actualiza(self, db_tmp):
        registrar_correccion_tercero("900111", "MALO", "900222", "BUENO", db_path=db_tmp)
        registrar_correccion_tercero("900111", "MALO", "900333", "MEJOR", db_path=db_tmp)

        corr = obtener_correccion_tercero("900111", db_tmp)
        assert corr["nit_corregido"] == "900333"
        assert corr["nombre_corregido"] == "MEJOR"

        registros = listar_correcciones_tercero(db_tmp)
        assert len(registros) == 1
        assert registros[0]["usos"] == 2

    def test_listar_retorna_todas(self, db_tmp):
        registrar_correccion_tercero("900111", "", "900222", "A", db_path=db_tmp)
        registrar_correccion_tercero("900333", "", "900444", "B", db_path=db_tmp)
        registros = listar_correcciones_tercero(db_tmp)
        assert {r["nit_original"] for r in registros} == {"900111", "900333"}


# ---------------------------------------------------------------------------
# Aprendizaje: reaplicar correcciones en el lote
# ---------------------------------------------------------------------------

class TestAplicarCorreccionesLote:
    def _df_base(self):
        """DataFrame mínimo ya 'identificado' (como tras procesar_terceros_lote)."""
        return pd.DataFrame({
            "tercero_nit":          ["900111", "555000"],
            "tercero_nombre":       ["NOMBRE MALO", "OTRO"],
            "tercero_encontrado":   [False, True],
            "tercero_nit_original": ["900111", "555000"],
        })

    def test_sin_db_marca_no_corregido(self):
        df = aplicar_correcciones_lote(self._df_base(), pd.DataFrame(), db_path=None)
        assert list(df["tercero_corregido"]) == [False, False]
        # No cambia los NITs
        assert list(df["tercero_nit"]) == ["900111", "555000"]

    def test_sin_correcciones_no_cambia(self, db_tmp):
        df = aplicar_correcciones_lote(self._df_base(), pd.DataFrame(), db_path=db_tmp)
        assert list(df["tercero_corregido"]) == [False, False]
        assert list(df["tercero_nit"]) == ["900111", "555000"]

    def test_aplica_correccion_registrada(self, db_tmp, df_terceros):
        # 800123456 está en el maestro df_terceros (fixture compartido).
        registrar_correccion_tercero(
            "900111", "NOMBRE MALO", "800123456", "", db_path=db_tmp,
        )
        df = aplicar_correcciones_lote(self._df_base(), df_terceros, db_path=db_tmp)

        # Fila 0 corregida y cruzada contra el maestro.
        assert df.loc[0, "tercero_corregido"]
        assert df.loc[0, "tercero_nit"] == "800123456"
        assert df.loc[0, "tercero_encontrado"]
        assert "CLIENTE" in df.loc[0, "tercero_nombre"] or "PROVEEDOR" in df.loc[0, "tercero_nombre"]

        # Fila 1 intacta.
        assert not df.loc[1, "tercero_corregido"]
        assert df.loc[1, "tercero_nit"] == "555000"

    def test_correccion_a_nit_fuera_de_maestro(self, db_tmp):
        registrar_correccion_tercero("900111", "MALO", "777999", "MANUAL SAS", db_path=db_tmp)
        df = aplicar_correcciones_lote(self._df_base(), pd.DataFrame(), db_path=db_tmp)
        assert df.loc[0, "tercero_nit"] == "777999"
        assert df.loc[0, "tercero_nombre"] == "MANUAL SAS"
        assert not df.loc[0, "tercero_encontrado"]

    def test_genera_columna_original_si_falta(self, db_tmp):
        df = pd.DataFrame({
            "tercero_nit":        ["900111"],
            "tercero_nombre":     ["X"],
            "tercero_encontrado": [False],
        })
        out = aplicar_correcciones_lote(df, pd.DataFrame(), db_path=db_tmp)
        assert "tercero_nit_original" in out.columns
        assert out.loc[0, "tercero_nit_original"] == "900111"

    def test_pipeline_terceros_preserva_original(self, df_radian_basico, df_terceros):
        from app.clasificador import clasificar_lote
        df = clasificar_lote(df_radian_basico)
        df = procesar_terceros_lote(df, df_terceros)
        assert "tercero_nit_original" in df.columns
        assert list(df["tercero_nit_original"]) == list(df["tercero_nit"])
