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

# ---------------------------------------------------------------------------
# Rutas por defecto
# ---------------------------------------------------------------------------
DATA_DIR: str = os.getenv("DATA_DIR", "data/")
INPUT_DIR: str = os.getenv("INPUT_DIR", "input/")
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output/")
DB_PATH: str = os.getenv("DB_PATH", "db/contable.db")

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
    "NOMINA":             "53",
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

# ---------------------------------------------------------------------------
# Impuestos que representan retenciones (afectan el sentido del asiento)
# ---------------------------------------------------------------------------
IMPUESTOS_RETENCION: set[str] = {"Rete IVA", "Rete Renta", "Rete ICA"}

# ---------------------------------------------------------------------------
# Configuración de logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
