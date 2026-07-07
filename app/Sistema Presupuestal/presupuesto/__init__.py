"""
Módulo de Presupuesto para 1ContaBot
=====================================

Sistema de estructuración de presupuestos basado en flujo de caja proyectado,
con diligenciamiento mensual del ejecutado (manual o automático desde el
software contable) y análisis comparativo.

Integración rápida en FastAPI:

    from presupuesto.api import router as presupuesto_router
    app.include_router(presupuesto_router)

Ver README.md para integración en Django/Flask.
"""

__version__ = "1.0.0"
