"""
Configuración central del sistema contable-auto.

Contiene todas las constantes, rutas por defecto, mapeos de comprobantes,
cuentas de impuestos y demás parámetros de configuración del sistema.
"""

import os
from dotenv import load_dotenv

# Cargar variables de entorno si existe archivo .env
load_dotenv()

# ---------------------------------------------------------------------------
# Identificación de la empresa
# ---------------------------------------------------------------------------
NIT_EMPRESA: str = os.getenv("NIT_EMPRESA", "901331657")
NOMBRE_EMPRESA: str = os.getenv("NOMBRE_EMPRESA", "1INVEST SAS")
# Sigla / nombre corto de la empresa (para la selección rápida en la UI).
# Si no se define, se usa el nombre completo como sigla.
SIGLA_EMPRESA: str = os.getenv("SIGLA_EMPRESA", "") or NOMBRE_EMPRESA

# ---------------------------------------------------------------------------
# Rutas por defecto
# ---------------------------------------------------------------------------
DATA_DIR: str = os.getenv("DATA_DIR", "data/")
INPUT_DIR: str = os.getenv("INPUT_DIR", "input/")
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output/")
DB_PATH: str = os.getenv("DB_PATH", "db/contable.db")

# ---------------------------------------------------------------------------
# Azure — Database & Storage (cloud deployment)
# ---------------------------------------------------------------------------
# When USE_SQLITE is true (default), the app uses SQLite locally.
# Set to false in Azure App Service to use Azure SQL Database.
USE_SQLITE: bool = os.getenv("USE_SQLITE", "true").lower() == "true"

# Azure SQL connection string (only used when USE_SQLITE=false)
# Format: Driver={ODBC Driver 18 for SQL Server};Server=tcp:<server>.database.windows.net,1433;Database=<db>;Uid=<user>;Pwd=<pass>;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# Azure Blob Storage (empty = use local filesystem)
AZURE_STORAGE_CONNECTION_STRING: str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_STORAGE_CONTAINER: str = os.getenv("AZURE_STORAGE_CONTAINER", "contable-auto")

# ---------------------------------------------------------------------------
# Clasificaciones de documento
# ---------------------------------------------------------------------------
CLASIFICACIONES = (
    "FACTURA_VENTA",
    "FACTURA_COMPRA",
    "DOCUMENTO_SOPORTE",
    "NOMINA",
    "NOTA_CREDITO_VENTA",
    "NOTA_CREDITO_COMPRA",
    "NOTA_DEBITO_VENTA",
    "NOTA_DEBITO_COMPRA",
    "SIN_CLASIFICAR",
)

# ---------------------------------------------------------------------------
# Mapeo clasificación → código de comprobante contable
# ---------------------------------------------------------------------------
MAPEO_COMPROBANTES: dict[str, str] = {
    "FACTURA_VENTA":      "40",
    "FACTURA_COMPRA":     "50",
    "DOCUMENTO_SOPORTE":  "52",
    "NOMINA":             "112",
    "NOTA_CREDITO_VENTA": "15",
    "NOTA_CREDITO_COMPRA": "15",
    "NOTA_DEBITO_VENTA":  "40",   # Pendiente confirmar con contabilidad
    "NOTA_DEBITO_COMPRA": "50",   # Pendiente confirmar con contabilidad
}

# ---------------------------------------------------------------------------
# Columnas de impuestos presentes en el archivo RADIAN
# ---------------------------------------------------------------------------
COLUMNAS_IMPUESTOS: list[str] = [
    "IVA", "ICA", "IC", "INC", "Timbre", "INC Bolsas",
    "IN Carbono", "IN Combustibles", "IC Datos", "ICL",
    "INPP", "IBUA", "ICUI", "Rete IVA", "Rete Renta", "Rete ICA",
]

# ---------------------------------------------------------------------------
# Cuentas contables por defecto para cada impuesto (compra / venta)
# ---------------------------------------------------------------------------
CUENTAS_IMPUESTOS: dict[str, dict[str, str]] = {
    "IVA":        {"compra": "24081001", "venta": "24080501"},
    "Rete Renta": {"compra": "23659001", "venta": "13551519"},
    "Rete IVA":   {"compra": "23670101", "venta": "13551701"},
    "Rete ICA":   {"compra": "23676801", "venta": "13551815"},
    "ICA":        {"compra": "13551001", "venta": "13551001"},
    "IC":         {"compra": "24082001", "venta": "24082001"},
    "INC":        {"compra": "24082501", "venta": "24082501"},
    "Timbre":     {"compra": "24083001", "venta": "24083001"},
}

# ---------------------------------------------------------------------------
# Cuentas de contrapartida principal por tipo de documento
# ---------------------------------------------------------------------------
CUENTAS_CONTRAPARTE: dict[str, str] = {
    "FACTURA_VENTA":     "13050501",   # CxC Clientes
    "FACTURA_COMPRA":    "22050501",   # Proveedores nacionales
    "DOCUMENTO_SOPORTE": "22100501",   # Proveedores exterior / no obligados
    "NOMINA":            "25050501",   # Salarios por pagar
    "NOTA_CREDITO_VENTA":  "13050501",
    "NOTA_CREDITO_COMPRA": "22050501",
    "NOTA_DEBITO_VENTA":   "13050501",
    "NOTA_DEBITO_COMPRA":  "22050501",
}

# ---------------------------------------------------------------------------
# Configuración de archivos maestros
# ---------------------------------------------------------------------------
# Las filas 1–6 son encabezados informativos (nombre empresa, NIT, etc.)
# Los encabezados reales de columnas están en la fila 7 (índice 6 en pandas)
FILA_ENCABEZADOS_MAESTROS: int = 6   # header=6 en pandas (fila 7 de Excel)
FILA_DATOS_MAESTROS: int = 7         # skiprows equivalente

# Nombres por defecto de los archivos maestros
ARCHIVO_TERCEROS: str = "Listado_de_Terceros.xlsx"
ARCHIVO_CUENTAS: str = "Listado_de_Cuentas_Contables.xlsx"
ARCHIVO_COMPROBANTES: str = "Tipos_de_comprobante_contable.xlsx"

# ---------------------------------------------------------------------------
# Columnas esperadas en el archivo RADIAN
# ---------------------------------------------------------------------------
COLUMNAS_RADIAN: list[str] = [
    "Tipo de documento", "CUFE/CUDE", "Folio", "Prefijo", "Divisa",
    "Forma de Pago", "Medio de Pago", "Fecha Emisión", "Fecha Recepción",
    "NIT Emisor", "Nombre Emisor", "NIT Receptor", "Nombre Receptor",
    "IVA", "ICA", "IC", "INC", "Timbre", "INC Bolsas", "IN Carbono",
    "IN Combustibles", "IC Datos", "ICL", "INPP", "IBUA", "ICUI",
    "Rete IVA", "Rete Renta", "Rete ICA", "Total", "Estado", "Grupo",
]

# ---------------------------------------------------------------------------
# Nombres de columnas en los archivos maestros
# ---------------------------------------------------------------------------
COL_CUENTAS_CODIGO: str = "Código"
COL_CUENTAS_NIVEL: str = "Nivel agrupación"
COL_CUENTAS_ACTIVO: str = "Activo"
COL_CUENTAS_VENCIMIENTOS: str = "Maneja vencimientos"

# ---------------------------------------------------------------------------
# Impuestos que representan retenciones (afectan el sentido del asiento)
# ---------------------------------------------------------------------------
IMPUESTOS_RETENCION: set[str] = {"Rete IVA", "Rete Renta", "Rete ICA"}

# ---------------------------------------------------------------------------
# Configuración de logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# SIIGO — integración Fase 3
# ---------------------------------------------------------------------------
# Credenciales API (solo necesarias con suscripción premium)
SIIGO_USERNAME: str = os.getenv("SIIGO_USERNAME", "")
SIIGO_ACCESS_KEY: str = os.getenv("SIIGO_ACCESS_KEY", "")
SIIGO_API_URL: str = os.getenv("SIIGO_API_URL", "https://api.siigo.com")

# Formato Excel de importación
SIIGO_MAX_FILAS_POR_ARCHIVO: int = 500   # Límite de SIIGO por archivo

# Mapeo clasificación → código de tipo de comprobante en SIIGO
# Estos códigos deben coincidir con los configurados en el sistema SIIGO del cliente
SIIGO_CODIGOS_COMPROBANTE: dict[str, int] = {
    "FACTURA_VENTA":       int(os.getenv("SIIGO_COMP_FACTURA_VENTA",       "40")),
    "FACTURA_COMPRA":      int(os.getenv("SIIGO_COMP_FACTURA_COMPRA",      "50")),
    "DOCUMENTO_SOPORTE":   int(os.getenv("SIIGO_COMP_DOCUMENTO_SOPORTE",   "52")),
    "NOMINA":              int(os.getenv("SIIGO_COMP_NOMINA",              "112")),
    "NOTA_CREDITO_VENTA":  int(os.getenv("SIIGO_COMP_NOTA_CREDITO_VENTA",  "15")),
    "NOTA_CREDITO_COMPRA": int(os.getenv("SIIGO_COMP_NOTA_CREDITO_COMPRA", "15")),
    "NOTA_DEBITO_VENTA":   int(os.getenv("SIIGO_COMP_NOTA_DEBITO_VENTA",   "40")),
    "NOTA_DEBITO_COMPRA":  int(os.getenv("SIIGO_COMP_NOTA_DEBITO_COMPRA",  "50")),
}

# ---------------------------------------------------------------------------
# Banco — procesamiento de extracto bancario CSV
# ---------------------------------------------------------------------------
BANCO_CUENTA_DEFAULT: str = os.getenv("BANCO_CUENTA_DEFAULT", "11100501")
BANCO_CUENTA_4X1000: str  = "53152001"   # Gasto impuesto 4x1000
BANCO_CODIGO_4X1000: str  = "3339"       # Código interno del banco para el 4x1000

# Código de comprobante SIIGO según sentido del movimiento
SIIGO_COMP_BANCO_INGRESO:  int = int(os.getenv("SIIGO_COMP_BANCO_INGRESO",  "111"))  # Recibo de caja
SIIGO_COMP_BANCO_EGRESO:   int = int(os.getenv("SIIGO_COMP_BANCO_EGRESO",   "112"))  # Recibo de pago/egreso
SIIGO_COMP_BANCO_TRASLADO: int = int(os.getenv("SIIGO_COMP_BANCO_TRASLADO", "110"))  # Traslado de fondos

# Códigos de detalle del banco (Col G del CSV) cuyos movimientos siempre
# se contabilizan a nombre del banco (no de un tercero externo).
# Incluye: 4x1000, intereses, cuota de manejo y demás gastos/ingresos bancarios.
# Se puede ampliar vía código sin tocar esta constante.
BANCO_CODIGOS_BANCARIOS: frozenset[str] = frozenset(
    os.getenv("BANCO_CODIGOS_BANCARIOS", "3339,2999,1280").split(",")
)

# Fragmentos de descripción (en mayúsculas, sin tildes) que identifican
# movimientos cuyo TERCERO siempre es el banco, independientemente del
# código de detalle que aparezca en el CSV.
# Cubre: 4x1000, intereses de ahorros y cuota de manejo de tarjeta débito.
BANCO_DESC_BANCARIOS: tuple[str, ...] = tuple(
    p.strip().upper()
    for p in os.getenv(
        "BANCO_DESC_BANCARIOS",
        "IMPTO GOBIERNO 4X1000,ABONO INTERESES AHORROS,CUOTA MANEJO TRJ DEB",
    ).split(",")
    if p.strip()
)

# Descripción exacta del movimiento de intereses de ahorros que debe
# consolidarse en un único movimiento al final de cada mes.
# Si el banco usa otra descripción se puede sobreescribir en el .env.
BANCO_DESC_INTERESES_AHORROS: str = os.getenv(
    "BANCO_DESC_INTERESES_AHORROS", "ABONO INTERESES AHORROS"
)
