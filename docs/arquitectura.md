# Arquitectura del sistema 1ContaBot

## Resumen

Sistema modular de automatización contable para 1 INVEST SAS (NIT 901.331.657-7).
Procesa reportes RADIAN de la DIAN y genera preasientos contables en Excel.

## Fases

| Fase | Descripción | Estado |
|------|-------------|--------|
| 1 | Procesamiento CLI: importar → clasificar → cruzar → preasiento → exportar | ✅ Implementada |
| 2 | Motor de sugerencias + interfaz web | ✅ Implementada (Flask en producción en Azure App Service) |
| 3 | Importación directa al sistema contable vía API | ✅ Implementada (`app/siigo/api_client.py`; requiere credenciales SIIGO Premium) |
| 4 | Dashboard de reportería y analytics | ✅ Implementada (ruta `/analytics`) |

El plan de trabajo vigente (multiempresa, RBAC, migración a cuentas oficiales)
se lleva por fases propias en `HANDOFF.md`, que es la fuente de verdad del estado.

## Flujo de datos (núcleo RADIAN)

```
RADIAN.xlsx → importador → clasificador → terceros → comprobantes
                                                          ↓
Excel salida ← exportador ← validaciones ← preasiento ← impuestos
```

## Módulos

| Módulo | Responsabilidad |
|--------|----------------|
| `config.py` | Constantes, mapeos, cuentas por defecto, flags de entorno |
| `models.py` | Dataclasses de dominio |
| `database/` | Paquete de persistencia dual SQLite/Azure SQL (core, schema, documentos, importaciones, analytics, aprendizaje, auth, caja, mixtos, sistema) |
| `importador.py` | Lectura y normalización de reportes RADIAN |
| `clasificador.py` | Reglas deterministas de tipo de documento |
| `terceros.py` / `terceros_rut.py` | Cruce con maestro de terceros; importación desde RUT |
| `impuestos.py` | Separación de impuestos y base gravable |
| `comprobantes.py` | Asignación de código de comprobante |
| `preasiento.py` | Generación de líneas contables por tipo |
| `validaciones.py` | Cuadre, unicidad CUFE, coherencia |
| `exportador.py` | Excel formateado con 4 pestañas |
| `bitacora.py` | Registro de acciones (memoria + BD) |
| `siigo/` | Mapeador y exportador de archivos SIIGO + cliente REST de la API (`api_client.py`) |
| `banco/` | Importación y mapeo de extractos bancarios |
| `caja/` | Módulo de caja general |
| `radian_auto/` | Descarga automatizada de reportes desde el portal DIAN |
| `aprendizaje.py` / `sugerencias.py` | Motor de sugerencias y ML de prediligenciamiento |
| `authn.py` / `authz.py` / `tenancy.py` / `audit.py` | Autenticación, RBAC, multi-tenencia y auditoría |
| `storage.py` | Almacenamiento local o Azure Blob |
| `web/` | App Flask: factory, rutas por módulo (`web/routes/`), plantillas y estáticos |

## Base de datos

Modo dual: **SQLite** (local/dev, un archivo por empresa `contable_<id>.db`) o
**Azure SQL** (`USE_SQLITE=false`; tablas compartidas con columna `empresa_id` e
índices tenant-aware). Tablas principales:

- `documentos_importados`: registro de CUFEs procesados (detección de duplicados)
- `bitacora`: log persistente de todas las acciones
- `historial_cuentas`: aprendizaje de cuentas por tercero/clasificación
- `importaciones`: ciclo de estados + snapshot editable durable (`preasientos_json`)
- `procesos_banco`, `correcciones_tercero`: módulo bancos y aprendizaje de terceros
- Tablas de sistema (sin filtro por empresa): `empresas`, `usuarios`, `roles`,
  `permisos`, `role_permissions`, `usuario_empresa_roles`, `usuario_global_roles`,
  `audit_log`
