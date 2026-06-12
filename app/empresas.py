"""
Gestión de múltiples empresas.

Cada empresa puede tener:
- Su propio NIT y nombre.
- Su propia base de datos (db/contable_<id>.db).
- Su propia carpeta de archivos maestros (data/<id>/).
- Cuentas contables propias (contraparte, impuestos, banco).
- Formato propio del extracto bancario (posiciones de columnas,
  separador, formato de fecha, etc.).

El registro de empresas se guarda en data/empresas.json (o en Blob Storage
en modo cloud). La empresa "principal" siempre existe y se construye desde
las variables de entorno, manteniendo compatibilidad con el comportamiento
de una sola empresa.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field, asdict

from app import config
from app import storage as store

logger = logging.getLogger(__name__)

ARCHIVO_EMPRESAS = "empresas.json"
EMPRESA_PRINCIPAL_ID = "principal"

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Formato del extracto bancario
# ---------------------------------------------------------------------------

# Valores por defecto = formato actual (CSV sin encabezados, fecha yyyymmdd)
FORMATO_BANCO_DEFAULT: dict = {
    "delimitador": ",",
    "filas_encabezado": 0,        # nº de filas a saltar al inicio
    "col_cuenta": 0,              # columna del nº de cuenta bancaria
    "col_codigo_banco": 1,        # columna del código interno del banco
    "col_fecha": 3,               # columna de la fecha
    "col_valor": 5,               # columna del valor del movimiento
    "col_codigo_detalle": 6,      # columna del código de detalle
    "col_descripcion": 7,         # columna de la descripción
    "formato_fecha": "%Y%m%d",    # formato strptime de la fecha
    "separador_decimal": ".",
    "separador_miles": ",",
}


@dataclass
class Empresa:
    """Configuración completa de una empresa."""

    id: str
    nit: str
    nombre: str
    # Overrides sobre los valores por defecto de config.py.
    # Solo se guardan las claves que difieren; el resto hereda del default.
    cuentas_contraparte: dict = field(default_factory=dict)
    cuentas_impuestos: dict = field(default_factory=dict)
    # Banco
    cuenta_banco_default: str = ""
    nit_banco: str = ""
    formato_banco: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Valores efectivos (defaults de config.py + overrides de la empresa)
    # ------------------------------------------------------------------

    @property
    def es_principal(self) -> bool:
        return self.id == EMPRESA_PRINCIPAL_ID

    @property
    def db_path(self) -> str:
        if self.es_principal:
            return config.DB_PATH
        return f"db/contable_{self.id}.db"

    @property
    def data_category(self) -> str:
        """Categoría/carpeta donde viven los archivos maestros de la empresa."""
        if self.es_principal:
            return "data"
        return f"data/{self.id}"

    def cuentas_contraparte_efectivas(self) -> dict:
        return {**config.CUENTAS_CONTRAPARTE, **(self.cuentas_contraparte or {})}

    def cuentas_impuestos_efectivas(self) -> dict:
        base = {k: dict(v) for k, v in config.CUENTAS_IMPUESTOS.items()}
        for nombre, cuentas in (self.cuentas_impuestos or {}).items():
            base.setdefault(nombre, {}).update(cuentas)
        return base

    def formato_banco_efectivo(self) -> dict:
        return {**FORMATO_BANCO_DEFAULT, **(self.formato_banco or {})}

    def cuenta_banco_efectiva(self) -> str:
        return self.cuenta_banco_default or config.BANCO_CUENTA_DEFAULT

    def ruta_maestro(self, filename: str) -> str:
        """Ruta local a un archivo maestro de esta empresa."""
        return store.get_local_data_path(filename, category=self.data_category)


def _empresa_principal() -> Empresa:
    """Empresa por defecto, construida desde las variables de entorno."""
    return Empresa(
        id=EMPRESA_PRINCIPAL_ID,
        nit=config.NIT_EMPRESA,
        nombre=config.NOMBRE_EMPRESA,
        cuenta_banco_default=config.BANCO_CUENTA_DEFAULT,
    )


# ---------------------------------------------------------------------------
# Persistencia del registro (data/empresas.json)
# ---------------------------------------------------------------------------

def _leer_registro() -> dict:
    try:
        path = store.get_local_data_path(ARCHIVO_EMPRESAS)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _escribir_registro(registro: dict) -> None:
    data = json.dumps(registro, ensure_ascii=False, indent=2).encode("utf-8")
    store.save_file(data, "data", ARCHIVO_EMPRESAS)


def _desde_dict(d: dict) -> Empresa:
    return Empresa(
        id=d["id"],
        nit=d.get("nit", ""),
        nombre=d.get("nombre", ""),
        cuentas_contraparte=d.get("cuentas_contraparte", {}) or {},
        cuentas_impuestos=d.get("cuentas_impuestos", {}) or {},
        cuenta_banco_default=d.get("cuenta_banco_default", ""),
        nit_banco=d.get("nit_banco", ""),
        formato_banco=d.get("formato_banco", {}) or {},
    )


def listar_empresas() -> list[Empresa]:
    """Retorna todas las empresas; la principal siempre va primero."""
    registro = _leer_registro()
    empresas = [_empresa_principal()]
    for emp_id in sorted(registro):
        if emp_id != EMPRESA_PRINCIPAL_ID:
            empresas.append(_desde_dict(registro[emp_id]))
    return empresas


def obtener_empresa(empresa_id: str | None) -> Empresa:
    """
    Retorna la empresa con el id dado. Si no existe (o id es None/vacío)
    retorna la empresa principal.
    """
    if not empresa_id or empresa_id == EMPRESA_PRINCIPAL_ID:
        return _empresa_principal()
    registro = _leer_registro()
    if empresa_id in registro:
        return _desde_dict(registro[empresa_id])
    logger.warning("Empresa '%s' no encontrada; usando la principal.", empresa_id)
    return _empresa_principal()


def _slug(nombre: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", nombre.strip().lower()).strip("_")
    return s or "empresa"


def guardar_empresa(empresa: Empresa) -> Empresa:
    """Crea o actualiza una empresa en el registro (excepto la principal)."""
    if empresa.es_principal:
        raise ValueError(
            "La empresa principal se configura por variables de entorno, "
            "no se puede editar desde el registro."
        )
    with _lock:
        registro = _leer_registro()
        registro[empresa.id] = asdict(empresa)
        _escribir_registro(registro)
    logger.info("Empresa guardada: %s (%s)", empresa.nombre, empresa.id)
    return empresa


def crear_empresa(nit: str, nombre: str, **kwargs) -> Empresa:
    """Crea una empresa nueva con id derivado del nombre."""
    base = _slug(nombre)
    with _lock:
        registro = _leer_registro()
        emp_id, n = base, 2
        while emp_id in registro or emp_id == EMPRESA_PRINCIPAL_ID:
            emp_id = f"{base}_{n}"
            n += 1
    empresa = Empresa(id=emp_id, nit=nit.strip(), nombre=nombre.strip(), **kwargs)
    return guardar_empresa(empresa)


def eliminar_empresa(empresa_id: str) -> None:
    """Elimina una empresa del registro (la principal no puede eliminarse)."""
    if empresa_id == EMPRESA_PRINCIPAL_ID:
        raise ValueError("La empresa principal no puede eliminarse.")
    with _lock:
        registro = _leer_registro()
        if empresa_id in registro:
            del registro[empresa_id]
            _escribir_registro(registro)
            logger.info("Empresa eliminada: %s", empresa_id)
