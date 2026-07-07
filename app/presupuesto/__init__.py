"""
Sistema Presupuestal de 1ContaBot
=================================

Sistema de estructuración de presupuestos basado en flujo de caja proyectado,
con diligenciamiento mensual del ejecutado (manual, por CSV o automático desde
el software contable vía conectores Siigo/Alegra) y análisis comparativo con
semáforos y alertas.

Integrado en la interfaz web Flask a través de ``app/web/routes/presupuesto.py``
(menú Finanzas → Sistema Presupuestal). ``api.py`` conserva el APIRouter
FastAPI original por si se quiere exponer como API REST independiente
(requiere instalar ``fastapi``, que no es dependencia del proyecto).

Ver README.md para la arquitectura del módulo.
"""

__version__ = "1.0.0"
