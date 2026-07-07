"""
Tests del detector automático del formato de movimientos bancarios.

Cubre la detección heurística (app/banco/detector_formato.py) y el endpoint
POST /empresas/detectar-formato-banco que usa el asistente del formulario de
empresas para llenar los campos del formato automáticamente.
"""

import io
import os

import pytest

os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-fixed-key-no-dev-1234567890")

from app.banco.detector_formato import detectar_formato  # noqa: E402


CSV_DEFAULT = (
    "551-000068-95, 551, , 20260430, , 1.77, 2999, ABONO INTERESES AHORROS, 0,\n"
    "551-000068-95, 551, , 20260430, , -470036.00, 3160, TRANSFERENCIA CTA SUC VIRTUAL, 0,\n"
    "551-000068-95, 551, , 20260430, , -1880.14, 3339, IMPTO GOBIERNO 4X1000, 0,\n"
    "551-000068-95, 551, , 20260416, , -526500.00, 7513, PAGO PSE ENLACE OPERATIVO S.A, 0,\n"
    "551-000068-95, 99, , 20260409, , -1783.90, 9183, COMPRA INTL  Microsoft G151211, 0,\n"
    "551-000068-95, 551, , 20260408, , 3.18, 2999, ABONO INTERESES AHORROS, 0,\n"
)

CSV_PUNTOYCOMA = (
    "cuenta;codigo;fecha;valor;detalle;descripcion\n"
    "551-000068-95;551;30/04/2026;-1.000.000,50;2999;PAGO PROVEEDOR ACME\n"
    "551-000068-95;551;16/04/2026;500.000,00;3160;CONSIGNACION CLIENTE\n"
    "551-000068-95;99;09/04/2026;-35.000,00;9183;COMPRA SUPERMERCADO\n"
)


class TestDetectarFormato:
    def test_formato_bancolombia_default(self, tmp_path):
        """El CSV real del banco (sin encabezados, comas, yyyymmdd)."""
        csv = tmp_path / "movs.csv"
        csv.write_text(CSV_DEFAULT)

        r = detectar_formato(csv)
        assert r["ok"]
        fmt = r["formato"]
        assert fmt["delimitador"] == ","
        assert fmt["filas_encabezado"] == 0
        assert fmt["col_cuenta"] == 0
        assert fmt["col_codigo_banco"] == 1
        assert fmt["col_fecha"] == 3
        assert fmt["col_valor"] == 5
        assert fmt["col_codigo_detalle"] == 6
        assert fmt["col_descripcion"] == 7
        assert fmt["formato_fecha"] == "%Y%m%d"
        assert fmt["separador_decimal"] == "."
        # La validación real leyó los movimientos del archivo
        assert r["n_movimientos"] > 0
        assert r["roles"][3] == "Fecha" and r["roles"][5] == "Valor"
        assert len(r["preview"]) > 0

    def test_formato_punto_y_coma_con_encabezado(self, tmp_path):
        """CSV con encabezado, ';', fecha dd/mm/yyyy y decimales con coma."""
        csv = tmp_path / "movs.csv"
        csv.write_text(CSV_PUNTOYCOMA)

        r = detectar_formato(csv)
        assert r["ok"]
        fmt = r["formato"]
        assert fmt["delimitador"] == ";"
        assert fmt["filas_encabezado"] == 1
        assert fmt["col_cuenta"] == 0
        assert fmt["col_fecha"] == 2
        assert fmt["col_valor"] == 3
        assert fmt["col_descripcion"] == 5
        assert fmt["formato_fecha"] == "%d/%m/%Y"
        assert fmt["separador_decimal"] == ","
        assert fmt["separador_miles"] == "."
        assert r["n_movimientos"] == 3

    def test_archivo_vacio(self, tmp_path):
        csv = tmp_path / "vacio.csv"
        csv.write_text("")
        r = detectar_formato(csv)
        assert not r["ok"]

    def test_archivo_sin_movimientos(self, tmp_path):
        csv = tmp_path / "texto.csv"
        csv.write_text("esto es un documento\ncualquiera sin datos bancarios\n")
        r = detectar_formato(csv)
        assert not r["ok"]
        assert "movimientos" in r["error"]


class TestEndpointDetectarFormato:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "contable.db"))
        monkeypatch.setattr(config, "SYSTEM_DB_PATH", str(tmp_path / "sistema.db"))
        import app.empresas as emp_mod
        emp_mod._sistema_listo.clear()

        from app.web import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.test_client() as c:
            yield c
        emp_mod._sistema_listo.clear()

    def test_detecta_desde_upload(self, client):
        resp = client.post(
            "/empresas/detectar-formato-banco",
            data={"archivo": (io.BytesIO(CSV_DEFAULT.encode()), "movs.csv")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"]
        assert data["formato"]["delimitador"] == ","
        assert data["formato"]["col_valor"] == 5
        assert data["n_movimientos"] > 0

    def test_sin_archivo(self, client):
        resp = client.post("/empresas/detectar-formato-banco", data={})
        assert resp.status_code == 400
        assert not resp.get_json()["ok"]

    def test_archivo_no_interpretable(self, client):
        resp = client.post(
            "/empresas/detectar-formato-banco",
            data={"archivo": (io.BytesIO(b"hola mundo\nsin datos\n"), "x.csv")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 422
        assert not resp.get_json()["ok"]
