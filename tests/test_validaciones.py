"""
Tests del módulo validaciones.

Verifica cuadre correcto/incorrecto, CUFE duplicado, tercero activo
y coherencia de emisor.
"""

import pytest

from app.models import LineaContable, PreasientoContable
from app.validaciones import (
    validar_cuadre,
    validar_cufe_unico,
    validar_tercero_activo,
    validar_cuenta_transaccional,
    validar_coherencia_emisor,
    validar_preasiento_completo,
)
from app.config import NIT_EMPRESA

NIT_EMPRESA_TEST = NIT_EMPRESA
NIT_TERCERO = "800123456"


def _linea(cuenta, debito, credito):
    return LineaContable(
        cufe="CUFE-TEST", numero_linea=1,
        cuenta=cuenta, descripcion_cuenta="Desc",
        debito=debito, credito=credito,
        concepto="Test", tercero_nit="123", tercero_nombre="Test",
        es_pendiente=(cuenta == "[PENDIENTE]"),
    )


def _preasiento(lineas, clasificacion="FACTURA_COMPRA", cuadra=True,
                tercero_encontrado=True):
    return PreasientoContable(
        cufe="CUFE-TEST-001",
        tipo_documento="Factura electrónica",
        clasificacion=clasificacion,
        codigo_comprobante="50",
        titulo_comprobante="Facturas de compra",
        fecha_emision=None,
        folio="1",
        prefijo="FC",
        tercero_nit=NIT_TERCERO,
        tercero_nombre="PROVEEDOR",
        tercero_encontrado=tercero_encontrado,
        total=1000000.0,
        base_gravable=1000000.0,
        lineas=lineas,
        cuadra=cuadra,
        excepciones=[],
    )


class TestValidarCuadre:
    def test_cuadra_correcto(self):
        lineas = [_linea("22050501", 0, 1000000), _linea("[PENDIENTE]", 1000000, 0)]
        assert validar_cuadre(lineas) is True

    def test_no_cuadra(self):
        lineas = [_linea("22050501", 0, 1000000), _linea("[PENDIENTE]", 500000, 0)]
        assert validar_cuadre(lineas) is False

    def test_cuadra_con_tolerancia_centavos(self):
        lineas = [_linea("22050501", 0, 1000000.00), _linea("[PENDIENTE]", 999999.995, 0)]
        assert validar_cuadre(lineas) is True

    def test_lista_vacia_cuadra(self):
        assert validar_cuadre([]) is True


class TestValidarCufeUnico:
    def test_cufe_nuevo_es_valido(self, tmp_path):
        from app.database import inicializar_db
        db = str(tmp_path / "test.db")
        inicializar_db(db)
        assert validar_cufe_unico("CUFE-NUEVO-XYZ", db_path=db) is True

    def test_cufe_duplicado_invalido(self, tmp_path):
        from app.database import inicializar_db, registrar_documento
        db = str(tmp_path / "test.db")
        inicializar_db(db)
        registrar_documento("CUFE-DUP", "Factura", "FACTURA_COMPRA", "1", "",
                            NIT_TERCERO, "Prov", NIT_EMPRESA_TEST, "1INVEST",
                            1000.0, None, "archivo.xlsx", db_path=db)
        assert validar_cufe_unico("CUFE-DUP", db_path=db) is False

    def test_cufe_vacio_invalido(self, tmp_path):
        from app.database import inicializar_db
        db = str(tmp_path / "test.db")
        inicializar_db(db)
        assert validar_cufe_unico("", db_path=db) is False


class TestValidarTerceroActivo:
    def test_tercero_activo(self):
        assert validar_tercero_activo({"Estado": "Activo", "Identificación": "123"}) is True

    def test_tercero_inactivo(self):
        assert validar_tercero_activo({"Estado": "Inactivo", "Identificación": "123"}) is False

    def test_tercero_none(self):
        assert validar_tercero_activo(None) is False

    def test_tercero_dict_vacio(self):
        assert validar_tercero_activo({}) is False


class TestValidarCuentaTransaccional:
    def test_cuenta_valida(self, df_cuentas):
        assert validar_cuenta_transaccional("13050501", df_cuentas) is True

    def test_cuenta_no_existe(self, df_cuentas):
        assert validar_cuenta_transaccional("99999999", df_cuentas) is False

    def test_cuenta_pendiente_invalida(self, df_cuentas):
        assert validar_cuenta_transaccional("[PENDIENTE]", df_cuentas) is False

    def test_sin_maestro_retorna_true(self):
        assert validar_cuenta_transaccional("13050501", None) is True


class TestValidarCoherenciaEmisor:
    def test_factura_venta_emisor_empresa_ok(self):
        assert validar_coherencia_emisor("FACTURA_VENTA", NIT_EMPRESA_TEST) is True

    def test_factura_venta_emisor_tercero_falla(self):
        assert validar_coherencia_emisor("FACTURA_VENTA", NIT_TERCERO) is False

    def test_factura_compra_emisor_tercero_ok(self):
        assert validar_coherencia_emisor("FACTURA_COMPRA", NIT_TERCERO) is True

    def test_factura_compra_emisor_empresa_falla(self):
        assert validar_coherencia_emisor("FACTURA_COMPRA", NIT_EMPRESA_TEST) is False

    def test_sin_clasificar_no_falla(self):
        assert validar_coherencia_emisor("SIN_CLASIFICAR", NIT_TERCERO) is True


class TestValidarPreasientoCompleto:
    def test_preasiento_ok(self):
        lineas = [_linea("22050501", 0, 1000000), _linea("XXXXX", 1000000, 0)]
        p = _preasiento(lineas, tercero_encontrado=True)
        errores = validar_preasiento_completo(p)
        assert errores == []

    def test_preasiento_con_pendiente_da_error(self):
        lineas = [_linea("22050501", 0, 1000000), _linea("[PENDIENTE]", 1000000, 0)]
        p = _preasiento(lineas)
        errores = validar_preasiento_completo(p)
        assert any("PENDIENTE" in e for e in errores)

    def test_tercero_no_encontrado_da_error(self):
        lineas = [_linea("22050501", 0, 1000000), _linea("XXXXX", 1000000, 0)]
        p = _preasiento(lineas, tercero_encontrado=False)
        errores = validar_preasiento_completo(p)
        assert any("tercero" in e.lower() for e in errores)
