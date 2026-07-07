"""Configuración de base de datos (SQLAlchemy 2.x).

El módulo es agnóstico del motor: SQLite para demo/desarrollo,
PostgreSQL/MySQL en producción vía la variable de entorno
PRESUPUESTO_DATABASE_URL (o la que ya use 1ContaBot).
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.getenv("PRESUPUESTO_DATABASE_URL", "sqlite:///./presupuesto.db")

_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in DATABASE_URL:
        # Una sola conexión compartida (necesario para tests multi-hilo)
        _kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """Dependencia FastAPI: sesión por request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crea las tablas si no existen."""
    from . import models  # noqa: F401  (registra los modelos)

    Base.metadata.create_all(bind=engine)
