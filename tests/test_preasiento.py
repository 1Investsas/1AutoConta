"""
Tests del módulo preasiento.

Verifica la estructura de asiento por cada tipo de documento
y que los cálculos de cuadre sean correctos.
"""

import pytest

from app.preasiento import generar_preasiento, generar_lote, CUENTA_PENDIENTE
from app.config import NIT_EMPRESA


NIT_EMPRESA_TEST = NIT_EMPRESA
NIT_TERCERO = "800123456"


def _documento_base(clasificacion: str, nit_emisor: str, nit_receptor: str,
                    total: float = 1190000.0) -> dict:
    return {
        "CUFE/CUDE": f"CUFE-TEST-{clasificacion}",
        "Tipo de documento": clasificacion,
        "clasificacion": clasificacion,
        "NIT Emisor": nit_emisor,
        "Nombre Emisor": "EMISOR TEST",
        "NIT Receptor": nit_receptor,
        "Nombre Receptor": "RECEPTOR TEST",
        "Folio": "9999",
        "Prefijo": "TS",
        "Fecha Emisión": None,
        "Total": total,
        "tercero_encontrado": True,
    }


def _impuestos_basicos(sentido: str = "compra"):
    return [
        {"nombre_impuesto": "IVA", "valor": 190000.0,
         "cuenta_sugerida": "24081001" if sentido == "compra" else "24080501",
         "es_retencion": False, "sentido": sentido},
    ]


class TestGenararPreasientoFacturaCompra:
    def setup_method(self):
        self.doc = _documento_base("FACTURA_COMPRA", NIT_TERCERO, NIT_EMPRESA_TEST)
        self.tercero = {"nit": NIT_TERCERO, "nombre": "PROVEEDOR"}
        self.impuestos = _impuestos_basicos("compra")
        self.base = 1000000.0

    def test_genera_objeto_preasiento(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_COMPRA")
        assert p is not None
        assert p.clasificacion == "FACTURA_COMPRA"

    def test_primera_linea_es_proveedor(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_COMPRA")
        assert p.lineas[0].cuenta == "22050501"
        assert p.lineas[0].credito == 1190000.0
        assert p.lineas[0].debito == 0.0

    def test_segunda_linea_es_pendiente(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_COMPRA")
        assert p.lineas[1].cuenta == CUENTA_PENDIENTE
        assert p.lineas[1].debito == 1000000.0

    def test_linea_iva_es_debito(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_COMPRA")
        linea_iva = next(l for l in p.lineas if l.descripcion_cuenta == "IVA")
        assert linea_iva.debito == 190000.0
        assert linea_iva.credito == 0.0

    def test_cuadra_con_impuestos(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_COMPRA")
        total_d = sum(l.debito for l in p.lineas)
        total_c = sum(l.credito for l in p.lineas)
        assert abs(total_d - total_c) < 0.01


class TestGenerarPreasientoFacturaVenta:
    def setup_method(self):
        self.doc = _documento_base("FACTURA_VENTA", NIT_EMPRESA_TEST, NIT_TERCERO)
        self.tercero = {"nit": NIT_TERCERO, "nombre": "CLIENTE"}
        self.impuestos = _impuestos_basicos("venta")
        self.base = 1000000.0

    def test_primera_linea_es_cxc(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_VENTA")
        assert p.lineas[0].cuenta == "13050501"
        assert p.lineas[0].debito == 1190000.0

    def test_segunda_linea_es_ingreso_pendiente(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_VENTA")
        assert p.lineas[1].cuenta == CUENTA_PENDIENTE
        assert p.lineas[1].credito == 1000000.0

    def test_iva_es_credito_en_venta(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_VENTA")
        linea_iva = next(l for l in p.lineas if l.descripcion_cuenta == "IVA")
        assert linea_iva.credito == 190000.0

    def test_cuadra(self):
        p = generar_preasiento(self.doc, self.tercero, self.impuestos, self.base, "FACTURA_VENTA")
        assert abs(sum(l.debito for l in p.lineas) - sum(l.credito for l in p.lineas)) < 0.01


class TestGenerarPreasientoNomina:
    def test_primera_linea_pendiente_debito(self):
        doc = _documento_base("NOMINA", NIT_EMPRESA_TEST, "99887766", total=2000000.0)
        tercero = {"nit": "99887766", "nombre": "EMPLEADO"}
        p = generar_preasiento(doc, tercero, [], 2000000.0, "NOMINA")
        assert p.lineas[0].cuenta == CUENTA_PENDIENTE
        assert p.lineas[0].debito == 2000000.0

    def test_segunda_linea_salarios(self):
        doc = _documento_base("NOMINA", NIT_EMPRESA_TEST, "99887766", total=2000000.0)
        tercero = {"nit": "99887766", "nombre": "EMPLEADO"}
        p = generar_preasiento(doc, tercero, [], 2000000.0, "NOMINA")
        assert p.lineas[1].cuenta == "25050501"
        assert p.lineas[1].credito == 2000000.0


class TestGenerarPreasientoDocumentoSoporte:
    def test_primera_linea_proveedor_exterior(self):
        doc = _documento_base("DOCUMENTO_SOPORTE", NIT_EMPRESA_TEST, "12345678", total=500000.0)
        tercero = {"nit": "12345678", "nombre": "NO OBLIGADO"}
        p = generar_preasiento(doc, tercero, [], 500000.0, "DOCUMENTO_SOPORTE")
        assert p.lineas[0].cuenta == "22100501"
        assert p.lineas[0].credito == 500000.0


class TestGenerarLote:
    def test_genera_un_preasiento_por_fila(self, df_radian_basico):
        from app.clasificador import clasificar_lote
        from app.terceros import procesar_terceros_lote
        from app.impuestos import procesar_impuestos_lote
        from app.comprobantes import asignar_comprobantes_lote
        import pandas as pd

        df = clasificar_lote(df_radian_basico)
        df = procesar_terceros_lote(df, pd.DataFrame(columns=["Identificación"]))
        df = procesar_impuestos_lote(df)
        df = asignar_comprobantes_lote(df)

        preasientos = generar_lote(df)
        assert len(preasientos) == len(df_radian_basico)
