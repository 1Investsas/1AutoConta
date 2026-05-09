"""
Tests unitarios para app/siigo/mapeador.py — Fase 3.

Valida la transformación de PreasientoContable a FilaSiigo y las
utilidades de chunking.
"""

import pytest
import pandas as pd
from datetime import datetime

from app.models import PreasientoContable, LineaContable
from app.siigo.mapeador import (
    ENCABEZADOS_SIIGO,
    FilaSiigo,
    mapear_preasiento,
    mapear_lote,
    partir_en_chunks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linea(n, cuenta, debito=0.0, credito=0.0, pendiente=False, concepto="Concepto"):
    return LineaContable(
        cufe="CUFE-TEST",
        numero_linea=n,
        cuenta=cuenta,
        descripcion_cuenta=f"Desc {cuenta}",
        debito=debito,
        credito=credito,
        concepto=concepto,
        tercero_nit="800123456",
        tercero_nombre="PROVEEDOR SA",
        es_pendiente=pendiente,
    )


def _preasiento(
    cufe="CUFE-TEST-001",
    clasificacion="FACTURA_COMPRA",
    lineas=None,
    fecha=None,
    folio="2001",
    prefijo="FC",
):
    if lineas is None:
        lineas = [
            _linea(1, "22050501", credito=1055750.0, concepto="Proveedor"),
            _linea(2, "[PENDIENTE]", debito=1000000.0, pendiente=True, concepto="Gasto"),
            _linea(3, "24081001", debito=55750.0, concepto="IVA"),
        ]
    return PreasientoContable(
        cufe=cufe,
        tipo_documento="Factura electrónica",
        clasificacion=clasificacion,
        codigo_comprobante="50",
        titulo_comprobante="Facturas de compra",
        fecha_emision=fecha or datetime(2025, 3, 1),
        folio=folio,
        prefijo=prefijo,
        tercero_nit="800123456",
        tercero_nombre="PROVEEDOR SA",
        tercero_encontrado=True,
        total=1055750.0,
        base_gravable=1000000.0,
        lineas=lineas,
        cuadra=True,
    )


# ---------------------------------------------------------------------------
# FilaSiigo.a_lista
# ---------------------------------------------------------------------------

class TestFilaSiigoALista:
    def test_longitud_igual_a_encabezados(self):
        fila = FilaSiigo(
            tipo_comprobante=50,
            consecutivo_comprobante=1,
            fecha="01/03/2025",
            codigo_cuenta="22050501",
            nit_tercero="800123456",
        )
        assert len(fila.a_lista()) == len(ENCABEZADOS_SIIGO)

    def test_27_columnas(self):
        fila = FilaSiigo(
            tipo_comprobante=50,
            consecutivo_comprobante=1,
            fecha="01/03/2025",
            codigo_cuenta="22050501",
            nit_tercero="800123456",
        )
        assert len(fila.a_lista()) == 27

    def test_orden_correcto(self):
        fila = FilaSiigo(
            tipo_comprobante=40,
            consecutivo_comprobante=3,
            fecha="15/03/2025",
            codigo_cuenta="13050501",
            nit_tercero="900111222",
            debito=500000.0,
            credito=0.0,
            centro_costo="CC01",
        )
        lista = fila.a_lista()
        assert lista[0]  == 40            # col 1:  Tipo de comprobante
        assert lista[1]  == 3             # col 2:  Consecutivo comprobante
        assert lista[2]  == "15/03/2025"  # col 3:  Fecha de elaboración
        assert lista[5]  == "13050501"    # col 6:  Código cuenta contable
        assert lista[6]  == "900111222"   # col 7:  Identificación tercero
        assert lista[21] == 500000.0      # col 22: Débito
        assert lista[22] == 0.0           # col 23: Crédito
        assert lista[20] == "CC01"        # col 21: Código centro/subcentro de costos


# ---------------------------------------------------------------------------
# mapear_preasiento
# ---------------------------------------------------------------------------

class TestMapearPreasiento:
    def test_genera_una_fila_por_linea(self):
        p = _preasiento()
        filas = mapear_preasiento(p)
        assert len(filas) == len(p.lineas)

    def test_tipo_comprobante_correcto(self):
        p = _preasiento(clasificacion="FACTURA_COMPRA")
        filas = mapear_preasiento(p)
        assert all(f.tipo_comprobante == 50 for f in filas)

    def test_tipo_comprobante_venta(self):
        p = _preasiento(
            clasificacion="FACTURA_VENTA",
            lineas=[_linea(1, "13050501", debito=1190000.0, concepto="CxC")],
        )
        filas = mapear_preasiento(p)
        assert filas[0].tipo_comprobante == 40

    def test_fecha_formato_dd_mm_yyyy(self):
        p = _preasiento(fecha=datetime(2025, 6, 15))
        filas = mapear_preasiento(p)
        assert all(f.fecha == "15/06/2025" for f in filas)

    def test_fecha_vacia_cuando_none(self):
        p = _preasiento()
        p.fecha_emision = None
        filas = mapear_preasiento(p)
        assert all(f.fecha == "" for f in filas)

    def test_observaciones_incluye_folio_y_tercero(self):
        p = _preasiento(folio="2001", prefijo="FC")
        filas = mapear_preasiento(p)
        assert "FC-2001" in filas[0].observaciones
        assert "PROVEEDOR SA" in filas[0].observaciones

    def test_observaciones_sin_prefijo(self):
        p = _preasiento(folio="9999", prefijo="")
        filas = mapear_preasiento(p)
        assert "9999" in filas[0].observaciones
        assert "FC-" not in filas[0].observaciones

    def test_linea_pendiente_tiene_cuenta_vacia(self):
        p = _preasiento()
        filas = mapear_preasiento(p)
        pendientes = [f for f in filas if f.es_pendiente]
        assert len(pendientes) == 1
        assert pendientes[0].codigo_cuenta == ""

    def test_linea_pendiente_indica_pendiente_en_descripcion(self):
        p = _preasiento()
        filas = mapear_preasiento(p)
        pendientes = [f for f in filas if f.es_pendiente]
        assert "[PENDIENTE]" in pendientes[0].descripcion

    def test_linea_normal_tiene_cuenta_correcta(self):
        p = _preasiento()
        filas = mapear_preasiento(p)
        normales = [f for f in filas if not f.es_pendiente]
        cuentas = {f.codigo_cuenta for f in normales}
        assert "22050501" in cuentas
        assert "24081001" in cuentas

    def test_debito_y_credito_correctos(self):
        p = _preasiento()
        filas = mapear_preasiento(p)
        # Fila 0: crédito 1055750
        assert filas[0].credito == 1055750.0
        assert filas[0].debito == 0.0
        # Fila 2: débito 55750
        assert filas[2].debito == 55750.0
        assert filas[2].credito == 0.0

    def test_nit_tercero_propagado(self):
        p = _preasiento()
        filas = mapear_preasiento(p)
        assert all(f.nit_tercero == "800123456" for f in filas)

    def test_prefijo_y_folio_vacios_sin_vencimiento(self):
        # Sin cuentas_vencimiento, las cols 13-16 deben quedar vacías
        p = _preasiento(folio="2001", prefijo="FC")
        filas = mapear_preasiento(p)
        assert all(f.prefijo == "" for f in filas)
        assert all(f.folio == "" for f in filas)
        assert all(f.no_cuota == "" for f in filas)
        assert all(f.fecha_vencimiento == "" for f in filas)

    def test_observaciones_usa_prefijo_y_folio_del_documento(self):
        # Aunque cols 13-16 estén vacías, las observaciones siguen mostrando el doc
        p = _preasiento(folio="2001", prefijo="FC")
        filas = mapear_preasiento(p)
        assert "FC-2001" in filas[0].observaciones

    def test_vencimiento_rellena_cols_13_16(self):
        # Cuenta "22050501" marcada con vencimiento → cols 13-16 se rellenan
        p = _preasiento(
            fecha=datetime(2026, 1, 5),
            lineas=[
                _linea(1, "22050501", credito=1000.0, concepto="CxP"),  # con vencimiento
                _linea(2, "51050501", debito=1000.0, concepto="Gasto"),  # sin vencimiento
            ],
        )
        filas = mapear_preasiento(
            p,
            consecutivo_comprobante=7,
            cuentas_vencimiento=frozenset({"22050501"}),
        )
        # Línea con vencimiento
        assert filas[0].prefijo == "CC"
        assert filas[0].folio == "7"
        assert filas[0].no_cuota == "1"
        assert filas[0].fecha_vencimiento == "05/01/2026"
        # Línea sin vencimiento
        assert filas[1].prefijo == ""
        assert filas[1].folio == ""
        assert filas[1].no_cuota == ""
        assert filas[1].fecha_vencimiento == ""


# ---------------------------------------------------------------------------
# mapear_lote
# ---------------------------------------------------------------------------

class TestMapearLote:
    def test_sin_preasientos_retorna_vacio(self):
        assert mapear_lote([]) == []

    def test_suma_filas_de_todos_los_preasientos(self):
        p1 = _preasiento(cufe="CUFE-A", lineas=[_linea(1, "11050501", debito=100)])
        p2 = _preasiento(cufe="CUFE-B", lineas=[_linea(1, "22050501", credito=100), _linea(2, "51050501", debito=100)])
        filas = mapear_lote([p1, p2])
        assert len(filas) == 3

    def test_consecutivo_comprobante_por_preasiento(self):
        # Ambos preasientos usan la fecha por defecto: 2025-03-01 → prefijo 202503
        # misma clasificacion → misma secuencia
        p1 = _preasiento(cufe="CUFE-A", lineas=[_linea(1, "11050501", debito=100)])
        p2 = _preasiento(cufe="CUFE-B", lineas=[_linea(1, "22050501", credito=100), _linea(2, "51050501", debito=100)])
        filas = mapear_lote([p1, p2])
        assert filas[0].consecutivo_comprobante == 20250301  # primer comprobante de mar-2025
        assert filas[1].consecutivo_comprobante == 20250302  # segundo comprobante de mar-2025
        assert filas[2].consecutivo_comprobante == 20250302  # misma línea del segundo

    def test_consecutivo_comprobante_reinicia_por_mes(self):
        # Bug fix #2: al ordenar por fecha, mar-15 < mar-20, entonces:
        #   mar-15 → 20250301, abr-01 → 20250401, mar-20 → 20250302
        # Aunque se insertan [mar, abr, mar2], el sort pone mar2 al final en su mes.
        p_mar = _preasiento(cufe="CUFE-MAR", fecha=datetime(2025, 3, 15),
                            lineas=[_linea(1, "11050501", debito=100)])
        p_abr = _preasiento(cufe="CUFE-ABR", fecha=datetime(2025, 4, 1),
                            lineas=[_linea(1, "22050501", credito=100)])
        p_mar2 = _preasiento(cufe="CUFE-MAR2", fecha=datetime(2025, 3, 20),
                             lineas=[_linea(1, "51050501", debito=200)])
        filas = mapear_lote([p_mar, p_abr, p_mar2])
        # Después del sort: mar-15, mar-20, abr-01
        assert filas[0].consecutivo_comprobante == 20250301  # mar-15: 1.º de marzo
        assert filas[1].consecutivo_comprobante == 20250302  # mar-20: 2.º de marzo
        assert filas[2].consecutivo_comprobante == 20250401  # abr-01: 1.º de abril

    def test_consecutivo_independiente_por_tipo_comprobante(self):
        # Bug fix #1: FACTURA_COMPRA (comp=50) y NOMINA (comp=112) en el mismo
        # mes deben tener secuencias independientes, ambas empezando en 01.
        p_compra = _preasiento(
            cufe="CUFE-COMPRA",
            clasificacion="FACTURA_COMPRA",
            fecha=datetime(2025, 3, 1),
            lineas=[_linea(1, "22050501", credito=100)],
        )
        p_nomina = _preasiento(
            cufe="CUFE-NOMINA",
            clasificacion="NOMINA",
            fecha=datetime(2025, 3, 5),
            lineas=[_linea(1, "25050501", credito=200)],
        )
        p_compra2 = _preasiento(
            cufe="CUFE-COMPRA2",
            clasificacion="FACTURA_COMPRA",
            fecha=datetime(2025, 3, 10),
            lineas=[_linea(1, "22050501", credito=300)],
        )
        filas = mapear_lote([p_compra, p_nomina, p_compra2])
        # FACTURA_COMPRA: primer → 20250301, segundo → 20250302
        # NOMINA:         primer → 20250301 (secuencia propia)
        assert filas[0].consecutivo_comprobante == 20250301  # compra: 1.ª
        assert filas[1].consecutivo_comprobante == 20250301  # nomina: 1.ª (independiente)
        assert filas[2].consecutivo_comprobante == 20250302  # compra: 2.ª

    def test_vencimiento_en_lote_con_df_cuentas(self):
        # df_cuentas con "22050501" como cuenta con vencimiento en proveedores
        df_cuentas = pd.DataFrame({
            "Código": ["22050501", "51050501"],
            "Maneja vencimientos": ["Con vencimiento en proveedores", "Sin vencimiento"],
        })
        p = _preasiento(
            cufe="CUFE-VEN",
            fecha=datetime(2026, 1, 5),
            lineas=[
                _linea(1, "22050501", credito=1000.0, concepto="CxP"),
                _linea(2, "51050501", debito=1000.0, concepto="Gasto"),
            ],
        )
        filas = mapear_lote([p], df_cuentas=df_cuentas)
        fila_cxp   = next(f for f in filas if f.codigo_cuenta == "22050501")
        fila_gasto = next(f for f in filas if f.codigo_cuenta == "51050501")
        assert fila_cxp.prefijo == "CC"
        assert fila_cxp.no_cuota == "1"
        assert fila_cxp.fecha_vencimiento == "05/01/2026"
        assert fila_gasto.prefijo == ""
        assert fila_gasto.no_cuota == ""

    def test_incluir_pendientes_true(self):
        p = _preasiento()  # tiene 1 pendiente
        filas = mapear_lote([p], incluir_pendientes=True)
        pendientes = [f for f in filas if f.es_pendiente]
        assert len(pendientes) == 1

    def test_incluir_pendientes_false_omite_pendientes(self):
        p = _preasiento()  # tiene 1 pendiente
        filas = mapear_lote([p], incluir_pendientes=False)
        pendientes = [f for f in filas if f.es_pendiente]
        assert len(pendientes) == 0

    def test_incluir_pendientes_false_mantiene_normales(self):
        p = _preasiento()  # 3 líneas: 1 pendiente + 2 normales
        filas = mapear_lote([p], incluir_pendientes=False)
        assert len(filas) == 2


# ---------------------------------------------------------------------------
# partir_en_chunks
# ---------------------------------------------------------------------------

class TestPartirEnChunks:
    def _filas(self, n: int) -> list[FilaSiigo]:
        return [
            FilaSiigo(50, i, "01/01/2025", "11050501", "900", debito=float(i))
            for i in range(n)
        ]

    def test_sin_filas_retorna_lista_vacia(self):
        assert partir_en_chunks([], 500) == []

    def test_menos_de_500_un_solo_chunk(self):
        chunks = partir_en_chunks(self._filas(300), 500)
        assert len(chunks) == 1
        assert len(chunks[0]) == 300

    def test_exactamente_500_un_solo_chunk(self):
        chunks = partir_en_chunks(self._filas(500), 500)
        assert len(chunks) == 1

    def test_501_dos_chunks(self):
        chunks = partir_en_chunks(self._filas(501), 500)
        assert len(chunks) == 2
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 1

    def test_1000_dos_chunks(self):
        chunks = partir_en_chunks(self._filas(1000), 500)
        assert len(chunks) == 2
        assert all(len(c) == 500 for c in chunks)

    def test_1001_tres_chunks(self):
        chunks = partir_en_chunks(self._filas(1001), 500)
        assert len(chunks) == 3

    def test_tamano_cero_lanza_error(self):
        with pytest.raises(ValueError):
            partir_en_chunks(self._filas(10), 0)

    def test_total_filas_preservado(self):
        filas = self._filas(1234)
        chunks = partir_en_chunks(filas, 500)
        total = sum(len(c) for c in chunks)
        assert total == 1234
