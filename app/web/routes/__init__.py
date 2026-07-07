"""
Rutas de la interfaz web.

Un único Blueprint ``web`` (creado en ``base``) compartido por módulos de
rutas por funcionalidad, de modo que los endpoints conservan sus nombres
(``web.index``, ``web.procesar``, …) y las plantillas no cambian:

- ``base``           — blueprint, claves de sesión y helpers comunes.
- ``auth_admin``     — login/logout/health, usuarios y auditoría.
- ``home``           — dashboard y categorías del menú.
- ``radian``         — pipeline RADIAN, resultado editable y export SIIGO.
- ``radian_auto``    — descarga diaria automática desde el portal DIAN.
- ``importaciones``  — historial durable de importaciones RADIAN.
- ``aprendizaje``    — machine learning (entrenamiento y sugerencias).
- ``analytics``      — analítica e historial de cuentas.
- ``api``            — endpoints JSON de autocompletado (cuentas/terceros).
- ``terceros``       — maestro de terceros, RUT y cuentas bancarias.
- ``banco``          — extracto bancario, resultado, histórico y SIIGO.
- ``empresas``       — selección/CRUD de empresas y maestros.
- ``caja``           — Caja General (efectivo mensual).
- ``mixtos``         — Flujos Mixtos (efectivo sin límite de período).
- ``presupuesto``    — Sistema Presupuestal (flujo de caja proyectado vs ejecutado).

Importar este paquete registra todas las rutas en el blueprint.
"""

from .base import (  # noqa: F401
    bp,
    KEY_RESULTADO,
    KEY_BANCO,
    KEY_EMPRESA,
    ALLOWED_EXT,
    ALLOWED_EXT_CSV,
    MAESTROS_EMPRESA,
    _ref_maestro,
    _maestros_disponibles,
)

# El orden solo agrupa: cada módulo añade sus rutas al mismo blueprint.
from . import (  # noqa: E402, F401
    base,
    auth_admin,
    home,
    radian,
    radian_auto,
    importaciones,
    aprendizaje,
    analytics,
    api,
    terceros,
    banco,
    empresas,
    caja,
    mixtos,
    presupuesto,
)
