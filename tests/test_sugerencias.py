"""
Tests unitarios para el motor de sugerencias (app/sugerencias.py).

Usa SQLite en memoria (:memory:) para no tocar el disco.
"""

import sqlite3
import pytest

from app.sugerencias import (
    sugerir_cuenta,
    registrar_confirmacion,
    enriquecer_con_sugerencias,
    registrar_lote_confirmaciones,
)
from app.models import LineaContable, PreasientoContable
from app.database import inicializar_db


# ---------------------------------------------------------------------------
# Fixture: base de datos temporal en memoria
# ---------------------------------------------------------------------------

@pytest.fixture
def db_tmp(tmp_path):
    """Base de datos SQLite temporal en disco (tmp_path de pytest)."""
    db_path = str(tmp_path / "test_sugerencias.db")
    inicializar_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Helpers para construir objetos de prueba
# ---------------------------------------------------------------------------

def _make_linea(
    cufe: str = "CUFE-TEST",
    numero: int = 1,
    cuenta: str = "[PENDIENTE]",
    concepto: str = "Base gravable",
    debito: float = 1000.0,
    credito: float = 0.0,
    es_pendiente: bool = True,
    tercero_nit: str = "800123456",
) -> LineaContable:
    return LineaContable(
        cufe=cufe,
        numero_linea=numero,
        cuenta=cuenta,
        descripcion_cuenta="Gasto/Costo",
        debito=debito,
        credito=credito,
        concepto=concepto,
        tercero_nit=tercero_nit,
        tercero_nombre="PROVEEDOR TEST SA",
        es_pendiente=es_pendiente,
        es_sugerida=False,
    )


def _make_preasiento(
    cufe: str = "CUFE-TEST",
    clasificacion: str = "FACTURA_COMPRA",
    tercero_nit: str = "800123456",
    lineas: list | None = None,
) -> PreasientoContable:
    return PreasientoContable(
        cufe=cufe,
        tipo_documento="Factura electrónica",
        clasificacion=clasificacion,
        codigo_comprobante="50",
        titulo_comprobante="Facturas de compra",
        fecha_emision=None,
        folio="1001",
        prefijo="FC",
        tercero_nit=tercero_nit,
        tercero_nombre="PROVEEDOR TEST SA",
        tercero_encontrado=True,
        total=1190000.0,
        base_gravable=1000000.0,
        lineas=lineas or [],
        cuadra=False,
        excepciones=["1 línea(s) con cuenta [PENDIENTE]"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSugerirCuenta:
    def test_sin_historial_retorna_none(self, db_tmp):
        """Sin datos previos, sugerir_cuenta debe retornar None."""
        resultado = sugerir_cuenta("FACTURA_COMPRA", "800123456", "base", db_tmp)
        assert resultado is None

    def test_registrar_y_sugerir(self, db_tmp):
        """Después de registrar, debe sugerir la misma cuenta."""
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "51050501", db_tmp)
        resultado = sugerir_cuenta("FACTURA_COMPRA", "800123456", "base", db_tmp)
        assert resultado == "51050501"

    def test_mas_frecuente_gana(self, db_tmp):
        """La cuenta más recientemente confirmada es la que queda en el historial.

        La tabla historial_cuentas tiene un único registro por tripleta
        (clasificacion, nit_tercero, tipo_linea). El UPSERT reemplaza la cuenta
        con la última confirmada e incrementa el contador de usos.
        Así, la cuenta que se confirma al final es la que gana.
        """
        # Confirmar cuenta A varias veces y luego cuenta B una vez
        for _ in range(3):
            registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "51050501", db_tmp)
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "52050501", db_tmp)

        # La última cuenta escrita (52050501) debe ser la sugerida
        resultado = sugerir_cuenta("FACTURA_COMPRA", "800123456", "base", db_tmp)
        assert resultado == "52050501"

        # El contador debe haberse ido acumulando (A×3 + B×1 = 4)
        conn = __import__("sqlite3").connect(db_tmp)
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT usos FROM historial_cuentas WHERE tipo_linea='base'"
        ).fetchone()
        conn.close()
        assert row["usos"] == 4


    def test_diferente_clasificacion_no_interfiere(self, db_tmp):
        """Las sugerencias son independientes por clasificación."""
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "51050501", db_tmp)
        # Para FACTURA_VENTA no hay historial
        resultado = sugerir_cuenta("FACTURA_VENTA", "800123456", "base", db_tmp)
        assert resultado is None

    def test_no_registra_pendiente(self, db_tmp):
        """registrar_confirmacion no debe guardar la cadena '[PENDIENTE]'."""
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "[PENDIENTE]", db_tmp)
        resultado = sugerir_cuenta("FACTURA_COMPRA", "800123456", "base", db_tmp)
        assert resultado is None


class TestEnriquecerConSugerencias:
    def test_reemplaza_pendiente_cuando_hay_historial(self, db_tmp):
        """enriquecer_con_sugerencias debe reemplazar [PENDIENTE] con la cuenta sugerida."""
        # Preparar historial
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "51050501", db_tmp)

        linea_pendiente = _make_linea(es_pendiente=True, cuenta="[PENDIENTE]")
        preasiento = _make_preasiento(lineas=[linea_pendiente])

        resultado = enriquecer_con_sugerencias([preasiento], db_tmp)

        linea = resultado[0].lineas[0]
        assert linea.cuenta == "51050501"
        assert linea.es_sugerida is True
        assert linea.es_pendiente is False

    def test_no_modifica_cuentas_confirmadas(self, db_tmp):
        """Líneas con cuenta real no deben ser alteradas."""
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "99999999", db_tmp)

        linea_confirmada = _make_linea(
            cuenta="51050501", concepto="Base gravable",
            es_pendiente=False,
        )
        preasiento = _make_preasiento(lineas=[linea_confirmada])
        preasiento.excepciones = []

        resultado = enriquecer_con_sugerencias([preasiento], db_tmp)

        linea = resultado[0].lineas[0]
        # La cuenta NO debe cambiar a 99999999 porque es_pendiente=False
        assert linea.cuenta == "51050501"
        assert linea.es_sugerida is False

    def test_sin_historial_no_modifica_pendiente(self, db_tmp):
        """Sin historial, las líneas [PENDIENTE] deben permanecer igual."""
        linea_pendiente = _make_linea(es_pendiente=True, cuenta="[PENDIENTE]")
        preasiento = _make_preasiento(lineas=[linea_pendiente])

        resultado = enriquecer_con_sugerencias([preasiento], db_tmp)

        linea = resultado[0].lineas[0]
        assert linea.cuenta == "[PENDIENTE]"
        assert linea.es_pendiente is True
        assert linea.es_sugerida is False

    def test_excepciones_se_actualizan(self, db_tmp):
        """Cuando todas las líneas son resueltas, la excepción de [PENDIENTE] desaparece."""
        registrar_confirmacion("FACTURA_COMPRA", "800123456", "base", "51050501", db_tmp)

        linea_pendiente = _make_linea(es_pendiente=True, cuenta="[PENDIENTE]")
        preasiento = _make_preasiento(lineas=[linea_pendiente])

        resultado = enriquecer_con_sugerencias([preasiento], db_tmp)

        # No deben quedar excepciones de [PENDIENTE]
        pendiente_exceptions = [
            e for e in resultado[0].excepciones if "PENDIENTE" in e
        ]
        assert len(pendiente_exceptions) == 0


class TestRegistrarLoteConfirmaciones:
    def test_registra_cuentas_reales(self, db_tmp):
        """registrar_lote_confirmaciones guarda en historial las cuentas no-pendientes."""
        linea_real = _make_linea(
            cuenta="51050501", concepto="Base gravable", es_pendiente=False,
        )
        preasiento = _make_preasiento(lineas=[linea_real])
        preasiento.excepciones = []

        total = registrar_lote_confirmaciones([preasiento], db_tmp)
        assert total >= 1

        # Verificar que la sugerencia ya está disponible
        resultado = sugerir_cuenta("FACTURA_COMPRA", "800123456", "base", db_tmp)
        assert resultado == "51050501"

    def test_no_registra_sugeridas(self, db_tmp):
        """Las líneas marcadas como es_sugerida=True no deben alimentar el historial."""
        linea_sugerida = _make_linea(
            cuenta="51050501", concepto="Base gravable", es_pendiente=False,
        )
        linea_sugerida.es_sugerida = True

        preasiento = _make_preasiento(lineas=[linea_sugerida])

        total = registrar_lote_confirmaciones([preasiento], db_tmp)
        assert total == 0

        # No debe haber historial
        resultado = sugerir_cuenta("FACTURA_COMPRA", "800123456", "base", db_tmp)
        assert resultado is None
