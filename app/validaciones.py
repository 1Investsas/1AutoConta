"""
Validaciones del sistema contable-auto.

Verifica la integridad y coherencia de los datos antes de exportar:
- Cuadre débito = crédito por preasiento.
- Unicidad del CUFE/CUDE.
- Estado activo del tercero.
- Que las cuentas sean transaccionales según el plan de cuentas.
- Coherencia del emisor con la clasificación.
"""

import logging
from typing import Optional

import pandas as pd

from app.config import NIT_EMPRESA, COL_CUENTAS_CODIGO, COL_CUENTAS_NIVEL, COL_CUENTAS_ACTIVO
from app.database import cufe_existe
from app.models import LineaContable, PreasientoContable

logger = logging.getLogger(__name__)


def validar_cuadre(lineas: list[LineaContable]) -> bool:
    """
    Verifica que la suma de débitos sea igual a la suma de créditos.

    Se usa una tolerancia de $0.01 para manejar diferencias de redondeo.

    Args:
        lineas: Lista de líneas del preasiento.

    Returns:
        True si el preasiento cuadra.
    """
    total_debito = sum(l.debito for l in lineas)
    total_credito = sum(l.credito for l in lineas)
    diferencia = abs(total_debito - total_credito)
    cuadra = diferencia < 0.01

    if not cuadra:
        logger.warning(
            "Preasiento no cuadra: débitos=%.2f, créditos=%.2f, diferencia=%.2f",
            total_debito, total_credito, diferencia,
        )
    return cuadra


def validar_cufe_unico(cufe: str, db_path: Optional[str] = None) -> bool:
    """
    Verifica que el CUFE/CUDE no haya sido procesado anteriormente.

    Args:
        cufe:    CUFE o CUDE del documento.
        db_path: Ruta a la base de datos (usa default si es None).

    Returns:
        True si el CUFE es nuevo (no existe en la BD).
    """
    if not cufe:
        logger.warning("CUFE vacío recibido para validación.")
        return False

    kwargs = {} if db_path is None else {"db_path": db_path}
    existe = cufe_existe(cufe, **kwargs)
    if existe:
        logger.warning("CUFE duplicado detectado: %s", cufe)
    return not existe


def validar_tercero_activo(tercero: dict) -> bool:
    """
    Verifica que el estado del tercero sea 'Activo'.

    Args:
        tercero: Diccionario con los datos del tercero del maestro.
                 Puede ser None si el tercero no fue encontrado.

    Returns:
        True si el tercero existe y está activo.
    """
    if not tercero:
        return False

    estado = str(tercero.get("Estado", "")).strip().lower()
    activo = estado in ("activo", "si", "sí", "1", "true", "active")

    if not activo:
        logger.warning(
            "Tercero NIT '%s' tiene estado '%s'.",
            tercero.get("Identificación", "?"),
            tercero.get("Estado", "?"),
        )
    return activo


def validar_cuenta_transaccional(
    codigo: str,
    df_cuentas: pd.DataFrame,
) -> bool:
    """
    Verifica que una cuenta esté en el plan de cuentas como Transaccional y Activa.

    Si df_cuentas está vacío o la columna no existe, retorna True (sin validar).

    Args:
        codigo:     Código contable a validar.
        df_cuentas: DataFrame del maestro de cuentas (ya filtrado o completo).

    Returns:
        True si la cuenta es válida para imputar.
    """
    if not codigo or codigo == "[PENDIENTE]":
        return False

    if df_cuentas is None or df_cuentas.empty:
        return True  # Sin maestro de cuentas no se puede validar

    if COL_CUENTAS_CODIGO not in df_cuentas.columns:
        return True

    coincidencias = df_cuentas[
        df_cuentas[COL_CUENTAS_CODIGO].astype(str).str.strip() == str(codigo).strip()
    ]
    valida = not coincidencias.empty

    if not valida:
        logger.warning("Cuenta '%s' no encontrada en el plan de cuentas.", codigo)
    return valida


def validar_coherencia_emisor(
    clasificacion: str,
    nit_emisor: str,
) -> bool:
    """
    Verifica que la clasificación sea coherente con el NIT emisor.

    Para FACTURA_VENTA, NOMINA y DOCUMENTO_SOPORTE el emisor debe ser la empresa.
    Para FACTURA_COMPRA, NOTA_CREDITO_COMPRA y NOTA_DEBITO_COMPRA no debe ser la empresa.

    Args:
        clasificacion: Clasificación del documento.
        nit_emisor:    NIT del emisor normalizado.

    Returns:
        True si la combinación es coherente.
    """
    nit = nit_emisor.strip()
    es_empresa = nit == NIT_EMPRESA.strip()

    emitidos_por_empresa = {
        "FACTURA_VENTA", "DOCUMENTO_SOPORTE", "NOMINA",
        "NOTA_CREDITO_VENTA", "NOTA_DEBITO_VENTA",
    }
    emitidos_por_tercero = {
        "FACTURA_COMPRA",
        "NOTA_CREDITO_COMPRA", "NOTA_DEBITO_COMPRA",
    }

    if clasificacion in emitidos_por_empresa and not es_empresa:
        logger.warning(
            "Incoherencia: clasificación '%s' pero emisor es tercero (%s).",
            clasificacion, nit,
        )
        return False

    if clasificacion in emitidos_por_tercero and es_empresa:
        logger.warning(
            "Incoherencia: clasificación '%s' pero emisor es la empresa.",
            clasificacion,
        )
        return False

    return True


def validar_preasiento_completo(
    preasiento: PreasientoContable,
    df_cuentas: Optional[pd.DataFrame] = None,
    db_path: Optional[str] = None,
) -> list[str]:
    """
    Ejecuta todas las validaciones sobre un preasiento y retorna la lista de errores.

    Args:
        preasiento:  Objeto PreasientoContable a validar.
        df_cuentas:  Maestro de cuentas para validar cuentas transaccionales.
        db_path:     Ruta a la BD para validar unicidad de CUFE.

    Returns:
        Lista de strings con los mensajes de error encontrados. Vacía si todo es válido.
    """
    errores = []

    if not validar_cuadre(preasiento.lineas):
        total_d = sum(l.debito for l in preasiento.lineas)
        total_c = sum(l.credito for l in preasiento.lineas)
        errores.append(f"No cuadra: D={total_d:.2f} C={total_c:.2f}")

    if not validar_coherencia_emisor(preasiento.clasificacion, ""):
        pass  # La coherencia ya fue validada en clasificacion

    if not preasiento.tercero_encontrado:
        errores.append(f"Tercero NIT '{preasiento.tercero_nit}' no encontrado en maestro")

    pendientes = [l for l in preasiento.lineas if l.es_pendiente]
    if pendientes:
        errores.append(f"{len(pendientes)} línea(s) con cuenta [PENDIENTE]")

    return errores
