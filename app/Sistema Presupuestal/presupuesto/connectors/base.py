"""Contrato común de los conectores contables."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MovimientoContable:
    """Movimiento contable normalizado, independiente del software de origen.

    valor: positivo = movimiento neto en la naturaleza de la cuenta
    (crédito neto en ingresos, débito neto en gastos/costos).
    """
    codigo_cuenta: str
    nombre_cuenta: str
    valor: float
    fecha: str  # ISO YYYY-MM-DD


class ConectorContable(ABC):
    """Interfaz que debe implementar todo conector.

    Para agregar un software contable nuevo (World Office, SIESA, etc.):
    1. Crear una clase que herede de ConectorContable.
    2. Implementar obtener_movimientos(anio, mes).
    3. Registrarla en REGISTRO_CONECTORES (connectors/__init__.py).
    """

    def __init__(self, config: dict):
        self.config = config or {}

    @abstractmethod
    def probar_conexion(self) -> tuple[bool, str]:
        """Valida credenciales. Devuelve (exito, mensaje)."""

    @abstractmethod
    def obtener_movimientos(self, anio: int, mes: int) -> list[MovimientoContable]:
        """Movimientos contables netos del mes, agregados por cuenta."""
