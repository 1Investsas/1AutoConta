"""
Configuración central del sistema 1ContaBot.

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


def _en_azure_app_service() -> bool:
    """True cuando la app corre en Azure App Service.

    App Service define estas variables de entorno automáticamente. Solo se usa
    para elegir una ubicación de datos persistente por defecto.
    """
    return bool(os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("WEBSITE_SITE_NAME"))


def _db_dir_por_defecto() -> str:
    """Directorio por defecto para la base de datos SQLite.

    En Azure App Service el directorio /home es almacenamiento PERSISTENTE:
    sobrevive a reinicios del contenedor y a los redespliegues (que solo
    reemplazan /home/site/wwwroot). Guardar la BD en /home/data —fuera de
    wwwroot— evita que se borre en cada despliegue y que la app "empiece desde
    cero". En local se usa la carpeta db/ del proyecto (comportamiento original).
    """
    if _en_azure_app_service():
        return "/home/data/db"
    return "db"


# Carpeta donde viven las bases de datos SQLite (la principal y la de cada
# empresa). Configurable con DB_DIR para apuntarla a un volumen persistente.
DB_DIR: str = os.getenv("DB_DIR", _db_dir_por_defecto())
DB_PATH: str = os.getenv("DB_PATH", os.path.join(DB_DIR, "contable.db"))

# Base de datos "de sistema": registro central de empresas (y, más adelante,
# usuarios/roles para el RBAC de la Fase 3). A diferencia de las BD por-empresa
# (contable_<id>.db), esta es CENTRAL: debe poder consultarse antes de saber qué
# empresa está activa (p. ej. para listar las empresas disponibles). En modo
# Azure SQL (USE_SQLITE=false) todo vive en la misma BD, así que esta ruta se
# ignora y la tabla `empresas` queda como una tabla compartida más.
SYSTEM_DB_PATH: str = os.getenv("SYSTEM_DB_PATH", os.path.join(DB_DIR, "sistema.db"))

# Modo de journal de SQLite. WAL no es compatible con sistemas de archivos de
# red (el montaje /home de Azure App Service es SMB), por lo que en la nube se
# usa el journal por defecto (DELETE). En local se mantiene WAL por rendimiento.
_JOURNAL_VALIDOS = {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}
DB_JOURNAL_MODE: str = os.getenv(
    "DB_JOURNAL_MODE", "DELETE" if _en_azure_app_service() else "WAL"
).upper()
if DB_JOURNAL_MODE not in _JOURNAL_VALIDOS:
    DB_JOURNAL_MODE = "DELETE"

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
AZURE_STORAGE_CONTAINER: str = os.getenv("AZURE_STORAGE_CONTAINER", "1contabot")

# Application Insights (observabilidad — Fase 4). Vacío = telemetría desactivada.
# La fija azure-setup.sh §9; la SDK (azure-monitor-opentelemetry) también lee
# esta misma variable de entorno directamente.
APPLICATIONINSIGHTS_CONNECTION_STRING: str = os.getenv(
    "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
)

# ---------------------------------------------------------------------------
# Autenticación y autorización (Fase 3 / Fase 4)
# ---------------------------------------------------------------------------
# AUTH_MODE controla cómo se identifica al usuario:
#   - "dev"   (por defecto): stub de desarrollo. Sin login real; se resuelve un
#             usuario administrador local y la UI permite cambiar de usuario para
#             probar roles. NO usar en producción.
#   - "entra": App Service Authentication con Microsoft Entra ID. La identidad
#             llega en la cabecera X-MS-CLIENT-PRINCIPAL-NAME (Fase 4).
AUTH_MODE: str = os.getenv("AUTH_MODE", "dev").lower()

# Usuario administrador del stub de desarrollo (solo aplica con AUTH_MODE=dev).
# Se autoprovisiona con rol de administrador global la primera vez.
DEV_AUTH_EMAIL: str = os.getenv("DEV_AUTH_EMAIL", "admin@local").strip().lower()
DEV_AUTH_NOMBRE: str = os.getenv("DEV_AUTH_NOMBRE", "Administrador (dev)")

# Email que siempre recibe rol de administrador global al iniciar sesión
# (bootstrap del primer admin real en Entra). Vacío = desactivado.
BOOTSTRAP_ADMIN_EMAIL: str = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()

# Tenant de Entra permitido (GUID). Si se define, solo se aceptan identidades
# cuyo claim `tid` coincida (defensa extra si el app registration quedara
# multi-tenant por error). Vacío = no se valida el tenant.
ENTRA_TENANT_ID: str = os.getenv("ENTRA_TENANT_ID", "").strip().lower()

# Rutas de login/logout de App Service Authentication (Easy Auth). Solo habría
# que cambiarlas si Azure moviera los endpoints /.auth (no es lo normal).
ENTRA_LOGIN_PATH: str = os.getenv("ENTRA_LOGIN_PATH", "/.auth/login/aad")
ENTRA_LOGOUT_PATH: str = os.getenv("ENTRA_LOGOUT_PATH", "/.auth/logout")

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
# Aplica al plan de cuentas y a los comprobantes: las filas 1–6 son encabezados
# informativos (nombre empresa, NIT, etc.) y los encabezados reales de columnas
# están en la fila 7 (índice 6 en pandas).
#
# El maestro de TERCEROS usa el modelo de Siigo Nube (encabezados en la fila 1);
# su estructura y la detección de la fila de encabezados viven en
# ``app/terceros_schema.py``, no aquí.
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
# RADIAN automático — importación diaria desde el portal DIAN
# ---------------------------------------------------------------------------
# Portal de facturación electrónica / catálogo RADIAN de la DIAN. De aquí se
# descarga el reporte que hoy se sube manualmente al módulo RADIAN.
DIAN_PORTAL_URL: str = os.getenv("DIAN_PORTAL_URL", "https://catalogo-vpfe.dian.gov.co")

# Datos del correo que envía la DIAN con el enlace de acceso (token temporal).
# Se usan para localizar e identificar el correo en el buzón del representante
# legal registrado en el RUT.
DIAN_EMAIL_REMITENTE: str = os.getenv(
    "DIAN_EMAIL_REMITENTE", "facturacionelectronica@dian.gov.co"
)
DIAN_EMAIL_ASUNTO: str = os.getenv("DIAN_EMAIL_ASUNTO", "Token Acceso DIAN")

# Vigencia del token de la DIAN (minutos) y ventana de espera del correo.
DIAN_TOKEN_VIGENCIA_MIN: int = int(os.getenv("DIAN_TOKEN_VIGENCIA_MIN", "60"))
DIAN_EMAIL_ESPERA_SEG: int = int(os.getenv("DIAN_EMAIL_ESPERA_SEG", "300"))
DIAN_EMAIL_INTERVALO_SEG: int = int(os.getenv("DIAN_EMAIL_INTERVALO_SEG", "15"))

# Configuración IMAP por defecto para leer el correo del token (Gmail por
# defecto, ya que la DIAN suele notificar a una cuenta de correo del cliente).
# La contraseña NUNCA se define aquí; se toma de la configuración por empresa o
# de la variable de entorno DIAN_EMAIL_PASSWORD (contraseña de aplicación).
DIAN_IMAP_HOST: str = os.getenv("DIAN_IMAP_HOST", "imap.gmail.com")
DIAN_IMAP_PORT: int = int(os.getenv("DIAN_IMAP_PORT", "993"))
DIAN_EMAIL_USER: str = os.getenv("DIAN_EMAIL_USER", "")
DIAN_EMAIL_PASSWORD: str = os.getenv("DIAN_EMAIL_PASSWORD", "")

# Scheduler interno de importación diaria. Si está activo, la app web arranca un
# hilo en segundo plano que ejecuta la importación una vez al día a la hora
# programada de cada empresa. En despliegues con varias instancias conviene
# desactivarlo y disparar la importación con un cron externo (ver más abajo).
RADIAN_SCHEDULER_ENABLED: bool = (
    os.getenv("RADIAN_SCHEDULER_ENABLED", "false").lower() == "true"
)
# Hora por defecto (HH:MM, 24h) a la que corre la importación automática.
RADIAN_HORA_DEFAULT: str = os.getenv("RADIAN_HORA_DEFAULT", "06:00")
# Token compartido que protege el endpoint POST /radian/auto/cron usado por un
# programador externo (Azure Scheduler, cron, GitHub Action…). Vacío = endpoint
# deshabilitado.
RADIAN_CRON_TOKEN: str = os.getenv("RADIAN_CRON_TOKEN", "")

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
