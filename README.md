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

### Archivos maestros

Coloca en la carpeta `data/`:

| Archivo | Descripción |
|---------|-------------|
| `Listado_de_Terceros.xlsx` | Exportado del sistema contable. Encabezados en fila 7. |
| `Listado_de_Cuentas_Contables.xlsx` | Plan de cuentas. Encabezados en fila 7. |
| `Tipos_de_comprobante_contable.xlsx` | Catálogo de comprobantes. Encabezados en fila 7. |

---

## Estructura de carpetas

```
contable-auto/
├── app/                  # Módulos del sistema
│   ├── config.py         # Constantes y configuración central
│   ├── importador.py     # Lectura de RADIAN y archivos maestros
│   ├── clasificador.py   # Clasificación determinista de documentos
│   ├── terceros.py       # Cruce con maestro de terceros
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
| **3** | Importación directa al sistema contable vía API |
| **4** | Dashboard de reportería y analytics contable |

---

## Licencia

MIT — ver [LICENSE](LICENSE).
