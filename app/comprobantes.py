"""
Asignación del tipo de comprobante contable según la clasificación del documento.

Usa el mapeo definido en config.py y puede enriquecerse con el título
del comprobante obtenido del maestro de comprobantes.
"""

import logging
from typing import Optional

import pandas as pd

from app.config import MAPEO_COMPROBANTES

logger = logging.getLogger(__name__)


def asignar_comprobante(
    clasificacion: str,
    df_comprobantes: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Retorna el código y título del comprobante para una clasificación dada.

    Si se proporciona el DataFrame de comprobantes, intenta obtener
    el título oficial desde el maestro. De lo contrario usa un título genérico.

    Args:
        clasificacion:    Clasificación del documento.
        df_comprobantes:  DataFrame del maestro de tipos de comprobante (opcional).

    Returns:
        Diccionario con 'codigo' y 'titulo' del comprobante.
        Si la clasificación no tiene mapeo, retorna codigo='' y titulo='SIN COMPROBANTE'.
    """
    codigo = MAPEO_COMPROBANTES.get(clasificacion, "")

    if not codigo:
        logger.warning("Clasificación '%s' no tiene comprobante asignado.", clasificacion)
        return {"codigo": "", "titulo": "SIN COMPROBANTE"}

    titulo = _buscar_titulo(codigo, df_comprobantes)
    return {"codigo": codigo, "titulo": titulo}


def _buscar_titulo(
    codigo: str,
    df_comprobantes: Optional[pd.DataFrame],
) -> str:
    """
    Busca el título del comprobante en el maestro por código.

    Args:
        codigo:           Código del comprobante a buscar.
        df_comprobantes:  DataFrame del maestro de comprobantes.

    Returns:
        Título encontrado o una cadena genérica si no se encuentra.
    """
    if df_comprobantes is None or df_comprobantes.empty:
        return f"Comprobante {codigo}"

    col_codigo = "Código del comprobante"
    col_titulo = "Título comprobante"

    if col_codigo not in df_comprobantes.columns:
        return f"Comprobante {codigo}"

    coincidencias = df_comprobantes[
        df_comprobantes[col_codigo].astype(str).str.strip() == str(codigo).strip()
    ]

    if coincidencias.empty:
        return f"Comprobante {codigo}"

    if col_titulo in coincidencias.columns:
        return str(coincidencias.iloc[0][col_titulo]).strip()

    return f"Comprobante {codigo}"


def asignar_comprobantes_lote(
    df: pd.DataFrame,
    df_comprobantes: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Agrega columnas de comprobante a todo el DataFrame.

    Agrega:
    - 'codigo_comprobante': código del comprobante asignado.
    - 'titulo_comprobante': título del comprobante.

    Args:
        df:               DataFrame con columna 'clasificacion'.
        df_comprobantes:  DataFrame del maestro de comprobantes (opcional).

    Returns:
        DataFrame con las columnas de comprobante añadidas.
    """
    df = df.copy()
    codigos = []
    titulos = []

    for _, row in df.iterrows():
        comp = asignar_comprobante(
            str(row.get("clasificacion", "")),
            df_comprobantes,
        )
        codigos.append(comp["codigo"])
        titulos.append(comp["titulo"])

    df["codigo_comprobante"] = codigos
    df["titulo_comprobante"] = titulos

    return df
