"""
Detección automática del formato de los movimientos bancarios.

Un usuario del común no sabe qué delimitador usa su banco ni en qué columna
viene cada dato. Este módulo recibe un archivo de ejemplo (el CSV que el banco
le entrega) y deduce todo el formato que necesita el importador:

  - delimitador y filas de encabezado
  - posición de las columnas (cuenta, código banco, fecha, valor,
    código detalle, descripción)
  - formato de la fecha y separadores decimal/de miles

El resultado incluye una vista previa (primeras filas con el rol detectado de
cada columna) para que el usuario confirme visualmente antes de guardar, y una
validación real: se intenta leer el archivo con el formato propuesto y se
informa cuántos movimientos se interpretaron.

Todo son heurísticas sobre una muestra del archivo; el formulario de la
empresa sigue permitiendo ajustar cualquier campo a mano.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path

_DELIMITADORES = (",", ";", "\t", "|")
_MAX_LINEAS_MUESTRA = 300
_MAX_FILAS_ANALISIS = 120
_N_FILAS_PREVIEW = 5

# Formatos de fecha que emiten los bancos en Colombia, en orden de preferencia
# (ante ambigüedad dd/mm vs mm/dd se asume día primero, como es local).
_FORMATOS_FECHA = (
    "%Y%m%d", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
    "%Y/%m/%d", "%d/%m/%y", "%m/%d/%Y",
)

# Números estilo "1,234.56" / "1234.56" (decimal punto, miles coma)
_RE_NUM_PUNTO = re.compile(r"^-?\$?\s*(\d{1,3}(,\d{3})+|\d*)(\.\d+)?$")
# Números estilo "1.234,56" / "1234,56" (decimal coma, miles punto)
_RE_NUM_COMA = re.compile(r"^-?\$?\s*(\d{1,3}(\.\d{3})+|\d*)(,\d+)?$")
_RE_ENTERO = re.compile(r"^\d{1,6}$")
_RE_CUENTA = re.compile(r"^[\d][\d\s.-]{5,}$")
_RE_LETRAS = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2,}")


def _leer_lineas(path: str | Path) -> list[str]:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 nunca falla
        texto = data.decode("utf-8", errors="replace")
    lineas = texto.splitlines()[:_MAX_LINEAS_MUESTRA]
    return [ln for ln in lineas if ln.strip()]


def _detectar_delimitador(lineas: list[str]) -> str:
    """El delimitador correcto aparece el mismo nº de veces en casi todas las filas."""
    mejor, mejor_score = ",", -1.0
    for delim in _DELIMITADORES:
        conteos = [ln.count(delim) for ln in lineas]
        moda = max(set(conteos), key=conteos.count)
        if moda == 0:
            continue
        consistencia = sum(1 for c in conteos if c == moda) / len(conteos)
        # Ante empate en consistencia gana el que produce más columnas.
        score = consistencia + min(moda, 12) / 100.0
        if score > mejor_score:
            mejor, mejor_score = delim, score
    return mejor


def _parsear_fecha(celda: str) -> str | None:
    """Retorna el formato strptime que interpreta la celda como fecha, o None."""
    celda = celda.strip()
    if not celda:
        return None
    for fmt in _FORMATOS_FECHA:
        try:
            f = datetime.strptime(celda, fmt)
        except ValueError:
            continue
        if 1990 <= f.year <= 2100:
            return fmt
    return None


class _PerfilColumna:
    """Estadísticas de una columna sobre las filas de datos de la muestra."""

    def __init__(self) -> None:
        self.total = 0
        self.vacias = 0
        self.fechas: dict[str, int] = {}
        self.num_punto = 0
        self.num_coma = 0
        self.negativos = 0
        self.dec_punto = 0          # con parte decimal estilo punto ("12.34")
        self.dec_coma = 0           # con parte decimal estilo coma ("12,34")
        self.enteros = 0
        self.cuenta_like = 0
        self.con_letras = 0
        self.long_total = 0
        self.valores: set[str] = set()

    def observar(self, celda: str) -> None:
        celda = celda.strip()
        self.total += 1
        if not celda:
            self.vacias += 1
            return
        self.valores.add(celda)
        self.long_total += len(celda)

        fmt = _parsear_fecha(celda)
        if fmt:
            self.fechas[fmt] = self.fechas.get(fmt, 0) + 1

        limpio = celda.replace("$", "").replace(" ", "")
        if _RE_NUM_PUNTO.match(limpio) and any(ch.isdigit() for ch in limpio):
            self.num_punto += 1
            if "." in limpio:
                self.dec_punto += 1
        if _RE_NUM_COMA.match(limpio) and any(ch.isdigit() for ch in limpio):
            self.num_coma += 1
            if "," in limpio:
                self.dec_coma += 1
        if limpio.startswith("-"):
            self.negativos += 1
        if _RE_ENTERO.match(limpio):
            self.enteros += 1
        if _RE_CUENTA.match(celda) and not fmt:
            self.cuenta_like += 1
        if _RE_LETRAS.search(celda):
            self.con_letras += 1

    # -- proporciones sobre celdas no vacías -------------------------------
    @property
    def n(self) -> int:
        return max(self.total - self.vacias, 1)

    def frac_fecha(self) -> float:
        return max(self.fechas.values(), default=0) / self.n

    def formato_fecha(self) -> str | None:
        if not self.fechas:
            return None
        return max(self.fechas, key=self.fechas.get)

    def frac_numerica(self) -> float:
        return max(self.num_punto, self.num_coma) / self.n

    def frac_letras(self) -> float:
        return self.con_letras / self.n

    def longitud_media(self) -> float:
        return self.long_total / self.n

    def mayormente_vacia(self) -> bool:
        return self.total > 0 and self.vacias / self.total > 0.9


def _es_fila_datos(fila: list[str]) -> bool:
    """Una fila de datos tiene al menos una fecha y al menos un número."""
    tiene_fecha = any(_parsear_fecha(c) for c in fila)

    def _es_num(celda: str) -> bool:
        limpio = celda.strip().replace("$", "").replace(" ", "")
        if not any(ch.isdigit() for ch in limpio):
            return False
        return bool(_RE_NUM_PUNTO.match(limpio) or _RE_NUM_COMA.match(limpio))

    return tiene_fecha and any(_es_num(c) for c in fila)


def detectar_formato(path: str | Path) -> dict:
    """
    Analiza un archivo de ejemplo y propone el formato de importación.

    Returns:
        dict con:
          ok (bool), error (str, solo si ok=False)
          formato (dict con las claves de FORMATO_BANCO_DEFAULT)
          roles (list[str], rol detectado de cada columna, para la vista previa)
          preview (list[list[str]], primeras filas de datos)
          n_columnas (int)
          n_movimientos (int, movimientos leídos al validar con el importador)
          avisos (list[str], advertencias de la detección)
    """
    from app.empresas import FORMATO_BANCO_DEFAULT

    lineas = _leer_lineas(path)
    if len(lineas) < 1:
        return {"ok": False,
                "error": "El archivo está vacío o no se pudo leer como texto."}

    avisos: list[str] = []
    delimitador = _detectar_delimitador(lineas)
    filas = list(csv.reader(io.StringIO("\n".join(lineas)),
                            delimiter=delimitador))

    # Filas de encabezado: todo lo anterior a la primera fila con pinta de datos
    filas_encabezado = 0
    for i, fila in enumerate(filas):
        if _es_fila_datos(fila):
            filas_encabezado = i
            break
    else:
        return {"ok": False,
                "error": "No se encontraron filas con fecha y valor. "
                         "Verifica que el archivo sea el de movimientos del banco."}

    datos = [f for f in filas[filas_encabezado:] if any(c.strip() for c in f)]
    datos = datos[:_MAX_FILAS_ANALISIS]
    n_columnas = max(len(f) for f in datos)

    perfiles = [_PerfilColumna() for _ in range(n_columnas)]
    for fila in datos:
        for c in range(n_columnas):
            perfiles[c].observar(fila[c] if c < len(fila) else "")

    asignadas: set[int] = set()

    # --- Fecha -------------------------------------------------------------
    col_fecha, mejor = None, 0.0
    for c, p in enumerate(perfiles):
        if p.frac_fecha() >= 0.9 and p.frac_fecha() > mejor:
            col_fecha, mejor = c, p.frac_fecha()
    if col_fecha is None:
        return {"ok": False,
                "error": "No se pudo identificar la columna de la fecha."}
    formato_fecha = perfiles[col_fecha].formato_fecha()
    asignadas.add(col_fecha)

    # --- Valor ---------------------------------------------------------------
    # Entre las columnas numéricas (sin contar la fecha), el valor es la que
    # tiene signos negativos y/o decimales y más valores distintos.
    col_valor, mejor = None, -1.0
    for c, p in enumerate(perfiles):
        if c in asignadas or p.mayormente_vacia() or p.frac_numerica() < 0.9:
            continue
        if p.frac_fecha() >= 0.9:
            continue
        score = (2.0 * p.negativos / p.n
                 + (p.dec_punto + p.dec_coma) / p.n
                 + min(len(p.valores) / p.n, 1.0))
        if score > mejor:
            col_valor, mejor = c, score
    if col_valor is None:
        return {"ok": False,
                "error": "No se pudo identificar la columna del valor."}
    asignadas.add(col_valor)

    pv = perfiles[col_valor]
    if pv.dec_coma > pv.dec_punto:
        separador_decimal, separador_miles = ",", "."
    else:
        separador_decimal, separador_miles = ".", ","
    if separador_miles == delimitador:
        # Con delimitador coma los valores nunca traen miles con coma
        # (partirían la celda); no hay nada que limpiar.
        separador_miles = "" if separador_decimal == "." else separador_miles

    # --- Descripción ---------------------------------------------------------
    col_desc, mejor = None, -1.0
    for c, p in enumerate(perfiles):
        if c in asignadas or p.mayormente_vacia():
            continue
        if p.frac_letras() < 0.5 or p.cuenta_like / p.n > 0.5:
            continue
        if p.longitud_media() > mejor:
            col_desc, mejor = c, p.longitud_media()
    if col_desc is None:
        col_desc = n_columnas - 1
        avisos.append("No se identificó con certeza la columna de la "
                      "descripción; revisa la vista previa.")
    asignadas.add(col_desc)

    # --- Nº de cuenta ----------------------------------------------------------
    # Suele ser una columna con el mismo valor en todas las filas (la cuenta
    # del cliente) con 6+ dígitos, posiblemente con guiones.
    col_cuenta, mejor = None, -1.0
    for c, p in enumerate(perfiles):
        if c in asignadas or p.mayormente_vacia():
            continue
        if p.cuenta_like / p.n < 0.9:
            continue
        constancia = 1.0 if len(p.valores) == 1 else 0.0
        score = constancia + p.cuenta_like / p.n
        if score > mejor:
            col_cuenta, mejor = c, score
    if col_cuenta is None:
        col_cuenta = 0
        avisos.append("No se identificó la columna del nº de cuenta; se asume "
                      "la primera. Revisa la vista previa.")
    asignadas.add(col_cuenta)

    # --- Códigos internos (banco y detalle) -----------------------------------
    # Columnas de enteros cortos que no son fecha/valor/cuenta. El código del
    # banco suele venir antes de la fecha; el de detalle, junto a la descripción.
    codigos = [c for c, p in enumerate(perfiles)
               if c not in asignadas and not p.mayormente_vacia()
               and p.enteros / p.n >= 0.9]

    def _mas_cercana(cols: list[int], objetivo: int) -> int | None:
        return min(cols, key=lambda c: abs(c - objetivo)) if cols else None

    col_cod_detalle = _mas_cercana(codigos, col_desc)
    restantes = [c for c in codigos if c != col_cod_detalle]
    col_cod_banco = _mas_cercana(restantes, col_cuenta)

    if col_cod_detalle is None:
        col_cod_detalle = col_valor  # sin códigos: cualquier columna estable
        avisos.append("El archivo no parece traer un código de detalle; el "
                      "4x1000 se identificará solo por la descripción.")
    if col_cod_banco is None:
        col_cod_banco = col_cuenta
    asignadas.update({col_cod_detalle, col_cod_banco})

    formato = {
        "delimitador": delimitador,
        "filas_encabezado": filas_encabezado,
        "col_cuenta": col_cuenta,
        "col_codigo_banco": col_cod_banco,
        "col_fecha": col_fecha,
        "col_valor": col_valor,
        "col_codigo_detalle": col_cod_detalle,
        "col_descripcion": col_desc,
        "formato_fecha": formato_fecha,
        "separador_decimal": separador_decimal,
        "separador_miles": separador_miles,
    }
    # Completar cualquier clave faltante con el default (robustez ante cambios)
    formato = {**FORMATO_BANCO_DEFAULT, **formato}

    # --- Roles por columna para la vista previa -------------------------------
    roles = ["" for _ in range(n_columnas)]
    for c, rol in ((col_cuenta, "Nº de cuenta"), (col_cod_banco, "Código banco"),
                   (col_fecha, "Fecha"), (col_valor, "Valor"),
                   (col_cod_detalle, "Código detalle"),
                   (col_desc, "Descripción")):
        roles[c] = f"{roles[c]} + {rol}" if roles[c] else rol

    preview = [
        [(fila[c] if c < len(fila) else "").strip() for c in range(n_columnas)]
        for fila in datos[:_N_FILAS_PREVIEW]
    ]

    # --- Validación real: leer el archivo con el formato propuesto ------------
    n_movimientos = 0
    try:
        from app.banco.importador_banco import leer_csv_banco
        n_movimientos = len(leer_csv_banco(path, formato=formato))
    except Exception as exc:  # heurística fallida: informar, no romper
        avisos.append(f"El formato propuesto no leyó el archivo completo: {exc}")

    if n_movimientos == 0 and not any("no leyó" in a for a in avisos):
        avisos.append("Con el formato propuesto no se interpretó ningún "
                      "movimiento; revisa la vista previa y ajusta a mano.")

    return {
        "ok": True,
        "formato": formato,
        "roles": roles,
        "preview": preview,
        "n_columnas": n_columnas,
        "n_movimientos": n_movimientos,
        "avisos": avisos,
    }
