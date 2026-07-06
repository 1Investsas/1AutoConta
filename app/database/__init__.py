"""
Gestión de la base de datos del sistema 1ContaBot.

Soporta dos backends según la variable de entorno USE_SQLITE:
- SQLite  (local, desarrollo) — comportamiento original.
- Azure SQL Database (producción en la nube) — vía pyodbc.

El paquete está dividido por dominios; este ``__init__`` reexporta la API
completa, de modo que ``from app.database import X`` y ``app.database.X``
siguen funcionando igual que cuando todo vivía en un solo módulo:

- ``core``           — conexión dual (sqlite3/pyodbc), respaldo de la BD en
                       Blob, aislamiento por empresa y helpers de dialecto SQL.
- ``schema``         — DDL idempotente y migraciones aditivas.
- ``sistema``        — registro central de empresas (BD de sistema).
- ``auth``           — RBAC: usuarios, roles, permisos y auditoría.
- ``documentos``     — documentos importados, bitácora, historial de cuentas,
                       correcciones de tercero y cuentas bancarias de terceros.
- ``analytics``      — KPIs, evolución mensual y resumen del dashboard.
- ``importaciones``  — histórico durable de importaciones RADIAN y procesos
                       de banco (con snapshot editable).
- ``caja``           — Caja General: cuentas, períodos y movimientos.
- ``mixtos``         — Flujos Mixtos: cuentas, flujos y movimientos.
- ``aprendizaje``    — persistencia del motor de aprendizaje (ML).
"""

from . import core  # noqa: F401  (permite parchear core.get_connection en tests)
from .core import (  # noqa: F401
    DictRow,
    DbConnection,
    get_connection,
    init_app,
    _abrir_conexion,
    _and_empresa,
    _cond_empresa,
    _where_empresa,
    _empresa_id_desde_db_path,
    _flush_todos_los_respaldos,
    _db_restauradas,
    _db_timers,
    _month_expr,
    _substr_expr,
    _ultimo_id,
)
from .schema import (  # noqa: F401
    inicializar_db,
    reset_inicializacion_db,
    _asegurar_columna,
    _asegurar_indices_mssql,
    _columna_existe,
    _create_tables_sqlite,
    _create_tables_mssql,
    _INDICES_MSSQL,
)
from .sistema import (  # noqa: F401
    inicializar_db_sistema,
    contar_empresas_registro,
    listar_empresas_registro,
    obtener_empresa_registro,
    guardar_empresa_registro,
    eliminar_empresa_registro,
)
from .auth import (  # noqa: F401
    inicializar_db_auth,
    obtener_usuario_por_email,
    crear_usuario,
    actualizar_usuario,
    registrar_acceso_usuario,
    listar_usuarios,
    obtener_o_crear_rol,
    obtener_o_crear_permiso,
    vincular_rol_permiso,
    listar_roles,
    asignar_rol_global,
    asignar_rol_empresa,
    revocar_rol_empresa,
    revocar_rol_global,
    revocar_roles_empresa_usuario,
    tiene_rol_global,
    permisos_usuario,
    empresas_de_usuario,
    roles_de_usuario,
    registrar_evento_auditoria,
    listar_auditoria,
)
from .documentos import (  # noqa: F401
    cufe_existe,
    registrar_documento,
    registrar_bitacora_db,
    obtener_historial_cuenta,
    listar_historial_cuentas,
    actualizar_historial_cuenta,
    obtener_correccion_tercero,
    registrar_correccion_tercero,
    listar_correcciones_tercero,
    registrar_cuenta_bancaria_tercero,
    listar_cuentas_bancarias_tercero,
    contar_cuentas_bancarias_tercero,
    eliminar_cuenta_bancaria_tercero,
)
from .analytics import (  # noqa: F401
    obtener_kpis,
    obtener_evolucion_mensual,
    obtener_distribucion_clasificacion,
    obtener_top_terceros,
    obtener_actividad_reciente,
    obtener_resumen_dashboard,
)
from .importaciones import (  # noqa: F401
    registrar_importacion,
    actualizar_importacion,
    obtener_importacion,
    obtener_snapshot_importacion,
    listar_importaciones,
    registrar_proceso_banco,
    actualizar_proceso_banco,
    listar_procesos_banco,
    obtener_proceso_banco,
    obtener_snapshot_proceso_banco,
)
from .caja import (  # noqa: F401
    crear_cash_account,
    listar_cash_accounts,
    obtener_cash_account,
    actualizar_cash_account,
    crear_cash_period,
    listar_cash_periods,
    obtener_cash_period,
    obtener_cash_period_por_mes,
    actualizar_cash_period_saldos,
    actualizar_cash_period_estado,
    listar_cash_movements,
    reemplazar_cash_movements,
)
from .mixtos import (  # noqa: F401
    crear_mixed_account,
    listar_mixed_accounts,
    obtener_mixed_account,
    actualizar_mixed_account,
    crear_mixed_period,
    listar_mixed_periods,
    obtener_mixed_period,
    actualizar_mixed_period_saldos,
    actualizar_mixed_period_estado,
    listar_mixed_movements,
    reemplazar_mixed_movements,
)
from .aprendizaje import (  # noqa: F401
    registrar_aprendizaje_lote,
    obtener_patrones_exactos,
    obtener_tokens_aprendidos,
    totales_tokens_por_valor,
    listar_patrones_aprendidos,
    estadisticas_aprendizaje,
    eliminar_patron_aprendido,
    registrar_importacion_conocimiento,
    listar_importaciones_conocimiento,
)
