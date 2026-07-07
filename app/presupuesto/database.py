"""Configuración de base de datos (SQLAlchemy 2.x).

El módulo es agnóstico del motor: SQLite por defecto (archivo
``presupuesto.db`` junto a las demás BDs del sistema, en DB_DIR),
PostgreSQL/MySQL en producción vía la variable de entorno
PRESUPUESTO_DATABASE_URL.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _url_por_defecto() -> str:
    from app.config import DB_DIR
    return "sqlite:///" + os.path.join(DB_DIR, "presupuesto.db")


DATABASE_URL = os.getenv("PRESUPUESTO_DATABASE_URL") or _url_por_defecto()

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

    # Con SQLite en archivo, garantizar que exista el directorio destino.
    if DATABASE_URL.startswith("sqlite:///") and ":memory:" not in DATABASE_URL:
        carpeta = os.path.dirname(DATABASE_URL.removeprefix("sqlite:///"))
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)

    Base.metadata.create_all(bind=engine)
