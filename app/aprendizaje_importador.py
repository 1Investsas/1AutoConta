"""
Importador de conocimiento externo para el motor de aprendizaje.

Permite "sembrar" el aprendizaje con datos históricos de otras fuentes, sin
esperar a que el usuario digite todo desde cero: exportes del programa de
contabilidad (p. ej. el movimiento contable de SIIGO), auxiliares contables,
o cualquier Excel/CSV que relacione una descripción con la cuenta contable
y/o el NIT del tercero usados.

El archivo NO necesita un formato fijo: se detectan las columnas por su
encabezado (aceptando variantes con/sin tildes y mayúsculas):

- **Texto**  → Descripción / Detalle / Concepto / Observaciones / Glosa /
  Nombre del tercero / Razón social.
- **Cuenta** → Cuenta contable / Código cuenta / Código contable / Cuenta.
- **NIT**    → NIT / Identificación / NIT tercero / Cédula.

También se detecta automáticamente la fila de encabezados (SIIGO y otros
programas suelen exportar con títulos o filtros en las primeras filas).

Cada fila válida se convierte en observaciones del motor:
    texto → cuenta   (campo 'cuenta')
    texto → NIT      (campo 'nit_tercero')

Por defecto el conocimiento se deposita en el módulo ``general`` (fallback de
todos los módulos); opcionalmente puede dirigirse a un módulo específico
('banco', 'caja', 'radian').
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from app.aprendizaje import MODULO_GENERAL, aprender_lote, normalizar_texto
from app.config import DB_PATH

logger = logging.getLogger(__name__)

# Máximo de filas de arranque a inspeccionar buscando los encabezados.
_MAX_FILAS_ENCABEZADO = 15

# Encabezados reconocidos por tipo de columna (comparados ya normalizados).
# El orden importa: se usa la primera coincidencia.
_ENCABEZADOS_TEXTO = (
    "DESCRIPCION", "DETALLE", "CONCEPTO", "OBSERVACION", "OBSERVACIONES",
    "GLOSA", "NOMBRE TERCERO", "NOMBRE DEL TERCERO", "TERCERO NOMBRE",
    "RAZON SOCIAL", "NOMBRE",
)
_ENCABEZADOS_CUENTA = (
    "CUENTA CONTABLE", "CODIGO CUENTA", "CODIGO CONTABLE",
    "CODIGO CUENTA CONTABLE", "COD CUENTA", "CUENTA",
)
_ENCABEZADOS_NIT = (
    "NIT TERCERO", "NIT DEL TERCERO", "IDENTIFICACION", "NUMERO IDENTIFICACION",
    "NIT", "CEDULA", "CC NIT",
)


def _norm_encabezado(valor) -> str:
    """Encabezado normalizado (sin tildes, mayúsculas, espacios simples)."""
    return normalizar_texto(str(valor or ""))


def _buscar_columna(encabezados: list[str], candidatos: tuple[str, ...]) -> Optional[int]:
    """
    Índice de la primera columna cuyo encabezado coincide con un candidato.

    Primero busca coincidencia exacta; luego, que el encabezado CONTENGA el
    candidato (p. ej. 'CODIGO DE LA CUENTA CONTABLE' contiene 'CUENTA CONTABLE').
    """
    for cand in candidatos:
        for i, enc in enumerate(encabezados):
            if enc == cand:
                return i
    for cand in candidatos:
        for i, enc in enumerate(encabezados):
            if enc and cand in enc:
                return i
    return None


def _detectar_encabezados(df: pd.DataFrame) -> Optional[tuple[int, dict]]:
    """
    Busca la fila de encabezados en las primeras filas del archivo.

    Una fila califica si permite mapear la columna de texto Y al menos una de
    cuenta o NIT. Retorna (índice_fila, columnas) o None.
    """
    tope = min(_MAX_FILAS_ENCABEZADO, len(df))
    for fila in range(tope):
        encabezados = [_norm_encabezado(v) for v in df.iloc[fila].tolist()]
        col_texto = _buscar_columna(encabezados, _ENCABEZADOS_TEXTO)
        col_cuenta = _buscar_columna(encabezados, _ENCABEZADOS_CUENTA)
        col_nit = _buscar_columna(encabezados, _ENCABEZADOS_NIT)
        if col_texto is not None and (col_cuenta is not None or col_nit is not None):
            return fila, {
                "texto": col_texto,
                "cuenta": col_cuenta,
                "nit": col_nit,
                "nombres": [str(v) for v in df.iloc[fila].tolist()],
            }
    return None


def _limpiar_codigo_cuenta(valor) -> str:
    """
    Normaliza un código de cuenta contable leído de Excel/CSV.

    Acepta números leídos como float ('51050501.0'), con separadores o con el
    nombre pegado ('51050501 - Gastos'). Retorna '' si no parece una cuenta.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    if not texto or texto.lower() in ("nan", "none"):
        return ""
    # Float de Excel: 51050501.0 → 51050501
    texto = re.sub(r"\.0+$", "", texto)
    # Primer grupo de dígitos (una cuenta PUC tiene al menos 4).
    m = re.match(r"\s*(\d{4,})", texto)
    return m.group(1) if m else ""


def _limpiar_nit(valor) -> str:
    """NIT a solo dígitos, sin dígito de verificación pegado con guion."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    if not texto or texto.lower() in ("nan", "none"):
        return ""
    texto = re.sub(r"\.0+$", "", texto)
    # '901331657-7' → tomar la parte antes del guion (sin DV).
    texto = texto.split("-")[0]
    digitos = re.sub(r"\D", "", texto)
    return digitos if len(digitos) >= 5 else ""


def _leer_archivo(path: Union[str, Path]) -> pd.DataFrame:
    """Lee un Excel o CSV crudo (sin asumir encabezados) como texto."""
    ruta = Path(path)
    sufijo = ruta.suffix.lower()
    if sufijo == ".csv":
        return pd.read_csv(ruta, header=None, dtype=str,
                           keep_default_na=False, sep=None, engine="python")
    return pd.read_excel(ruta, header=None, dtype=str)


def importar_conocimiento(
    origen: Union[str, Path, pd.DataFrame],
    db_path: str = DB_PATH,
    modulo: str = MODULO_GENERAL,
) -> dict:
    """
    Entrena el motor de aprendizaje con un archivo externo (o DataFrame crudo).

    Args:
        origen:  Ruta a .xlsx/.xls/.csv, o DataFrame ya leído SIN encabezados.
        db_path: BD de la empresa que recibe el conocimiento.
        modulo:  Módulo destino ('general' por defecto = fallback de todos).

    Returns:
        Dict resumen: {'filas', 'aprendidos', 'columnas', 'mensaje'}.

    Raises:
        ValueError: si no se reconocen las columnas necesarias.
    """
    if isinstance(origen, pd.DataFrame):
        df = origen.astype(str) if not origen.empty else origen
    else:
        df = _leer_archivo(origen)

    if df is None or df.empty:
        raise ValueError("El archivo está vacío.")

    deteccion = _detectar_encabezados(df)
    if not deteccion:
        raise ValueError(
            "No se reconocieron las columnas del archivo. Se necesita una "
            "columna de texto (Descripción / Detalle / Concepto / Nombre del "
            "tercero) y al menos una de Cuenta contable o NIT."
        )
    fila_enc, cols = deteccion
    datos = df.iloc[fila_enc + 1:]

    observaciones: list[dict] = []
    filas_validas = 0
    for _, row in datos.iterrows():
        texto = str(row.iloc[cols["texto"]]).strip()
        if (not texto or texto.lower() in ("nan", "none")
                or not normalizar_texto(texto)):
            continue

        cuenta = (_limpiar_codigo_cuenta(row.iloc[cols["cuenta"]])
                  if cols["cuenta"] is not None else "")
        nit = (_limpiar_nit(row.iloc[cols["nit"]])
               if cols["nit"] is not None else "")
        if not cuenta and not nit:
            continue

        filas_validas += 1
        if cuenta:
            observaciones.append({
                "modulo": modulo, "campo": "cuenta",
                "texto": texto, "valor": cuenta,
            })
        if nit:
            observaciones.append({
                "modulo": modulo, "campo": "nit_tercero",
                "texto": texto, "valor": nit,
            })

    aprendidos = aprender_lote(observaciones, db_path)

    columnas_usadas = {
        "texto": cols["nombres"][cols["texto"]],
        "cuenta": (cols["nombres"][cols["cuenta"]]
                   if cols["cuenta"] is not None else None),
        "nit": (cols["nombres"][cols["nit"]]
                if cols["nit"] is not None else None),
    }
    resumen = {
        "filas": filas_validas,
        "aprendidos": aprendidos,
        "columnas": columnas_usadas,
        "mensaje": (
            f"Se leyeron {filas_validas} fila(s) útiles y se registraron "
            f"{aprendidos} observación(es) de aprendizaje."
        ),
    }
    logger.info("Conocimiento importado (%s): %s", modulo, resumen["mensaje"])
    return resumen
