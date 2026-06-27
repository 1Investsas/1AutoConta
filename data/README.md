# Archivos maestros

Coloca aquí los archivos exportados del sistema contable:

| Archivo | Descripción |
|---------|-------------|
| `Listado_de_Terceros.xlsx` | Maestro de terceros, según el **Modelo de importación de terceros de Siigo Nube**: 29 columnas, encabezados en la **fila 1**, datos desde la **fila 2** y **todas las celdas en formato de texto (`@`)**. |
| `Listado_de_Cuentas_Contables.xlsx` | Plan de cuentas. Encabezados en fila 7, datos desde fila 8. |
| `Tipos_de_comprobante_contable.xlsx` | Catálogo de comprobantes. Encabezados en fila 7, datos desde fila 8. |

> **Terceros (modelo Siigo Nube):** las identificaciones y los códigos (país,
> departamento, ciudad, tipo de identificación, dígito de verificación, código
> postal…) llevan ceros a la izquierda; por eso **todas las celdas son de texto**.
> El sistema conserva ese formato en cada importación o actualización del maestro.
> La estructura está definida en `app/terceros_schema.py`.

> **Cuentas y comprobantes:** las filas 1–6 contienen información de la empresa
> (nombre, NIT, etc.) y el sistema las ignora automáticamente.
