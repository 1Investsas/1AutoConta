"""
Tests del motor de aprendizaje generalizado (app/aprendizaje.py), de su capa
de datos (app/database.py) y del importador de conocimiento externo
(app/aprendizaje_importador.py).
"""

import pandas as pd
import pytest

from app import aprendizaje as ap
from app.aprendizaje_importador import importar_conocimiento
from app.database import (
    inicializar_db,
    estadisticas_aprendizaje,
    eliminar_patron_aprendido,
    listar_importaciones_conocimiento,
    listar_patrones_aprendidos,
    registrar_importacion_conocimiento,
)


@pytest.fixture
def db_tmp(tmp_path):
    """Base de datos SQLite temporal en disco (tmp_path de pytest)."""
    db_path = str(tmp_path / "test_aprendizaje.db")
    inicializar_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Normalización y tokenización
# ---------------------------------------------------------------------------

class TestNormalizacion:
    def test_mayusculas_sin_tildes(self):
        assert ap.normalizar_texto("Pago Nómina eléctrica") == "PAGO NOMINA ELECTRICA"

    def test_quita_numeros_sueltos(self):
        """Números de factura/fechas varían por documento: no son señal."""
        assert ap.normalizar_texto("PAGO FACT 12345 DEL 2026") == "PAGO FACT DEL"

    def test_conserva_tokens_alfanumericos(self):
        assert "4X1000" in ap.normalizar_texto("IMPTO GOBIERNO 4X1000")

    def test_vacio(self):
        assert ap.normalizar_texto("") == ""
        assert ap.normalizar_texto(None) == ""

    def test_tokenizar_filtra_stopwords_y_duplica(self):
        tokens = ap.tokenizar("Pago de la nómina de nómina")
        assert tokens == ["PAGO", "NOMINA"]


# ---------------------------------------------------------------------------
# Aprender y predecir
# ---------------------------------------------------------------------------

class TestAprenderPredecir:
    def test_sin_datos_retorna_none(self, db_tmp):
        assert ap.predecir("banco", "cuenta", "PAGO ARRIENDO BODEGA", db_tmp) is None

    def test_prediccion_exacta(self, db_tmp):
        """El mismo texto (aunque cambien números) predice con origen 'exacto'."""
        ap.aprender("banco", "cuenta", "PAGO ARRIENDO BODEGA 111", "51201001", db_tmp)
        pred = ap.predecir("banco", "cuenta", "PAGO ARRIENDO BODEGA 999", db_tmp)
        assert pred is not None
        assert pred.valor == "51201001"
        assert pred.origen == "exacto"

    def test_prediccion_por_texto_generaliza(self, db_tmp):
        """Un texto NUNCA visto hereda por tokens compartidos (Naive Bayes)."""
        ap.aprender("banco", "cuenta", "PAGO NOMINA ELECTRONICA ACME", "51050501", db_tmp)
        ap.aprender("banco", "cuenta", "PAGO NOMINA ELECTRONICA ACME", "51050501", db_tmp)
        pred = ap.predecir("banco", "cuenta", "PAGO NOMINA BANCOLOMBIA", db_tmp)
        assert pred is not None
        assert pred.valor == "51050501"
        assert pred.origen == "texto"
        assert 0 < pred.confianza <= 1

    def test_valor_mas_confirmado_gana_en_exacto(self, db_tmp):
        ap.aprender("caja", "cuenta", "COMPRA CAFETERIA", "51951001", db_tmp)
        ap.aprender("caja", "cuenta", "COMPRA CAFETERIA", "51951001", db_tmp)
        ap.aprender("caja", "cuenta", "COMPRA CAFETERIA", "62050101", db_tmp)
        pred = ap.predecir("caja", "cuenta", "COMPRA CAFETERIA", db_tmp)
        assert pred.valor == "51951001"
        assert pred.confianza == pytest.approx(2 / 3)

    def test_no_aprende_pendiente_ni_vacio(self, db_tmp):
        assert ap.aprender("banco", "cuenta", "ALGO", "[PENDIENTE]", db_tmp) is False
        assert ap.aprender("banco", "cuenta", "ALGO", "", db_tmp) is False
        assert ap.aprender("banco", "cuenta", "", "51050501", db_tmp) is False
        assert ap.predecir("banco", "cuenta", "ALGO", db_tmp) is None

    def test_coincidencia_debil_no_pasa_umbral(self, db_tmp):
        """Un solo token compartido entre muchos no alcanza la confianza mínima."""
        ap.aprender("banco", "cuenta", "PAGO NOMINA", "51050501", db_tmp)
        pred = ap.predecir(
            "banco", "cuenta",
            "PAGO PROVEEDOR INTERNACIONAL MERCANCIA IMPORTADA BODEGA", db_tmp,
        )
        assert pred is None

    def test_fallback_conocimiento_general(self, db_tmp):
        """Lo importado al módulo 'general' responde para cualquier módulo."""
        ap.aprender("general", "cuenta", "SERVICIO VIGILANCIA", "51101001", db_tmp)
        pred = ap.predecir("banco", "cuenta", "SERVICIO VIGILANCIA", db_tmp)
        assert pred is not None
        assert pred.valor == "51101001"
        assert pred.modulo == "general"

    def test_modulo_especifico_tiene_prioridad(self, db_tmp):
        ap.aprender("general", "cuenta", "SERVICIO ASEO", "51101002", db_tmp)
        ap.aprender("banco", "cuenta", "SERVICIO ASEO", "51109999", db_tmp)
        pred = ap.predecir("banco", "cuenta", "SERVICIO ASEO", db_tmp)
        assert pred.valor == "51109999"
        assert pred.modulo == "banco"

    def test_predecir_campos(self, db_tmp):
        ap.aprender("caja", "cuenta", "TAXI GERENCIA", "51553501", db_tmp)
        ap.aprender("caja", "nit_tercero", "TAXI GERENCIA", "901111222", db_tmp)
        preds = ap.predecir_campos("caja", "TAXI GERENCIA",
                                   ["cuenta", "nit_tercero"], db_tmp)
        assert preds["cuenta"].valor == "51553501"
        assert preds["nit_tercero"].valor == "901111222"

    def test_aprender_lote(self, db_tmp):
        n = ap.aprender_lote([
            {"modulo": "banco", "campo": "cuenta",
             "texto": "INTERESES AHORROS", "valor": "42100501"},
            {"modulo": "banco", "campo": "cuenta",
             "texto": "", "valor": "42100501"},          # inválida: sin texto
            {"modulo": "banco", "campo": "cuenta",
             "texto": "CUOTA MANEJO", "valor": "[PENDIENTE]"},  # inválida
        ], db_tmp)
        assert n == 1
        assert ap.predecir("banco", "cuenta", "INTERESES AHORROS", db_tmp).valor == "42100501"


# ---------------------------------------------------------------------------
# Capa de datos: estadísticas, listado, eliminación
# ---------------------------------------------------------------------------

class TestCapaDatos:
    def test_estadisticas_y_listado(self, db_tmp):
        ap.aprender("banco", "cuenta", "PAGO ARRIENDO", "51201001", db_tmp)
        ap.aprender("banco", "cuenta", "PAGO ARRIENDO", "51201001", db_tmp)
        ap.aprender("caja", "nit_tercero", "TAXI GERENCIA", "901111222", db_tmp)

        stats = estadisticas_aprendizaje(db_tmp)
        assert stats["total_patrones"] == 2
        assert stats["total_confirmaciones"] == 3
        assert stats["vocabulario"] > 0
        modulos = {m["modulo"] for m in stats["por_modulo"]}
        assert modulos == {"banco", "caja"}

        patrones = listar_patrones_aprendidos(db_tmp)
        assert len(patrones) == 2
        assert patrones[0]["usos"] == 2  # ordenado por usos DESC

        solo_banco = listar_patrones_aprendidos(db_tmp, modulo="banco")
        assert len(solo_banco) == 1
        con_filtro = listar_patrones_aprendidos(db_tmp, q="ARRIENDO")
        assert len(con_filtro) == 1

    def test_eliminar_patron(self, db_tmp):
        ap.aprender("banco", "cuenta", "PAGO ARRIENDO", "51201001", db_tmp)
        patron = listar_patrones_aprendidos(db_tmp)[0]
        eliminar_patron_aprendido(patron["id"], db_tmp)
        assert listar_patrones_aprendidos(db_tmp) == []

    def test_historial_importaciones_conocimiento(self, db_tmp):
        registrar_importacion_conocimiento(
            "movimiento_siigo.xlsx", "general", 120, 240, db_path=db_tmp,
        )
        historial = listar_importaciones_conocimiento(db_tmp)
        assert len(historial) == 1
        assert historial[0]["archivo_nombre"] == "movimiento_siigo.xlsx"
        assert historial[0]["aprendidos"] == 240
        assert historial[0]["estado"] == "completada"


# ---------------------------------------------------------------------------
# Importador de conocimiento externo
# ---------------------------------------------------------------------------

def _df_siigo() -> pd.DataFrame:
    """Simula un exporte de movimiento contable (encabezados en la fila 2)."""
    return pd.DataFrame([
        ["Movimiento contable", "", "", ""],
        ["Cuenta contable", "Nit", "Descripción", "Valor"],
        ["51100501.0", "800123456-1", "SERVICIO VIGILANCIA SEDE NORTE", "100"],
        ["51100501", "800123456", "SERVICIO VIGILANCIA SEDE SUR", "120"],
        ["23652501", "830999888", "HONORARIOS ABOGADO EXTERNO", "300"],
        ["", "", "", ""],  # fila vacía: se ignora
    ])


class TestImportadorConocimiento:
    def test_importa_y_predice(self, db_tmp):
        resumen = importar_conocimiento(_df_siigo(), db_tmp, "general")
        assert resumen["filas"] == 3
        assert resumen["aprendidos"] == 6  # cuenta + NIT por fila
        assert resumen["columnas"]["texto"] == "Descripción"

        # La cuenta se limpia de '.0' y el NIT pierde el dígito de verificación.
        pred = ap.predecir("banco", "cuenta", "SERVICIO VIGILANCIA SEDE NORTE", db_tmp)
        assert pred.valor == "51100501"
        pred = ap.predecir("caja", "nit_tercero", "HONORARIOS ABOGADO EXTERNO", db_tmp)
        assert pred.valor == "830999888"

    def test_archivo_excel_en_disco(self, db_tmp, tmp_path):
        ruta = tmp_path / "conocimiento.xlsx"
        _df_siigo().to_excel(ruta, header=False, index=False)
        resumen = importar_conocimiento(ruta, db_tmp)
        assert resumen["aprendidos"] == 6

    def test_archivo_csv_en_disco(self, db_tmp, tmp_path):
        ruta = tmp_path / "conocimiento.csv"
        _df_siigo().to_csv(ruta, header=False, index=False)
        resumen = importar_conocimiento(ruta, db_tmp)
        assert resumen["aprendidos"] == 6

    def test_solo_columna_nit_tambien_sirve(self, db_tmp):
        df = pd.DataFrame([
            ["Nombre del tercero", "Identificación"],
            ["TRANSPORTES EL VELOZ SAS", "900555444"],
        ])
        resumen = importar_conocimiento(df, db_tmp)
        assert resumen["aprendidos"] == 1
        pred = ap.predecir("radian", "nit_tercero", "TRANSPORTES EL VELOZ SAS", db_tmp)
        assert pred.valor == "900555444"

    def test_columnas_no_reconocidas_lanza_error(self, db_tmp):
        df = pd.DataFrame([["A", "B"], ["1", "2"]])
        with pytest.raises(ValueError):
            importar_conocimiento(df, db_tmp)


# ---------------------------------------------------------------------------
# Integración con RADIAN (fallback del motor en enriquecer_con_sugerencias)
# ---------------------------------------------------------------------------

class TestIntegracionRadian:
    def test_fallback_para_tercero_nuevo(self, db_tmp):
        """Sin historial exacto, el motor de texto rellena el [PENDIENTE]."""
        from app.models import LineaContable, PreasientoContable
        from app.sugerencias import enriquecer_con_sugerencias

        # Conocimiento previo: otro tercero con nombre parecido.
        ap.aprender("radian", "cuenta_base",
                    "FACTURA_COMPRA TRANSPORTES RAPIDOS DEL NORTE SAS",
                    "51354001", db_tmp)
        ap.aprender("radian", "cuenta_base",
                    "FACTURA_COMPRA TRANSPORTES UNIDOS SA",
                    "51354001", db_tmp)

        linea = LineaContable(
            cufe="CUFE-NUEVO", numero_linea=1, cuenta="[PENDIENTE]",
            descripcion_cuenta="Gasto/Costo", debito=1000.0, credito=0.0,
            concepto="Base gravable", tercero_nit="999888777",
            tercero_nombre="TRANSPORTES LA COSTA SAS", es_pendiente=True,
        )
        p = PreasientoContable(
            cufe="CUFE-NUEVO", tipo_documento="Factura electrónica",
            clasificacion="FACTURA_COMPRA", codigo_comprobante="50",
            titulo_comprobante="Facturas de compra", fecha_emision=None,
            folio="1", prefijo="FC", tercero_nit="999888777",
            tercero_nombre="TRANSPORTES LA COSTA SAS", tercero_encontrado=True,
            total=1000.0, base_gravable=1000.0, lineas=[linea], cuadra=False,
            excepciones=["1 línea(s) con cuenta [PENDIENTE]"],
        )

        enriquecer_con_sugerencias([p], db_tmp)
        assert linea.cuenta == "51354001"
        assert linea.es_pendiente is False
        assert linea.es_sugerida is True
