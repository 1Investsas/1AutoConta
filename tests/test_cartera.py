"""
Tests del módulo Cartera y Cuentas por Pagar (app/database/cartera.py).

Cubre: sincronización desde documentos RADIAN (idempotente), condiciones de
pago con cuotas (una fecha de vencimiento por cuota), abonos FIFO, aplicación
de pagos desde Flujos Directos (idempotente por referencia), estados y resumen.
"""

from datetime import date, timedelta

import pytest

from app.database import (
    inicializar_db,
    registrar_documento,
    registrar_obligacion,
    obtener_obligacion,
    listar_obligaciones,
    actualizar_datos_obligacion,
    anular_obligacion,
    sincronizar_desde_documentos,
    listar_cuotas,
    definir_condiciones_pago,
    registrar_pago,
    listar_pagos,
    pagos_ya_aplicados,
    aplicar_pago_tercero,
    aplicar_pagos_flujos_directos,
    resumen_cartera,
)


@pytest.fixture
def db_tmp(tmp_path):
    """Base de datos SQLite temporal en disco."""
    db_path = str(tmp_path / "test_cartera.db")
    inicializar_db(db_path)
    return db_path


def _doc(db_tmp, cufe, clasif, total, nit_e="900111222", nom_e="PROVEEDOR SA",
         nit_r="800333444", nom_r="CLIENTE SAS"):
    registrar_documento(
        cufe=cufe, tipo_documento="Factura electrónica", clasificacion=clasif,
        folio="10", prefijo="FE", nit_emisor=nit_e, nombre_emisor=nom_e,
        nit_receptor=nit_r, nombre_receptor=nom_r, total=total,
        fecha_emision=None, archivo_origen="radian.xlsx", db_path=db_tmp,
    )


class TestSincronizacion:
    def test_crea_cxc_y_cxp_desde_documentos(self, db_tmp):
        _doc(db_tmp, "C1", "FACTURA_VENTA", 1_000_000)
        _doc(db_tmp, "C2", "FACTURA_COMPRA", 500_000)
        _doc(db_tmp, "C3", "DOCUMENTO_SOPORTE", 200_000)
        _doc(db_tmp, "C4", "NOMINA", 300_000)  # excluida de la cartera

        r = sincronizar_desde_documentos(db_tmp)
        assert r == {"creadas": 3, "revisadas": 3}

        cxc = listar_obligaciones(db_tmp, tipo="cxc")
        cxp = listar_obligaciones(db_tmp, tipo="cxp")
        assert len(cxc) == 1 and len(cxp) == 2
        # Venta: el tercero es el receptor; compra: el emisor.
        assert cxc[0]["nit_tercero"] == "800333444"
        assert all(o["nit_tercero"] == "900111222" for o in cxp)
        assert cxc[0]["saldo"] == cxc[0]["valor_total"] == 1_000_000
        assert cxc[0]["estado"] == "pendiente"
        assert cxc[0]["documento"] == "FE-10"

    def test_es_idempotente_y_conserva_lo_trabajado(self, db_tmp):
        _doc(db_tmp, "C1", "FACTURA_COMPRA", 500_000)
        sincronizar_desde_documentos(db_tmp)
        oblig = listar_obligaciones(db_tmp)[0]
        registrar_pago(oblig["id"], 100_000, db_path=db_tmp)

        r = sincronizar_desde_documentos(db_tmp)
        assert r["creadas"] == 0
        oblig = obtener_obligacion(oblig["id"], db_tmp)
        assert oblig["saldo"] == 400_000  # el abono no se pierde
        assert len(listar_obligaciones(db_tmp)) == 1


class TestCondicionesPago:
    def _oblig(self, db_tmp, total=600_000):
        return registrar_obligacion(
            "cxp", "900111222", total, nombre_tercero="PROVEEDOR SA",
            cufe="CX", origen="radian", db_path=db_tmp,
        )

    def test_contado_fija_una_fecha(self, db_tmp):
        oid = self._oblig(db_tmp)
        definir_condiciones_pago(oid, "contado", fecha_vencimiento="2026-08-01",
                                 db_path=db_tmp)
        o = obtener_obligacion(oid, db_tmp)
        assert o["condicion_pago"] == "contado"
        assert o["fecha_vencimiento"] == "2026-08-01"
        assert listar_cuotas(oid, db_tmp) == []

    def test_credito_crea_cuotas_con_vencimiento_propio(self, db_tmp):
        oid = self._oblig(db_tmp)
        definir_condiciones_pago(oid, "credito", cuotas=[
            {"fecha_vencimiento": "2026-08-01", "valor": 200_000},
            {"fecha_vencimiento": "2026-09-01", "valor": 200_000},
            {"fecha_vencimiento": "2026-10-01", "valor": 200_000},
        ], db_path=db_tmp)
        o = obtener_obligacion(oid, db_tmp)
        cuotas = listar_cuotas(oid, db_tmp)
        assert o["condicion_pago"] == "credito" and o["num_cuotas"] == 3
        assert o["fecha_vencimiento"] == "2026-08-01"  # primera cuota
        assert [c["fecha_vencimiento"] for c in cuotas] == \
            ["2026-08-01", "2026-09-01", "2026-10-01"]
        assert all(c["saldo"] == c["valor"] == 200_000 for c in cuotas)

    def test_credito_exige_que_las_cuotas_cuadren(self, db_tmp):
        oid = self._oblig(db_tmp)
        with pytest.raises(ValueError, match="igualar el valor"):
            definir_condiciones_pago(oid, "credito", cuotas=[
                {"fecha_vencimiento": "2026-08-01", "valor": 100_000},
            ], db_path=db_tmp)
        with pytest.raises(ValueError, match="fecha de vencimiento"):
            definir_condiciones_pago(oid, "credito", cuotas=[
                {"fecha_vencimiento": "", "valor": 600_000},
            ], db_path=db_tmp)
        with pytest.raises(ValueError, match="al menos una cuota"):
            definir_condiciones_pago(oid, "credito", cuotas=[], db_path=db_tmp)

    def test_redefinir_cuotas_reaplica_lo_abonado(self, db_tmp):
        oid = self._oblig(db_tmp)
        registrar_pago(oid, 250_000, db_path=db_tmp)
        definir_condiciones_pago(oid, "credito", cuotas=[
            {"fecha_vencimiento": "2026-08-01", "valor": 300_000},
            {"fecha_vencimiento": "2026-09-01", "valor": 300_000},
        ], db_path=db_tmp)
        cuotas = listar_cuotas(oid, db_tmp)
        # El abono previo de 250k cubre parcialmente la primera cuota (FIFO).
        assert cuotas[0]["saldo"] == 50_000 and cuotas[0]["estado"] == "parcial"
        assert cuotas[1]["saldo"] == 300_000 and cuotas[1]["estado"] == "pendiente"


class TestPagos:
    def _oblig_con_cuotas(self, db_tmp):
        oid = registrar_obligacion("cxp", "900111222", 600_000, cufe="CP",
                                   db_path=db_tmp)
        definir_condiciones_pago(oid, "credito", cuotas=[
            {"fecha_vencimiento": "2026-08-01", "valor": 300_000},
            {"fecha_vencimiento": "2026-09-01", "valor": 300_000},
        ], db_path=db_tmp)
        return oid

    def test_abono_fifo_sobre_cuotas(self, db_tmp):
        oid = self._oblig_con_cuotas(db_tmp)
        aplicado = registrar_pago(oid, 400_000, fecha="2026-07-15",
                                  origen="manual", db_path=db_tmp)
        assert aplicado == 400_000
        o = obtener_obligacion(oid, db_tmp)
        cuotas = listar_cuotas(oid, db_tmp)
        assert o["saldo"] == 200_000 and o["estado"] == "parcial"
        assert cuotas[0]["estado"] == "pagada" and cuotas[0]["saldo"] == 0
        assert cuotas[1]["estado"] == "parcial" and cuotas[1]["saldo"] == 200_000

    def test_no_aplica_mas_que_el_saldo(self, db_tmp):
        oid = self._oblig_con_cuotas(db_tmp)
        assert registrar_pago(oid, 1_000_000, db_path=db_tmp) == 600_000
        o = obtener_obligacion(oid, db_tmp)
        assert o["saldo"] == 0 and o["estado"] == "pagada"
        # Un pago adicional sobre una obligación pagada no aplica nada.
        assert registrar_pago(oid, 50_000, db_path=db_tmp) == 0
        assert len(listar_pagos(oid, db_tmp)) == 1

    def test_pago_a_tercero_reparte_por_vencimiento(self, db_tmp):
        # Dos obligaciones del mismo tercero: vence primero la segunda creada.
        o1 = registrar_obligacion("cxp", "900111222", 100_000, cufe="A",
                                  fecha_vencimiento="2026-12-01", db_path=db_tmp)
        o2 = registrar_obligacion("cxp", "900111222", 100_000, cufe="B",
                                  fecha_vencimiento="2026-08-01", db_path=db_tmp)
        aplicado = aplicar_pago_tercero("900.111.222", "cxp", 150_000,
                                        origen="banco", db_path=db_tmp)
        assert aplicado == 150_000
        assert obtener_obligacion(o2, db_tmp)["estado"] == "pagada"
        assert obtener_obligacion(o1, db_tmp)["saldo"] == 50_000

    def test_valores_invalidos_no_aplican(self, db_tmp):
        oid = self._oblig_con_cuotas(db_tmp)
        assert registrar_pago(oid, 0, db_path=db_tmp) == 0
        assert registrar_pago(oid, -100, db_path=db_tmp) == 0
        assert aplicar_pago_tercero("", "cxp", 100, db_path=db_tmp) == 0


class TestFlujosDirectos:
    def test_aplica_ingresos_a_cxc_y_egresos_a_cxp(self, db_tmp):
        cxc = registrar_obligacion("cxc", "800333444", 900_000, cufe="V1",
                                   db_path=db_tmp)
        cxp = registrar_obligacion("cxp", "900111222", 500_000, cufe="P1",
                                   db_path=db_tmp)
        res = aplicar_pagos_flujos_directos([
            {"nit": "800333444", "valor": 900_000, "sentido": "ingreso",
             "fecha": "2026-07-10", "detalle": "consignación cliente"},
            {"nit": "900111222", "valor": 200_000, "sentido": "egreso",
             "fecha": "2026-07-11", "detalle": "pago proveedor"},
            {"nit": "999999999", "valor": 50_000, "sentido": "egreso"},  # sin obligación
        ], "banco", "banco:7", db_tmp)
        assert res["omitido"] is False
        assert res["n_pagos"] == 2 and res["aplicado"] == 1_100_000
        assert obtener_obligacion(cxc, db_tmp)["estado"] == "pagada"
        assert obtener_obligacion(cxp, db_tmp)["saldo"] == 300_000
        pagos = listar_pagos(cxp, db_tmp)
        assert pagos[0]["origen"] == "banco" and pagos[0]["referencia"] == "banco:7"

    def test_idempotente_por_referencia(self, db_tmp):
        cxp = registrar_obligacion("cxp", "900111222", 500_000, cufe="P1",
                                   db_path=db_tmp)
        movs = [{"nit": "900111222", "valor": 100_000, "sentido": "egreso"}]
        aplicar_pagos_flujos_directos(movs, "caja", "caja:3", db_tmp)
        assert pagos_ya_aplicados("caja", "caja:3", db_tmp)

        res = aplicar_pagos_flujos_directos(movs, "caja", "caja:3", db_tmp)
        assert res == {"aplicado": 0.0, "n_pagos": 0, "omitido": True}
        assert obtener_obligacion(cxp, db_tmp)["saldo"] == 400_000
        # Otra referencia (otro cierre/proceso) sí aplica.
        res = aplicar_pagos_flujos_directos(movs, "caja", "caja:4", db_tmp)
        assert res["n_pagos"] == 1
        assert obtener_obligacion(cxp, db_tmp)["saldo"] == 300_000


class TestListadoYResumen:
    def test_vencida_y_dias(self, db_tmp):
        ayer = (date.today() - timedelta(days=1)).isoformat()
        en_10 = (date.today() + timedelta(days=10)).isoformat()
        registrar_obligacion("cxp", "1", 100_000, cufe="A",
                             fecha_vencimiento=ayer, db_path=db_tmp)
        registrar_obligacion("cxc", "2", 200_000, cufe="B",
                             fecha_vencimiento=en_10, db_path=db_tmp)
        obs = {o["cufe"]: o for o in listar_obligaciones(db_tmp)}
        assert obs["A"]["vencida"] is True and obs["A"]["dias_vencimiento"] == -1
        assert obs["B"]["vencida"] is False and obs["B"]["dias_vencimiento"] == 10

        r = resumen_cartera(db_tmp)
        assert r["por_pagar"] == 100_000 and r["vencido_cxp"] == 100_000
        assert r["por_cobrar"] == 200_000 and r["vencido_cxc"] == 0
        assert r["proximos_30"] == 200_000

    def test_anulada_no_aparece_ni_recibe_pagos(self, db_tmp):
        oid = registrar_obligacion("cxp", "1", 100_000, cufe="A", db_path=db_tmp)
        anular_obligacion(oid, db_tmp)
        assert listar_obligaciones(db_tmp) == []
        assert registrar_pago(oid, 50_000, db_path=db_tmp) == 0

    def test_actualizar_datos_gestion(self, db_tmp):
        oid = registrar_obligacion("cxp", "1", 100_000, cufe="A", db_path=db_tmp)
        actualizar_datos_obligacion(
            oid, db_path=db_tmp,
            contacto_nombre="Ana Pérez", contacto_telefono="3001234567",
            contacto_correo="ana@proveedor.com", fuente_recursos="Cta ahorros 123",
        )
        o = obtener_obligacion(oid, db_tmp)
        assert o["contacto_nombre"] == "Ana Pérez"
        assert o["fuente_recursos"] == "Cta ahorros 123"
        # Campos no pasados no se tocan; campos no permitidos se ignoran.
        actualizar_datos_obligacion(oid, db_path=db_tmp, contacto_telefono="311",
                                    saldo=0)
        o = obtener_obligacion(oid, db_tmp)
        assert o["contacto_telefono"] == "311"
        assert o["contacto_nombre"] == "Ana Pérez"
        assert o["saldo"] == 100_000

    def test_registrar_sin_duplicar_por_cufe(self, db_tmp):
        assert registrar_obligacion("cxp", "1", 100, cufe="A", db_path=db_tmp)
        assert registrar_obligacion("cxp", "1", 100, cufe="A", db_path=db_tmp) is None
        assert registrar_obligacion("xxx", "1", 100, db_path=db_tmp) is None
        assert registrar_obligacion("cxp", "", 100, db_path=db_tmp) is None


class TestParserMontos:
    """Formatos de monto del formulario web (separadores de miles colombianos)."""

    def test_a_float(self):
        from app.web.routes.cartera import _a_float

        assert _a_float("250.000") == 250_000       # un punto + 3 dígitos = miles
        assert _a_float("250,000") == 250_000
        assert _a_float("5.000.000") == 5_000_000
        assert _a_float("1.234.567,89") == 1_234_567.89
        assert _a_float("1,234,567.89") == 1_234_567.89
        assert _a_float("250,5") == 250.5           # decimal explícito
        assert _a_float("250.55") == 250.55
        assert _a_float("$ 1.500.000") == 1_500_000
        assert _a_float("800000") == 800_000
        assert _a_float("") is None
        assert _a_float("abc") is None
