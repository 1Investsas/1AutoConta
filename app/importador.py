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
    # Elegir motor según la extensión:
    # .xls  → formato BIFF binario, requiere xlrd
    # .xlsx → formato ZIP/OpenXML, requiere openpyxl
    ext = path.suffix.lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"

    try:
        df = pd.read_excel(filepath, header=0, dtype=str, engine=engine)
    except ImportError as exc:
        if "xlrd" in str(exc):
            raise ValueError(
                "Para leer archivos .xls (formato antiguo) instala xlrd: "
                "pip install xlrd"
            ) from exc
        raise ValueError(f"Motor Excel no disponible: {exc}") from exc
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
    Lee el maestro de terceros con la estructura del modelo de Siigo Nube.

    El modelo de Siigo trae los encabezados en la **fila 1** y los datos desde la
    fila 2. La fila de encabezados se detecta automáticamente, de modo que el
    lector también acepta la planilla antigua (encabezados en la fila 7) sin
    cambios, por compatibilidad.

    Además de las columnas propias del modelo, el DataFrame expone columnas
    *canónicas* que el resto del sistema espera —``Identificación`` (solo
    dígitos), ``Nombre tercero`` y ``Estado``— para que el cruce de terceros, el
    autocompletado y las plantillas sigan funcionando sin cambios.

    Args:
        filepath: Ruta al archivo Listado_de_Terceros.xlsx.

    Returns:
        DataFrame con los terceros, con las columnas canónicas garantizadas.

    Raises:
        FileNotFoundError: Si el archivo no existe.
    """
    from app import terceros_schema as esquema

    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Maestro de terceros no encontrado: {filepath}")

    logger.info("Cargando maestro de terceros: %s", filepath)

    # Detectar la fila de encabezados (modelo Siigo: fila 1; planilla antigua: 7).
    crudo = pd.read_excel(filepath, header=None, nrows=15, dtype=str)
    fila_enc = esquema.fila_encabezados_desde_valores(crudo.values.tolist())
    df = pd.read_excel(filepath, header=fila_enc, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    df.dropna(how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)

    _agregar_columnas_canonicas(df, esquema)

    logger.info("Terceros cargados: %d registros.", len(df))
    return df


def _agregar_columnas_canonicas(df: pd.DataFrame, esquema) -> None:
    """Garantiza en ``df`` las columnas canónicas que usa el resto del sistema.

    Resuelve, por su nombre (modelo Siigo o planilla antigua), las columnas de
    identificación, nombre y estado, y las copia a ``Identificación`` (solo
    dígitos), ``Nombre tercero`` y ``Estado``. El nombre se toma de la razón
    social y, si está vacía, se compone con los nombres y apellidos del tercero.
    """
    def _col(campo: str):
        for c in df.columns:
            if esquema.campo_de_encabezado(c) == campo:
                return c
        return None

    col_id = _col("identificacion")
    if col_id is not None:
        df["Identificación"] = df[col_id].apply(_limpiar_nit)
    elif "Identificación" not in df.columns:
        df["Identificación"] = ""

    col_razon = _col("razon_social")
    col_nombres = _col("nombres")
    col_apellidos = _col("apellidos")

    def _nombre(row) -> str:
        razon = str(row[col_razon]).strip() if col_razon is not None and pd.notna(row[col_razon]) else ""
        if razon and razon.lower() != "nan":
            return razon
        partes = []
        for c in (col_nombres, col_apellidos):
            if c is not None and pd.notna(row[c]):
                v = str(row[c]).strip()
                if v and v.lower() != "nan":
                    partes.append(v)
        return " ".join(partes)

    if col_razon is not None or col_nombres is not None or col_apellidos is not None:
        df["Nombre tercero"] = df.apply(_nombre, axis=1)
    elif "Nombre tercero" not in df.columns:
        df["Nombre tercero"] = ""

    col_estado = _col("estado")
    if col_estado is not None and col_estado != "Estado":
        df["Estado"] = df[col_estado]
    elif "Estado" not in df.columns:
        df["Estado"] = ""


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
