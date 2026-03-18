"""
Importador de archivos Excel del sistema RADIAN y archivos maestros.

Responsable de:
- Leer el reporte RADIAN (.xlsx) descargado del portal DIAN.
- Normalizar NITs, fechas y columnas numéricas.
- Detectar duplicados contra la base de datos.
- Leer archivos maestros (terceros, cuentas, comprobantes).
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from app.config import (
    COLUMNAS_IMPUESTOS,
    FILA_ENCABEZADOS_MAESTROS,
    COL_CUENTAS_NIVEL,
    COL_CUENTAS_ACTIVO,
)
from app.database import cufe_existe

logger = logging.getLogger(__name__)


def _limpiar_nit(valor: object) -> str:
    """
    Normaliza un NIT eliminando puntos, guiones y espacios.

    Ejemplo: '901.331.657-7' → '9013316577'
    Solo se conservan los dígitos.
    """
    if pd.isna(valor):
        return ""
    return str(valor).replace(".", "").replace("-", "").strip()


def importar_radian(filepath: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """
    Lee el reporte RADIAN (.xlsx) y retorna un DataFrame limpio y estandarizado.

    Normaliza:
    - NITs emisor y receptor (solo dígitos).
    - Columnas de impuestos: float, NaN → 0.0.
    - Total: float.
    - Fechas: datetime con formato DD-MM-YYYY o ISO.

    Args:
        filepath: Ruta al archivo .xlsx descargado de RADIAN.
        db_path:  Ruta a la base de datos SQLite para detección de duplicados.
                  Si es None usa el valor por defecto de config.

    Returns:
        DataFrame normalizado. Agrega columna '_duplicado' (bool).

    Raises:
        FileNotFoundError: Si el archivo no existe.
        ValueError: Si el archivo no tiene las columnas mínimas esperadas.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Archivo RADIAN no encontrado: {filepath}")

    logger.info("Leyendo RADIAN: %s", filepath)
    try:
        df = pd.read_excel(filepath, header=0, dtype=str)
    except Exception as exc:
        raise ValueError(f"No se pudo leer el archivo Excel: {exc}") from exc

    # Limpiar nombres de columnas (espacios extra)
    df.columns = [str(c).strip() for c in df.columns]

    # Validar columnas mínimas obligatorias
    cols_requeridas = ["Tipo de documento", "CUFE/CUDE", "NIT Emisor", "NIT Receptor", "Total"]
    faltantes = [c for c in cols_requeridas if c not in df.columns]
    if faltantes:
        raise ValueError(f"Columnas faltantes en RADIAN: {faltantes}")

    # Eliminar filas completamente vacías
    df.dropna(how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Normalizar NITs
    df["NIT Emisor"] = df["NIT Emisor"].apply(_limpiar_nit)
    df["NIT Receptor"] = df["NIT Receptor"].apply(_limpiar_nit)

    # Normalizar columnas de impuestos → float
    for col in COLUMNAS_IMPUESTOS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    # Normalizar Total → float
    df["Total"] = pd.to_numeric(df["Total"], errors="coerce").fillna(0.0)

    # Normalizar fechas
    for col_fecha in ["Fecha Emisión", "Fecha Recepción"]:
        if col_fecha in df.columns:
            df[col_fecha] = pd.to_datetime(
                df[col_fecha], dayfirst=True, errors="coerce"
            )

    # Limpiar strings genéricos
    for col in ["Tipo de documento", "CUFE/CUDE", "Folio", "Prefijo",
                "Estado", "Grupo", "Nombre Emisor", "Nombre Receptor"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    # Detectar duplicados contra la BD
    kwargs = {} if db_path is None else {"db_path": db_path}
    df["_duplicado"] = df["CUFE/CUDE"].apply(
        lambda cufe: cufe_existe(cufe, **kwargs) if cufe else False
    )
    n_dup = df["_duplicado"].sum()
    if n_dup:
        logger.warning("%d documento(s) ya existen en la base de datos (duplicados).", n_dup)

    logger.info("RADIAN importado: %d filas, %d duplicados.", len(df), n_dup)
    return df


def cargar_maestro_terceros(filepath: str) -> pd.DataFrame:
    """
    Lee el archivo maestro de terceros exportado del sistema contable.

    Los encabezados reales se encuentran en la fila 7 (índice 6).
    Los datos comienzan en la fila 8.

    Args:
        filepath: Ruta al archivo Listado_de_Terceros.xlsx.

    Returns:
        DataFrame con los terceros. Columna 'Identificación' normalizada.

    Raises:
        FileNotFoundError: Si el archivo no existe.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Maestro de terceros no encontrado: {filepath}")

    logger.info("Cargando maestro de terceros: %s", filepath)
    df = pd.read_excel(filepath, header=FILA_ENCABEZADOS_MAESTROS, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    df.dropna(how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)

    if "Identificación" in df.columns:
        df["Identificación"] = df["Identificación"].apply(_limpiar_nit)

    logger.info("Terceros cargados: %d registros.", len(df))
    return df


def cargar_maestro_cuentas(filepath: str) -> pd.DataFrame:
    """
    Lee el plan de cuentas contables exportado del sistema.

    Filtra únicamente cuentas con Nivel agrupación == 'Transaccional'
    y Activo == 'Sí'.

    Args:
        filepath: Ruta al archivo Listado_de_Cuentas_Contables.xlsx.

    Returns:
        DataFrame filtrado con cuentas transaccionales activas.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Maestro de cuentas no encontrado: {filepath}")

    logger.info("Cargando maestro de cuentas: %s", filepath)
    df = pd.read_excel(filepath, header=FILA_ENCABEZADOS_MAESTROS, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    df.dropna(how="all", inplace=True)

    # Filtrar cuentas transaccionales activas si las columnas existen
    if COL_CUENTAS_NIVEL in df.columns and COL_CUENTAS_ACTIVO in df.columns:
        df = df[
            (df[COL_CUENTAS_NIVEL].str.strip() == "Transaccional") &
            (df[COL_CUENTAS_ACTIVO].str.strip() == "Sí")
        ].copy()

    df.reset_index(drop=True, inplace=True)
    logger.info("Cuentas transaccionales activas: %d registros.", len(df))
    return df


def cargar_maestro_comprobantes(filepath: str) -> pd.DataFrame:
    """
    Lee el catálogo de tipos de comprobante contable.

    Args:
        filepath: Ruta al archivo Tipos_de_comprobante_contable.xlsx.

    Returns:
        DataFrame con los comprobantes disponibles.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Maestro de comprobantes no encontrado: {filepath}")

    logger.info("Cargando maestro de comprobantes: %s", filepath)
    df = pd.read_excel(filepath, header=FILA_ENCABEZADOS_MAESTROS, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    df.dropna(how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)

    logger.info("Comprobantes cargados: %d registros.", len(df))
    return df
