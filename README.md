# contable-auto

Sistema de automatización contable para **1 INVEST SAS** (NIT 901.331.657-7).

Procesa reportes de facturación electrónica descargados desde el portal RADIAN de la DIAN y genera preasientos contables en Excel listos para importar al sistema de contabilidad.

---

## Requisitos

- Python 3.11 o superior
- pip

## Instalación

```bash
git clone <repo-url>
cd contable-auto
pip install -r requirements.txt
```

O en modo desarrollo:

```bash
pip install -e .
```

---

## Uso rápido

```bash
python main.py procesar --radian input/RADIAN.xlsx
```

Con archivos maestros explícitos:

```bash
python main.py procesar \
  --radian input/RADIAN.xlsx \
  --terceros data/Listado_de_Terceros.xlsx \
  --cuentas data/Listado_de_Cuentas_Contables.xlsx \
  --comprobantes data/Tipos_de_comprobante_contable.xlsx \
  --output output/
```

---

## Archivos de entrada

### Reporte RADIAN

Descarga el reporte desde https://catalogo-vpfe.dian.gov.co/ en formato `.xlsx` y colócalo en la carpeta `input/`.

Columnas requeridas: `Tipo de documento`, `CUFE/CUDE`, `NIT Emisor`, `NIT Receptor`, `Total`, más las columnas de impuestos.

> 💡 ¿Cansado de descargarlo a mano cada día? El módulo **RADIAN automático**
> puede hacerlo solo. Ver [Importación automática de RADIAN](#importación-automática-de-radian-dian).

### Archivos maestros

Coloca en la carpeta `data/`:

| Archivo | Descripción |
|---------|-------------|
| `Listado_de_Terceros.xlsx` | Modelo de importación de terceros de **Siigo Nube** (29 columnas, encabezados en fila 1, todas las celdas en formato de texto). |
| `Listado_de_Cuentas_Contables.xlsx` | Plan de cuentas. Encabezados en fila 7. |
| `Tipos_de_comprobante_contable.xlsx` | Catálogo de comprobantes. Encabezados en fila 7. |

---

## Módulo Terceros (web)

En **Configuraciones › Terceros** puedes actualizar automáticamente el
`Listado_de_Terceros.xlsx` importando el **RUT de la DIAN en PDF**:

- Soporta **persona jurídica** y **persona natural**.
- Lee NIT/DV, razón social o nombre completo, tipo de identificación, dirección,
  ciudad, departamento, correo y teléfono de la primera hoja del RUT.
- Hace *upsert* por NIT/cédula: agrega los terceros nuevos y actualiza los
  existentes, **conservando el formato de las casillas del modelo de Siigo**
  (cada celda escrita queda en formato de texto, de modo que las
  identificaciones y los códigos nunca pierden los ceros a la izquierda).
- Si el maestro aún no existe, lo crea con la estructura del modelo de Siigo
  Nube. El resultado queda listo para el cruce de terceros del módulo RADIAN.

Puedes subir varios RUT a la vez y descargar el maestro actualizado.

---

## Módulo Caja General (web)

En **Flujos directos › Caja general** (ruta `/caja`) se controlan los
movimientos de **efectivo** (billetes y monedas). A diferencia de Bancos, aquí
la aplicación **estructura el formato** que el usuario diligencia; no se importa
un extracto externo.

Flujo de trabajo:

1. **Crear una cuenta de caja** (caja menor, general, por sede o centro de costo),
   **asociada a una cuenta contable** del plan de cuentas (maestro), que se elige
   con autocompletado al momento de crearla.
2. **Abrir un período mensual** con su **saldo inicial** (se sugiere el saldo
   final del mes anterior).
3. **Registrar movimientos** de entrada/salida en la app o con la **plantilla de
   Excel**. El **saldo acumulado se calcula solo** (saldo inicial + entradas −
   salidas) en orden cronológico y nunca se digita.
4. **Guardar avances** parciales y continuar después.
5. Llevar el período por sus **estados**: Borrador → En revisión → Aprobado →
   Cerrado → Reabierto. Un mes cerrado no se modifica sin reapertura.

Cada movimiento lleva su **Tipo de comprobante** (Recibo de caja / Recibo de
pago / Traslado, como en Bancos) y su **Contrapartida** (la cuenta contable del
otro lado del asiento, con autocompletado). La caja se asienta siempre contra su
**cuenta contable** asociada.

Características:

- **Autocompletado NIT ↔ nombre** del tercero y **autocompletado de cuentas**
  (contrapartida) contra los maestros de la empresa.
- **Plantilla de Excel** descargable **vacía** o **prediligenciada**, con la
  cuenta contable en el encabezado, fórmula de saldo protegida, lista de tipo de
  comprobante, validación de fecha, formato monetario y una hoja auxiliar
  `Terceros` para autocompletar en Excel.
- **Importación** de la plantilla diligenciada: valida fila por fila, recalcula
  el saldo (no confía en el digitado en Excel) y no guarda nada si hay errores.
- **Generar SIIGO**: produce el Excel de importación de comprobantes para SIIGO
  Nube, igual que el módulo Bancos — un asiento de dos líneas por movimiento
  (cuenta de caja + contrapartida), con tipo de comprobante y consecutivo.
- **Permisos por rol** (`caja.ver`, `caja.gestionar`, `caja.procesar`,
  `caja.exportar`, `caja.aprobar`, `caja.cerrar`) y trazabilidad en auditoría.

Cada empresa tiene sus propias cuentas, períodos y movimientos de caja
(aislamiento por empresa, igual que el resto de módulos).

---

## Estructura de carpetas

```
contable-auto/
├── app/                  # Módulos del sistema
│   ├── config.py         # Constantes y configuración central
│   ├── importador.py     # Lectura de RADIAN y archivos maestros
│   ├── clasificador.py   # Clasificación determinista de documentos
│   ├── terceros.py       # Cruce con maestro de terceros
│   ├── rut.py            # Lectura del RUT de la DIAN (PDF) → datos del tercero
│   ├── terceros_rut.py   # Actualiza el maestro de terceros con el RUT importado
│   ├── impuestos.py      # Separación de impuestos y base gravable
│   ├── comprobantes.py   # Asignación de tipo de comprobante
│   ├── preasiento.py     # Generación de líneas contables
│   ├── validaciones.py   # Cuadre, unicidad CUFE, coherencia
│   ├── exportador.py     # Exportación a Excel formateado
│   └── bitacora.py       # Registro de acciones
├── data/                 # Archivos maestros (terceros, cuentas, comprobantes)
├── input/                # Archivos RADIAN descargados
├── output/               # Excel de salida generados
├── db/                   # Base de datos SQLite
├── tests/                # Tests unitarios
└── main.py               # CLI principal
```

---

## Salida generada

El sistema genera un archivo Excel en `output/` con cuatro pestañas:

| Pestaña | Contenido |
|---------|-----------|
| **Resumen** | Estadísticas del proceso: total por tipo, fecha, archivo origen |
| **Preasientos** | Todas las líneas contables (débitos y créditos) |
| **Excepciones** | Documentos con errores: tercero no encontrado, sin clasificar, no cuadra |
| **Bitácora** | Log cronológico de todas las acciones del proceso |

Las filas con **cuenta [PENDIENTE]** aparecen en rojo — son líneas donde el usuario debe asignar manualmente la cuenta de gasto/costo/ingreso.

---

## Reglas de clasificación

| Tipo de documento | Condición | Clasificación |
|---|---|---|
| Factura electrónica | Emisor = empresa | FACTURA_VENTA |
| Factura electrónica | Emisor = tercero | FACTURA_COMPRA |
| Documento soporte con no obligados | — | DOCUMENTO_SOPORTE |
| Nomina Individual | — | NOMINA |
| Nota crédito | Emisor = empresa | NOTA_CREDITO_VENTA |
| Nota crédito | Emisor = tercero | NOTA_CREDITO_COMPRA |

---

## Ejecutar los tests

```bash
pytest tests/ -v
```

---

## Roadmap

| Fase | Descripción |
|------|-------------|
| **1 ✅** | CLI completo: importar, clasificar, cruzar terceros, generar preasientos, exportar Excel |
| **2** | Motor de sugerencias de cuentas basado en historial + interfaz web |
| **3** | Importación directa al sistema contable vía API · **Descarga automática diaria de RADIAN desde la DIAN ✅** |
| **4** | Dashboard de reportería y analytics contable |

---

## Importación automática de RADIAN (DIAN)

El módulo **RADIAN automático** (menú lateral › *Flujos indirectos › RADIAN
automático*, o ruta `/radian/auto`) descarga e importa el reporte RADIAN desde
`https://catalogo-vpfe.dian.gov.co/` **todos los días**, sin intervención manual.

La DIAN autentica el portal con un **token temporal enviado por correo**. Hay
dos modos, ambos disponibles:

#### Modo manual con enlace (activo hoy)

Mientras se habilita un acceso de máquina (certificado digital ante la DIAN o un
proveedor tecnológico), se importa así, sin descargar/subir el Excel a mano:

1. **Solicita el token** (botón en `/radian/auto`, o entra tú al portal). La DIAN
   envía un correo con el enlace `…/User/AuthToken?pk=…&rk=…&token=…`.
2. **Pega el enlace** del correo en la app.
3. La app **activa la sesión, descarga el reporte y lo procesa** con el pipeline
   del módulo RADIAN, y abre la pantalla de resultados (editable y exportable).

#### Modo 100% automático con IMAP (opcional)

Cuando exista un buzón de correo dedicado al que llegue el token, la app puede
hacerlo todo sola, a diario:

1. **Solicita el token** con las credenciales del representante legal.
2. **Lee el correo** (remitente `facturacionelectronica@dian.gov.co`, asunto
   *«Token Acceso DIAN»*) por **IMAP** y extrae el enlace de acceso.
3. **Activa la sesión** (válido 60 minutos) y **descarga el reporte**.
4. Lo procesa y lo deja en **Importaciones**, listo para revisar y exportar a SIIGO.

> El siguiente paso recomendado para la automatización total es un
> **certificado digital** de factura electrónica (directo ante la DIAN o vía un
> proveedor tecnológico como SIIGO).

### Configuración (por empresa)

En `/radian/auto` se configura, para la empresa activa:

| Campo | Descripción |
|-------|-------------|
| Tipo de identificación + NIT representante legal | Credenciales del portal DIAN |
| NIT de la empresa | Opcional; por defecto el NIT de la empresa |
| Correo / contraseña de aplicación + IMAP host/puerto | Buzón donde llega el token |
| Hora diaria + días hacia atrás | Programación de la descarga |

> 🔐 **Gmail:** usa una **contraseña de aplicación** (no la contraseña normal).
> La contraseña puede definirse en la UI o, de forma más segura, en la variable
> de entorno `DIAN_EMAIL_PASSWORD` (que tiene prioridad y no se guarda en la BD).

### Cómo se dispara «todos los días»

Hay tres formas (elige una):

| Mecanismo | Cómo se activa | Recomendado para |
|-----------|----------------|------------------|
| **Programador interno** | `RADIAN_SCHEDULER_ENABLED=true` | Una sola instancia siempre encendida |
| **Cron externo** | `POST /radian/auto/cron` con cabecera `X-Radian-Token: <RADIAN_CRON_TOKEN>` | Azure Scheduler, cron, GitHub Action (varias instancias) |
| **CLI** | `python main.py radian-auto` | Tareas programadas del sistema operativo / WebJob |

```bash
# Una empresa concreta, rango por defecto:
python main.py radian-auto --empresa principal
# Todas las empresas habilitadas:
python main.py radian-auto
```

También hay un botón **«Ejecutar ahora»** en la UI para una corrida inmediata.

### Nota de calibración del portal

El **algoritmo del dígito de verificación del NIT**, la **construcción/lectura del
enlace `AuthToken`** y la **extracción del token desde el correo** están
implementados y cubiertos por tests. En cambio, las rutas HTTP exactas del
portal (el endpoint del formulario de ingreso y el de descarga del reporte) **no
están publicadas por la DIAN** y pueden cambiar: por eso son **configurables**
(*Opciones avanzadas del portal* en la UI, o `login_path`/`descarga_path`) en
lugar de estar incrustadas, y deben confirmarse contra el portal real la primera
vez. Si una corrida no devuelve un archivo, ajústalas ahí.

**Error `403 Forbidden` al activar la sesión.** El portal está detrás de un WAF
que rechaza las peticiones que no parecen venir de un navegador; el cliente ya se
presenta con cabeceras de navegador (`User-Agent` de Chrome, `Accept-Language`,
`sec-ch-ua`, `Sec-Fetch-*`) para evitarlo. Si aun así aparece un 403 al pegar el
enlace, casi siempre es por una de estas razones, en orden:

1. **El token expiró** (vence a los **60 minutos**): genera uno nuevo y pégalo de
   inmediato.
2. **El enlace ya se usó**: el `AuthToken` es de un solo uso; no lo abras en el
   navegador antes de pegarlo.
3. **IP fuera de Colombia**: si la app corre en un servidor en el exterior, la
   DIAN puede bloquear el acceso. Ejecútala desde una red en Colombia (o un proxy
   de salida en Colombia).

Si el portal endurece el filtro, el `User-Agent` y cabeceras extra del cliente
son ajustables sin tocar el código (parámetros `user_agent` / `extra_headers` de
`DianClient`).

---

## Persistencia de datos

El sistema guarda información en dos lugares:

| Dato | Local (`USE_SQLITE=true`) | Azure |
|------|---------------------------|-------|
| Documentos importados, historial de cuentas, importaciones | SQLite en `DB_DIR` | SQLite en `DB_DIR` **o** Azure SQL si `USE_SQLITE=false` |
| Empresas, archivos maestros, uploads, Excel generados | Carpetas del proyecto | Azure Blob Storage si `AZURE_STORAGE_CONNECTION_STRING` está configurado |

### En Azure App Service (importante)

El sistema de archivos del contenedor es **efímero**: solo `/home` persiste, y los
despliegues reemplazan `/home/site/wwwroot`. Por eso la base de datos SQLite se
guarda automáticamente en **`/home/data/db`** (fuera de `wwwroot`), que **sobrevive
a reinicios y redespliegues**. La detección es automática (variables de entorno de
App Service); puedes forzar otra ruta con `DB_DIR`.

> Requisito: `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true` (valor por defecto en Linux).

Los archivos maestros, el registro de empresas y los Excel generados persisten en
**Azure Blob Storage** cuando `AZURE_STORAGE_CONNECTION_STRING` está configurada.

### Opcional: Azure SQL en vez de SQLite

Para mayor concurrencia/robustez puedes migrar a Azure SQL (`USE_SQLITE=false` +
`DATABASE_URL`). Ver `azure-setup.sh`.

> ⚠️ Pendiente conocido: en modo Azure SQL todas las empresas comparten las mismas
> tablas (la separación por empresa hoy depende de archivos SQLite distintos). Si se
> habilita Azure SQL con varias empresas, hay que añadir antes una columna
> discriminadora por empresa.

---

## Licencia

MIT — ver [LICENSE](LICENSE).
