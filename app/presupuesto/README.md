# Sistema Presupuestal — 1ContaBot

Sistema de estructuración de presupuestos basado en **flujo de caja proyectado**, con diligenciamiento **mes a mes del ejecutado** (manual, por CSV o automático desde el software contable) y **análisis comparativo** con semáforos y alertas.

Está integrado en la interfaz web de 1ContaBot en **Finanzas → Sistema Presupuestal** (`app/web/routes/presupuesto.py` + plantillas `presupuesto.html` / `presupuesto_detalle.html`), protegido por los permisos RBAC `presupuesto.ver`, `presupuesto.gestionar` y `presupuesto.procesar`.

## Arquitectura

```
app/presupuesto/
├── models.py          Modelo de datos (SQLAlchemy 2): Empresa → Presupuesto →
│                      Categoría → Línea → ValorMensual (proyectado/ejecutado)
│                      + MapeoCuenta (PUC) + LogSincronizacion (auditoría)
├── schemas.py         Esquemas Pydantic v2 de entrada/salida
├── database.py        Engine/Session propios (BD presupuesto.db en DB_DIR;
│                      motor configurable con PRESUPUESTO_DATABASE_URL)
├── api.py             APIRouter FastAPI opcional (no se monta en la web Flask;
│                      requiere instalar fastapi aparte)
├── automation.py      Sincronización mensual programada (APScheduler, opt-in)
├── services/
│   ├── motor.py           Matriz de flujo de caja, flujo neto, saldo acumulado
│   ├── analisis.py        Variaciones, semáforos, cumplimiento, alertas
│   └── sincronizacion.py  Cruce contabilidad ↔ líneas por prefijo PUC
├── connectors/
│   ├── base.py        Interfaz ConectorContable (extensible)
│   ├── siigo.py       Siigo Nube (Bearer token, Partner-Id)
│   ├── alegra.py      Alegra (Basic Auth email:token)
│   └── csv_file.py    Balance de prueba / auxiliar CSV
└── plantillas/
    └── ejecutado_ejemplo.csv   Plantilla de importación del ejecutado
```

## Multi-empresa

La app principal maneja sus propias empresas (`app/empresas.py`, ids tipo texto). Cada empresa presupuestal (`pres_empresas`) se vincula a la empresa activa de la sesión por la columna `ref_externa`; las rutas web crean el vínculo automáticamente y todos los presupuestos quedan aislados por empresa.

### Base de datos
Por defecto usa SQLite en `DB_DIR/presupuesto.db` (junto a las demás BDs del sistema). Para otro motor:
```bash
export PRESUPUESTO_DATABASE_URL="postgresql://user:pass@host/1contabot"
```
Las tablas usan prefijo `pres_` para no chocar con las existentes.

## Flujo de trabajo (interfaz web)

1. **Crear el presupuesto anual** en Finanzas → Sistema Presupuestal (opcionalmente con la estructura estándar de categorías y líneas).
2. **Ajustar la estructura**: categorías (ingreso/egreso), líneas y **mapeo de cuentas PUC** por línea (prefijos: `4135` captura 413501, 413524…).
3. **Cargar el proyectado** de los 12 meses en la pestaña *Valores*.
4. **Diligenciar el ejecutado** cada mes por cualquiera de las tres vías:
   - Manual: pestaña *Valores* → Ejecutado.
   - CSV: pestaña *Sincronización* → Importar CSV (plantilla en `plantillas/`).
   - Automático: pestaña *Sincronización* → Sincronizar (requiere conector Siigo/Alegra/CSV configurado en la página del módulo).
5. **Consultar** la pestaña *Dashboard*: tarjetas de resumen, gráficas (flujo neto y saldo acumulado), análisis con semáforos (mensual o acumulado YTD) y matriz completa de flujo de caja. El historial de sincronizaciones queda en la pestaña *Sincronización*.

## Automatización

`automation.py` puede programar la sincronización de todos los presupuestos activos el día 3 de cada mes a las 2:00 (America/Bogota), trayendo el mes anterior. Configurable con `PRESUPUESTO_SYNC_DIA` y `PRESUPUESTO_SYNC_HORA`; requiere `apscheduler` y llamar `iniciar_programador()` desde el arranque, o invocar `sincronizar_todo()` desde un cron propio.

### Credenciales de conectores (campo `conector_config`, JSON)
```jsonc
// Siigo  (docs: https://developers.siigo.com)
{"username": "correo@empresa.com", "access_key": "...", "partner_id": "1ContaBot"}
// Alegra (docs: https://developer.alegra.com)
{"email": "correo@empresa.com", "token": "..."}
```
Se administran desde el formulario «Conector del ejecutado» de la página del módulo. En producción, considerar cifrar este campo o moverlo a un gestor de secretos.

### Agregar otro software contable
Heredar de `ConectorContable`, implementar `obtener_movimientos(anio, mes)` devolviendo `MovimientoContable(codigo_cuenta, nombre_cuenta, valor, fecha)` y registrarlo en `REGISTRO_CONECTORES`. Nada más cambia.

## Pruebas

```bash
python -m pytest tests/test_presupuesto.py -v
```
Cubren el motor de flujo de caja, la sincronización por prefijo PUC, el análisis con semáforos, el parser CSV y las rutas web del módulo.
