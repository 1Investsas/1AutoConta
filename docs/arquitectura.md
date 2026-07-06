# Arquitectura del sistema 1ContaBot

## Resumen

Sistema modular de automatización contable para 1 INVEST SAS (NIT 901.331.657-7).
Procesa reportes RADIAN de la DIAN y genera preasientos contables en Excel.

## Fases

| Fase | Descripción | Estado |
|------|-------------|--------|
| 1 | Procesamiento CLI: importar → clasificar → cruzar → preasiento → exportar | ✅ Implementada |
| 2 | Motor de sugerencias + interfaz web | 🔲 Scaffold |
| 3 | Importación directa al sistema contable vía API | 🔲 Pendiente |
| 4 | Dashboard de reportería y analytics | 🔲 Pendiente |

## Flujo de datos (Fase 1)

```
RADIAN.xlsx → importador → clasificador → terceros → comprobantes
                                                          ↓
Excel salida ← exportador ← validaciones ← preasiento ← impuestos
```

## Módulos

| Módulo | Responsabilidad |
|--------|----------------|
| `config.py` | Constantes, mapeos, cuentas por defecto |
| `models.py` | Dataclasses de dominio |
| `database.py` | SQLite: duplicados, bitácora, historial |
| `importador.py` | Lectura y normalización de archivos Excel |
| `clasificador.py` | Reglas deterministas de tipo de documento |
| `terceros.py` | Cruce con maestro de terceros |
| `impuestos.py` | Separación de impuestos y base gravable |
| `comprobantes.py` | Asignación de código de comprobante |
| `preasiento.py` | Generación de líneas contables por tipo |
| `validaciones.py` | Cuadre, unicidad CUFE, coherencia |
| `exportador.py` | Excel formateado con 4 pestañas |
| `bitacora.py` | Registro de acciones (memoria + BD) |

## Base de datos SQLite

- `documentos_importados`: registro de CUFEs procesados (detección de duplicados)
- `bitacora`: log persistente de todas las acciones
- `historial_cuentas`: aprendizaje de cuentas por tercero/clasificación (Fase 2)
