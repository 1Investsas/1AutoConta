"""
Modelos de datos del sistema 1ContaBot.

Define los dataclasses usados internamente para representar documentos,
terceros, cuentas, comprobantes, líneas contables y registros de bitácora.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class DocumentoImportado:
    """Representa un documento electrónico importado desde RADIAN."""
    cufe: str
    tipo_documento: str
    folio: str
    prefijo: str
    divisa: str
    forma_pago: str
    medio_pago: str
    fecha_emision: Optional[datetime]
    fecha_recepcion: Optional[datetime]
    nit_emisor: str
    nombre_emisor: str
    nit_receptor: str
    nombre_receptor: str
    iva: float
    ica: float
    ic: float
    inc: float
    timbre: float
    inc_bolsas: float
    in_carbono: float
    in_combustibles: float
    ic_datos: float
    icl: float
    inpp: float
    ibua: float
    icui: float
    rete_iva: float
    rete_renta: float
    rete_ica: float
    total: float
    estado: str
    grupo: str
    clasificacion: str = "SIN_CLASIFICAR"


@dataclass
class Tercero:
    """Representa un tercero del maestro de terceros."""
    nombre: str
    tipo_identificacion: str
    identificacion: str
    digito_verificacion: str
    sucursal: str
    tipo_regimen_iva: str
    direccion: str
    ciudad: str
    telefono: str
    nombres_contacto: str
    estado: str


@dataclass
class CuentaContable:
    """Representa una cuenta del plan de cuentas."""
    codigo: str
    nombre: str
    categoria: str
    clase: str
    relacion_con: str
    maneja_vencimientos: str
    diferencia_fiscal: str
    activo: str
    nivel_agrupacion: str


@dataclass
class TipoComprobante:
    """Representa un tipo de comprobante contable."""
    codigo: str
    titulo: str


@dataclass
class LineaContable:
    """Representa una línea dentro de un preasiento contable."""
    cufe: str
    numero_linea: int
    cuenta: str
    descripcion_cuenta: str
    debito: float
    credito: float
    concepto: str
    tercero_nit: str
    tercero_nombre: str
    es_pendiente: bool = False  # True si la cuenta aún debe definir el usuario
    es_sugerida: bool = False   # True si la cuenta fue sugerida por el motor (Fase 2)


@dataclass
class PreasientoContable:
    """Agrupa las líneas de un documento en un preasiento completo."""
    cufe: str
    tipo_documento: str
    clasificacion: str
    codigo_comprobante: str
    titulo_comprobante: str
    fecha_emision: Optional[datetime]
    folio: str
    prefijo: str
    tercero_nit: str
    tercero_nombre: str
    tercero_encontrado: bool
    total: float
    base_gravable: float
    lineas: list[LineaContable] = field(default_factory=list)
    cuadra: bool = False
    excepciones: list[str] = field(default_factory=list)
    # NIT identificado originalmente desde RADIAN, antes de cualquier corrección
    # manual o aprendida. Sirve de clave para trazabilidad/aprendizaje (Fase 1).
    tercero_nit_original: str = ""
    # True si el tercero fue reemplazado por una corrección aprendida del historial.
    tercero_corregido: bool = False


@dataclass
class RegistroBitacora:
    """Registro de una acción en la bitácora del sistema."""
    timestamp: datetime
    nivel: str          # INFO, WARNING, ERROR
    modulo: str
    accion: str
    detalle: str
    cufe: Optional[str] = None


@dataclass
class HistorialCuenta:
    """Historial de asignación de cuentas a tipos de documento y tercero (Fase 2)."""
    clasificacion: str
    nit_tercero: str
    tipo_linea: str     # "base", "iva", "rete_renta", etc.
    cuenta: str
    usos: int = 1
    ultima_vez: Optional[datetime] = None
