# Handoff — Plan, estado y siguiente paso · `contable-auto`

> **Cómo usar este archivo:** súbelo (o pégalo) al iniciar un chat nuevo. Es autocontenido: explica el proyecto, el plan por fases, **lo que ya se hizo**, **el siguiente paso**, y la orientación mínima del código para que una IA pueda continuar sin re-explorar todo.
>
> **Prompt sugerido para el chat nuevo:**
> *"Continúa el proyecto `contable-auto` según este handoff. Trabaja en la rama `claude/brave-meitner-cehpv0`. Retoma desde la sección 'Siguiente paso'. No abras una PR salvo que yo lo pida: los commits van a esa rama."*

---

## 0. Resumen ejecutivo

`contable-auto` es una app **Flask** de automatización contable colombiana: importa **RADIAN** y **extractos bancarios**, clasifica movimientos y genera **archivos para SIIGO**. Hoy corre en **cuentas de PRUEBA** (GitHub + Azure) y se quiere habilitar para el **equipo administrativo** en las **cuentas OFICIALES** de la empresa.

El trabajo está definido por dos documentos fuente:
- **Handoff multiempresa/Azure**: convertir la app en multi-tenant seguro (RBAC, autenticación, auditoría, aislamiento de datos/archivos, Azure SQL, Key Vault, Managed Identity, RLS).
- **Especificación SIIGO**: mejoras funcionales (editar tercero, mapeo concepto/observación, dividir/agregar movimientos, bug "Generando archivo SIIGO", retomar importaciones, módulos nuevos, ML, cruce CxC/CxP, rediseño UI).

**Problema central detectado en el código:** la app **no tiene autenticación ni autorización**. Cualquiera con la URL puede cambiar `session["empresa_id"]` y ver datos de otra empresa. El modelo multiempresa por `empresa_id` ya existe en Azure SQL, pero faltan `usuarios/roles/permisos/audit_log`, validación de acceso, aislamiento de blobs por empresa y los arreglos funcionales. **Es el bloqueante #1** para habilitarlo al equipo administrativo.

**Resultado esperado:** una primera entrega **segura y usable** (auth + RBAC + correcciones críticas) sobre las cuentas oficiales, y luego iteración de módulos nuevos y automatización.

---

## 1. Decisiones ya tomadas (con el usuario)

| Tema | Decisión |
|---|---|
| **Identidad / login** | **Microsoft Entra ID (M365)** vía App Service Authentication |
| **Alcance 1ª entrega** | **Mínimo seguro y usable**: auth + RBAC + Fase 1 funcional → go-live, luego iterar |
| **Migración a cuentas oficiales** | **Híbrida**: desarrollar en prueba; migrar justo antes de cablear la auth real |
| **Mapeo SIIGO Descripción/Observaciones (RADIAN)** | La **referencia del documento** va en **Descripción** (col 20); **Observaciones** (col 24) queda **vacía** (ver §4) |

---

## 2. Recomendación de migración (el "cuándo")

**Migrar en el límite entre la Fase 3 y la Fase 4**: después de construir y probar en prueba todo el *código* (correcciones + RBAC + aislamiento) con un *stub* de identidad de desarrollo, y **justo antes** de configurar la autenticación real con Entra.

- **No migrar "ya":** desarrollar contra producción implica rehacer recursos muchas veces, costo y riesgo. El código viaja con el repo; no se pierde nada por construirlo en prueba.
- **No migrar "al final":** la auth con Entra solo se configura/prueba bien contra el *tenant* oficial (ahí viven las identidades del equipo). Dejarlo para el final implica configurar la seguridad **dos veces**.
- **El punto híbrido** permite iterar barato en prueba y configurar **una sola vez** sobre el entorno real lo atado al tenant (Entra, Key Vault, Managed Identity, RLS, alertas). Antes de ese punto no hay datos reales que perder.

**Checklist de migración (se ejecuta entre Fase 3 y Fase 4):**
- Parametrizar `azure-setup.sh` (hoy nombres de prueba `1Contigo`/`rg-1contigo`/`sql-1contigo`/`st1contigo`) → variables por entorno.
- Crear recursos en la suscripción oficial con el script idempotente: RG, App Service, Azure SQL, Storage, Application Insights, Log Analytics, **Key Vault**.
- Mover/duplicar el repo a la organización GitHub oficial.
- Crear **service principal + OIDC trust** entre el repo oficial y el tenant oficial; actualizar los 3 secretos en `.github/workflows/main_contable-auto.yml` (hoy apuntan a UUIDs del tenant de prueba) y `app-name`/`resource-group`.
- App Settings oficiales: `USE_SQLITE=false`, `DATABASE_URL`, `AZURE_STORAGE_*`, `FLASK_SECRET_KEY` fijo, `NIT/NOMBRE/SIGLA_EMPRESA`.
- Backups/rollback: export App Settings, backup Azure SQL, tag del commit.

---

## 3. Plan por fases (ordenado por dependencias)

> Regla de orden: primero lo que **desbloquea o reduce riesgo** y no depende de la migración (código, en prueba); luego la migración; luego lo atado al tenant oficial; al final los módulos nuevos.

### Fase 0 — Higiene y preparación (en prueba)
Parametrizar recursos/entornos en `azure-setup.sh` y `.env.example`; baseline de rollback; confirmar comportamiento `USE_SQLITE`/`DATABASE_URL` en `app/config.py`.

### Fase 1 — Quick wins funcionales (en prueba)  ← **CERRADA**
- ✅ **Bug "Generando archivo SIIGO"** (módulo bancos). **HECHO** — en `main`, PR #18.
- ✅ **Editar tercero inline** + trazabilidad/aprendizaje. **HECHO** (ver §4).
- ✅ **Mapeo concepto/observación** (export RADIAN). **HECHO** (ver §4).
- ✅ **Agregar/dividir movimientos** en RADIAN (caso capital/intereses), validando cuadre. **HECHO** (ver §4).
- ✅ **Estandarización de UI**: template de edición unificado (modelo RADIAN) + página inicial de módulo unificada (modelo Bancos) + landing de RADIAN. **HECHO** (ver §4).

### Fase 2 — Modelo de datos durable + retomar importaciones  ← **CASI CERRADA**
- ✅ **Empresas → SQL**: la fuente de verdad del registro de empresas pasó de `data/empresas.json` a la tabla SQL `empresas` (BD de sistema central). **HECHO** (ver §4). Es la base para el RBAC de la Fase 3.
- ✅ **Modelo durable de importaciones + retomar conservando correcciones**: cada importación guarda un **snapshot editable durable** en BD (`importaciones.preasientos_json`) con ciclo de **estados** (`procesando → procesada → corregida → exportada`, + `error`/`anulada`). Nuevo endpoint **«Abrir»** carga el estado guardado (con las correcciones) sin reprocesar; **«Regenerar»** sigue reprocesando desde cero. **HECHO** (ver §4).
- ⏳ Confirmar `empresa_id` + índices tenant-aware (pendiente menor; el aislamiento ya funciona por archivo en SQLite y por `empresa_id` en Azure SQL).

### Fase 3 — RBAC + autorización en la app (con stub de auth dev)
Tablas/seeds `usuarios/roles/permisos/role_permissions/usuario_empresa_roles/usuario_global_roles/audit_log`. Módulos nuevos `app/authn.py`, `app/authz.py`, `app/tenancy.py`, `app/audit.py`. Decorador `require_permission` en rutas. Validar selección de empresa. Aislamiento de blobs por empresa (`empresas/{empresa_id}/...`). Auditoría de acciones clave.

### ★ MIGRACIÓN a cuentas oficiales (punto híbrido) ★
Ejecutar el **Checklist de migración** (§2).

### Fase 4 — Autenticación real + endurecimiento Azure (en oficial)
App Service Authentication con **Entra ID**; resolver/crear usuario en `dbo.usuarios`. **Key Vault + Managed Identity**. **Row-Level Security** en Azure SQL + `sp_set_session_context` por conexión. Observabilidad (App Insights, alertas, budgets).

### Fase 5 — Pruebas de aislamiento y primer go-live
Pruebas por rol y de acceso prohibido entre empresas; end-to-end RADIAN/banco→SIIGO por empresa; auditoría; rollback. **Onboarding equipo administrativo. GO-LIVE.**

### Fase 6 — Módulos nuevos (post go-live)
Caja general (plantilla Excel), inversiones en bolsa, mutuos/arriendos/periódicos. Reutilizan el patrón de `app/banco/`.

### Fase 7 — Automatización avanzada
Reglas recurrentes; cruce bancos con CxC/CxP; aprendizaje histórico/ML como **motor de sugerencias con nivel de confianza** (no contabilización automática), sobre `historial_cuentas`/`app/sugerencias.py`. **Ya existe una primera pieza de aprendizaje**: `correcciones_tercero` (ver §4).

### Fase 8 — Optimización de producto
Rediseño gráfico, dashboard ejecutivo/operativo, analítica de errores, auditoría avanzada.

---

## 4. Lo que YA se hizo (estado actual)

> **Rama de trabajo actual: `claude/brave-meitner-cehpv0`.** Parte de `main` (que ya incluye la Fase 1 vía PRs #21/#22). **No hay PR abierta** por ahora (los commits van a la rama; abrir PR solo si el usuario lo pide).

### ✅ Bug "Generando archivo SIIGO" (módulo bancos) — en `main` (PR #18)
La descarga no navega de página → el overlay *"Generando archivo SIIGO…"* nunca se ocultaba. Solución con **cookie de descarga**: `_responder_descarga(resp)` en `app/web/routes.py` adjunta cookie `descargaSiigo=<token>`; `app/web/templates/base.html` expone `window.descargaConOverlay(form, mensaje)` que muestra el overlay y lo oculta al detectar la cookie (timeout de 120 s). Usado en `banco_resultado.html`.

### ✅ Editar tercero inline + trazabilidad/aprendizaje (commit `f833d86`)
Permite corregir el tercero de un preasiento **RADIAN** antes de exportar y **aprende** la corrección.
- **UI `resultado.html`**: la celda del tercero es editable inline (vista ✎ / edición) con **autocomplete propio** (reusa `GET /api/terceros`). Badges `corregido` / `sin maestro`.
- **Endpoint `POST /corregir-tercero`** (`routes.py`): actualiza el resultado en sesión (refleja el cambio en pantalla **y** en la exportación SIIGO, porque las líneas heredan el tercero del preasiento) y registra la corrección. Helper `_resolver_tercero()` busca el nombre oficial en el maestro.
- **BD nueva tabla `correcciones_tercero`** (SQLite y Azure SQL, aislada por `empresa_id`), UPSERT por `nit_original`, contador `usos`. Funciones en `app/database.py`: `obtener_correccion_tercero`, `registrar_correccion_tercero`, `listar_correcciones_tercero` (esta última **aún sin UI**; lista para una vista de trazabilidad futura).
- **Aprendizaje en el pipeline**: `app/terceros.py::aplicar_correcciones_lote()` reaplica las correcciones tras el cruce de terceros y recalcula `tercero_encontrado` contra el maestro. Se invoca en `_ejecutar_pipeline` (routes.py) justo después de `procesar_terceros_lote`.
- **Modelo**: `PreasientoContable` ganó `tercero_nit_original` (clave estable de aprendizaje) y `tercero_corregido` (`app/models.py`); se serializan/deserializan en routes.py.
- **Tests**: `tests/test_correcciones_tercero.py` (12 casos: CRUD UPSERT + reaplicación en lote).
- **Nota**: en `banco_resultado.html` el NIT del tercero **ya era editable** con autocomplete (`/api/terceros`); ahí la Opción A ya estaba cubierta y no se tocó.

### ✅ Mapeo concepto/observación — export RADIAN→SIIGO (commit `5b17cdf`)
Decisión de negocio confirmada con caso real: en `app/siigo/mapeador.py::mapear_preasiento`,
- **Descripción** (col 20) = referencia del documento → `"{CLASIFICACIÓN} {prefijo}-{folio} | {tercero}"` (lo que antes iba en Observaciones).
- **Observaciones** (col 24) = **vacía**.
- El nombre contable genérico de la línea (`Gasto/Costo`, `Proveedores nacionales`, …) **ya no aparece**.
- Las líneas `[PENDIENTE]` conservan el prefijo `[PENDIENTE]` en Descripción y se siguen coloreando en rojo.
- **No se tocó el módulo de Bancos** (su `Descripción` es el texto real del movimiento; convención propia — ver §9).
- **Tests** actualizados en `tests/test_siigo_mapeador.py`.

### ✅ Agregar/dividir movimientos en RADIAN (commit `5525edd`)
Permite **partir una línea de un preasiento en N cuentas** antes de exportar, conservando el lado contable (débito/crédito) y el **cuadre** (Σ partes = monto original). Caso típico: separar un pago en **capital + intereses**, o repartir una base/gasto entre varias cuentas.
- **Endpoint `POST /dividir-linea`** (`routes.py`): reemplaza la línea por las partes (mismo lado), renumera, recalcula `cuadra`/`excepciones` con el helper `_recalcular_preasiento()` y actualiza la sesión. El cambio se refleja en pantalla **y** en la exportación SIIGO (las líneas fluyen vía `_deserializar_preasientos`; el mapeador SIIGO no usa `numero_linea`).
- **UI `resultado.html`**: botón ✂ por línea (pendiente y asignada) + **modal** con filas dinámicas (cuenta con autocomplete + concepto + monto), validación de suma en vivo (faltan/sobran/✓ cuadra) y submit deshabilitado hasta cuadrar. El autocomplete de cuentas se extendió a la clase `cuenta-ac`.
- **Tests**: `tests/test_dividir_linea.py` (6 casos: débito/crédito OK + validaciones suma/cuenta/<2 partes/doc inexistente). Es el **primer test de rutas web** con `test_client` (siembra la sesión vía `session_store`/`storage`).

### ✅ Estandarización de UI (commits `4956b03`, `94e986d`)
Un solo modelo visual por tipo de página, con parametrización por módulo (decisión del usuario).
- **Template de edición** (modelo **RADIAN/`resultado.html`**): `banco_resultado.html` adopta la jerarquía *summary cards → leyenda dedicada → tabla*. Se reemplazó el `banco-header`/`sticky-bar` por una cuadrícula de tarjetas (panel **Acciones** con Generar SIIGO) + leyenda de pills + tarjeta **"Configuración del extracto"** (cuenta/NIT banco). Se conservan todos los IDs/clases del JS y la lógica 4x1000/intereses. CSS muerto eliminado.
- **Página inicial de módulo** (modelo **Bancos/`banco_upload.html`**): nueva **landing de RADIAN** (`GET /radian` + `radian_upload.html`) con *¿qué hace el módulo? · carga · guía rápida · actividad reciente*; antes RADIAN solo existía como modal "Automatizar proceso" (que sigue disponible en la topbar). El sidebar enlaza RADIAN a su landing.
- **Partial genérico `_actividad_items.html`** (claves `archivo/estado/fecha/count/unidad/ext`) reutilizado por Bancos y RADIAN; reemplaza a `banco_actividad_items.html` (eliminado). Helpers `_actividad_radian()` (sobre `importaciones`) y `_actividad_banco()` exponen esas claves.

### ✅ Empresas → SQL (Fase 2, parte 1)
La fuente de verdad del registro de empresas pasó de `data/empresas.json` a una **tabla SQL `empresas`** en una **BD de sistema central** (`config.SYSTEM_DB_PATH` = `db/sistema.db` en SQLite; tabla compartida en Azure SQL). Es la base que la Fase 3 reutilizará para `usuarios/roles`.
- **`app/config.py`**: nueva `SYSTEM_DB_PATH` (BD central; se ignora en Azure SQL, donde todo vive en la misma BD).
- **`app/database.py`**: tabla `empresas` (SQLite + T-SQL) + funciones `inicializar_db_sistema`, `listar_empresas_registro`, `obtener_empresa_registro`, `guardar_empresa_registro` (UPSERT: `ON CONFLICT`/`MERGE`), `eliminar_empresa_registro`, `contar_empresas_registro`. Los campos con estructura (`cuentas_*`, `bancos`, `formato_banco`) se guardan serializados como **JSON** en columnas de texto (no se normalizó de más; el objetivo es mover la fuente de verdad a SQL, no rediseñar la config de empresa). **La tabla `empresas` NO se filtra por `empresa_id`**: ES el catálogo de empresas.
- **`app/empresas.py`**: la persistencia (`_leer_registro`, `guardar_empresa`, `eliminar_empresa`) ahora va contra la BD. **Migración automática** una sola vez por proceso: si la tabla está vacía y existe el `empresas.json` legado, se importa (`_asegurar_sistema`/`_migrar_json_legacy`). El `Empresa` dataclass y la API pública **no cambiaron** (rutas/tests intactos).
- **Tests**: `tests/test_empresas_db.py` (11 casos: CRUD UPSERT, columnas JSON, conteo, MERGE T-SQL con conteo de parámetros, catálogo sin filtro `empresa_id`) + 3 casos de migración en `tests/test_empresas.py`. Fixture de `test_empresas.py` adaptado para redirigir la BD de sistema a un temporal.

### ✅ Modelo durable de importaciones + retomar conservando correcciones (Fase 2, parte 2)
Antes, los preasientos vivían **solo en la sesión** (server-side, efímeros) y «Retomar» reprocesaba el RADIAN desde cero → **perdía las correcciones manuales**. Ahora cada importación guarda un **snapshot editable durable en BD** y se puede **«Abrir»** para seguir donde se quedó.
- **`app/database.py`**: nueva columna `importaciones.preasientos_json` (SQLite + T-SQL) con **migración aditiva** (`_asegurar_columna`/`_columna_existe`: `ALTER TABLE ... ADD` si falta, para BD ya existentes). `actualizar_importacion` acepta `preasientos_json` (COALESCE: una transición de estado conserva el snapshot/Excel previos). Nueva `obtener_snapshot_importacion`. `listar_importaciones` ahora selecciona columnas explícitas + `tiene_snapshot` (CASE) **sin** arrastrar el JSON pesado.
- **Ciclo de estados**: `procesando → procesada → corregida → exportada` (+ `error`, `anulada`). `/procesar` y `/importaciones/<id>/reprocesar` → `procesada`; las ediciones (`/corregir-tercero`, `/dividir-linea`, `/confirmar`) → `corregida`; `/exportar-siigo` → `exportada`.
- **`app/web/routes.py`**: helper `_persistir_importacion(emp, datos, estado)` (best-effort: guarda el snapshot durable junto al de sesión en cada punto de cambio). Nuevos endpoints **`POST /importaciones/<id>/abrir`** (carga el snapshot durable en la sesión → `/resultado`, sin reprocesar) y **`POST /importaciones/<id>/anular`**. `session_store` sigue siendo la copia de trabajo rápida; la BD es la copia durable (se actualizan juntas).
- **UI `importaciones.html`**: pills por estado (`_ESTADOS_IMPORTACION`), botón **📂 Abrir** (cuando hay snapshot), **🔄 Regenerar/Retomar** (reprocesa desde cero, con tooltip), **⬇️ Excel** y **✕ Anular** (con confirmación); filas anuladas atenuadas. Partial `_actividad_items.html` ganó una rama `anulada` (Bancos no la emite). Dos clases CSS nuevas: `.pill-info`, `.pill-muted`.
- **Tests**: `tests/test_importaciones_durable.py` (9 casos: migración de columna, round-trip del snapshot, COALESCE en transición de estado, `tiene_snapshot`, y rutas Abrir/editar→corregida/Anular/render). Fixture de rutas que redirige `config.DB_PATH`/`SYSTEM_DB_PATH` a temporales.

**Verificación global:** `pytest` → **232/232 OK**.

---

## 5. Siguiente paso

**Fase 2 prácticamente cerrada.** Hechas la **parte 1 (Empresas → SQL)** y la **parte 2 (modelo durable de importaciones + «Abrir» conservando correcciones)** (ver §4). Queda un pendiente menor: confirmar `empresa_id` + **índices** tenant-aware en las tablas por-empresa (el aislamiento ya funciona; faltan índices para rendimiento en Azure SQL).

Siguiente fase grande: **Fase 3 — RBAC + autorización en la app** (con stub de auth dev). Tablas/seeds `usuarios/roles/permisos/role_permissions/usuario_empresa_roles/usuario_global_roles/audit_log` (reutilizan la **BD de sistema** ya creada en la parte 1). Módulos `app/authn.py`, `app/authz.py`, `app/tenancy.py`, `app/audit.py`. Decorador `require_permission`. Validar selección de empresa. Aislamiento de blobs por empresa. Es el **bloqueante #1** para habilitar el equipo administrativo (ver §0).

Opciones rápidas si el usuario lo pide:
- **Abrir PR** de esta rama (`claude/brave-meitner-cehpv0`) para revisar los incrementos de la Fase 2 antes de seguir.
- **Índices tenant-aware** en Azure SQL (`empresa_id` + claves de búsqueda) — pendiente menor de la Fase 2.
- **Llevar la división y el modelo durable a Bancos** (hoy el durable es solo RADIAN; Bancos usa su propio `procesos_banco` sin snapshot editable).
- **Vista de trazabilidad** de `listar_correcciones_tercero()` (aún sin UI, §9).

---

## 6. Orientación del código (para no re-explorar)

**Stack:** Python 3.11 + Flask, Gunicorn en Azure App Service Linux. SQLite (local/dev: un archivo por empresa `contable_<id>.db`) **o** Azure SQL vía `pyodbc` (`USE_SQLITE=false`, tablas con columna `empresa_id`). Storage local **o** Azure Blob. Sin librerías de auth aún.

**Archivos clave:**
- **Web/rutas:** `app/web/routes.py` (~28 rutas; `_empresa_actual()`, `_ejecutar_pipeline()`, `_deserializar_preasientos()`, `_resolver_tercero()`, `_recalcular_preasiento()`, `_persistir_importacion()` (snapshot durable), `_actividad_radian()`/`_actividad_banco()`, endpoints `/radian`, `/confirmar`, `/corregir-tercero`, `/dividir-linea`, `/exportar-siigo`, `/importaciones/<id>/{abrir,reprocesar,anular,descargar}`, `/banco/*`), `app/web/__init__.py` (factory `create_app`, `FLASK_SECRET_KEY`, CSRF flask-wtf), `app/web/session_store.py` (copia de trabajo server-side; claves `resultado_ref`, `banco_ref`, `empresa_id`; la copia **durable** del resultado vive en `importaciones.preasientos_json`).
- **Plantillas:** `app/web/templates/{base,index,radian_upload,resultado,banco_resultado,banco_upload,banco_historial,importaciones,empresas,analytics,historial}.html` + partial `_actividad_items.html`. UI en HTML + CSS propio (`static/style.css`), JS vanilla, sin framework. **Modelo visual único:** páginas de edición siguen `resultado.html` (autocomplete de **cuentas**/**terceros**, edición inline `toggleEditCuenta`/`toggleEditTercero`, división ✂ por línea); páginas iniciales de módulo siguen `banco_upload.html` (¿qué hace? · carga · guía · actividad).
- **Datos/multiempresa:** `app/database.py` (conexión dual, esquema SQLite/T-SQL, filtros `empresa_id` vía `_and_empresa`/`_where_empresa`; migración aditiva de columnas vía `_asegurar_columna`; tablas por-empresa: `documentos_importados`, `bitacora`, `historial_cuentas`, `importaciones` —con **`preasientos_json`** (snapshot durable) y estados; `procesos_banco`, **`correcciones_tercero`**; **tabla de sistema `empresas`** —registro central, sin filtro `empresa_id`— con `inicializar_db_sistema`/`*_empresa_registro`; snapshot vía `obtener_snapshot_importacion`/`actualizar_importacion(preasientos_json=…)`), `app/empresas.py` (dataclass `Empresa`; persiste en la tabla SQL `empresas` vía BD de sistema `config.SYSTEM_DB_PATH`; migra `empresas.json` legado la 1ª vez), `app/storage.py` (local/Blob; maestros aislados en `data/{empresa_id}`, pero uploads/output/db **no**).
- **Dominio:** `app/importador.py` (RADIAN), `app/clasificador.py`, `app/terceros.py` (`identificar_tercero`, `cruzar_tercero`, `procesar_terceros_lote`, **`aplicar_correcciones_lote`**), `app/preasiento.py` (genera `LineaContable`/`PreasientoContable`), `app/models.py` (`PreasientoContable` con `tercero_nit_original`/`tercero_corregido`, `LineaContable`, `MovimientoBanco`), `app/sugerencias.py` (motor de cuentas por historial), `app/validaciones.py`.
- **SIIGO:** `app/siigo/mapeador.py` (27 columnas; **Descripción=referencia del doc, Observaciones vacía**), `app/siigo/exportador_siigo.py`, `app/siigo/api_client.py`.
- **Banco:** `app/banco/{importador_banco,mapeador_banco,exportador_banco}.py` (consolida intereses, enlaza 4x1000; su `Descripción`/`Observaciones` siguen su propia convención).
- **Infra/deploy:** `application.py`, `startup.sh` (instala ODBC 18 si `USE_SQLITE=false`; gunicorn), `azure-setup.sh`, `.github/workflows/main_contable-auto.yml` (CI test + deploy OIDC), `app/config.py`, `.env.example`. Docs: `CONTEXTO_IA.md`, `docs/arquitectura.md`.

**Rutas que aceptan IDs de objeto (revisar ownership en Fase 3):** `/importaciones/<imp_id>/{abrir,reprocesar,anular,descargar}`, `/empresas/<empresa_id>/...`. (Hoy se aíslan por empresa vía `db_path`/`empresa_id`; falta validar que el usuario tenga acceso a esa empresa.)

---

## 7. Cómo correr y verificar (local)

```bash
pip install --ignore-installed blinker -r requirements.txt   # deps (el flag evita conflicto con blinker del sistema)
pip install pytest                                           # si no está
python -m pytest tests/ -q                                   # 204 tests
# Smoke: arrancar app y render del índice
USE_SQLITE=true FLASK_SECRET_KEY=dev python -c "from app.web import create_app; c=create_app().test_client(); print(c.get('/').status_code)"
```

---

## 8. Notas de entorno y git

- **Rama de trabajo:** `claude/brave-meitner-cehpv0` (parte de `main`, que ya incluye la Fase 1 vía PRs #21/#22). **No hay PR abierta** (abrir solo si el usuario lo pide; push a la rama la prepara). Último incremento: **Empresas → SQL** (Fase 2, parte 1).
- **Cuentas actuales: de PRUEBA** (GitHub `JuanCamiloVergara/contable-auto` + Azure de prueba). La migración a cuentas oficiales se hace en el punto híbrido (§2).
- **Entorno remoto efímero:** todo lo que valga la pena debe quedar **commiteado y pusheado**. Este handoff vive en el repo como `HANDOFF.md`.

---

## 9. Riesgos / decisiones pendientes

- **Bancos — Descripción/Observaciones:** ¿aplicar también al export de Bancos la regla de "Observaciones vacía"? Hoy Bancos mantiene su convención propia (Descripción = texto real del movimiento; Observaciones = metadatos `Banco … | Cód… | …`). Pendiente de decisión del usuario.
- **Agregar/dividir movimientos:** implementado en **RADIAN** (`/dividir-linea`). Pendiente (opcional) llevarlo a **Bancos** sobre `MovimientoBanco` si el usuario lo pide; el template de edición ya está unificado.
- **El modelo durable de importaciones** (Fase 2, parte 2) se resolvió con un **snapshot por importación** (`importaciones.preasientos_json`) en vez de tablas normalizadas de líneas/versiones — pragmático y de bajo riesgo. Si en el futuro se requieren versiones/diffs finos o consultas por línea, habría que normalizar; por ahora el snapshot cubre «retomar conservando correcciones» y la trazabilidad por estado.
- La auth Entra exige que el equipo administrativo tenga identidades en el **tenant oficial** (asumido por la decisión tomada).
- `listar_correcciones_tercero()` existe pero **no tiene UI**; opcional: una vista de trazabilidad de correcciones de tercero.
