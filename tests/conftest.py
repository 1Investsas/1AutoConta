"""
Fixtures de pytest para el sistema 1ContaBot.

Proporciona DataFrames y objetos de ejemplo que replican
la estructura real de los archivos RADIAN y maestros.
"""

import pandas as pd
import pytest

from app.config import NIT_EMPRESA


@pytest.fixture(autouse=True)
def _reset_cache_esquema():
    """Limpia el caché de esquemas inicializados entre tests.

    `inicializar_db` memoiza por ruta de BD para no reejecutar el DDL en cada
    request en producción. Los tests recrean BDs (a veces en la misma ruta), así
    que se limpia el caché antes de cada uno para que el esquema/migración corran.
    """
    from app import database as _db
    _db.reset_inicializacion_db()
    yield
    _db.reset_inicializacion_db()


# ---------------------------------------------------------------------------
# NIT del emisor empresa y de un tercero genérico
# ---------------------------------------------------------------------------
NIT_EMPRESA_TEST = NIT_EMPRESA          # "901331657"
NIT_TERCERO_TEST = "800123456"
NIT_EMPLEADO_TEST = "12345678"


@pytest.fixture
def df_radian_basico():
    """
    DataFrame mínimo con 6 documentos representativos de todos los tipos.
    Las columnas replican la estructura del reporte RADIAN descargado.
    """
    data = {
        "Tipo de documento": [
            "Factura electrónica",
            "Factura electrónica",
            "Documento soporte con no obligados",
            "Nomina Individual",
            "Nota crédito",
            "Nota crédito",
        ],
        "CUFE/CUDE": [
            "CUFE-FV-001",
            "CUFE-FC-001",
            "CUFE-DS-001",
            "CUFE-NOM-001",
            "CUFE-NCV-001",
            "CUFE-NCC-001",
        ],
        "Folio": ["1001", "2001", "3001", "4001", "1002", "2002"],
        "Prefijo": ["FV", "FC", "DS", "NOM", "NCV", "NCC"],
        "Divisa": ["COP"] * 6,
        "Forma de Pago": ["Crédito"] * 6,
        "Medio de Pago": ["Transferencia"] * 6,
        "Fecha Emisión": ["01-03-2025"] * 6,
        "Fecha Recepción": ["02-03-2025"] * 6,
        "NIT Emisor": [
            NIT_EMPRESA_TEST,    # Factura venta: emisor = empresa
            NIT_TERCERO_TEST,    # Factura compra: emisor = tercero
            NIT_EMPRESA_TEST,    # Documento soporte: emisor = empresa
            NIT_EMPRESA_TEST,    # Nómina: emisor = empresa
            NIT_EMPRESA_TEST,    # NC venta: emisor = empresa
            NIT_TERCERO_TEST,    # NC compra: emisor = tercero
        ],
        "Nombre Emisor": [
            "1INVEST SAS",
            "PROVEEDOR SA",
            "1INVEST SAS",
            "1INVEST SAS",
            "1INVEST SAS",
            "PROVEEDOR SA",
        ],
        "NIT Receptor": [
            NIT_TERCERO_TEST,
            NIT_EMPRESA_TEST,
            NIT_EMPLEADO_TEST,
            NIT_EMPLEADO_TEST,
            NIT_TERCERO_TEST,
            NIT_EMPRESA_TEST,
        ],
        "Nombre Receptor": [
            "CLIENTE ABC",
            "1INVEST SAS",
            "NO OBLIGADO XYZ",
            "JUAN EMPLEADO",
            "CLIENTE ABC",
            "1INVEST SAS",
        ],
        "IVA":          [190000.0, 95000.0, 0.0,  0.0,  19000.0, 9500.0],
        "ICA":          [0.0,      0.0,     0.0,  0.0,  0.0,     0.0],
        "IC":           [0.0] * 6,
        "INC":          [0.0] * 6,
        "Timbre":       [0.0] * 6,
        "INC Bolsas":   [0.0] * 6,
        "IN Carbono":   [0.0] * 6,
        "IN Combustibles": [0.0] * 6,
        "IC Datos":     [0.0] * 6,
        "ICL":          [0.0] * 6,
        "INPP":         [0.0] * 6,
        "IBUA":         [0.0] * 6,
        "ICUI":         [0.0] * 6,
        "Rete IVA":     [0.0,     14250.0, 0.0,  0.0,  0.0,     0.0],
        "Rete Renta":   [0.0,     25000.0, 0.0,  0.0,  0.0,     0.0],
        "Rete ICA":     [0.0,     0.0,     0.0,  0.0,  0.0,     0.0],
        "Total":        [1190000.0, 1055750.0, 500000.0, 2000000.0, 119000.0, 105575.0],
        "Estado":       ["Vigente"] * 6,
        "Grupo":        [""] * 6,
        "_duplicado":   [False] * 6,
    }
    return pd.DataFrame(data)


@pytest.fixture
def df_terceros():
    """DataFrame que simula el maestro de terceros."""
    data = {
        "Nombre tercero": [
            "CLIENTE ABC SAS",
            "PROVEEDOR SA",
            "JUAN EMPLEADO",
            "NO OBLIGADO XYZ",
        ],
        "Tipo de identificación": ["NIT", "NIT", "CC", "CC"],
        "Identificación": [
            NIT_TERCERO_TEST,
            NIT_TERCERO_TEST,  # mismo NIT para ejemplo
            NIT_EMPLEADO_TEST,
            NIT_EMPLEADO_TEST,
        ],
        "Digito verificación": ["5", "5", "", ""],
        "Sucursal": [""] * 4,
        "Tipo de regimen IVA": ["Responsable"] * 4,
        "Dirección": ["Calle 1"] * 4,
        "Ciudad": ["Medellín"] * 4,
        "Teléfono": [""] * 4,
        "Nombres contacto": [""] * 4,
        "Estado": ["Activo", "Activo", "Activo", "Activo"],
    }
    return pd.DataFrame(data)


@pytest.fixture
def df_cuentas():
    """DataFrame que simula el plan de cuentas (solo transaccionales activas)."""
    codigos = [
        "13050501", "22050501", "22100501", "25050501",
        "24081001", "24080501", "23659001", "13551519",
        "23670101", "13551701", "23676801", "13551815",
    ]
    data = {
        "Código": codigos,
        "Nombre": [f"Cuenta {c}" for c in codigos],
        "Categoría": [""] * len(codigos),
        "Clase": [""] * len(codigos),
        "Relación con": [""] * len(codigos),
        "Maneja vencimientos": ["No"] * len(codigos),
        "Diferencia fiscal": ["No"] * len(codigos),
        "Activo": ["Sí"] * len(codigos),
        "Nivel agrupación": ["Transaccional"] * len(codigos),
    }
    return pd.DataFrame(data)


@pytest.fixture
def df_comprobantes():
    """DataFrame que simula el catálogo de comprobantes."""
    data = {
        "Código del comprobante": ["15", "40", "50", "52", "53"],
        "Título comprobante": [
            "Notas crédito/débito",
            "Facturas de venta",
            "Facturas de compra",
            "Documentos soporte",
            "Nómina electrónica",
        ],
        "Editar": [""] * 5,
    }
    return pd.DataFrame(data)
