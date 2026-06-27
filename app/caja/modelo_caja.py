"""
Modelo de dominio del módulo Caja General.

Caja General controla los movimientos de efectivo (billetes y monedas) de una
cuenta de caja, organizados por período mensual. A diferencia de Bancos —donde
los movimientos provienen de un extracto externo— aquí la propia aplicación
estructura el formato en el que el usuario diligencia las entradas y salidas.

Este módulo concentra la lógica pura (sin BD ni web):

- ``MovimientoCaja``: una entrada o salida de efectivo.
- ``recalcular_saldos``: ordena cronológicamente y calcula el saldo acumulado
  a partir del saldo inicial del período.
- ``totales`` / ``saldo_final``: agregados del período (entradas, salidas, cierre).
- Serialización ``a_dict`` / ``desde_dict`` para sesión Flask y BD.

El saldo NUNCA se digita: siempre se deriva del saldo inicial y de las
entradas/salidas, recalculándose ante cualquier cambio (agregar, eliminar,
modificar fecha/valor o cambiar el saldo inicial).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

# Tipos de movimiento controlados.
ENTRADA = "entrada"
SALIDA = "salida"
TIPOS_MOVIMIENTO = (ENTRADA, SALIDA)

# Estados del período de caja (orden sugerido del flujo de trabajo).
ESTADO_BORRADOR = "borrador"
ESTADO_EN_REVISION = "en_revision"
ESTADO_APROBADO = "aprobado"
ESTADO_CERRADO = "cerrado"
ESTADO_REABIERTO = "reabierto"

# Estados en los que el período se considera editable.
ESTADOS_EDITABLES = (ESTADO_BORRADOR, ESTADO_EN_REVISION, ESTADO_REABIERTO)

ESTADOS_CAJA = {
    ESTADO_BORRADOR: "Borrador",
    ESTADO_EN_REVISION: "En revisión",
    ESTADO_APROBADO: "Aprobado",
    ESTADO_CERRADO: "Cerrado",
    ESTADO_REABIERTO: "Reabierto",
}

MESES_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def a_decimal(valor) -> Decimal:
    """Convierte un valor heterogéneo (str/int/float/None) a Decimal.

    Acepta separadores de miles y comas decimales típicos del usuario
    colombiano ("1.234.567,89" o "1,234,567.89"). Retorna ``Decimal('0')``
    ante valores vacíos o no numéricos, sin lanzar excepción.
    """
    if valor is None or valor == "":
        return Decimal("0")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        try:
            return Decimal(str(valor))
        except InvalidOperation:
            return Decimal("0")
    texto = str(valor).strip()
    if not texto:
        return Decimal("0")
    texto = texto.replace("$", "").replace(" ", "")
    # Normaliza separadores: si tiene ',' y '.', el último es el decimal.
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        # Coma sola: tratarla como separador decimal.
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return Decimal("0")


def _parse_fecha(valor) -> Optional[date]:
    """Convierte un valor a ``date`` aceptando varios formatos comunes."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class MovimientoCaja:
    """Una entrada o salida de efectivo dentro de un período de caja."""

    sequence: int                       # consecutivo interno (orden de registro)
    movement_date: Optional[date]
    movement_type: str                  # ENTRADA | SALIDA
    concept: str = ""
    third_party_nit: str = ""
    third_party_name: str = ""
    cost_center: str = ""
    category: str = ""
    inflow_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    outflow_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    running_balance: Decimal = field(default_factory=lambda: Decimal("0"))
    observations: str = ""
    id: Optional[int] = None            # id en BD (None si aún no persistido)

    @property
    def monto(self) -> Decimal:
        """Valor neto del movimiento (positivo entrada, negativo salida)."""
        if self.movement_type == SALIDA:
            return -abs(self.outflow_amount)
        return abs(self.inflow_amount)


def normalizar_tipo(valor: str) -> str:
    """Normaliza el texto del tipo de movimiento a ENTRADA/SALIDA.

    Acepta variantes ("Entrada", "ingreso", "salida", "egreso", "E"/"S").
    Retorna "" si no reconoce el valor (para que la validación lo marque).
    """
    t = (valor or "").strip().lower()
    if t in ("entrada", "ingreso", "e", "in", "+"):
        return ENTRADA
    if t in ("salida", "egreso", "s", "out", "-"):
        return SALIDA
    return ""


def recalcular_saldos(
    movimientos: list[MovimientoCaja],
    opening_balance,
) -> list[MovimientoCaja]:
    """Ordena cronológicamente y recalcula el saldo acumulado de cada movimiento.

    Reglas (sección 8 de la especificación):
      - El primer movimiento parte del saldo inicial del período.
      - Saldo N = Saldo N-1 + Entrada N - Salida N.
      - El orden es cronológico; ante igual fecha se respeta el consecutivo
        interno (``sequence``), que refleja el orden de registro.

    Modifica ``running_balance`` in place y retorna la lista ya ordenada.
    """
    saldo = a_decimal(opening_balance)
    # Movimientos sin fecha van al final, conservando su consecutivo.
    ordenados = sorted(
        movimientos,
        key=lambda m: (m.movement_date or date.max, m.sequence),
    )
    for mov in ordenados:
        saldo = saldo + abs(mov.inflow_amount) - abs(mov.outflow_amount)
        mov.running_balance = saldo
    return ordenados


def totales(movimientos: Iterable[MovimientoCaja]) -> tuple[Decimal, Decimal]:
    """Retorna (total_entradas, total_salidas) del período."""
    entradas = sum((abs(m.inflow_amount) for m in movimientos), Decimal("0"))
    salidas = sum((abs(m.outflow_amount) for m in movimientos), Decimal("0"))
    return entradas, salidas


def saldo_final(opening_balance, movimientos: Iterable[MovimientoCaja]) -> Decimal:
    """Saldo final del mes = saldo inicial + total entradas - total salidas."""
    entradas, salidas = totales(movimientos)
    return a_decimal(opening_balance) + entradas - salidas


def renumerar(movimientos: list[MovimientoCaja]) -> list[MovimientoCaja]:
    """Reasigna consecutivos 1..N en el orden actual de la lista."""
    for i, mov in enumerate(movimientos, start=1):
        mov.sequence = i
    return movimientos


# ---------------------------------------------------------------------------
# Serialización (sesión Flask / BD)
# ---------------------------------------------------------------------------

def a_dict(m: MovimientoCaja) -> dict:
    """Convierte un MovimientoCaja a dict serializable (montos como str)."""
    return {
        "id": m.id,
        "sequence": m.sequence,
        "movement_date": m.movement_date.isoformat() if m.movement_date else "",
        "movement_type": m.movement_type,
        "concept": m.concept,
        "third_party_nit": m.third_party_nit,
        "third_party_name": m.third_party_name,
        "cost_center": m.cost_center,
        "category": m.category,
        "inflow_amount": str(m.inflow_amount),
        "outflow_amount": str(m.outflow_amount),
        "running_balance": str(m.running_balance),
        "observations": m.observations,
    }


def desde_dict(d: dict) -> MovimientoCaja:
    """Reconstruye un MovimientoCaja desde un dict (sesión/BD/formulario)."""
    return MovimientoCaja(
        id=d.get("id"),
        sequence=int(d.get("sequence") or 0),
        movement_date=_parse_fecha(d.get("movement_date")),
        movement_type=normalizar_tipo(d.get("movement_type", "")),
        concept=str(d.get("concept", "")).strip(),
        third_party_nit=str(d.get("third_party_nit", "")).strip(),
        third_party_name=str(d.get("third_party_name", "")).strip(),
        cost_center=str(d.get("cost_center", "")).strip(),
        category=str(d.get("category", "")).strip(),
        inflow_amount=a_decimal(d.get("inflow_amount")),
        outflow_amount=a_decimal(d.get("outflow_amount")),
        running_balance=a_decimal(d.get("running_balance")),
        observations=str(d.get("observations", "")).strip(),
    )


# ---------------------------------------------------------------------------
# Validación (sección 13 de la especificación)
# ---------------------------------------------------------------------------

def validar_movimiento(
    m: MovimientoCaja,
    anio: Optional[int] = None,
    mes: Optional[int] = None,
) -> list[str]:
    """Valida un movimiento y retorna la lista de errores (vacía si es válido).

    Reglas:
      - Fecha obligatoria y, si se da el período, dentro del mes/año.
      - Concepto obligatorio.
      - Tipo de movimiento controlado (entrada/salida).
      - El valor debe ir en el campo del tipo: entrada→inflow, salida→outflow.
      - No se permite entrada y salida simultáneas, ni movimientos en cero.
      - Los montos deben ser positivos.
    """
    errores: list[str] = []

    if m.movement_date is None:
        errores.append("La fecha es obligatoria y debe ser válida.")
    elif anio and mes and (m.movement_date.year != anio or m.movement_date.month != mes):
        errores.append(
            f"La fecha {m.movement_date.isoformat()} está fuera del período "
            f"{mes:02d}/{anio}."
        )

    if not m.concept.strip():
        errores.append("El concepto es obligatorio.")

    if m.movement_type not in TIPOS_MOVIMIENTO:
        errores.append("El tipo de movimiento debe ser 'entrada' o 'salida'.")

    entrada = abs(m.inflow_amount)
    salida = abs(m.outflow_amount)

    if m.inflow_amount < 0 or m.outflow_amount < 0:
        errores.append("Los valores deben ser positivos.")

    if entrada > 0 and salida > 0:
        errores.append("No se permite entrada y salida en el mismo movimiento.")
    elif entrada == 0 and salida == 0:
        errores.append("El movimiento no puede tener valor cero.")
    elif m.movement_type == ENTRADA and entrada == 0:
        errores.append("Un movimiento de entrada debe tener valor en 'Entrada'.")
    elif m.movement_type == SALIDA and salida == 0:
        errores.append("Un movimiento de salida debe tener valor en 'Salida'.")

    return errores

