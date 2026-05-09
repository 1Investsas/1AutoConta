"""
Exportador de extracto bancario al formato SIIGO.

Reutiliza _escribir_chunk del exportador RADIAN para mantener el mismo
formato de Excel (mismos colores, anchos, estilos).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.banco.importador_banco import MovimientoBanco
from app.banco.mapeador_banco import mapear_banco_a_siigo
from app.config import OUTPUT_DIR, SIIGO_MAX_FILAS_POR_ARCHIVO
from app.siigo.exportador_siigo import _escribir_chunk
from app.siigo.mapeador import partir_en_chunks

logger = logging.getLogger(__name__)


def exportar_banco_siigo(
    movimientos: list[MovimientoBanco],
    cuenta_banco: str,
    asignaciones: list[dict],
    nit_banco: str = "",
    output_path: str | None = None,
    max_filas: int = SIIGO_MAX_FILAS_POR_ARCHIVO,
    df_cuentas=None,
) -> list[str]:
    """
    Genera los archivos Excel SIIGO para los movimientos bancarios.

    Args:
        movimientos:   Lista de MovimientoBanco (resultado de leer_csv_banco).
        cuenta_banco:  Código contable del banco (ej. "11100501").
        asignaciones:  Lista con las asignaciones del usuario.
        nit_banco:     NIT del banco (auto-aplicado a 4x1000 y movimientos bancarios).
        output_path:   Directorio de salida (default: OUTPUT_DIR).
        max_filas:     Máximo de filas por archivo (default 500).
        df_cuentas:    (reservado para futura lógica de vencimientos).

    Returns:
        Lista de rutas absolutas de los archivos generados.
    """
    base_dir   = Path(output_path or OUTPUT_DIR)
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_nombre = f"siigo_banco_{ts}"

    filas = mapear_banco_a_siigo(
        movimientos, cuenta_banco, asignaciones,
        nit_banco=nit_banco, df_cuentas=df_cuentas,
    )

    if not filas:
        raise ValueError("No hay filas para exportar.")

    chunks = partir_en_chunks(filas, max_filas)
    rutas: list[str] = []

    for i, chunk in enumerate(chunks, start=1):
        sufijo   = f"_parte{i}" if len(chunks) > 1 else ""
        filepath = base_dir / f"{base_nombre}{sufijo}.xlsx"
        _escribir_chunk(chunk, filepath)
        rutas.append(str(filepath.resolve()))
        logger.info("Archivo banco SIIGO generado: %s (%d filas)", filepath, len(chunk))

    return rutas
