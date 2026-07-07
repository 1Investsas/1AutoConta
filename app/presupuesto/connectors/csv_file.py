"""Conector CSV: importa el ejecutado desde un balance de prueba o auxiliar
exportado de cualquier software contable.

Formato esperado (separador , o ;):
    codigo_cuenta,nombre_cuenta,valor
    4135,Comercio al por mayor y al por menor,45000000
    5105,Gastos de personal,12500000

`valor` = movimiento neto del mes en la naturaleza de la cuenta.

config esperado: {"ruta": "/path/al/archivo.csv"} o pasar el contenido
directamente a `parsear_contenido`.
"""
import csv
import io

from .base import ConectorContable, MovimientoContable


def parsear_contenido(contenido: str, fecha: str = "") -> list[MovimientoContable]:
    """Parsea el contenido CSV (detecta , o ; y normaliza decimales)."""
    delimitador = ";" if contenido.splitlines()[0].count(";") > contenido.splitlines()[0].count(",") else ","
    lector = csv.DictReader(io.StringIO(contenido), delimiter=delimitador)
    if lector.fieldnames:
        lector.fieldnames = [f.strip().lower().replace(" ", "_") for f in lector.fieldnames]

    movimientos = []
    for fila in lector:
        codigo = str(fila.get("codigo_cuenta", "") or fila.get("cuenta", "")).strip()
        if not codigo:
            continue
        bruto = str(fila.get("valor", "0")).strip().replace("$", "").replace(" ", "")
        # Normalizar formatos: 1.234.567,89 → 1234567.89 | 1,234,567.89 → 1234567.89
        if "," in bruto and "." in bruto:
            if bruto.rfind(",") > bruto.rfind("."):
                bruto = bruto.replace(".", "").replace(",", ".")
            else:
                bruto = bruto.replace(",", "")
        elif "," in bruto:
            bruto = bruto.replace(",", ".") if bruto.count(",") == 1 else bruto.replace(",", "")
        try:
            valor = float(bruto)
        except ValueError:
            continue
        movimientos.append(MovimientoContable(
            codigo_cuenta=codigo,
            nombre_cuenta=str(fila.get("nombre_cuenta", "") or codigo).strip(),
            valor=valor,
            fecha=fecha,
        ))
    return movimientos


class ConectorCSV(ConectorContable):
    def probar_conexion(self) -> tuple[bool, str]:
        ruta = self.config.get("ruta")
        if not ruta:
            return False, "No se ha configurado la ruta del archivo CSV."
        try:
            with open(ruta, encoding="utf-8-sig") as f:
                f.readline()
            return True, "Archivo CSV accesible."
        except OSError as e:
            return False, f"No se pudo leer el archivo: {e}"

    def obtener_movimientos(self, anio: int, mes: int) -> list[MovimientoContable]:
        with open(self.config["ruta"], encoding="utf-8-sig") as f:
            contenido = f.read()
        return parsear_contenido(contenido, fecha=f"{anio}-{mes:02d}-01")
