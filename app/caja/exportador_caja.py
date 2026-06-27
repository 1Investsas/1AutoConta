"""
Exportador de movimientos de Caja General al formato SIIGO.

Reutiliza ``_escribir_chunk`` del exportador RADIAN/Bancos para mantener el
mismo formato de Excel (colores, anchos, estilos) y el límite de 500 filas por
archivo.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.caja.mapeador_caja import mapear_caja_a_siigo
from app.caja.modelo_caja import MovimientoCaja
from app.config import OUTPUT_DIR, SIIGO_MAX_FILAS_POR_ARCHIVO
from app.siigo.exportador_siigo import _escribir_chunk
from app.siigo.mapeador import partir_en_chunks

logger = logging.getLogger(__name__)


def exportar_caja_siigo(
    movimientos: list[MovimientoCaja],
    cuenta_caja: str,
    output_path: str | None = None,
    max_filas: int = SIIGO_MAX_FILAS_POR_ARCHIVO,
    df_cuentas=None,
) -> list[str]:
    """Genera los archivos Excel SIIGO para los movimientos de un período de caja.

    Args:
        movimientos:  Lista de MovimientoCaja del período.
        cuenta_caja:  Código contable de la cuenta de caja (lado fijo del asiento).
        output_path:  Directorio de salida (default: OUTPUT_DIR).
        max_filas:    Máximo de filas por archivo (default 500).
        df_cuentas:   (reservado) maestro de cuentas.

    Returns:
        Lista de rutas absolutas de los archivos generados.
    """
    base_dir = Path(output_path or OUTPUT_DIR)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_nombre = f"siigo_caja_{ts}"

    filas = mapear_caja_a_siigo(movimientos, cuenta_caja, df_cuentas=df_cuentas)
    if not filas:
        raise ValueError("No hay movimientos de caja para exportar.")

    chunks = partir_en_chunks(filas, max_filas)
    rutas: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        sufijo = f"_parte{i}" if len(chunks) > 1 else ""
        filepath = base_dir / f"{base_nombre}{sufijo}.xlsx"
        _escribir_chunk(chunk, filepath)
        rutas.append(str(filepath.resolve()))
        logger.info("Archivo caja SIIGO generado: %s (%d filas)", filepath, len(chunk))

    return rutas
