"""Esquemas Pydantic (v2) para la API."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import FuenteDato, TipoFlujo, TipoValor

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
         "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


# ---------- Empresa ----------
class EmpresaCrear(BaseModel):
    nombre: str
    nit: str | None = None
    conector: FuenteDato = FuenteDato.MANUAL
    conector_config: str | None = None  # JSON string


class EmpresaOut(EmpresaCrear):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- Estructura del presupuesto ----------
class LineaCrear(BaseModel):
    nombre: str
    orden: int = 0
    cuentas: list[str] = Field(default_factory=list, description="Códigos/prefijos PUC")


class CategoriaCrear(BaseModel):
    nombre: str
    tipo: TipoFlujo
    orden: int = 0
    lineas: list[LineaCrear] = Field(default_factory=list)


class PresupuestoCrear(BaseModel):
    empresa_id: int
    anio: int
    nombre: str
    saldo_inicial_caja: float = 0.0
    umbral_alerta: float = 5.0
    umbral_critico: float = 15.0
    categorias: list[CategoriaCrear] = Field(default_factory=list)


class MapeoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    codigo_cuenta: str
    invertir_signo: bool


class LineaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre: str
    orden: int
    mapeos: list[MapeoOut] = []


class CategoriaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre: str
    tipo: TipoFlujo
    orden: int
    lineas: list[LineaOut] = []


class PresupuestoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    empresa_id: int
    anio: int
    nombre: str
    saldo_inicial_caja: float
    umbral_alerta: float
    umbral_critico: float
    categorias: list[CategoriaOut] = []


# ---------- Carga de valores ----------
class ValorItem(BaseModel):
    linea_id: int
    mes: int = Field(ge=1, le=12)
    valor: float


class CargaValores(BaseModel):
    """Carga masiva de valores proyectados o ejecutados."""
    tipo: TipoValor
    fuente: FuenteDato = FuenteDato.MANUAL
    valores: list[ValorItem]


# ---------- Salidas de análisis ----------
class LineaFlujo(BaseModel):
    linea_id: int
    nombre: str
    proyectado: list[float]   # 12 posiciones
    ejecutado: list[float]    # 12 posiciones
    total_proyectado: float
    total_ejecutado: float


class CategoriaFlujo(BaseModel):
    categoria_id: int
    nombre: str
    tipo: TipoFlujo
    lineas: list[LineaFlujo]
    subtotal_proyectado: list[float]
    subtotal_ejecutado: list[float]


class FlujoCaja(BaseModel):
    presupuesto_id: int
    anio: int
    meses: list[str] = MESES
    saldo_inicial_caja: float
    categorias: list[CategoriaFlujo]
    ingresos_proyectados: list[float]
    ingresos_ejecutados: list[float]
    egresos_proyectados: list[float]
    egresos_ejecutados: list[float]
    flujo_neto_proyectado: list[float]
    flujo_neto_ejecutado: list[float]
    saldo_acumulado_proyectado: list[float]
    saldo_acumulado_ejecutado: list[float]


class VariacionLinea(BaseModel):
    linea_id: int
    categoria: str
    nombre: str
    tipo: TipoFlujo
    proyectado: float
    ejecutado: float
    variacion_absoluta: float
    variacion_pct: float | None  # None si proyectado == 0
    cumplimiento_pct: float | None
    semaforo: str  # verde | amarillo | rojo | sin_dato
    favorable: bool | None


class AnalisisComparativo(BaseModel):
    presupuesto_id: int
    anio: int
    mes: int | None  # None = acumulado YTD
    alcance: str     # "mes" o "acumulado"
    lineas: list[VariacionLinea]
    resumen: dict
    alertas: list[str]


class ResultadoSync(BaseModel):
    exito: bool
    fuente: FuenteDato
    anio: int
    mes: int
    lineas_actualizadas: int
    mensaje: str
    detalle: list[dict] = []
