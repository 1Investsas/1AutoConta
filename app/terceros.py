"""
Identificación y cruce de terceros en el maestro contable.

Determina cuál NIT usar según la clasificación del documento
y busca al tercero en el maestro de terceros exportado del sistema.
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Clasificaciones donde el tercero es el RECEPTOR
_TERCERO_ES_RECEPTOR = {
    "FACTURA_VENTA",
    "DOCUMENTO_SOPORTE",
    "NOMINA",
    "NOTA_CREDITO_VENTA",
    "NOTA_DEBITO_VENTA",
}

# Clasificaciones donde el tercero es el EMISOR
_TERCERO_ES_EMISOR = {
    "FACTURA_COMPRA",
    "NOTA_CREDITO_COMPRA",
    "NOTA_DEBITO_COMPRA",
}


def identificar_tercero(
    clasificacion: str,
    nit_emisor: str,
    nombre_emisor: str,
    nit_receptor: str,
    nombre_receptor: str,
) -> dict:
    """
    Determina qué NIT y nombre usar como tercero según la clasificación.

    Args:
        clasificacion:   Clasificación del documento (p. ej. 'FACTURA_COMPRA').
        nit_emisor:      NIT del emisor normalizado.
        nombre_emisor:   Nombre del emisor.
        nit_receptor:    NIT del receptor normalizado.
        nombre_receptor: Nombre del receptor.

    Returns:
        Diccionario con claves 'nit' y 'nombre' del tercero identificado.
    """
    if clasificacion in _TERCERO_ES_RECEPTOR:
        return {"nit": nit_receptor, "nombre": nombre_receptor}

    if clasificacion in _TERCERO_ES_EMISOR:
        return {"nit": nit_emisor, "nombre": nombre_emisor}

    # Para SIN_CLASIFICAR u otros: intentar con emisor
    logger.warning(
        "Clasificación '%s' no tiene regla de tercero definida; se usa emisor.",
        clasificacion,
    )
    return {"nit": nit_emisor, "nombre": nombre_emisor}


def cruzar_tercero(
    nit: str,
    df_terceros: pd.DataFrame,
) -> Optional[dict]:
    """
    Busca un tercero en el maestro por coincidencia exacta de NIT.

    El NIT de búsqueda y los NITs del maestro deben estar normalizados
    (solo dígitos, sin puntos ni guiones).

    Args:
        nit:         NIT a buscar (ya normalizado).
        df_terceros: DataFrame del maestro de terceros.

    Returns:
        Diccionario con todos los campos del tercero encontrado,
        o None si no existe coincidencia.
    """
    if not nit or df_terceros.empty:
        return None

    col_id = "Identificación"
    if col_id not in df_terceros.columns:
        logger.error("El maestro de terceros no tiene la columna '%s'.", col_id)
        return None

    coincidencias = df_terceros[df_terceros[col_id].astype(str) == nit]

    if coincidencias.empty:
        logger.debug("Tercero NIT '%s' no encontrado en el maestro.", nit)
        return None

    # Tomar la primera coincidencia
    fila = coincidencias.iloc[0]
    resultado = {col: str(fila[col]) if pd.notna(fila[col]) else "" for col in df_terceros.columns}
    return resultado


def procesar_terceros_lote(
    df: pd.DataFrame,
    df_terceros: pd.DataFrame,
) -> pd.DataFrame:
    """
    Enriquece el DataFrame RADIAN con información del tercero cruzado.

    Agrega columnas:
    - 'tercero_nit': NIT del tercero según clasificación.
    - 'tercero_nombre': Nombre del tercero según clasificación.
    - 'tercero_encontrado': bool indicando si existe en el maestro.
    - 'tercero_estado': estado del tercero en el maestro ('Activo', 'Inactivo', etc.).

    Args:
        df:          DataFrame con columna 'clasificacion' ya asignada.
        df_terceros: DataFrame del maestro de terceros.

    Returns:
        DataFrame enriquecido con las columnas de tercero.
    """
    df = df.copy()

    terceros_nit = []
    terceros_nombre = []
    terceros_encontrado = []
    terceros_estado = []

    for _, row in df.iterrows():
        info = identificar_tercero(
            clasificacion=str(row.get("clasificacion", "")),
            nit_emisor=str(row.get("NIT Emisor", "")),
            nombre_emisor=str(row.get("Nombre Emisor", "")),
            nit_receptor=str(row.get("NIT Receptor", "")),
            nombre_receptor=str(row.get("Nombre Receptor", "")),
        )
        nit = info["nit"]
        nombre = info["nombre"]

        cruce = cruzar_tercero(nit, df_terceros)
        encontrado = cruce is not None
        estado = cruce.get("Estado", "") if cruce else ""

        terceros_nit.append(nit)
        terceros_nombre.append(nombre)
        terceros_encontrado.append(encontrado)
        terceros_estado.append(estado)

    df["tercero_nit"] = terceros_nit
    df["tercero_nombre"] = terceros_nombre
    df["tercero_encontrado"] = terceros_encontrado
    df["tercero_estado"] = terceros_estado

    no_encontrados = sum(1 for x in terceros_encontrado if not x)
    logger.info(
        "Cruce de terceros: %d encontrados, %d no encontrados.",
        len(df) - no_encontrados,
        no_encontrados,
    )
    return df
