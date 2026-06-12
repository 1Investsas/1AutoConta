"""
Separación y cálculo de impuestos por documento.

Para cada documento, extrae las columnas de impuesto con valor > 0,
asigna la cuenta contable sugerida según el tipo de operación (compra/venta)
y calcula la base gravable como Total menos suma de impuestos.
"""

import logging
from typing import Optional

import pandas as pd

from app.config import COLUMNAS_IMPUESTOS, CUENTAS_IMPUESTOS, IMPUESTOS_RETENCION

logger = logging.getLogger(__name__)

# Clasificaciones que representan operaciones de "compra" (sentido débito gasto)
_CLASIFICACIONES_COMPRA = {
    "FACTURA_COMPRA",
    "DOCUMENTO_SOPORTE",
    "NOTA_CREDITO_COMPRA",
    "NOTA_DEBITO_COMPRA",
}

# Clasificaciones que representan operaciones de "venta" (sentido crédito ingreso)
_CLASIFICACIONES_VENTA = {
    "FACTURA_VENTA",
    "NOTA_CREDITO_VENTA",
    "NOTA_DEBITO_VENTA",
}


def _sentido_operacion(clasificacion: str) -> str:
    """
    Retorna 'compra' o 'venta' según la clasificación del documento.
    Para nómina y sin clasificar retorna 'compra' como valor por defecto.
    """
    if clasificacion in _CLASIFICACIONES_VENTA:
        return "venta"
    return "compra"


def separar_impuestos(
    row: pd.Series,
    clasificacion: Optional[str] = None,
    cuentas_impuestos: Optional[dict] = None,
) -> list[dict]:
    """
    Extrae las líneas de impuesto de una fila del DataFrame RADIAN.

    Por cada columna de impuesto con valor > 0, genera un diccionario con:
    - nombre_impuesto: nombre de la columna (p. ej. 'IVA').
    - valor: monto del impuesto.
    - cuenta_sugerida: cuenta contable por defecto según config.
    - es_retencion: True si el impuesto es una retención practicada.
    - sentido: 'compra' o 'venta'.

    Args:
        row:           Fila del DataFrame RADIAN.
        clasificacion: Clasificación del documento; si None se lee de la fila.

    Returns:
        Lista de diccionarios, uno por impuesto con valor > 0.
    """
    if clasificacion is None:
        clasificacion = str(row.get("clasificacion", ""))

    if cuentas_impuestos is None:
        cuentas_impuestos = CUENTAS_IMPUESTOS

    sentido = _sentido_operacion(clasificacion)
    impuestos = []

    for nombre in COLUMNAS_IMPUESTOS:
        valor = float(row.get(nombre, 0.0) or 0.0)
        if valor > 0:
            cuentas_col = cuentas_impuestos.get(nombre, {})
            cuenta_sugerida = cuentas_col.get(sentido, "")

            impuestos.append({
                "nombre_impuesto": nombre,
                "valor": valor,
                "cuenta_sugerida": cuenta_sugerida,
                "es_retencion": nombre in IMPUESTOS_RETENCION,
                "sentido": sentido,
            })

    return impuestos


def calcular_base_gravable(total: float, impuestos: list[dict]) -> float:
    """
    Calcula la base gravable del documento.

    Base gravable = Total - suma de todos los impuestos con valor > 0.

    Args:
        total:     Valor total del documento.
        impuestos: Lista de impuestos retornada por separar_impuestos().

    Returns:
        Base gravable como float. Mínimo 0.0.
    """
    suma_impuestos = sum(imp["valor"] for imp in impuestos)
    base = total - suma_impuestos
    if base < 0:
        logger.warning(
            "Base gravable negativa (%.2f). Total=%.2f, Impuestos=%.2f",
            base, total, suma_impuestos,
        )
    return round(base, 2)


def procesar_impuestos_lote(
    df: pd.DataFrame,
    cuentas_impuestos: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Agrega columnas de impuestos y base gravable al DataFrame.

    Agrega:
    - '_impuestos': lista de dicts con los impuestos del documento.
    - '_base_gravable': float con la base gravable calculada.

    Args:
        df: DataFrame con columna 'clasificacion' y columnas de impuestos.

    Returns:
        DataFrame con las columnas '_impuestos' y '_base_gravable'.
    """
    df = df.copy()

    impuestos_lista = []
    bases = []

    for _, row in df.iterrows():
        clasificacion = str(row.get("clasificacion", ""))
        impuestos = separar_impuestos(row, clasificacion, cuentas_impuestos)
        total = float(row.get("Total", 0.0) or 0.0)
        base = calcular_base_gravable(total, impuestos)

        impuestos_lista.append(impuestos)
        bases.append(base)

    df["_impuestos"] = impuestos_lista
    df["_base_gravable"] = bases

    return df
