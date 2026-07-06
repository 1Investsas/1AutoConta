# Handoff — Plan, estado y siguiente paso · `1ContaBot`

> **Cómo usar este archivo:** súbelo (o pégalo) al iniciar un chat nuevo. Es autocontenido: explica el proyecto, el plan por fases, **lo que ya se hizo**, **el siguiente paso**, y la orientación mínima del código para que una IA pueda continuar sin re-explorar todo.
>
> **Prompt sugerido para el chat nuevo:**
> *"Continúa el proyecto `1ContaBot` según este handoff. Trabaja en la rama que tenga asignada la sesión. Retoma desde la sección 'Siguiente paso'. No abras una PR salvo que yo lo pida: los commits van a esa rama."*

---

## 0. Resumen ejecutivo

`1ContaBot` es una app **Flask** de automatización contable colombiana: importa **RADIAN** y **extractos bancarios**, clasifica movimientos y genera **archivos para SIIGO**. Hoy corre en **cuentas de PRUEBA** (GitHub + Azure) y se quiere habilitar para el **equipo administrativo** en las **cuentas OFICIALES** de la empresa.

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
- Parametrizar `azure-setup.sh` (hoy nombres de prueba `1contabot`/`rg-1contabot`/`sql-1contabot`/`st1contabot`) → variables por entorno.
- Crear recursos en la suscripción oficial con el script idempotente: RG, App Service, Azure SQL, Storage, Application Insights, Log Analytics, **Key Vault**.
- Mover/duplicar el repo a la organización GitHub oficial.
- Crear **service principal + OIDC trust** entre el repo oficial y el tenant oficial; actualizar los 3 secretos en `.github/workflows/main_1contabot.yml` (hoy apuntan a UUIDs del tenant de prueba) y `app-name`/`resource-group`.
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

### Fase 2 — Modelo de datos durable + retomar importaciones  ← **CERRADA**
- ✅ **Empresas → SQL**: la fuente de verdad del registro de empresas pasó de `data/empresas.json` a la tabla SQL `empresas` (BD de sistema central). **HECHO** (ver §4). Es la base para el RBAC de la Fase 3.
- ✅ **Modelo durable de importaciones + retomar conservando correcciones**: cada importación guarda un **snapshot editable durable** en BD (`importaciones.preasientos_json`) con ciclo de **estados** (`procesando → procesada → corregida → exportada`, + `error`/`anulada`). Nuevo endpoint **«Abrir»** carga el estado guardado (con las correcciones) sin reprocesar; **«Regenerar»** sigue reprocesando desde cero. **HECHO** (ver §4).
- ✅ **`empresa_id` + índices tenant-aware**: confirmado que toda tabla compartida lleva `empresa_id` y que las consultas filtran por él; añadidos índices en Azure SQL para listados/analítica por empresa (`ix_importaciones_empresa`, `ix_procesos_banco_empresa`, `ix_documentos_empresa_clasif`). **HECHO** (ver §4). El aislamiento ya funcionaba: por archivo en SQLite y por `empresa_id` en Azure SQL.

### Fase 3 — RBAC + autorización en la app (con stub de auth dev)  ← **CERRADA**
- ✅ Tablas/seeds `usuarios/roles/permisos/role_permissions/usuario_empresa_roles/usuario_global_roles/audit_log`. **HECHO** (ver §4).
- ✅ Módulos nuevos `app/authn.py`, `app/authz.py`, `app/tenancy.py`, `app/audit.py`. **HECHO**.
- ✅ Decorador `require_permission` en todas las rutas de datos. **HECHO**.
- ✅ Validar selección de empresa (arreglo del bloqueante #1). **HECHO**.
- ✅ Aislamiento de uploads por empresa (`empresas/{empresa_id}/uploads`). **HECHO** (output/web_sessions blob quedan como follow-up; ver §9).
- ✅ Auditoría de acciones clave + intentos denegados, con UI de bitácora. **HECHO**.

### ★ MIGRACIÓN a cuentas oficiales (punto híbrido) ★
Ejecutar el **Checklist de migración** (§2).

### Fase 4 — Autenticación real + endurecimiento Azure (en oficial)
- ✅ **Autenticación con Entra ID (lado app)**: parseo completo del principal de App Service Authentication (`X-MS-CLIENT-PRINCIPAL`: email/nombre/oid/tid), autoprovisión con sincronización de nombre/`entra_oid`, validación opcional de tenant (`ENTRA_TENANT_ID`), login vía `/.auth/login/aad`, logout que cierra también Easy Auth, página de "cuenta sin acceso" sin bucles, y sección 7 de `azure-setup.sh` (app registration + `az webapp auth`). **HECHO** (ver §4).
- ⏳ Ejecutar la configuración en el entorno oficial (correr `azure-setup.sh` §7 tras la migración). **Key Vault + Managed Identity**. **Row-Level Security** en Azure SQL + `sp_set_session_context` por conexión. Observabilidad (App Insights, alertas, budgets).

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

> **La Fase 3 ya está en `main`** (la rama `claude/charming-lovelace-myhcez` se fusionó vía PR #25). Desde entonces `main` también incorporó módulos posteriores (PRs #26–#50 y siguientes: terceros/RUT, caja general, flujos mixtos, ML de prediligenciamiento, refactor de `app/database.py` y `app/web/routes.py` en paquetes, y el rename a **1ContaBot**). Cada sesión nueva trabaja en su propia rama `claude/*` a partir de `main`.

### ✅ Fase 4 (parte 1) — Autenticación real con Microsoft Entra ID (lado app)
Todo lo que depende del código para la auth real quedó implementado y probado; lo que falta de la Fase 4 es de infraestructura (ejecutar `azure-setup.sh` §7 en el entorno oficial, Key Vault, RLS, observabilidad).
- **`app/authn.py`**: `principal_entra()` decodifica la cabecera **`X-MS-CLIENT-PRINCIPAL`** (JSON base64 con los claims del token: email `preferred_username`/`emailaddress`/`upn`, `name`, `oid`, `tid`; acepta nombres cortos v2 y URIs WS-Fed) con fallback a `X-MS-CLIENT-PRINCIPAL-NAME`/`-ID`. Con **`ENTRA_TENANT_ID`** definido se exige que el claim `tid` coincida (identidades de otro tenant no entran ni se provisionan). `_resolver_usuario_entra()` autoprovisiona el usuario **sin roles** y sincroniza nombre/`entra_oid` cuando Entra trae valores nuevos (antes el "nombre" se tomaba por error del GUID `X-MS-CLIENT-PRINCIPAL-ID`); el último acceso se registra una vez por sesión. `url_login_entra()`/`url_logout_entra()` construyen las URLs de Easy Auth. `iniciar_sesion()` queda deshabilitado en modo entra (el formulario dev no puede suplantar identidades).
- **Rutas (`auth_admin.py`)**: en modo entra `/login` muestra el botón **«Continuar con Microsoft»** (`/.auth/login/aad?post_login_redirect_uri=…`) y, si llega una identidad Entra válida pero **sin acceso** (cuenta desactivada o sin provisionar), responde 200 con el aviso y un enlace "cambiar de cuenta" — sin bucle de redirecciones — y audita el intento; `/logout` limpia la sesión Flask y redirige a **`/.auth/logout`** para cerrar también la sesión de Easy Auth. `/health` y `/radian/auto/cron` siguen públicos.
- **Config**: `ENTRA_TENANT_ID`, `ENTRA_LOGIN_PATH`, `ENTRA_LOGOUT_PATH` (`app/config.py`, `.env.example`).
- **`azure-setup.sh` §7**: crea el app registration single-tenant con la callback de Easy Auth, activa `az webapp auth` (authV2, **AllowAnonymous** porque la compuerta la aplica la app) y fija `AUTH_MODE=entra`, `ENTRA_TENANT_ID` y `BOOTSTRAP_ADMIN_EMAIL` (vaciar tras el primer login del admin).
- **Tests**: `tests/test_authn_entra.py` (17 casos: decodificación del principal y claims alternos, autoprovisión + sincronización, bootstrap admin, tenant correcto/ajeno/sin `tid`, principal corrupto, gate → login → Microsoft, cuenta desactivada sin bucle + auditoría, logout Easy Auth, POST de login deshabilitado, `/health` público, y que el modo dev **ignora** las cabeceras Entra).

### ✅ Fase 3 — RBAC + autorización + multi-tenencia (con stub de auth dev)
El bloqueante #1 (cualquiera podía fijar `session["empresa_id"]` y ver otra empresa) queda resuelto: ahora hay identidad, permisos por rol y validación de acceso a empresa.
- **BD de sistema (`app/database.py`)**: nuevas tablas RBAC (`usuarios`, `roles`, `permisos`, `role_permissions`, `usuario_global_roles`, `usuario_empresa_roles`, `audit_log`) con DDL SQLite + T-SQL e idempotencia. CRUD: usuarios (`crear_usuario`/`obtener_usuario_por_email`/`actualizar_usuario`/`registrar_acceso_usuario`/`listar_usuarios`), roles/permisos (`obtener_o_crear_rol`/`obtener_o_crear_permiso`/`vincular_rol_permiso`), asignaciones (`asignar_rol_global`/`asignar_rol_empresa`/`revocar_*`), consultas de autorización (`permisos_usuario` = unión de roles globales + de empresa, `empresas_de_usuario`, `tiene_rol_global`, `roles_de_usuario`), auditoría (`registrar_evento_auditoria`/`listar_auditoria`). Estas tablas NO se filtran por `empresa_id`: son el control de acceso transversal.
- **`app/authz.py`**: catálogo de **permisos** (`dashboard.ver`, `radian.*`, `banco.*`, `importaciones.*`, `analitica.ver`, `ml.ver`, `empresas.*`, `usuarios.gestionar`, `auditoria.ver`) y **roles** alineados al menú (`admin`, `contador`, `auxiliar`=Digitación, `consulta`=Visualización). `seed_rbac()` idempotente, `tiene_permiso()` y el decorador **`require_permission(permiso)`** (resuelve usuario+empresa activa, deniega con 403 y audita).
- **`app/authn.py`**: stub de dev (`AUTH_MODE=dev`): autologin de un admin local (`DEV_AUTH_EMAIL`, autoprovisiona rol admin global) y página de login para cambiar de usuario y probar roles; `cerrar_sesion()` suprime el autologin. Listo para **Entra** (`AUTH_MODE=entra`): lee la cabecera `X-MS-CLIENT-PRINCIPAL-NAME` de App Service Authentication y autoprovisiona el usuario (+ `BOOTSTRAP_ADMIN_EMAIL` opcional). Compuerta `gate()` (before_request) exige sesión salvo en `/login`, `/logout`, `/health`, estáticos.
- **`app/tenancy.py`**: `empresa_actual()` resuelve y **valida** la empresa de la sesión (si no es accesible, cae a la primera accesible y corrige la sesión); `puede_acceder_empresa()`, `empresas_accesibles()`, `seleccionar_empresa()`.
- **`app/audit.py`**: `registrar(accion, …)` best-effort con usuario/IP del contexto.
- **`app/web/routes.py`**: `require_permission` en las ~28 rutas; `_empresa_actual()` delega en tenancy; el context processor solo expone **empresas accesibles** + el usuario; `/empresas/seleccionar` valida acceso (audita denegados). Rutas nuevas: `/login`, `/logout`, `/health`, **`/usuarios`** (+ crear/asignar/revocar/estado) y **`/auditoria`**. Auditoría en procesar/exportar/dividir/corregir/abrir/anular/reprocesar/empresa.*. Uploads aislados por empresa (`Empresa.upload_category`).
- **UI**: `login.html` (autónomo), `usuarios.html` (gestión de roles), `auditoria.html` (bitácora); `base.html` muestra el usuario real + logout y enlaza Usuarios/Auditoría.
- **Config**: `AUTH_MODE`, `DEV_AUTH_EMAIL`, `DEV_AUTH_NOMBRE`, `BOOTSTRAP_ADMIN_EMAIL` en `app/config.py`.
- **Tests**: `tests/test_rbac.py` (17 casos: seed, CRUD usuarios, unión de permisos, rol global, tenancy, gate/login/logout, 403 + auditoría, aislamiento de empresa ajena).

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

### ✅ `empresa_id` + índices tenant-aware (Fase 2, cierre)
- **Confirmación**: todas las tablas compartidas de Azure SQL llevan `empresa_id` y todas las consultas filtran por él (`_where_empresa`/`_and_empresa`). En SQLite el aislamiento es por archivo (`contable_<id>.db`), sin columna `empresa_id`.
- **`app/database.py`**: helper idempotente `_asegurar_indices_mssql` (invocado desde `inicializar_db` solo en Azure SQL) que crea índices `IF NOT EXISTS` (chequeo por `name` + `object_id`): `ix_importaciones_empresa(empresa_id,id)`, `ix_procesos_banco_empresa(empresa_id,id)`, `ix_documentos_empresa_clasif(empresa_id,clasificacion)`. Las tablas con `UNIQUE(empresa_id,…)` (documentos/historial/correcciones) ya tenían índice que cubre el filtro; `bitacora` solo se escribe, así que no se indexa.
- **Tests**: `TestTenantAwareAzure` en `tests/test_aislamiento_empresa.py` (4 casos: DDL con `empresa_id` en las 6 tablas, índices idempotentes y por `empresa_id`, que `inicializar_db` los emite en Azure y NO en SQLite).

**Verificación global:** `pytest` → **437/437 OK** (la suite creció con los módulos posteriores a la Fase 3: terceros/RUT, caja general, flujos mixtos, ML).

---

## 5. Siguiente paso

**Fase 3 CERRADA** (fusionada a `main` vía PR #25) y **Fase 4 (parte 1) — autenticación real con Entra ID — implementada en el código** (ver §4): principal completo de Easy Auth, validación de tenant, login/logout Entra, autoprovisión con nombre/oid y `azure-setup.sh` §7. `pytest` 453/453.

Siguiente hito: **★ MIGRACIÓN a cuentas oficiales (punto híbrido) ★** y luego cerrar la **Fase 4 en infraestructura**: ejecutar el **Checklist de migración** (§2), correr `azure-setup.sh` (incluida la sección 7 de Entra: app registration + `az webapp auth` + `AUTH_MODE=entra`/`ENTRA_TENANT_ID`/`BOOTSTRAP_ADMIN_EMAIL`), y después Key Vault + Managed Identity, RLS en Azure SQL y observabilidad. Tras el primer login del admin real, vaciar `BOOTSTRAP_ADMIN_EMAIL`.

Opciones rápidas si el usuario lo pide:
- **Granularidad/roles**: ajustar el catálogo de permisos o los roles seed (`app/authz.py`) si el equipo administrativo necesita otra separación de funciones.
- **Aislamiento de blobs** de `output` y `web_sessions` por empresa (hoy solo `uploads` está aislado; §9).
- **Llevar la división y el modelo durable a Bancos** (hoy el durable es solo RADIAN; Bancos usa su propio `procesos_banco` sin snapshot editable).
- **Vista de trazabilidad** de `listar_correcciones_tercero()` (aún sin UI, §9).

---

## 6. Orientación del código (para no re-explorar)

**Stack:** Python 3.11 + Flask, Gunicorn en Azure App Service Linux. SQLite (local/dev: un archivo por empresa `contable_<id>.db`) **o** Azure SQL vía `pyodbc` (`USE_SQLITE=false`, tablas con columna `empresa_id`). Storage local **o** Azure Blob. **Auth/RBAC propios** (sin librería externa): stub de dev + Entra ID vía App Service Authentication (ver §4).

**Archivos clave:**
- **Web/rutas:** `app/web/routes.py` (~28 rutas; `_empresa_actual()`, `_ejecutar_pipeline()`, `_deserializar_preasientos()`, `_resolver_tercero()`, `_recalcular_preasiento()`, `_persistir_importacion()` (snapshot durable), `_actividad_radian()`/`_actividad_banco()`, endpoints `/radian`, `/confirmar`, `/corregir-tercero`, `/dividir-linea`, `/exportar-siigo`, `/importaciones/<id>/{abrir,reprocesar,anular,descargar}`, `/banco/*`), `app/web/__init__.py` (factory `create_app`, `FLASK_SECRET_KEY`, CSRF flask-wtf), `app/web/session_store.py` (copia de trabajo server-side; claves `resultado_ref`, `banco_ref`, `empresa_id`; la copia **durable** del resultado vive en `importaciones.preasientos_json`).
- **Plantillas:** `app/web/templates/{base,index,radian_upload,resultado,banco_resultado,banco_upload,banco_historial,importaciones,empresas,analytics,historial}.html` + partial `_actividad_items.html`. UI en HTML + CSS propio (`static/style.css`), JS vanilla, sin framework. **Modelo visual único:** páginas de edición siguen `resultado.html` (autocomplete de **cuentas**/**terceros**, edición inline `toggleEditCuenta`/`toggleEditTercero`, división ✂ por línea); páginas iniciales de módulo siguen `banco_upload.html` (¿qué hace? · carga · guía · actividad).
- **Datos/multiempresa:** `app/database.py` (conexión dual, esquema SQLite/T-SQL, filtros `empresa_id` vía `_and_empresa`/`_where_empresa`; migración aditiva de columnas vía `_asegurar_columna`; tablas por-empresa: `documentos_importados`, `bitacora`, `historial_cuentas`, `importaciones` —con **`preasientos_json`** (snapshot durable) y estados; `procesos_banco`, **`correcciones_tercero`**; **tabla de sistema `empresas`** —registro central, sin filtro `empresa_id`— con `inicializar_db_sistema`/`*_empresa_registro`; snapshot vía `obtener_snapshot_importacion`/`actualizar_importacion(preasientos_json=…)`), `app/empresas.py` (dataclass `Empresa`; persiste en la tabla SQL `empresas` vía BD de sistema `config.SYSTEM_DB_PATH`; migra `empresas.json` legado la 1ª vez), `app/storage.py` (local/Blob; maestros aislados en `data/{empresa_id}` y **uploads** en `empresas/{empresa_id}/uploads`; output/web_sessions/db aún no — §9).
- **Dominio:** `app/importador.py` (RADIAN), `app/clasificador.py`, `app/terceros.py` (`identificar_tercero`, `cruzar_tercero`, `procesar_terceros_lote`, **`aplicar_correcciones_lote`**), `app/preasiento.py` (genera `LineaContable`/`PreasientoContable`), `app/models.py` (`PreasientoContable` con `tercero_nit_original`/`tercero_corregido`, `LineaContable`, `MovimientoBanco`), `app/sugerencias.py` (motor de cuentas por historial), `app/validaciones.py`.
- **SIIGO:** `app/siigo/mapeador.py` (27 columnas; **Descripción=referencia del doc, Observaciones vacía**), `app/siigo/exportador_siigo.py`, `app/siigo/api_client.py`.
- **Banco:** `app/banco/{importador_banco,mapeador_banco,exportador_banco}.py` (consolida intereses, enlaza 4x1000; su `Descripción`/`Observaciones` siguen su propia convención).
- **Auth/RBAC (Fases 3-4):** `app/authn.py` (identidad: stub dev + Entra ID vía Easy Auth — `principal_entra()`, `url_login_entra`/`url_logout_entra`; `gate()` before_request), `app/authz.py` (catálogo permisos/roles, `seed_rbac`, `require_permission`), `app/tenancy.py` (acceso a empresas: `empresa_actual` validada, `puede_acceder_empresa`), `app/audit.py` (`registrar`). Tablas RBAC en la BD de sistema (`app/database.py`). Plantillas `login.html`/`usuarios.html`/`auditoria.html`.
- **Infra/deploy:** `application.py`, `startup.sh` (instala ODBC 18 si `USE_SQLITE=false`; gunicorn), `azure-setup.sh`, `.github/workflows/main_1contabot.yml` (CI test + deploy OIDC), `app/config.py` (incluye `AUTH_MODE`/`DEV_AUTH_*`/`BOOTSTRAP_ADMIN_EMAIL`), `.env.example`. Docs: `CONTEXTO_IA.md`, `docs/arquitectura.md`.

**Rutas que aceptan IDs de objeto:** `/importaciones/<imp_id>/{abrir,reprocesar,anular,descargar}` se aíslan por la BD de la empresa activa (un id de otra empresa no aparece en la BD activa). `/empresas/<empresa_id>/...` exige `empresas.gestionar` (hoy solo el admin global). La empresa activa siempre se valida en `tenancy.empresa_actual`.

---

## 7. Cómo correr y verificar (local)

```bash
pip install --ignore-installed blinker -r requirements.txt   # deps (el flag evita conflicto con blinker del sistema)
pip install pytest                                           # si no está
python -m pytest tests/ -q                                   # 437 tests
# Auth: por defecto AUTH_MODE=dev → autologin de un admin local (DEV_AUTH_EMAIL).
# Para probar roles: /logout y luego /login eligiendo otro usuario en /usuarios.
# Smoke: arrancar app y render del índice
USE_SQLITE=true FLASK_SECRET_KEY=dev python -c "from app.web import create_app; c=create_app().test_client(); print(c.get('/').status_code)"
```

---

## 8. Notas de entorno y git

- **Rama de trabajo:** cada sesión usa su propia rama `claude/*` a partir de `main`. `main` ya incluye Fases 1–3 (la Fase 3 se fusionó vía PR #25) y los módulos posteriores (PRs #26–#50+). **No abrir PR salvo que el usuario lo pida** (push a la rama la prepara).
- **Cuentas actuales: de PRUEBA** (GitHub `1investsas/1autoconta` + Azure de prueba). La migración a cuentas oficiales se hace en el punto híbrido (§2).
- **Entorno remoto efímero:** todo lo que valga la pena debe quedar **commiteado y pusheado**. Este handoff vive en el repo como `HANDOFF.md`.

---

## 9. Riesgos / decisiones pendientes

- **Bancos — Descripción/Observaciones:** ¿aplicar también al export de Bancos la regla de "Observaciones vacía"? Hoy Bancos mantiene su convención propia (Descripción = texto real del movimiento; Observaciones = metadatos `Banco … | Cód… | …`). Pendiente de decisión del usuario.
- **Agregar/dividir movimientos:** implementado en **RADIAN** (`/dividir-linea`) y en **Bancos** (subdivisión de la **contrapartida**). En Bancos el movimiento bancario permanece por un solo valor (una línea) y la contrapartida puede repartirse en varias cuentas/terceros por importes distintos que sumen el valor del movimiento: botón ✂ por fila en `banco_resultado.html` (modal con cuenta/NIT/concepto/monto + validación de suma en vivo); las partes viajan como arreglos `sub_<idx>_*` en el form, las recoge `_recolectar_contrapartidas()` (valida la suma) en `banco_exportar` y el `mapeador_banco` genera 1 línea de banco + N de contrapartida en el mismo asiento (`_normalizar_contrapartidas`). Tests en `tests/test_subdividir_banco.py`.
- **El modelo durable de importaciones** (Fase 2, parte 2) se resolvió con un **snapshot por importación** (`importaciones.preasientos_json`) en vez de tablas normalizadas de líneas/versiones — pragmático y de bajo riesgo. Si en el futuro se requieren versiones/diffs finos o consultas por línea, habría que normalizar; por ahora el snapshot cubre «retomar conservando correcciones» y la trazabilidad por estado.
- La auth Entra exige que el equipo administrativo tenga identidades en el **tenant oficial** (asumido por la decisión tomada). El código ya soporta `AUTH_MODE=entra`; falta cablear App Service Authentication en el entorno oficial (Fase 4).
- **Aislamiento de blobs (Fase 3):** se aislaron los **uploads** por empresa (`empresas/{id}/uploads`). Los `output` (Excel generados) y `web_sessions` (JSON de resultados en sesión) siguen en categorías planas; en modo Azure Blob convendría aislarlos también (follow-up de bajo riesgo: la referencia vive server-side en la sesión del usuario).
- **Roles/permisos seed (Fase 3):** taxonomía elegida `admin/contador/auxiliar/consulta` con permisos por módulo (`app/authz.py`). Es una decisión reversible vía seed; ajustar si el equipo administrativo necesita otra separación de funciones (p. ej. que el auxiliar también exporte a SIIGO).
- `listar_correcciones_tercero()` existe pero **no tiene UI**; opcional: una vista de trazabilidad de correcciones de tercero.
