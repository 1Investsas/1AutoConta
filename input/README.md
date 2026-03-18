# Archivos de entrada RADIAN

Descarga el reporte desde el portal RADIAN de la DIAN:
https://catalogo-vpfe.dian.gov.co/

Coloca el archivo `.xlsx` aquí y referencialo al ejecutar:

```bash
python main.py procesar --radian input/RADIAN_2025_03.xlsx
```

El sistema detecta automáticamente documentos ya procesados (CUFE duplicados)
y los omite para evitar doble contabilización.
