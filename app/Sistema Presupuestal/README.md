# Módulo de Presupuesto — 1ContaBot

Sistema de estructuración de presupuestos basado en **flujo de caja proyectado**, con diligenciamiento **mes a mes del ejecutado** (manual, por CSV o automático desde el software contable) y **análisis comparativo** con semáforos y alertas.

## Arquitectura

```
presupuesto/
├── models.py          Modelo de datos (SQLAlchemy 2): Empresa → Presupuesto →
│                      Categoría → Línea → ValorMensual (proyectado/ejecutado)
│                      + MapeoCuenta (PUC) + LogSincronizacion (auditoría)
├── schemas.py         Esquemas Pydantic v2 de entrada/salida
├── database.py        Engine/Session; motor configurable por env var
├── api.py             APIRouter FastAPI: /api/presupuesto/*
├── automation.py      Sincronización mensual programada (APScheduler)
├── services/
│   ├── motor.py           Matriz de flujo de caja, flujo neto, saldo acumulado
│   ├── analisis.py        Variaciones, semáforos, cumplimiento, alertas
│   └── sincronizacion.py  Cruce contabilidad ↔ líneas por prefijo PUC
└── connectors/
    ├── base.py        Interfaz ConectorContable (extensible)
    ├── siigo.py       Siigo Nube (Bearer token, Partner-Id)
    ├── alegra.py      Alegra (Basic Auth email:token)
    └── csv_file.py    Balance de prueba / auxiliar CSV
```

## Integración en 1ContaBot

### FastAPI (recomendado)
```python
from presupuesto.api import router as presupuesto_router
from presupuesto.database import init_db
from presupuesto.automation import iniciar_programador

init_db()
app.include_router(presupuesto_router)   # monta /api/presupuesto/*
iniciar_programador()                    # sync automático mensual (opcional)
```

### Django
Montar la API como sub-aplicación ASGI en `asgi.py`:
```python
from django.core.asgi import get_asgi_application
from fastapi import FastAPI
from presupuesto.api import router
from presupuesto.database import init_db

init_db()
api = FastAPI()
api.include_router(router)

from starlette.applications import Starlette
from starlette.routing import Mount
application = Starlette(routes=[
    Mount("/api/presupuesto", app=api),
    Mount("/", app=get_asgi_application()),
])
```
Llamar `iniciar_programador()` desde `AppConfig.ready()`. Alternativa: usar solo `services/` y `connectors/` (no dependen de FastAPI) detrás de vistas Django propias.

### Flask
Igual que Django: la lógica de `services/` y `connectors/` es framework-agnóstica; o servir la API FastAPI en paralelo con un reverse proxy.

### Base de datos
```bash
export PRESUPUESTO_DATABASE_URL="postgresql://user:pass@host/1contabot"
```
Sin configurar → SQLite local. Las tablas usan prefijo `pres_` para no chocar con las existentes.

## Flujo de trabajo

1. **Crear empresa** (`POST /api/presupuesto/empresas`) con su conector: `siigo`, `alegra`, `csv` o `manual`.
2. **Crear presupuesto anual** (`POST /presupuestos`) con categorías (ingreso/egreso), líneas y **mapeo de cuentas PUC** por línea (prefijos: `"4135"` captura 413501, 413524…).
3. **Cargar el proyectado** de los 12 meses (`PUT /presupuestos/{id}/valores`).
4. **Diligenciar el ejecutado** cada mes por cualquiera de las tres vías:
   - Automático: `POST /presupuestos/{id}/sincronizar/{mes}` (Siigo/Alegra) o el job mensual programado.
   - CSV: `POST /presupuestos/{id}/importar-csv/{mes}` (plantilla en `plantillas/`).
   - Manual: `PUT /presupuestos/{id}/valores` con `tipo: ejecutado`.
5. **Consultar**:
   - `GET /presupuestos/{id}/flujo-caja` → matriz mensual P vs E, flujo neto y saldo de caja acumulado.
   - `GET /presupuestos/{id}/analisis?mes=N` → variaciones del mes; sin `mes` → acumulado YTD. Incluye semáforo (verde/amarillo/rojo según umbrales configurables por presupuesto), % cumplimiento y alertas en texto listas para mostrar o enviar.
   - `GET /presupuestos/{id}/sync-logs` → auditoría de sincronizaciones.

## Automatización

`automation.py` programa la sincronización de todos los presupuestos activos el día 3 de cada mes a las 2:00 (America/Bogota), trayendo el mes anterior. Configurable con `PRESUPUESTO_SYNC_DIA` y `PRESUPUESTO_SYNC_HORA`. También puede invocarse `sincronizar_todo()` desde un cron/Celery propio.

### Credenciales de conectores (campo `conector_config`, JSON)
```jsonc
// Siigo  (docs: https://developers.siigo.com)
{"username": "correo@empresa.com", "access_key": "...", "partner_id": "1ContaBot"}
// Alegra (docs: https://developer.alegra.com)
{"email": "correo@empresa.com", "token": "..."}
```
En producción, cifrar este campo o moverlo a un gestor de secretos.

### Agregar otro software contable
Heredar de `ConectorContable`, implementar `obtener_movimientos(anio, mes)` devolviendo `MovimientoContable(codigo_cuenta, nombre_cuenta, valor, fecha)` y registrarlo en `REGISTRO_CONECTORES`. Nada más cambia.

## Demo y pruebas

```bash
pip install -r requirements.txt
python -m uvicorn demo.app:app --reload
# → http://localhost:8000 (dashboard) · http://localhost:8000/docs (API)

pip install pytest httpx
python -m pytest tests/ -v   # 6 tests: motor, sync PUC, análisis, CSV, API
```

La demo crea una empresa ejemplo con presupuesto 2026 y ejecución enero–junio; el dashboard muestra tarjetas de resumen, gráficas (flujo neto y saldo acumulado), tabla de análisis con semáforos y la matriz completa de flujo de caja.

> Nota: si el proyecto vive en una carpeta sincronizada con OneDrive, ejecutar la demo con la base de datos fuera de ella (p. ej. `PRESUPUESTO_DATABASE_URL=sqlite:///C:/temp/presupuesto.db`), porque SQLite no maneja bien los bloqueos de archivos en carpetas sincronizadas.
