"""Conectores contables. Cada conector devuelve movimientos normalizados
(MovimientoContable) que el servicio de sincronización agrega por línea
presupuestal usando los mapeos de cuentas PUC."""
from .base import ConectorContable, MovimientoContable
from .siigo import ConectorSiigo
from .alegra import ConectorAlegra
from .csv_file import ConectorCSV

from ..models import FuenteDato

REGISTRO_CONECTORES = {
    FuenteDato.SIIGO: ConectorSiigo,
    FuenteDato.ALEGRA: ConectorAlegra,
    FuenteDato.CSV: ConectorCSV,
}


def crear_conector(fuente: FuenteDato, config: dict) -> ConectorContable:
    cls = REGISTRO_CONECTORES.get(fuente)
    if cls is None:
        raise ValueError(f"No hay conector registrado para la fuente '{fuente}'")
    return cls(config)


__all__ = [
    "ConectorContable", "MovimientoContable",
    "ConectorSiigo", "ConectorAlegra", "ConectorCSV",
    "crear_conector", "REGISTRO_CONECTORES",
]
