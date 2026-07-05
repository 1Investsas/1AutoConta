"""Esquema de la base de datos: DDL idempotente y migraciones aditivas."""

import logging
import threading

from app.config import DB_PATH

from . import core
from .core import DbConnection

logger = logging.getLogger(__name__)

# Esquemas ya asegurados en este proceso. `inicializar_db` ejecuta DDL idempotente
# (CREATE TABLE IF NOT EXISTS + migraciones aditivas) que no cambia durante la
# vida del proceso, así que basta correrlo una vez por ruta de BD. Sin esto el
# DDL se reejecutaba en CADA request (dashboard, banco, radian, …): sobre un
# sistema de archivos de red (Azure /home es SMB) cada sentencia es una ida y
# vuelta lenta, y en modo nube el commit agendaba además una subida completa de
# la BD a Blob por visita. Mismo patrón que _db_restauradas / authn._auth_listo.
_init_lock = threading.Lock()
_db_inicializadas: set[str] = set()


def inicializar_db(db_path: str = DB_PATH) -> None:
    """
    Crea todas las tablas necesarias si no existen.

    Tablas: documentos_importados, bitacora, historial_cuentas, importaciones,
    procesos_banco, correcciones_tercero, cuentas_bancarias_tercero.

    El esquema se asegura una sola vez por proceso y por ruta de BD: el DDL es
    idempotente y estático, de modo que reejecutarlo en cada request solo añade
    latencia (relevante con SQLite sobre un FS de red). Para reinicializar —p. ej.
    en tests que recrean la BD— usar `reset_inicializacion_db()`.
    """
    if db_path in _db_inicializadas:
        return
    with _init_lock:
        if db_path in _db_inicializadas:
            return
        conn = core.get_connection(db_path)
        try:
            if conn.is_sqlite:
                _create_tables_sqlite(conn)
            else:
                _create_tables_mssql(conn)
            # Migraciones aditivas para BD ya existentes (las tablas nuevas ya
            # incluyen la columna; este ALTER cubre instalaciones previas).
            _asegurar_columna(conn, "importaciones", "preasientos_json",
                              "TEXT", "NVARCHAR(MAX)")
            # Modelo durable del módulo Bancos: archivo original + snapshot editable.
            _asegurar_columna(conn, "procesos_banco", "archivo_ref",
                              "TEXT", "NVARCHAR(500)")
            _asegurar_columna(conn, "procesos_banco", "snapshot_json",
                              "TEXT", "NVARCHAR(MAX)")
            # Caja General: cuenta contable del maestro asociada a cada caja.
            _asegurar_columna(conn, "cash_accounts", "account_code",
                              "TEXT", "NVARCHAR(50)")
            _asegurar_columna(conn, "cash_accounts", "account_name",
                              "TEXT", "NVARCHAR(300)")
            # Caja General: contrapartida y tipo de comprobante por movimiento
            # (para generar el archivo de importación SIIGO, igual que Bancos).
            _asegurar_columna(conn, "cash_movements", "contrapartida",
                              "TEXT", "NVARCHAR(50)")
            _asegurar_columna(conn, "cash_movements", "comprobante",
                              "TEXT", "NVARCHAR(10)")
            # Subdivisión de la contrapartida en varias cuentas (JSON).
            _asegurar_columna(conn, "cash_movements", "contrapartidas_json",
                              "TEXT", "NVARCHAR(MAX)")
            # Índices tenant-aware: solo en Azure SQL (tablas compartidas). En SQLite
            # cada empresa tiene su propio archivo y no hay columna empresa_id.
            if not conn.is_sqlite:
                _asegurar_indices_mssql(conn)
            conn.commit()
            logger.info("Base de datos inicializada correctamente.")
        finally:
            conn.close()
        _db_inicializadas.add(db_path)


def reset_inicializacion_db() -> None:
    """Olvida qué esquemas se aseguraron (para aislar tests que recrean la BD)."""
    with _init_lock:
        _db_inicializadas.clear()


def _columna_existe(conn: "DbConnection", tabla: str, columna: str) -> bool:
    """True si `tabla` ya tiene la columna `columna` (en ambos backends)."""
    if conn.is_sqlite:
        rows = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
        return any(r["name"] == columna for r in rows)
    row = conn.execute(
        "SELECT 1 FROM sys.columns "
        "WHERE object_id = OBJECT_ID(?) AND name = ?",
        (tabla, columna),
    ).fetchone()
    return row is not None


def _asegurar_columna(
    conn: "DbConnection", tabla: str, columna: str,
    tipo_sqlite: str, tipo_mssql: str,
) -> None:
    """Agrega una columna a una tabla existente si aún no está (migración aditiva)."""
    if _columna_existe(conn, tabla, columna):
        return
    if conn.is_sqlite:
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo_sqlite}")
    else:
        conn.execute(f"ALTER TABLE {tabla} ADD {columna} {tipo_mssql}")


# Índices tenant-aware para Azure SQL (tablas compartidas entre empresas). Cada
# entrada es (nombre_índice, tabla, columnas). El nombre lleva el prefijo de la
# tabla porque los nombres de índice son únicos por base de datos en SQL Server.
#
# Las tablas con UNIQUE(empresa_id, …) (documentos_importados, historial_cuentas,
# correcciones_tercero) ya tienen un índice que cubre el filtro por empresa, así
# que no se repiten aquí. `bitacora` solo se escribe (no se lee por empresa), por
# lo que indexarla solo añadiría costo de escritura.
_INDICES_MSSQL = (
    # Listados por empresa ordenados por id descendente.
    ("ix_importaciones_empresa",     "importaciones",        "empresa_id, id"),
    ("ix_procesos_banco_empresa",    "procesos_banco",       "empresa_id, id"),
    # Analítica: distribución/evolución agrupada por clasificación dentro de la empresa.
    ("ix_documentos_empresa_clasif", "documentos_importados", "empresa_id, clasificacion"),
    # Cuentas bancarias por empresa, consultadas por tercero.
    ("ix_cuentas_banco_tercero",     "cuentas_bancarias_tercero", "empresa_id, nit_tercero"),
    # Caja General: cuentas/períodos por empresa y movimientos por período.
    ("ix_cash_accounts_empresa",     "cash_accounts",        "empresa_id, id"),
    ("ix_cash_periods_cuenta",       "cash_periods",         "empresa_id, cash_account_id"),
    ("ix_cash_movements_periodo",    "cash_movements",       "empresa_id, cash_period_id"),
    # Flujos Mixtos: cuentas/flujos por empresa y movimientos por flujo.
    ("ix_mixed_accounts_empresa",    "mixed_accounts",       "empresa_id, id"),
    ("ix_mixed_periods_cuenta",      "mixed_periods",        "empresa_id, mixed_account_id"),
    ("ix_mixed_movements_periodo",   "mixed_movements",      "empresa_id, mixed_period_id"),
    # Machine learning: histórico de entrenamientos por empresa.
    ("ix_import_conocimiento_emp",   "importaciones_conocimiento", "empresa_id, id"),
)


def _asegurar_indices_mssql(conn: "DbConnection") -> None:
    """Crea los índices tenant-aware en Azure SQL si no existen (idempotente).

    No aplica a SQLite: allí cada empresa tiene su propio archivo .db y las tablas
    no tienen columna `empresa_id`.
    """
    for nombre, tabla, columnas in _INDICES_MSSQL:
        conn.execute(
            f"IF NOT EXISTS (SELECT 1 FROM sys.indexes "
            f"WHERE name = '{nombre}' AND object_id = OBJECT_ID('{tabla}')) "
            f"CREATE INDEX {nombre} ON {tabla} ({columnas})"
        )


def _create_tables_sqlite(conn: DbConnection) -> None:
    """Crea tablas con sintaxis SQLite."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documentos_importados (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cufe            TEXT    NOT NULL UNIQUE,
            tipo_documento  TEXT,
            clasificacion   TEXT,
            folio           TEXT,
            prefijo         TEXT,
            nit_emisor      TEXT,
            nombre_emisor   TEXT,
            nit_receptor    TEXT,
            nombre_receptor TEXT,
            total           REAL,
            fecha_emision   TEXT,
            fecha_proceso   TEXT    NOT NULL,
            archivo_origen  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bitacora (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            nivel       TEXT    NOT NULL,
            modulo      TEXT,
            accion      TEXT,
            detalle     TEXT,
            cufe        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historial_cuentas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            clasificacion   TEXT    NOT NULL,
            nit_tercero     TEXT    NOT NULL,
            tipo_linea      TEXT    NOT NULL,
            cuenta          TEXT    NOT NULL,
            usos            INTEGER DEFAULT 1,
            ultima_vez      TEXT,
            UNIQUE(clasificacion, nit_tercero, tipo_linea)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS importaciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TEXT    NOT NULL,
            archivo_nombre  TEXT,
            archivo_ref     TEXT,
            n_docs          INTEGER DEFAULT 0,
            n_excepciones   INTEGER DEFAULT 0,
            excel_ref       TEXT,
            estado          TEXT    NOT NULL DEFAULT 'procesando',
            error           TEXT,
            preasientos_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS procesos_banco (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TEXT    NOT NULL,
            archivo_nombre  TEXT,
            archivo_ref     TEXT,
            cuenta_banco    TEXT,
            nit_banco       TEXT,
            n_movimientos   INTEGER DEFAULT 0,
            estado          TEXT    NOT NULL DEFAULT 'procesando',
            error           TEXT,
            snapshot_json   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS correcciones_tercero (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nit_original     TEXT    NOT NULL,
            nombre_original  TEXT,
            nit_corregido    TEXT    NOT NULL,
            nombre_corregido TEXT,
            clasificacion    TEXT,
            usos             INTEGER DEFAULT 1,
            ultima_vez       TEXT,
            UNIQUE(nit_original)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuentas_bancarias_tercero (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nit_tercero     TEXT    NOT NULL,
            nombre_tercero  TEXT,
            tipo_documento  TEXT,
            banco           TEXT,
            tipo_producto   TEXT,
            numero_cuenta   TEXT    NOT NULL,
            fecha_apertura  TEXT,
            estado          TEXT,
            archivo_origen  TEXT,
            fecha_registro  TEXT,
            UNIQUE(nit_tercero, numero_cuenta)
        )
    """)
    # ── Módulo Caja General (efectivo) ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cash_accounts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    NOT NULL,
            description         TEXT,
            currency            TEXT    DEFAULT 'COP',
            responsible         TEXT,
            account_code        TEXT,
            account_name        TEXT,
            active              INTEGER DEFAULT 1,
            created_at          TEXT,
            updated_at          TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cash_periods (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            cash_account_id     INTEGER NOT NULL,
            year                INTEGER NOT NULL,
            month               INTEGER NOT NULL,
            opening_balance     TEXT    DEFAULT '0',
            total_inflows       TEXT    DEFAULT '0',
            total_outflows      TEXT    DEFAULT '0',
            closing_balance     TEXT    DEFAULT '0',
            status              TEXT    NOT NULL DEFAULT 'borrador',
            responsible         TEXT,
            created_by          TEXT,
            approved_by         TEXT,
            closed_by           TEXT,
            created_at          TEXT,
            updated_at          TEXT,
            closed_at           TEXT,
            UNIQUE(cash_account_id, year, month)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cash_movements (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            cash_period_id      INTEGER NOT NULL,
            sequence            INTEGER DEFAULT 0,
            movement_date       TEXT,
            movement_type       TEXT,
            concept             TEXT,
            third_party_nit     TEXT,
            third_party_name    TEXT,
            cost_center         TEXT,
            category            TEXT,
            contrapartida       TEXT,
            contrapartidas_json TEXT,
            comprobante         TEXT,
            inflow_amount       TEXT    DEFAULT '0',
            outflow_amount      TEXT    DEFAULT '0',
            running_balance     TEXT    DEFAULT '0',
            observations        TEXT,
            created_at          TEXT,
            updated_at          TEXT
        )
    """)
    # ── Módulo Flujos Mixtos (efectivo sin límite de período) ────────────────
    # Igual que Caja General pero el "flujo" no está atado a un mes/año: puede
    # cubrir cualquier rango de fechas (o correr de forma continua). Por eso el
    # período mixto guarda un nombre y fechas opcionales, sin la restricción
    # UNIQUE(cuenta, año, mes) de la caja mensual.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mixed_accounts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    NOT NULL,
            description         TEXT,
            currency            TEXT    DEFAULT 'COP',
            responsible         TEXT,
            account_code        TEXT,
            account_name        TEXT,
            active              INTEGER DEFAULT 1,
            created_at          TEXT,
            updated_at          TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mixed_periods (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mixed_account_id    INTEGER NOT NULL,
            name                TEXT    NOT NULL,
            start_date          TEXT,
            end_date            TEXT,
            opening_balance     TEXT    DEFAULT '0',
            total_inflows       TEXT    DEFAULT '0',
            total_outflows      TEXT    DEFAULT '0',
            closing_balance     TEXT    DEFAULT '0',
            status              TEXT    NOT NULL DEFAULT 'borrador',
            responsible         TEXT,
            created_by          TEXT,
            approved_by         TEXT,
            closed_by           TEXT,
            created_at          TEXT,
            updated_at          TEXT,
            closed_at           TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mixed_movements (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mixed_period_id     INTEGER NOT NULL,
            sequence            INTEGER DEFAULT 0,
            movement_date       TEXT,
            movement_type       TEXT,
            concept             TEXT,
            third_party_nit     TEXT,
            third_party_name    TEXT,
            cost_center         TEXT,
            category            TEXT,
            contrapartida       TEXT,
            comprobante         TEXT,
            inflow_amount       TEXT    DEFAULT '0',
            outflow_amount      TEXT    DEFAULT '0',
            running_balance     TEXT    DEFAULT '0',
            observations        TEXT,
            created_at          TEXT,
            updated_at          TEXT
        )
    """)
    # ── Motor de aprendizaje generalizado (machine learning) ────────────────
    # Patrones exactos: contexto normalizado → valor confirmado (con contador).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patrones_aprendidos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo      TEXT    NOT NULL,
            campo       TEXT    NOT NULL,
            contexto    TEXT    NOT NULL,
            valor       TEXT    NOT NULL,
            usos        INTEGER DEFAULT 1,
            ultima_vez  TEXT,
            UNIQUE(modulo, campo, contexto, valor)
        )
    """)
    # Frecuencias token→valor para el clasificador de texto (Naive Bayes).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens_aprendidos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo      TEXT    NOT NULL,
            campo       TEXT    NOT NULL,
            token       TEXT    NOT NULL,
            valor       TEXT    NOT NULL,
            usos        INTEGER DEFAULT 1,
            UNIQUE(modulo, campo, token, valor)
        )
    """)
    # Histórico de entrenamientos con archivos externos (SIIGO u otras fuentes).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS importaciones_conocimiento (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TEXT    NOT NULL,
            archivo_nombre  TEXT,
            modulo          TEXT,
            filas           INTEGER DEFAULT 0,
            aprendidos      INTEGER DEFAULT 0,
            estado          TEXT    NOT NULL DEFAULT 'completada',
            detalle         TEXT
        )
    """)


def _create_tables_mssql(conn: DbConnection) -> None:
    """Crea tablas con sintaxis T-SQL (Azure SQL Database)."""
    # Nota: cada tabla lleva una columna `empresa_id` discriminadora porque en
    # Azure SQL todas las empresas comparten las mismas tablas (a diferencia de
    # SQLite, donde cada empresa tiene su propio archivo). El valor por defecto
    # 'principal' preserva los datos de instalaciones de una sola empresa.
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'documentos_importados')
        CREATE TABLE documentos_importados (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            cufe            NVARCHAR(500)  NOT NULL,
            tipo_documento  NVARCHAR(100),
            clasificacion   NVARCHAR(100),
            folio           NVARCHAR(100),
            prefijo         NVARCHAR(100),
            nit_emisor      NVARCHAR(50),
            nombre_emisor   NVARCHAR(300),
            nit_receptor    NVARCHAR(50),
            nombre_receptor NVARCHAR(300),
            total           FLOAT,
            fecha_emision   NVARCHAR(50),
            fecha_proceso   NVARCHAR(50)   NOT NULL,
            archivo_origen  NVARCHAR(500),
            CONSTRAINT uq_doc_empresa_cufe UNIQUE(empresa_id, cufe)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'bitacora')
        CREATE TABLE bitacora (
            id          INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id  NVARCHAR(100) NOT NULL DEFAULT 'principal',
            timestamp   NVARCHAR(50)  NOT NULL,
            nivel       NVARCHAR(20)  NOT NULL,
            modulo      NVARCHAR(100),
            accion      NVARCHAR(100),
            detalle     NVARCHAR(MAX),
            cufe        NVARCHAR(500)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'historial_cuentas')
        CREATE TABLE historial_cuentas (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            clasificacion   NVARCHAR(100)  NOT NULL,
            nit_tercero     NVARCHAR(50)   NOT NULL,
            tipo_linea      NVARCHAR(100)  NOT NULL,
            cuenta          NVARCHAR(50)   NOT NULL,
            usos            INT DEFAULT 1,
            ultima_vez      NVARCHAR(50),
            CONSTRAINT uq_historial UNIQUE(empresa_id, clasificacion, nit_tercero, tipo_linea)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'importaciones')
        CREATE TABLE importaciones (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            fecha           NVARCHAR(50)   NOT NULL,
            archivo_nombre  NVARCHAR(300),
            archivo_ref     NVARCHAR(500),
            n_docs          INT DEFAULT 0,
            n_excepciones   INT DEFAULT 0,
            excel_ref       NVARCHAR(500),
            estado          NVARCHAR(30)   NOT NULL DEFAULT 'procesando',
            error           NVARCHAR(MAX),
            preasientos_json NVARCHAR(MAX)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'procesos_banco')
        CREATE TABLE procesos_banco (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            fecha           NVARCHAR(50)   NOT NULL,
            archivo_nombre  NVARCHAR(300),
            archivo_ref     NVARCHAR(500),
            cuenta_banco    NVARCHAR(50),
            nit_banco       NVARCHAR(50),
            n_movimientos   INT DEFAULT 0,
            estado          NVARCHAR(30)   NOT NULL DEFAULT 'procesando',
            error           NVARCHAR(MAX),
            snapshot_json   NVARCHAR(MAX)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'correcciones_tercero')
        CREATE TABLE correcciones_tercero (
            id               INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id       NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            nit_original     NVARCHAR(50)   NOT NULL,
            nombre_original  NVARCHAR(300),
            nit_corregido    NVARCHAR(50)   NOT NULL,
            nombre_corregido NVARCHAR(300),
            clasificacion    NVARCHAR(100),
            usos             INT DEFAULT 1,
            ultima_vez       NVARCHAR(50),
            CONSTRAINT uq_correccion_tercero UNIQUE(empresa_id, nit_original)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'cuentas_bancarias_tercero')
        CREATE TABLE cuentas_bancarias_tercero (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            nit_tercero     NVARCHAR(50)   NOT NULL,
            nombre_tercero  NVARCHAR(300),
            tipo_documento  NVARCHAR(20),
            banco           NVARCHAR(200),
            tipo_producto   NVARCHAR(100),
            numero_cuenta   NVARCHAR(50)   NOT NULL,
            fecha_apertura  NVARCHAR(50),
            estado          NVARCHAR(50),
            archivo_origen  NVARCHAR(300),
            fecha_registro  NVARCHAR(50),
            CONSTRAINT uq_cuenta_banco_tercero UNIQUE(empresa_id, nit_tercero, numero_cuenta)
        )
    """)
    # ── Módulo Caja General (efectivo) ──────────────────────────────────────
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'cash_accounts')
        CREATE TABLE cash_accounts (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            name            NVARCHAR(200)  NOT NULL,
            description     NVARCHAR(MAX),
            currency        NVARCHAR(10)   DEFAULT 'COP',
            responsible     NVARCHAR(200),
            account_code    NVARCHAR(50),
            account_name    NVARCHAR(300),
            active          BIT            DEFAULT 1,
            created_at      NVARCHAR(50),
            updated_at      NVARCHAR(50)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'cash_periods')
        CREATE TABLE cash_periods (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            cash_account_id INT            NOT NULL,
            year            INT            NOT NULL,
            month           INT            NOT NULL,
            opening_balance NVARCHAR(50)   DEFAULT '0',
            total_inflows   NVARCHAR(50)   DEFAULT '0',
            total_outflows  NVARCHAR(50)   DEFAULT '0',
            closing_balance NVARCHAR(50)   DEFAULT '0',
            status          NVARCHAR(30)   NOT NULL DEFAULT 'borrador',
            responsible     NVARCHAR(200),
            created_by      NVARCHAR(200),
            approved_by     NVARCHAR(200),
            closed_by       NVARCHAR(200),
            created_at      NVARCHAR(50),
            updated_at      NVARCHAR(50),
            closed_at       NVARCHAR(50),
            CONSTRAINT uq_cash_period UNIQUE(empresa_id, cash_account_id, year, month)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'cash_movements')
        CREATE TABLE cash_movements (
            id               INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id       NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            cash_period_id   INT            NOT NULL,
            sequence         INT            DEFAULT 0,
            movement_date    NVARCHAR(50),
            movement_type    NVARCHAR(20),
            concept          NVARCHAR(MAX),
            third_party_nit  NVARCHAR(50),
            third_party_name NVARCHAR(300),
            cost_center      NVARCHAR(200),
            category         NVARCHAR(200),
            contrapartida    NVARCHAR(50),
            contrapartidas_json NVARCHAR(MAX),
            comprobante      NVARCHAR(10),
            inflow_amount    NVARCHAR(50)   DEFAULT '0',
            outflow_amount   NVARCHAR(50)   DEFAULT '0',
            running_balance  NVARCHAR(50)   DEFAULT '0',
            observations     NVARCHAR(MAX),
            created_at       NVARCHAR(50),
            updated_at       NVARCHAR(50)
        )
    """)
    # ── Módulo Flujos Mixtos (efectivo sin límite de período) ────────────────
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'mixed_accounts')
        CREATE TABLE mixed_accounts (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            name            NVARCHAR(200)  NOT NULL,
            description     NVARCHAR(MAX),
            currency        NVARCHAR(10)   DEFAULT 'COP',
            responsible     NVARCHAR(200),
            account_code    NVARCHAR(50),
            account_name    NVARCHAR(300),
            active          BIT            DEFAULT 1,
            created_at      NVARCHAR(50),
            updated_at      NVARCHAR(50)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'mixed_periods')
        CREATE TABLE mixed_periods (
            id               INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id       NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            mixed_account_id INT            NOT NULL,
            name             NVARCHAR(300)  NOT NULL,
            start_date       NVARCHAR(50),
            end_date         NVARCHAR(50),
            opening_balance  NVARCHAR(50)   DEFAULT '0',
            total_inflows    NVARCHAR(50)   DEFAULT '0',
            total_outflows   NVARCHAR(50)   DEFAULT '0',
            closing_balance  NVARCHAR(50)   DEFAULT '0',
            status           NVARCHAR(30)   NOT NULL DEFAULT 'borrador',
            responsible      NVARCHAR(200),
            created_by       NVARCHAR(200),
            approved_by      NVARCHAR(200),
            closed_by        NVARCHAR(200),
            created_at       NVARCHAR(50),
            updated_at       NVARCHAR(50),
            closed_at        NVARCHAR(50)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'mixed_movements')
        CREATE TABLE mixed_movements (
            id               INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id       NVARCHAR(100)  NOT NULL DEFAULT 'principal',
            mixed_period_id  INT            NOT NULL,
            sequence         INT            DEFAULT 0,
            movement_date    NVARCHAR(50),
            movement_type    NVARCHAR(20),
            concept          NVARCHAR(MAX),
            third_party_nit  NVARCHAR(50),
            third_party_name NVARCHAR(300),
            cost_center      NVARCHAR(200),
            category         NVARCHAR(200),
            contrapartida    NVARCHAR(50),
            comprobante      NVARCHAR(10),
            inflow_amount    NVARCHAR(50)   DEFAULT '0',
            outflow_amount   NVARCHAR(50)   DEFAULT '0',
            running_balance  NVARCHAR(50)   DEFAULT '0',
            observations     NVARCHAR(MAX),
            created_at       NVARCHAR(50),
            updated_at       NVARCHAR(50)
        )
    """)
    # ── Motor de aprendizaje generalizado (machine learning) ────────────────
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'patrones_aprendidos')
        CREATE TABLE patrones_aprendidos (
            id          INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id  NVARCHAR(100) NOT NULL DEFAULT 'principal',
            modulo      NVARCHAR(50)  NOT NULL,
            campo       NVARCHAR(50)  NOT NULL,
            contexto    NVARCHAR(400) NOT NULL,
            valor       NVARCHAR(300) NOT NULL,
            usos        INT DEFAULT 1,
            ultima_vez  NVARCHAR(50),
            CONSTRAINT uq_patron_aprendido
                UNIQUE(empresa_id, modulo, campo, contexto, valor)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'tokens_aprendidos')
        CREATE TABLE tokens_aprendidos (
            id          INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id  NVARCHAR(100) NOT NULL DEFAULT 'principal',
            modulo      NVARCHAR(50)  NOT NULL,
            campo       NVARCHAR(50)  NOT NULL,
            token       NVARCHAR(100) NOT NULL,
            valor       NVARCHAR(300) NOT NULL,
            usos        INT DEFAULT 1,
            CONSTRAINT uq_token_aprendido
                UNIQUE(empresa_id, modulo, campo, token, valor)
        )
    """)
    conn.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'importaciones_conocimiento')
        CREATE TABLE importaciones_conocimiento (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            empresa_id      NVARCHAR(100) NOT NULL DEFAULT 'principal',
            fecha           NVARCHAR(50)  NOT NULL,
            archivo_nombre  NVARCHAR(300),
            modulo          NVARCHAR(50),
            filas           INT DEFAULT 0,
            aprendidos      INT DEFAULT 0,
            estado          NVARCHAR(30)  NOT NULL DEFAULT 'completada',
            detalle         NVARCHAR(MAX)
        )
    """)


