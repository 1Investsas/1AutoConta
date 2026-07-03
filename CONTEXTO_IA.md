# CONTEXTO_IA.md — Mapa completo del repositorio `contable-auto`

> **Propósito de este archivo.** Documento único y autocontenido para que una IA (o una
> persona nueva) entienda **cómo funciona todo el sistema sin tener que leer el repositorio
> completo**. Resume arquitectura, módulos, reglas de negocio, flujo de datos, esquema de BD,
> despliegue y convenciones. Si modificas el comportamiento del sistema, **actualiza también
> este archivo** para que siga siendo fiel.

---

## 1. Qué es y para qué sirve

`contable-auto` es un **sistema de automatización contable** para la empresa colombiana
**1 INVEST SAS** (NIT 901.331.657-7), extensible a múltiples empresas.

Toma el reporte de **facturación electrónica RADIAN** descargado del portal de la **DIAN**
(`https://catalogo-vpfe.dian.gov.co/`) y produce **preasientos contables** (asientos de
débito/crédito) listos para importar al software contable del cliente (principalmente
**SIIGO Nube**). También procesa **extractos bancarios CSV** y los convierte a comprobantes.

El sistema funciona de dos formas:
- **CLI** (`main.py`) — flujo batch por línea de comandos.
- **Aplicación web Flask** (`app/web/`) — interfaz de usuario con dashboard, carga de
  archivos, edición de cuentas, multi-empresa, analítica y exportación.

### Fases del producto (roadmap)

| Fase | Descripción | Estado |
|------|-------------|--------|
| **1** | Pipeline CLI: importar → clasificar → cruzar terceros → impuestos → preasiento → validar → exportar Excel | ✅ Implementada |
| **2** | Motor de sugerencias de cuentas (aprende del historial) + interfaz web | ✅ Implementada |
| **3** | Exportación/Importación a **SIIGO** (Excel oficial + API REST) | ✅ Implementada (Excel listo; API requiere plan premium) |
| **4** | Dashboard de reportería y analítica contable | ✅ Implementada (`/analytics`) |

> Nota: el `README.md` describe el roadmap con fases 2–4 como pendientes; el código real ya
> las implementa. **Este archivo refleja el estado real del código.**

---

## 2. Stack tecnológico

- **Lenguaje:** Python ≥ 3.11
- **CLI:** `click` + `rich` (consola con tablas/paneles).
- **Datos:** `pandas` (lectura/normalización de Excel), `openpyxl` (escritura `.xlsx`),
  `xlrd` (lectura de `.xls` antiguos).
- **Web:** `Flask` 3 + `flask-wtf` (CSRF). Plantillas Jinja2.
- **Servidores WSGI de producción:** `waitress` (Windows/local) y `gunicorn` (Azure/Linux).
- **Base de datos:** `sqlite3` (local, por defecto) o **Azure SQL** vía `pyodbc` (opcional).
- **Almacenamiento:** sistema de archivos local o **Azure Blob Storage** (`azure-storage-blob`).
- **Config:** `python-dotenv` (`.env`).
- **Tests:** `pytest`.

Dependencias completas en `requirements.txt`. El paquete se instala con `pip install -e .`
(ver `setup.py`, que expone el comando de consola `contable-auto=main:cli`).

---

## 3. Estructura del repositorio

```
contable-auto/
├── main.py                  # CLI principal (click): comandos `procesar` e `historial`
├── application.py           # Entry point WSGI para Azure (app = create_app())
├── web_server.py            # Servidor de DESARROLLO (Werkzeug, debug opcional)
├── serve_prod.py            # Servidor de PRODUCCIÓN local (Waitress)
├── startup.sh               # Arranque en Azure App Service (gunicorn + ODBC opcional)
├── azure-setup.sh           # Script de aprovisionamiento de recursos Azure
├── setup.py                 # Empaquetado/instalación
├── requirements.txt         # Dependencias
├── .env.example             # Plantilla de variables de entorno
├── README.md                # Documentación de usuario
├── docs/arquitectura.md     # Resumen breve de arquitectura
├── CONTEXTO_IA.md           # ESTE archivo
│
├── app/                     # ── Núcleo del sistema ──
│   ├── config.py            # ⭐ Constantes, mapeos, cuentas, reglas (fuente de verdad)
│   ├── models.py            # Dataclasses de dominio
│   ├── importador.py        # Lectura/normalización de RADIAN y maestros
│   ├── clasificador.py      # Reglas deterministas de tipo de documento
│   ├── terceros.py          # Identificación y cruce de terceros
│   ├── rut.py               # Lectura del RUT de la DIAN en PDF (jurídica/natural)
│   ├── terceros_rut.py      # Upsert del maestro de terceros a partir del RUT
│   ├── certificado_bancario.py # Lectura del certificado bancario (Bancolombia, PJ/PN)
│   ├── impuestos.py         # Separación de impuestos + base gravable
│   ├── comprobantes.py      # Asignación de código de comprobante
│   ├── preasiento.py        # ⭐ Generación de líneas contables (asientos)
│   ├── validaciones.py      # Cuadre, unicidad CUFE, coherencia
│   ├── exportador.py        # Excel de salida con 4 pestañas
│   ├── bitacora.py          # Registro de acciones (memoria + BD)
│   ├── database.py          # ⭐ BD: SQLite/Azure SQL, esquema, CRUD, analítica
│   ├── storage.py           # Abstracción de archivos (local vs Azure Blob)
│   ├── sugerencias.py       # Motor de sugerencias (Fase 2, aprende del historial)
│   ├── aprendizaje.py       # ⭐ Motor de aprendizaje generalizado (ML: exacto + Naive Bayes)
│   ├── aprendizaje_importador.py # Entrenamiento con archivos externos (SIIGO u otras fuentes)
│   ├── empresas.py          # Gestión multi-empresa
│   ├── web/                 # ── Aplicación Flask (Fase 2/4) ──
│   │   ├── __init__.py      # Application factory create_app()
│   │   ├── routes.py        # ⭐ Todos los endpoints HTTP (~1300 líneas)
│   │   ├── session_store.py # Estado server-side (cookies son muy pequeñas)
│   │   ├── templates/       # Plantillas Jinja2 (base, index, resultado, banco, etc.)
│   │   └── static/          # CSS, logo, favicon
│   ├── banco/               # ── Procesamiento de extracto bancario CSV (→ SIIGO) ──
│   │   ├── importador_banco.py  # Parser CSV + lógica 4x1000 + consolidación intereses
│   │   ├── mapeador_banco.py    # Movimientos → filas SIIGO
│   │   └── exportador_banco.py  # Escritura Excel SIIGO (chunks de 500)
│   └── siigo/               # ── Integración SIIGO (Fase 3) ──
│       ├── api_client.py        # Cliente REST (OAuth, /v1/journals)
│       ├── mapeador.py          # Preasientos → 27 columnas formato SIIGO
│       └── exportador_siigo.py  # Excel formato oficial SIIGO
│
├── data/                    # Archivos maestros (terceros, cuentas, comprobantes); empresas.json legado
├── input/                   # Archivos RADIAN descargados (.xlsx)
├── output/                  # Excel generados
├── db/                      # BD SQLite por-empresa (contable_<id>.db) + sistema.db (registro empresas) + .flask_secret_key
├── scripts/                 # Utilidades (instalar servicio Windows, optimizar logo)
├── tests/                   # Tests pytest (conftest con fixtures realistas)
└── .github/workflows/       # CI/CD: tests + deploy a Azure
```

⭐ = archivos clave si vas a entender o modificar reglas de negocio.

---

## 4. Conceptos de dominio (glosario)

- **RADIAN**: registro de la DIAN de la facturación electrónica. El reporte `.xlsx`
  descargado es la **entrada principal**. Columnas esperadas en `config.COLUMNAS_RADIAN`.
- **CUFE/CUDE**: identificador único de cada documento electrónico. Se usa como clave para
  **detectar duplicados** (un CUFE ya procesado no se reprocesa salvo `--incluir-duplicados`).
- **Clasificación**: categoría contable derivada del tipo de documento + quién es el emisor.
  Valores en `config.CLASIFICACIONES` (FACTURA_VENTA, FACTURA_COMPRA, DOCUMENTO_SOPORTE,
  NOMINA, NOTA_CREDITO_*, NOTA_DEBITO_*, SIN_CLASIFICAR).
- **Tercero**: la contraparte del documento (cliente o proveedor). Se identifica según la
  clasificación (emisor o receptor) y se cruza con el **maestro de terceros**.
- **Comprobante**: tipo de asiento en el software contable (cada clasificación mapea a un
  código). Mapeo en `config.MAPEO_COMPROBANTES` (interno) y `config.SIIGO_CODIGOS_COMPROBANTE`.
- **Preasiento**: el asiento contable generado (objeto `PreasientoContable` con sus
  `LineaContable`). "Pre" porque puede tener cuentas `[PENDIENTE]` que el contador completa.
- **Base gravable**: `Total − suma de impuestos`.
- **Cuenta `[PENDIENTE]`**: línea cuya cuenta de gasto/costo/ingreso debe decidir el usuario.
  Aparece **en rojo** en el Excel. El motor de sugerencias intenta rellenarlas.
- **Archivos maestros** (en `data/`):
  - `Listado_de_Terceros.xlsx` — sigue el **Modelo de importación de terceros de Siigo
    Nube**: 29 columnas, encabezados en la **fila 1**, datos desde la fila 2 y **todas
    las celdas en formato de texto (`@`)**. La estructura está centralizada en
    `app/terceros_schema.py`. El lector detecta la fila de encabezados automáticamente
    (también lee la planilla antigua, encabezados en la fila 7, por compatibilidad).
  - `Listado_de_Cuentas_Contables.xlsx` (encabezados en la fila 7; se filtran solo
    cuentas *Transaccional* + *Activo=Sí*)
  - `Tipos_de_comprobante_contable.xlsx` (encabezados en la fila 7)

---

## 5. Flujo de datos / pipeline (Fase 1, el corazón del sistema)

Tanto el CLI (`main.py procesar`) como la web (`POST /procesar` → `_ejecutar_pipeline`)
ejecutan estos **8 pasos**:

```
RADIAN.xlsx
   │  importador.importar_radian()      → DataFrame normalizado (+ columna _duplicado)
   ▼
1. Importar       (normaliza NITs, fechas, impuestos→float; detecta duplicados por CUFE)
2. Clasificar     clasificador.clasificar_lote()        → col 'clasificacion'
3. Cruzar terceros terceros.procesar_terceros_lote()     → tercero_nit/nombre/encontrado
4. Comprobantes   comprobantes.asignar_comprobantes_lote() → codigo/titulo_comprobante
5. Impuestos      impuestos.procesar_impuestos_lote()    → cols '_impuestos', '_base_gravable'
6. Preasiento     preasiento.generar_lote()              → list[PreasientoContable]
                  └─ (Fase 2) enriquecer_con_sugerencias() rellena [PENDIENTE] desde historial
7. Validar        validaciones.validar_preasiento_completo() → lista de excepciones por doc
                  └─ registrar_documento() en BD; registrar_lote_confirmaciones() alimenta historial
8. Exportar       exportador.exportar_excel()            → .xlsx con 4 pestañas
```

El DataFrame de pandas es el "vehículo" entre pasos: cada paso **agrega columnas** sin perder
las anteriores. Las columnas internas usan prefijo `_` (p. ej. `_impuestos`, `_base_gravable`,
`_duplicado`).

---

## 6. Reglas de negocio (lo más importante de entender)

### 6.1 Clasificación (`clasificador.py`)

Determinista, por orden de precedencia, sobre `Tipo de documento` (en minúsculas) y comparando
`NIT Emisor` contra el NIT de la empresa:

1. contiene `"nomina individual"` → **NOMINA**
2. contiene `"documento soporte"` → **DOCUMENTO_SOPORTE**
3. contiene `"factura electrónica"` → **FACTURA_VENTA** si emisor = empresa, si no **FACTURA_COMPRA**
4. contiene `"nota crédito"` → **NOTA_CREDITO_VENTA** / **NOTA_CREDITO_COMPRA** (según emisor)
5. contiene `"nota débito"` → **NOTA_DEBITO_VENTA** / **NOTA_DEBITO_COMPRA** (según emisor)
6. cualquier otro → **SIN_CLASIFICAR**

### 6.2 Identificación del tercero (`terceros.py`)

- Tercero = **RECEPTOR** para: FACTURA_VENTA, DOCUMENTO_SOPORTE, NOMINA, NOTA_CREDITO_VENTA, NOTA_DEBITO_VENTA.
- Tercero = **EMISOR** para: FACTURA_COMPRA, NOTA_CREDITO_COMPRA, NOTA_DEBITO_COMPRA.
- El cruce con el maestro es por **NIT exacto normalizado** (solo dígitos). Si no aparece,
  `tercero_encontrado=False` (se reporta como excepción, pero el preasiento se genera igual).

### 6.3 Impuestos (`impuestos.py`)

- Columnas de impuesto en `config.COLUMNAS_IMPUESTOS`. Por cada una con valor > 0 se crea una línea.
- **Retenciones** (`config.IMPUESTOS_RETENCION` = Rete IVA, Rete Renta, Rete ICA) cambian el
  sentido del asiento.
- Cuenta sugerida por impuesto y por sentido (compra/venta) en `config.CUENTAS_IMPUESTOS`.
- `base_gravable = Total − Σ impuestos` (mínimo 0; si sale negativa, se loguea advertencia).

### 6.4 Estructura de los asientos (`preasiento.py`)

Convención: línea 1 = contrapartida principal; línea 2 = base gravable (suele quedar `[PENDIENTE]`);
luego una línea por impuesto. El cuadre se verifica con tolerancia `< 0.01`.

| Clasificación | Línea contrapartida (cuenta default) | Base gravable | Impuestos no-retención | Retenciones |
|---|---|---|---|---|
| **FACTURA_COMPRA** / NC_COMPRA / ND_COMPRA | `22050501` Proveedores → **CRÉDITO** Total | `[PENDIENTE]` Gasto/Costo → **DÉBITO** | **DÉBITO** | **CRÉDITO** |
| **FACTURA_VENTA** / NC_VENTA / ND_VENTA | `13050501` CxC Clientes → **DÉBITO** Total | `[PENDIENTE]` Ingreso → **CRÉDITO** | **CRÉDITO** | **DÉBITO** (a favor) |
| **DOCUMENTO_SOPORTE** | `22100501` Prov. exterior/no obligados → **CRÉDITO** Total | `[PENDIENTE]` Gasto → **DÉBITO** | DÉBITO | CRÉDITO |
| **NOMINA** (refleja el PAGO) | `25050501` Salarios por pagar → **DÉBITO** Total | — | — | `[PENDIENTE]` Cuenta disponible (banco/caja) → **CRÉDITO** Total |
| **SIN_CLASIFICAR** | 2 líneas `[PENDIENTE]` que cuadran (revisar manual) | | | |

Las cuentas de contrapartida default están en `config.CUENTAS_CONTRAPARTE` y pueden
**sobreescribirse por empresa** (`Empresa.cuentas_contraparte`).

### 6.5 Validaciones (`validaciones.py`)

`validar_preasiento_completo()` devuelve una lista de errores (vacía = OK):
- No cuadra (débitos ≠ créditos).
- Tercero no encontrado en el maestro.
- Hay líneas con cuenta `[PENDIENTE]`.

Otras validaciones disponibles: `validar_cufe_unico`, `validar_tercero_activo`,
`validar_cuenta_transaccional`, `validar_coherencia_emisor`.

### 6.6 Motor de sugerencias (`sugerencias.py`, Fase 2)

Aprende qué cuenta se usa para cada combinación **(clasificación, NIT tercero, tipo_linea)**
y la guarda en la tabla `historial_cuentas` (UPSERT, incrementa `usos`).
- `enriquecer_con_sugerencias()`: rellena líneas `[PENDIENTE]` con la cuenta más usada
  (marca `es_sugerida=True`). Se ejecuta dentro de `generar_lote()` si se pasa `db_path`.
- `registrar_lote_confirmaciones()`: tras procesar, registra las cuentas **reales** (no
  pendientes ni sugeridas) para alimentar el aprendizaje.
- `tipo_linea` se deriva del concepto de la línea vía `_CONCEPTO_A_TIPO` (p. ej. "Base
  gravable" → `base`, "IVA" → `iva`, "Rete Renta" → `rete_renta`).
- Confirmación manual desde la web: `POST /confirmar`.
- Si el historial exacto no conoce la tripleta (tercero nuevo), cae al **motor de
  aprendizaje generalizado** (§6.7) que predice por texto (clasificación + nombre del
  tercero) e incluye el conocimiento importado de fuentes externas.

### 6.7 Motor de aprendizaje generalizado (`aprendizaje.py`, machine learning)

Aprende de CUALQUIER texto digitado en el sistema y **prediligencia** campos en los
módulos. Dos memorias por empresa, ambas con contador de usos:

1. **Patrones exactos** (`patrones_aprendidos`): texto normalizado completo → valor.
   Normalización: mayúsculas sin tildes, sin signos, sin grupos de solo dígitos
   (números de factura/fechas varían por documento).
2. **Clasificador de texto** (`tokens_aprendidos`): Naive Bayes multinomial sobre
   tokens (sin stopwords). Generaliza a textos nunca vistos; la confianza es el
   posterior × cobertura de tokens y solo se sugiere si supera `UMBRAL_CONFIANZA`
   (0.40). Sin dependencias nuevas (puro Python).

El conocimiento se guarda por **módulo** ('banco', 'caja', 'radian') y **campo**
('cuenta', 'nit_tercero', 'cuenta_<tipo_linea>' en RADIAN). El módulo especial
**`general`** recibe lo importado de archivos externos y es *fallback* de todos.
Orden de `predecir()`: exacto(módulo) → exacto(general) → texto(módulo) → texto(general).

**Dónde aprende** (todo best-effort, nunca rompe el flujo):
- Bancos: al exportar a SIIGO (descripción → cuenta/NIT de cada asignación no subdividida).
- Caja general y Flujos mixtos: al guardar la hoja o importar la plantilla
  (concepto → contrapartida/NIT; comparten el módulo 'caja').
- RADIAN: en `registrar_lote_confirmaciones` y `POST /confirmar`
  (clasificación + nombre tercero → cuenta por tipo de línea).
- Entrenamiento con **archivos externos** (`aprendizaje_importador.py`): Excel/CSV del
  programa de contabilidad (p. ej. movimiento contable SIIGO) o cualquier fuente.
  Detecta la fila de encabezados y las columnas (Descripción/Detalle/Concepto/Nombre,
  Cuenta contable, NIT) automáticamente; cada fila alimenta el motor por lotes.

**Dónde prediligencia**:
- Bancos (`/banco/previsualizar` y Retomar): cuenta contrapartida y NIT vacíos se
  rellenan y marcan en morado (clase CSS `.ml-pred`, tooltip con % de confianza).
- Caja/Mixto: al digitar el concepto, la hoja llama a `GET /api/aprendizaje/sugerir`
  y rellena contrapartida/NIT vacíos (marcados `.ml-pred`; se desmarcan al editar).
- RADIAN: fallback de `enriquecer_con_sugerencias` (§6.6).

**UI**: página `/aprendizaje` (menú Empresas → Machine learning): estadísticas,
patrones aprendidos (filtro/búsqueda/eliminación) y formulario de entrenamiento con
archivos externos (histórico en `importaciones_conocimiento`). Permisos: `ml.ver`
(consultar/API) y `ml.entrenar` (entrenar/eliminar patrones; roles admin y contador).

---

## 7. Referencia de módulos del núcleo (`app/`)

### `config.py` — fuente de verdad de la configuración
Todas las constantes y reglas viven aquí. Lee variables de `.env`. Lo más relevante:
- Identidad empresa: `NIT_EMPRESA`, `NOMBRE_EMPRESA`, `SIGLA_EMPRESA`.
- Rutas: `DATA_DIR`, `INPUT_DIR`, `OUTPUT_DIR`, `DB_DIR`, `DB_PATH`.
- **Detección automática de Azure App Service** (`_en_azure_app_service`): si corre en Azure,
  la BD por defecto va a `/home/data/db` (persistente) y el journal SQLite pasa a `DELETE`
  (WAL no funciona sobre el `/home` SMB de Azure).
- Backends: `USE_SQLITE` (default `true`), `DATABASE_URL` (Azure SQL),
  `AZURE_STORAGE_CONNECTION_STRING` / `AZURE_STORAGE_CONTAINER`.
- Mapeos de negocio: `CLASIFICACIONES`, `MAPEO_COMPROBANTES`, `COLUMNAS_IMPUESTOS`,
  `CUENTAS_IMPUESTOS`, `CUENTAS_CONTRAPARTE`, `IMPUESTOS_RETENCION`, `COLUMNAS_RADIAN`.
- Maestros de cuentas/comprobantes: encabezados en la fila 7 (`FILA_ENCABEZADOS_MAESTROS = 6`).
  El maestro de **terceros** usa el modelo de Siigo Nube (encabezados en la fila 1); su
  estructura vive en `app/terceros_schema.py`.
- SIIGO: `SIIGO_USERNAME/ACCESS_KEY/API_URL`, `SIIGO_MAX_FILAS_POR_ARCHIVO=500`,
  `SIIGO_CODIGOS_COMPROBANTE`.
- Banco: `BANCO_CUENTA_DEFAULT`, `BANCO_CUENTA_4X1000`, `BANCO_CODIGO_4X1000`,
  `BANCO_CODIGOS_BANCARIOS`, `BANCO_DESC_BANCARIOS`, `BANCO_DESC_INTERESES_AHORROS`,
  `SIIGO_COMP_BANCO_INGRESO/EGRESO/TRASLADO`.

### `models.py` — dataclasses de dominio
`DocumentoImportado`, `Tercero`, `CuentaContable`, `TipoComprobante`, **`LineaContable`**
(con `es_pendiente`, `es_sugerida`), **`PreasientoContable`** (agrupa líneas, `cuadra`,
`excepciones`), `RegistroBitacora`, `HistorialCuenta`.

### `importador.py`
- `importar_radian(filepath, db_path)`: lee `.xlsx`/`.xls`, valida columnas mínimas
  (`Tipo de documento`, `CUFE/CUDE`, `NIT Emisor`, `NIT Receptor`, `Total`), normaliza NITs
  (`_limpiar_nit` → solo dígitos), impuestos a float, fechas (`dayfirst=True`), y marca
  duplicados (`_duplicado`) consultando la BD.
- `cargar_maestro_terceros/cuentas/comprobantes`: leen con `header=6`. Cuentas se filtran a
  Transaccional + Activo=Sí.

### `clasificador.py`, `terceros.py`, `impuestos.py`, `comprobantes.py`
Cada uno expone una función `*_documento`/`identificar`/`separar`/`asignar` y su versión
`*_lote(df, ...)` que añade columnas al DataFrame. Ver §6.

### `preasiento.py`
`generar_preasiento(...)` y `generar_lote(df, df_comprobantes, db_path, cuentas_contraparte)`.
Diccionario `_GENERADORES` mapea clasificación → función generadora de líneas. Ver §6.4.

### `validaciones.py`, `exportador.py`, `bitacora.py`
- `exportador.exportar_excel(...)` genera `output/preasientos_<timestamp>.xlsx` con 4 pestañas:
  **Resumen**, **Preasientos** (filas `[PENDIENTE]` en rojo, sin-tercero en amarillo),
  **Excepciones**, **Bitácora**. En modo cloud lo sube a Blob y devuelve la referencia.
- `bitacora.registrar(...)` acumula en memoria (para el Excel) y persiste en la tabla `bitacora`.
  `limpiar_sesion()` se llama al inicio de cada proceso.

### `database.py` — capa de datos (doble backend)
- Abstracción `DbConnection` que funciona igual con `sqlite3` y `pyodbc` (Azure SQL).
- `get_connection(db_path)`, `inicializar_db(db_path)` (crea tablas si no existen).
- CRUD: `cufe_existe`, `registrar_documento`, `registrar_bitacora_db`,
  `obtener_historial_cuenta`, `actualizar_historial_cuenta` (UPSERT).
- Importaciones: `registrar_importacion`, `actualizar_importacion`, `obtener_importacion`,
  `listar_importaciones`.
- **Analítica (Fase 4)**: `obtener_kpis`, `obtener_evolucion_mensual`,
  `obtener_distribucion_clasificacion`, `obtener_top_terceros`, `obtener_actividad_reciente`.
  Usan helpers compatibles entre dialectos (`_month_expr`, `_substr_expr`) y aplican `LIMIT`
  en Python para evitar diferencias `TOP` vs `LIMIT`.
- **Lecturas para la web**: `obtener_resumen_dashboard` (dashboard `/`) y
  `listar_historial_cuentas` (`/historial`). Viven aquí —no como SQL crudo en `routes.py`—
  para ser compatibles con ambos backends (sin `LIMIT`/`SUBSTR` específicos de SQLite) y
  quedar aisladas por empresa en Azure SQL.
- **Persistencia de la BD SQLite en Blob** (fix "empieza desde cero"): si hay Blob
  configurado (modo cloud) y se usa SQLite, el archivo `.db` se **respalda en Blob** (categoría
  `db/`) y se **restaura** al abrir la primera conexión, por si el disco local es efímero
  (App Service for Containers / Container Apps sin almacenamiento persistente). Tras cada
  `commit` se reprograma una única subida con *debounce* (coalesce de muchas escrituras) y se
  hace `wal_checkpoint(TRUNCATE)` antes de subir para que el respaldo de un solo archivo sea
  consistente. `atexit` sube cualquier respaldo pendiente. No da concurrencia real entre
  workers (la última subida gana) → para eso, Azure SQL.
- **Aislamiento por empresa en Azure SQL** (`empresa_id`): con SQLite cada empresa tiene su
  propio archivo `.db` (aislamiento total, comportamiento intacto). Con Azure SQL las tablas
  son compartidas, así que cada tabla lleva una columna **`empresa_id`** y todas las
  consultas/escrituras la filtran. El id se deriva del nombre del archivo `.db` que cada
  empresa pasa como `db_path` (`contable.db`→`principal`, `contable_<id>.db`→`<id>`) vía
  `_empresa_id_desde_db_path`; los helpers `_cond_empresa` / `_and_empresa` / `_where_empresa`
  devuelven cláusula vacía en SQLite. Las restricciones únicas pasan a incluir `empresa_id`
  (`UNIQUE(empresa_id, cufe)`, `UNIQUE(empresa_id, clasificacion, nit_tercero, tipo_linea)`).
  **No se activa hoy** (`USE_SQLITE=true`); queda correcto para `USE_SQLITE=false` + `DATABASE_URL`.

#### Esquema de tablas
| Tabla | Para qué |
|---|---|
| `documentos_importados` | Registro de cada doc procesado (CUFE único → detección de duplicados) + base de la analítica |
| `bitacora` | Log persistente de acciones |
| `historial_cuentas` | Aprendizaje del motor de sugerencias. Único por (clasificación, nit_tercero, tipo_linea) |
| `importaciones` | Registro persistente de cada proceso RADIAN + **snapshot editable durable** (`preasientos_json`) con ciclo de estados (`procesando/procesada/corregida/exportada/error/anulada`). «Abrir» recupera el estado guardado con las correcciones; «Regenerar» reprocesa desde cero |
| `procesos_banco` | Histórico del módulo Bancos: cada extracto previsualizado/exportado (archivo, cuenta, NIT banco, nº movimientos, estado `procesando`/`completada`/`error`) |
| `cuentas_bancarias_tercero` | Cuentas bancarias de un tercero importadas del **certificado bancario** (Bancolombia, PJ y PN). Único por (`nit_tercero`, `numero_cuenta`); guarda banco, tipo de producto, fecha de apertura y estado. Lo alimenta el módulo Terceros |
| `patrones_aprendidos` | Motor de aprendizaje generalizado (§6.7): contexto exacto normalizado → valor, por módulo/campo. Único por (modulo, campo, contexto, valor) |
| `tokens_aprendidos` | Frecuencias token→valor del clasificador de texto (Naive Bayes) del motor de aprendizaje. Único por (modulo, campo, token, valor) |
| `importaciones_conocimiento` | Histórico de entrenamientos del ML con archivos externos (archivo, módulo destino, filas, observaciones, estado) |

CRUD de procesos de banco: `registrar_proceso_banco` (al previsualizar, estado
`procesando`), `actualizar_proceso_banco` (a `completada`/`error` al exportar) y
`listar_procesos_banco` (descendente por id, límite en Python).

CRUD de cuentas bancarias de terceros: `registrar_cuenta_bancaria_tercero`
(UPSERT por `nit_tercero`+`numero_cuenta`), `listar_cuentas_bancarias_tercero`
(todas o por tercero), `contar_cuentas_bancarias_tercero` y
`eliminar_cuenta_bancaria_tercero`. El PDF se lee con
`app/certificado_bancario.py::parsear_certificado_pdf`.

### `storage.py` — abstracción de archivos
API: `save_file`, `save_local_file`, `load_file`, `get_download_bytes`, `delete_file`,
`file_exists`, `get_local_data_path`, `is_cloud`.
- Local: rutas reales bajo la raíz del proyecto.
- Cloud (si `AZURE_STORAGE_CONNECTION_STRING`): blobs `blob://<categoria>/<archivo>`.
Categorías usadas: `uploads`, `output`, `data`, `web_sessions`.

### `empresas.py` — multi-empresa
- Dataclass `Empresa` con `id`, `nit`, `nombre`, `sigla` y overrides:
  `cuentas_contraparte`, `cuentas_impuestos`, `cuenta_banco_default`, `nit_banco`,
  `cuentas_banco`, `bancos`, `formato_banco`.
- **Bancos (varias cuentas / varios bancos):** la empresa configura **una sola vez** la o
  las cuentas contables de banco (`cuentas_banco` = `list[{cuenta, etiqueta}]`) y el o los
  bancos (`bancos` = `list[{nit, nombre}]`). `cuenta_banco_default`/`nit_banco` se conservan
  como valor único de compatibilidad (= primer elemento de cada lista). Métodos efectivos:
  `cuentas_banco_efectivas()` (siempre ≥ 1, cae al default global) y `bancos_efectivos()`
  (puede estar vacía). El módulo de Bancos solo muestra el selector de cuenta y/o de banco
  cuando hay **más de una** opción; con una sola, el valor se envía oculto.
- Propiedades efectivas combinan defaults de `config.py` con overrides de la empresa
  (`*_efectivas()` / `*_efectivo()`).
- Cada empresa tiene **su propia BD** (`db/contable_<id>.db`) y **su propia carpeta de maestros**
  (`data/<id>/`). La empresa **principal** usa `config.DB_PATH` y `data/`, y sale del `.env`
  (pero sus ediciones desde la UI se persisten en el registro y tienen prioridad).
- Registro persistente en la **tabla SQL `empresas`** (BD de sistema central:
  `config.SYSTEM_DB_PATH` = `db/sistema.db` en SQLite; tabla compartida en Azure SQL).
  La primera lectura **migra automáticamente** el `data/empresas.json` legado a la BD.
  API: `listar_empresas`, `obtener_empresa`, `crear_empresa`, `actualizar_empresa`,
  `guardar_empresa`, `eliminar_empresa` (la principal no se puede eliminar).
- **Aislamiento multi-empresa por backend:** en SQLite la separación es por **archivo** distinto
  (`db/contable_<id>.db`). En Azure SQL las tablas son compartidas y la separación es por la
  columna discriminadora **`empresa_id`** (derivada del `db_path`; ver `database.py`). Ambos
  caminos quedan correctos; Azure SQL no está activo hoy (`USE_SQLITE=true` por defecto).
- **Índices tenant-aware (Azure SQL):** las tablas con `UNIQUE(empresa_id, …)` ya tienen índice
  por `empresa_id`; además `inicializar_db` crea (idempotente, `_asegurar_indices_mssql`)
  `ix_importaciones_empresa`, `ix_procesos_banco_empresa` e `ix_documentos_empresa_clasif` para
  los listados/analítica por empresa. En SQLite no aplican (cada empresa es un archivo aparte).

---

## 8. Subsistema Web (`app/web/`)

### Application factory (`__init__.py`)
`create_app()` crea la app Flask, configura `MAX_CONTENT_LENGTH=50MB`, carpeta `uploads/`,
inicializa la BD una vez, registra el blueprint de rutas, **CSRF** (`flask-wtf`) y manejadores
de error (404, 413, CSRF, 500). La `FLASK_SECRET_KEY` se lee del entorno o se autogenera y se
persiste en `db/.flask_secret_key` (para que las sesiones sobrevivan reinicios en Azure).

### Estado server-side (`session_store.py`)
Las cookies de Flask (~4 KB) no alcanzan para los resultados. `guardar/cargar/eliminar`
serializan a JSON en `storage` (categoría `web_sessions`) y guardan solo una **referencia**
en `session`. Claves: `resultado_ref` (preasientos), `banco_ref` (movimientos), `empresa_id`.
Esta es la **copia de trabajo** (rápida, por sesión); la **copia durable** del resultado
RADIAN se guarda en paralelo en `importaciones.preasientos_json` (helper
`_persistir_importacion` en `routes.py`), para poder «Abrir» una importación más tarde
conservando las correcciones manuales sin reprocesar.

### Endpoints (`routes.py`)
Contexto global inyectado en todas las plantillas: empresa actual, empresas disponibles, NIT, sigla.

| Ruta | Método | Función |
|---|---|---|
| `/` | GET | Dashboard con KPIs |
| `/procesar` | POST | Sube RADIAN (+maestros opcionales), ejecuta el pipeline, registra importación |
| `/resultado` | GET | Muestra preasientos y excepciones del último proceso |
| `/descargar` | GET | Descarga el Excel generado |
| `/confirmar` | POST | Registra una cuenta confirmada en el historial (aprendizaje) |
| `/historial` | GET | Tabla de cuentas aprendidas (motor de sugerencias) |
| `/aprendizaje` | GET | Centro de Machine learning: estadísticas, patrones aprendidos y entrenamiento |
| `/aprendizaje/entrenar` | POST | Entrena el ML con un archivo externo (Excel/CSV de SIIGO u otra fuente) |
| `/aprendizaje/patron/<id>/eliminar` | POST | Elimina un patrón aprendido incorrecto |
| `/api/aprendizaje/sugerir` | GET | Predice campos para un texto (prediligenciamiento en vivo de Caja/Mixto) |
| `/importaciones` | GET | Lista de importaciones persistidas (con estado y acciones) |
| `/importaciones/<id>/abrir` | POST | Carga el snapshot durable en la sesión (retomar **conservando correcciones**), sin reprocesar |
| `/importaciones/<id>/reprocesar` | POST | Regenera: reprocesa el RADIAN original desde cero (pierde correcciones manuales) |
| `/importaciones/<id>/anular` | POST | Marca la importación como anulada (descartada) |
| `/importaciones/<id>/descargar` | GET | Descarga el Excel de una importación previa |
| `/exportar-siigo` | POST | Genera Excel(s) formato SIIGO (ZIP si son varios) |
| `/analytics` | GET | Reportería (Chart.js): evolución, distribución, top terceros |
| `/api/cuentas`, `/api/terceros` | GET | Autocompletar (con caché de maestros por mtime) |
| `/banco` | GET | Pantalla principal: carga de extracto CSV + guía rápida (stepper) + actividad reciente |
| `/banco/previsualizar` | POST | Parsea el CSV, registra el proceso (estado `procesando`) y muestra tabla editable |
| `/banco/exportar` | POST | Genera Excel(s) SIIGO del banco y marca el proceso `completada` (o `error`) |
| `/banco/historial` | GET | Histórico completo de procesos del módulo Bancos |
| `/empresas` (+ `/seleccionar`, `/crear`, `/<id>/editar`, `/<id>/actualizar`, `/<id>/eliminar`, `/maestros`) | GET/POST | Administración multi-empresa |
| `/test-procesar` | GET | Solo en DEBUG: procesa el primer RADIAN de `input/` |

### Plantillas (`templates/`)
`base.html` (layout: sidebar, topbar con selector de empresa, modal "Automatizar proceso",
flash, loading), `index.html`, `resultado.html`, `historial.html`, `analytics.html`,
`importaciones.html`, `empresas.html`, `banco_upload.html`, `banco_resultado.html`,
`banco_historial.html`, `banco_actividad_items.html` (parcial reutilizable de la lista de
actividad), `error.html`.

---

## 9. Subsistema Banco (`app/banco/`)

Convierte un **extracto bancario CSV** en comprobantes Excel formato SIIGO.

> **Pantalla principal e histórico (web).** La cuenta contable y el banco se eligen una sola
> vez en la empresa; en `/banco` solo se muestra un selector cuando hay varias opciones. El pie
> de la pantalla tiene una **guía rápida** (stepper de 4 pasos) y un panel de **actividad
> reciente** alimentado por la tabla `procesos_banco` (real, por empresa): cada previsualización
> crea un proceso `procesando` que pasa a `completada`/`error` al exportar. `/banco/historial`
> muestra el listado completo. Componentes CSS reutilizables: `.modulo-bottom`, `.guia-*`, `.act-*`.

- **Formato CSV** (configurable por empresa vía `formato_banco`; default = sin encabezados,
  separado por comas): col 0 cuenta, col 1 código banco, col 3 fecha (`yyyymmdd`), col 5 valor
  (**+ egreso, − ingreso**), col 6 código de detalle, col 7 descripción.
- `importador_banco.leer_csv_banco(path, formato)` → `list[MovimientoBanco]`. Además:
  - **Consolida intereses de ahorros** del mismo mes en un único movimiento (último día del mes).
  - **Enlaza el 4x1000** (código `3339`) con su egreso "padre" del mismo día buscando el que
    cuadre con el 0.4% (tolerancia ±$0.50).
  - Marca como "bancarios" (tercero = el banco) los movimientos cuyo código está en
    `BANCO_CODIGOS_BANCARIOS` o cuya descripción coincide con `BANCO_DESC_BANCARIOS`
    (4x1000, intereses, cuota de manejo).
  - `a_dict`/`desde_dict` serializan para la sesión Flask.
- `mapeador_banco.mapear_banco_a_siigo(...)` → `list[FilaSiigo]`. Decide tipo de comprobante
  por sentido (ingreso `111` / egreso `112` / traslado `110`), arma 2 líneas (banco vs
  contrapartida) y, si hay 4x1000 enlazado, añade 2 líneas más en el **mismo consecutivo** del
  padre (banco → CRÉDITO; cuenta `53152001` gasto 4x1000 → DÉBITO).
- `exportador_banco.exportar_banco_siigo(...)` parte en chunks de 500 filas y escribe Excel(s).

---

## 10. Subsistema SIIGO (`app/siigo/`)

Exporta los preasientos al sistema contable **SIIGO Nube**. Dos vías:

### Exportación Excel (siempre disponible) — `exportador_siigo.exportar_siigo(...)`
- Genera `output/siigo_comprobantes_<timestamp>[_parteN].xlsx`, hoja "Datos".
- Formato oficial de **27 columnas** (encabezados rojos=obligatorios / azules=opcionales,
  anchos del template SIIGO). Filas `[PENDIENTE]` en rojo, sin-NIT en amarillo.
- Máximo **500 filas por archivo** (`SIIGO_MAX_FILAS_POR_ARCHIVO`); si hay más, varios archivos.
- `mapeador.mapear_lote(preasientos, incluir_pendientes, df_cuentas)`:
  - Asigna **consecutivo** con formato `yyyymmNN` (correlativo **por año-mes Y por tipo de
    comprobante**), ordenando antes por fecha de emisión.
  - Cuentas con "Maneja vencimientos" rellenan columnas de vencimiento (13–16).

### API REST (requiere plan premium) — `api_client.SiigoClient`
- `autenticar()` (POST `/auth`, token válido 24 h, auto-renovación), `crear_comprobante()`,
  `crear_lote(preasientos, omitir_pendientes)` (POST `/v1/journals`), `listar_comprobantes()`.
- Credenciales `SIIGO_USERNAME` / `SIIGO_ACCESS_KEY` en `.env`. Excepciones
  `SiigoAuthError`, `SiigoAPIError`. No envía comprobantes con cuentas `[PENDIENTE]`.

---

## 11. Puntos de entrada

| Archivo | Entorno | Detalles |
|---|---|---|
| `main.py` | CLI | `procesar` (pipeline completo) e `historial` (cuentas aprendidas). Opciones: `--radian/-r`, `--terceros/-t`, `--cuentas/-c`, `--comprobantes/-k`, `--output/-o`, `--db`, `--incluir-duplicados`, `--log-nivel`. |
| `web_server.py` | Desarrollo | Werkzeug. `--host` (def. 127.0.0.1), `--port` 5000, `--debug`. No usar en prod. |
| `serve_prod.py` | Producción local | Waitress (`--threads` 4). Exige `FLASK_SECRET_KEY` real (aborta si es de desarrollo). |
| `application.py` + `startup.sh` | Azure App Service | gunicorn (`application:app`, 2 workers, timeout 600s). `startup.sh` instala ODBC 18 si `USE_SQLITE=false`. |

### Comandos típicos
```bash
# CLI (usa maestros de data/ por defecto)
python main.py procesar --radian input/RADIAN.xlsx
python main.py historial --top 20

# Web local
python web_server.py --debug            # desarrollo
python serve_prod.py --port 5000        # producción local (Waitress)

# Tests
pytest tests/ -v
```

---

## 12. Configuración (variables de entorno)

Plantilla en `.env.example`. Las más importantes:

| Variable | Default | Para qué |
|---|---|---|
| `NIT_EMPRESA`, `NOMBRE_EMPRESA`, `SIGLA_EMPRESA` | 901331657 / 1INVEST SAS / 1INVEST | Empresa principal |
| `FLASK_SECRET_KEY` | (autogenerada) | Sesiones Flask. **Obligatoria en producción** |
| `HOST`, `PORT` | 0.0.0.0 / 5000 | Servidor |
| `DATA_DIR`, `INPUT_DIR`, `OUTPUT_DIR` | data/ input/ output/ | Rutas locales |
| `DB_DIR`, `DB_PATH`, `DB_JOURNAL_MODE` | db / db/contable.db / WAL | BD SQLite (en Azure: `/home/data/db` y `DELETE` automáticos) |
| `USE_SQLITE` | true | `false` → Azure SQL (requiere `DATABASE_URL`) |
| `DATABASE_URL` | (vacío) | Cadena ODBC de Azure SQL |
| `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER` | (vacío) / contable-auto | Azure Blob (vacío = disco local) |
| `SIIGO_USERNAME`, `SIIGO_ACCESS_KEY`, `SIIGO_API_URL` | (vacío) / api.siigo.com | API SIIGO |
| `LOG_LEVEL` | INFO | DEBUG/INFO/WARNING/ERROR |

(Existen además overrides finos: `SIIGO_COMP_*`, `BANCO_*` — ver `config.py`.)

---

## 13. Persistencia: local vs Azure

| Dato | Local (`USE_SQLITE=true`, sin Blob) | Azure |
|---|---|---|
| Documentos, historial, importaciones, bitácora | SQLite en `DB_DIR` | SQLite en `/home/data/db` (+ **respaldo en Blob** si hay Blob) **o** Azure SQL si `USE_SQLITE=false` |
| Registro de empresas | Tabla `empresas` en `db/sistema.db` (BD de sistema central) | Tabla compartida `empresas` en Azure SQL (o `sistema.db` en `/home/data/db` con SQLite) |
| Maestros, uploads, Excel, sesiones web (`empresas.json` legado) | Carpetas del proyecto | Azure Blob Storage si está configurado |

En Azure App Service el sistema de archivos del contenedor es **efímero** (solo `/home`
persiste y los despliegues reemplazan `/home/site/wwwroot`). Por eso la BD va a `/home/data/db`.
Además, si hay **Blob configurado**, la BD SQLite se respalda/restaura desde Blob (categoría
`db/`): así no se "empieza desde cero" aunque el disco local sea efímero (p. ej. App Service for
Containers o Container Apps sin `/home` persistente). Ver `database.py` §7.

---

## 14. Despliegue y CI/CD

- **GitHub Actions** (`.github/workflows/main_contable-auto.yml`): en push a `main` instala
  dependencias, **corre `pytest`**, y si pasa, despliega a **Azure Web App `contable-auto`**
  (slot Production) vía OIDC. Oryx instala `requirements.txt` en el servidor.
- `azure-setup.sh`: aprovisionamiento inicial de recursos en Azure.
- `scripts/`: utilidades Windows (`install_service.ps1`, `start.ps1`, `uninstall_service.ps1`)
  y `optimizar_logo.py` (genera logo del sidebar + favicon).

---

## 15. Tests (`tests/`)

`pytest` con fixtures realistas en `conftest.py` (`df_radian_basico` con los 6 tipos de
documento, `df_terceros`, `df_cuentas`, `df_comprobantes`). Cobertura por módulo:
`test_clasificador`, `test_terceros`, `test_impuestos`, `test_preasiento`, `test_validaciones`,
`test_sugerencias`, `test_empresas`, `test_storage`, `test_siigo_mapeador`, `test_procesos_banco`,
`test_certificado_bancario` (lector PJ/PN), `test_cuentas_bancarias_db` (CRUD cuentas de terceros),
`test_aprendizaje` (motor ML: normalización, predicción exacta/por texto, fallback general,
importador de conocimiento externo, integración RADIAN).

> Los tests corren en CI antes de cada despliegue: **mantenerlos verdes es requisito para deploy**.

---

## 16. Convenciones y "gotchas"

- **Idioma:** todo el código, comentarios y docstrings están en **español**. Mantener ese estilo.
- **NITs** siempre normalizados a solo dígitos (`_limpiar_nit`); el cruce de terceros es por
  coincidencia exacta.
- **Maestros**: el de **terceros** sigue el modelo de Siigo Nube (29 columnas, encabezados
  en la **fila 1**, celdas en formato de texto; ver `app/terceros_schema.py`). Los de
  cuentas/comprobantes mantienen los encabezados en la **fila 7** (`header=6`); el plan de
  cuentas se filtra a Transaccional + Activo=Sí.
- **`[PENDIENTE]`** es el marcador literal de cuenta sin asignar (constante en `preasiento.py`).
  Nunca se registra en el historial de sugerencias.
- **Cuadre** con tolerancia `< 0.01`.
- **Duplicados**: por CUFE contra `documentos_importados`; `--incluir-duplicados` los reprocesa.
- **Columnas internas** del DataFrame con prefijo `_` (`_impuestos`, `_base_gravable`, `_duplicado`).
- **Backends de BD**: cualquier SQL nuevo debe funcionar en SQLite **y** T-SQL (ver helpers en
  `database.py`; preferir `LIMIT` en Python).
- **Estado web** grande → siempre vía `session_store`, nunca en la cookie.
- **Detección de Azure** es automática por variables de entorno de App Service.
- **Despliegue solo desde `main`:** el workflow publica en Azure **únicamente al hacer push a
  `main`**. Pushear a una rama de trabajo (p. ej. `claude/...`) **no** despliega; hay que
  mergear el PR a `main`. Además, en producción Flask **cachea las plantillas Jinja al arrancar**
  y el código Python solo se recarga al reiniciar el proceso: tras un deploy puede hacer falta
  recarga forzada del navegador (`Ctrl+Shift+R`) para ver el CSS nuevo.
- **Estandarización de Automatizaciones (en curso):** se está unificando el diseño de todos los
  módulos del menú *Automatizaciones* (Nómina, RADIAN, Bancos, Caja general, Cruces, Impuestos…).
  **Bancos (`/banco`) es el módulo de referencia** ya pulido; el patrón a replicar en los demás:
  (1) la cuenta/banco/parámetros se configuran **una sola vez** en la empresa y en la pantalla del
  módulo solo se piden cuando hay **varias** opciones (selector); (2) pie con **guía rápida**
  (stepper de pasos) + **actividad reciente** real; (3) histórico persistido en una tabla propia
  del módulo. Reutilizar los componentes CSS `.modulo-bottom`, `.guia-*`, `.act-*` y el parcial
  `banco_actividad_items.html`.

---

## 17. ¿Dónde toco qué? (guía rápida de cambios)

| Quiero… | Editar |
|---|---|
| Cambiar reglas de clasificación | `app/clasificador.py` |
| Cambiar cuentas default (contrapartida/impuestos) | `app/config.py` (`CUENTAS_CONTRAPARTE`, `CUENTAS_IMPUESTOS`) o por empresa en la UI |
| Cambiar la estructura de un asiento | `app/preasiento.py` (`_generar_lineas_*`) |
| Añadir/ajustar un impuesto | `app/config.py` (`COLUMNAS_IMPUESTOS`, `CUENTAS_IMPUESTOS`, `IMPUESTOS_RETENCION`) |
| Cambiar el Excel de salida | `app/exportador.py` |
| Cambiar el formato SIIGO | `app/siigo/mapeador.py` y `app/siigo/exportador_siigo.py` |
| Cambiar el parseo del extracto bancario | `app/banco/` + `Empresa.formato_banco` |
| Configurar cuentas/bancos de una empresa (varias cuentas o bancos) | `app/empresas.py` (`Empresa.cuentas_banco`, `bancos`, métodos `*_efectivas()`) + `templates/empresas.html` + `_parse_empresa_form` en `routes.py` |
| Tocar la pantalla principal del módulo Bancos (selector, guía, actividad) | `templates/banco_upload.html` + `web.banco` en `routes.py` |
| Añadir/ver el histórico de un módulo de Automatizaciones | tabla propia en `app/database.py` + helpers `registrar_/actualizar_/listar_*` (ver `procesos_banco`) + parcial `banco_actividad_items.html` |
| Tocar el motor de aprendizaje / prediligenciamiento (ML) | `app/aprendizaje.py` (motor), `app/aprendizaje_importador.py` (entrenamiento externo), rutas `aprendizaje*` y `api_aprendizaje_sugerir` en `routes.py`, plantilla `aprendizaje.html` |
| Añadir un endpoint web | `app/web/routes.py` (+ plantilla en `templates/`) |
| Tocar el esquema de BD | `app/database.py` (`_create_tables_sqlite` y `_create_tables_mssql`) |
| Añadir validaciones | `app/validaciones.py` |
| Configurar despliegue | `.github/workflows/main_contable-auto.yml`, `startup.sh`, `azure-setup.sh` |
```
