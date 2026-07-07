"""Modelo de datos del sistema presupuestal.

Jerarquía:
    Empresa → Presupuesto (por año) → CategoriaPresupuesto → LineaPresupuesto
    LineaPresupuesto → ValorMensual (proyectado y ejecutado, mes a mes)
    LineaPresupuesto → MapeoCuenta (cuentas contables PUC que alimentan el ejecutado)
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TipoFlujo(str, enum.Enum):
    """Naturaleza de la categoría dentro del flujo de caja."""
    INGRESO = "ingreso"
    EGRESO = "egreso"


class TipoValor(str, enum.Enum):
    PROYECTADO = "proyectado"
    EJECUTADO = "ejecutado"


class FuenteDato(str, enum.Enum):
    MANUAL = "manual"
    SIIGO = "siigo"
    ALEGRA = "alegra"
    CSV = "csv"


class Empresa(Base):
    __tablename__ = "pres_empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    nit: Mapped[str | None] = mapped_column(String(30))
    # Id de la empresa en el registro principal de 1ContaBot (app/empresas.py);
    # vincula cada empresa presupuestal con la empresa activa de la sesión web.
    ref_externa: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    # Conector contable configurado para esta empresa
    conector: Mapped[FuenteDato] = mapped_column(Enum(FuenteDato), default=FuenteDato.MANUAL)
    conector_config: Mapped[str | None] = mapped_column(Text)  # JSON con credenciales/params
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    presupuestos: Mapped[list["Presupuesto"]] = relationship(
        back_populates="empresa", cascade="all, delete-orphan"
    )


class Presupuesto(Base):
    """Un presupuesto anual de flujo de caja para una empresa."""
    __tablename__ = "pres_presupuestos"
    __table_args__ = (UniqueConstraint("empresa_id", "anio", name="uq_presupuesto_empresa_anio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("pres_empresas.id"))
    anio: Mapped[int] = mapped_column(Integer)
    nombre: Mapped[str] = mapped_column(String(200))
    saldo_inicial_caja: Mapped[float] = mapped_column(Float, default=0.0)
    # Umbrales del semáforo de variaciones (% sobre lo proyectado)
    umbral_alerta: Mapped[float] = mapped_column(Float, default=5.0)    # amarillo
    umbral_critico: Mapped[float] = mapped_column(Float, default=15.0)  # rojo
    notas: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    empresa: Mapped["Empresa"] = relationship(back_populates="presupuestos")
    categorias: Mapped[list["CategoriaPresupuesto"]] = relationship(
        back_populates="presupuesto", cascade="all, delete-orphan",
        order_by="CategoriaPresupuesto.orden",
    )


class CategoriaPresupuesto(Base):
    """Agrupador del flujo de caja: p. ej. Ingresos operacionales,
    Gastos de administración, Inversión (CAPEX), Financiación."""
    __tablename__ = "pres_categorias"

    id: Mapped[int] = mapped_column(primary_key=True)
    presupuesto_id: Mapped[int] = mapped_column(ForeignKey("pres_presupuestos.id"))
    nombre: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[TipoFlujo] = mapped_column(Enum(TipoFlujo))
    orden: Mapped[int] = mapped_column(Integer, default=0)

    presupuesto: Mapped["Presupuesto"] = relationship(back_populates="categorias")
    lineas: Mapped[list["LineaPresupuesto"]] = relationship(
        back_populates="categoria", cascade="all, delete-orphan",
        order_by="LineaPresupuesto.orden",
    )


class LineaPresupuesto(Base):
    """Concepto presupuestal: p. ej. Ventas de contado, Nómina, Arriendo."""
    __tablename__ = "pres_lineas"

    id: Mapped[int] = mapped_column(primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("pres_categorias.id"))
    nombre: Mapped[str] = mapped_column(String(200))
    orden: Mapped[int] = mapped_column(Integer, default=0)

    categoria: Mapped["CategoriaPresupuesto"] = relationship(back_populates="lineas")
    valores: Mapped[list["ValorMensual"]] = relationship(
        back_populates="linea", cascade="all, delete-orphan"
    )
    mapeos: Mapped[list["MapeoCuenta"]] = relationship(
        back_populates="linea", cascade="all, delete-orphan"
    )


class ValorMensual(Base):
    """Valor de una línea en un mes: proyectado o ejecutado."""
    __tablename__ = "pres_valores"
    __table_args__ = (
        UniqueConstraint("linea_id", "mes", "tipo", name="uq_valor_linea_mes_tipo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    linea_id: Mapped[int] = mapped_column(ForeignKey("pres_lineas.id"))
    mes: Mapped[int] = mapped_column(Integer)  # 1..12
    tipo: Mapped[TipoValor] = mapped_column(Enum(TipoValor))
    valor: Mapped[float] = mapped_column(Float, default=0.0)
    fuente: Mapped[FuenteDato] = mapped_column(Enum(FuenteDato), default=FuenteDato.MANUAL)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    linea: Mapped["LineaPresupuesto"] = relationship(back_populates="valores")


class MapeoCuenta(Base):
    """Vincula una línea presupuestal con cuentas contables (código PUC o
    prefijo). Permite que la sincronización automática sume los movimientos
    contables del mes en la línea correcta."""
    __tablename__ = "pres_mapeos"

    id: Mapped[int] = mapped_column(primary_key=True)
    linea_id: Mapped[int] = mapped_column(ForeignKey("pres_lineas.id"))
    # Prefijo de cuenta: "4135" cubre 413501, 413502, ...
    codigo_cuenta: Mapped[str] = mapped_column(String(20))
    descripcion: Mapped[str | None] = mapped_column(String(200))
    # Si el movimiento contable llega con signo contrario, invertir
    invertir_signo: Mapped[bool] = mapped_column(Boolean, default=False)

    linea: Mapped["LineaPresupuesto"] = relationship(back_populates="mapeos")


class LogSincronizacion(Base):
    """Auditoría de cada sincronización con el software contable."""
    __tablename__ = "pres_sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("pres_empresas.id"))
    presupuesto_id: Mapped[int | None] = mapped_column(ForeignKey("pres_presupuestos.id"))
    fuente: Mapped[FuenteDato] = mapped_column(Enum(FuenteDato))
    anio: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)
    exito: Mapped[bool] = mapped_column(Boolean, default=False)
    lineas_actualizadas: Mapped[int] = mapped_column(Integer, default=0)
    mensaje: Mapped[str | None] = mapped_column(Text)
    ejecutado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
