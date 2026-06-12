"""
Clasificador determinista de documentos electrónicos RADIAN.

Implementa las reglas de negocio para determinar el tipo contable
de cada documento según su 'Tipo de documento' y el NIT emisor.
"""

import logging

import pandas as pd

from app.config import NIT_EMPRESA

logger = logging.getLogger(__name__)

# Posibles valores de clasificación
FACTURA_VENTA = "FACTURA_VENTA"
FACTURA_COMPRA = "FACTURA_COMPRA"
DOCUMENTO_SOPORTE = "DOCUMENTO_SOPORTE"
NOMINA = "NOMINA"
NOTA_CREDITO_VENTA = "NOTA_CREDITO_VENTA"
NOTA_CREDITO_COMPRA = "NOTA_CREDITO_COMPRA"
NOTA_DEBITO_VENTA = "NOTA_DEBITO_VENTA"
NOTA_DEBITO_COMPRA = "NOTA_DEBITO_COMPRA"
SIN_CLASIFICAR = "SIN_CLASIFICAR"


def clasificar_documento(
    tipo_documento: str,
    nit_emisor: str,
    nit_empresa: str | None = None,
) -> str:
    """
    Clasifica un documento electrónico de forma determinista.

    Reglas aplicadas en orden de precedencia:
    1. 'Nomina Individual' → NOMINA
    2. 'Documento soporte con no obligados' → DOCUMENTO_SOPORTE
    3. 'Factura electrónica': si emisor == empresa → FACTURA_VENTA, sino → FACTURA_COMPRA
    4. Contiene 'Nota crédito': según emisor → NOTA_CREDITO_VENTA / NOTA_CREDITO_COMPRA
    5. Contiene 'Nota débito': según emisor → NOTA_DEBITO_VENTA / NOTA_DEBITO_COMPRA
    6. Cualquier otro caso → SIN_CLASIFICAR

    Args:
        tipo_documento: Valor de la columna 'Tipo de documento' del RADIAN.
        nit_emisor:     NIT del emisor ya normalizado (solo dígitos).

    Returns:
        String con la clasificación del documento.
    """
    if not tipo_documento:
        return SIN_CLASIFICAR

    td = tipo_documento.strip().lower()
    nit_propio = (nit_empresa if nit_empresa is not None else NIT_EMPRESA).strip()
    es_empresa = nit_emisor.strip() == nit_propio

    if "nomina individual" in td:
        return NOMINA

    if "documento soporte" in td:
        return DOCUMENTO_SOPORTE

    if "factura electrónica" in td or "factura electronica" in td:
        return FACTURA_VENTA if es_empresa else FACTURA_COMPRA

    if "nota crédito" in td or "nota credito" in td:
        return NOTA_CREDITO_VENTA if es_empresa else NOTA_CREDITO_COMPRA

    if "nota débito" in td or "nota debito" in td:
        return NOTA_DEBITO_VENTA if es_empresa else NOTA_DEBITO_COMPRA

    logger.warning("Tipo de documento no reconocido: '%s'", tipo_documento)
    return SIN_CLASIFICAR


def clasificar_lote(df: pd.DataFrame, nit_empresa: str | None = None) -> pd.DataFrame:
    """
    Aplica la clasificación a cada fila de un DataFrame RADIAN.

    Agrega la columna 'clasificacion' al DataFrame.

    Args:
        df: DataFrame importado desde RADIAN (resultado de importar_radian).

    Returns:
        El mismo DataFrame con la columna 'clasificacion' añadida.
    """
    df = df.copy()
    df["clasificacion"] = df.apply(
        lambda row: clasificar_documento(
            str(row.get("Tipo de documento", "")),
            str(row.get("NIT Emisor", "")),
            nit_empresa,
        ),
        axis=1,
    )

    # Log de resumen
    resumen = df["clasificacion"].value_counts().to_dict()
    logger.info("Clasificación completada: %s", resumen)

    return df
