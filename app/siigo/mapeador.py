"""
Mapeador SIIGO — Fase 3.

Convierte una lista de PreasientoContable al formato de filas que espera
el template de importación Excel de SIIGO Nube ("Subir desde excel –
Comprobantes contables").

Columnas generadas en el orden exacto del template SIIGO (27 columnas):
  1.  Tipo de comprobante
  2.  Consecutivo comprobante      ← agrupa líneas en el mismo asiento
  3.  Fecha de elaboración
  4.  Sigla moneda
  5.  Tasa de cambio
  6.  Código cuenta contable
  7.  Identificación tercero
  8.  Sucursal
  9.  Código producto
  10. Código de bodega
  11. Acción
  12. Cantidad producto
  13. Prefijo
  14. Consecutivo
  15. No. cuota
  16. Fecha vencimiento
  17. Código impuesto
  18. Código grupo activo fijo
  19. Código activo fijo
  20. Descripción
  21. Código centro/subcentro de costos
  22. Débito
  23. Crédito
  24. Observaciones
  25. Base gravable libro compras/ventas
  26. Base exenta libro compras/ventas
  27. Mes de cierre

Notas:
  - El campo "Consecutivo comprobante" (col 2) identifica en SIIGO el
    asiento contable. Todas las líneas del mismo preasiento comparten el
    mismo número; se asigna en mapear_lote().
  - Las filas con cuenta [PENDIENTE] se incluyen con la cuenta vacía y
    se marcan en Descripción para que el contador las identifique.
  - El límite de 500 filas por archivo es responsabilidad del exportador.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import PreasientoContable
from app.config import SIIGO_CODIGOS_COMPROBANTE, COL_CUENTAS_CODIGO, COL_CUENTAS_VENCIMIENTOS

# Valores del maestro de cuentas que activan las columnas de vencimiento
_VALORES_VENCIMIENTO: frozenset[str] = frozenset({
    "Con vencimiento en cartera",
    "Con vencimiento en proveedores",
})


# Encabezados en el orden exacto del template SIIGO
ENCABEZADOS_SIIGO = [
    "Tipo de comprobante",
    "Consecutivo comprobante",
    "Fecha de elaboraci\xf3n ",       # trailing space — igual que el template
    "Sigla moneda",
    "Tasa de cambio",
    "C\xf3digo cuenta contable",
    "Identificaci\xf3n tercero",
    "Sucursal",
    "C\xf3digo producto",
    "C\xf3digo de bodega",
    "Acci\xf3n",
    "Cantidad producto",
    "Prefijo",
    "Consecutivo",
    "No. cuota",
    "Fecha vencimiento",
    "C\xf3digo impuesto",
    "C\xf3digo grupo activo fijo",
    "C\xf3digo activo fijo",
    "Descripci\xf3n",
    "C\xf3digo centro/subcentro de costos",
    "D\xe9bito",
    "Cr\xe9dito",
    "Observaciones",
    "Base gravable libro compras/ventas  ",  # dos espacios — igual que el template
    "Base exenta libro compras/ventas",
    "Mes de cierre",
]

# Columnas obligatorias (1-indexadas) — se resaltan en rojo en el template
_COLS_REQUERIDAS = {1, 2, 3, 6, 7}


@dataclass
class FilaSiigo:
    """Una fila del archivo de importación SIIGO (27 columnas)."""
    tipo_comprobante: int
    consecutivo_comprobante: int    # Agrupa líneas en el mismo asiento
    fecha: str                      # DD/MM/YYYY
    codigo_cuenta: str
    nit_tercero: str
    descripcion: str = ""
    observaciones: str = ""
    # Cols 13-16: solo se rellenan cuando la cuenta tiene "Maneja vencimientos"
    # = "Con vencimiento en cartera" o "Con vencimiento en proveedores"
    prefijo: str = ""               # Col 13 — "CC" si tiene vencimiento, "" si no
    folio: str = ""                 # Col 14 — consecutivo_comprobante si tiene vencimiento, "" si no
    no_cuota: str = ""              # Col 15 — "1" si tiene vencimiento, "" si no
    fecha_vencimiento: str = ""     # Col 16 — igual a fecha si tiene vencimiento, "" si no
    centro_costo: str = ""
    debito: float = 0.0
    credito: float = 0.0
    es_pendiente: bool = False      # No se exporta; indica al exportador que coloree la fila

    def a_lista(self) -> list:
        """Retorna los 27 valores en el orden exacto de ENCABEZADOS_SIIGO."""
        return [
            self.tipo_comprobante,         # 1.  Tipo de comprobante
            self.consecutivo_comprobante,  # 2.  Consecutivo comprobante
            self.fecha,                    # 3.  Fecha de elaboración
            "",                            # 4.  Sigla moneda
            "",                            # 5.  Tasa de cambio
            self.codigo_cuenta,            # 6.  Código cuenta contable
            self.nit_tercero,              # 7.  Identificación tercero
            "",                            # 8.  Sucursal
            "",                            # 9.  Código producto
            "",                            # 10. Código de bodega
            "",                            # 11. Acción
            "",                            # 12. Cantidad producto
            self.prefijo,                  # 13. Prefijo         ("CC" o "")
            self.folio,                    # 14. Consecutivo     (consecutivo_comprobante o "")
            self.no_cuota,                 # 15. No. cuota       ("1" o "")
            self.fecha_vencimiento,        # 16. Fecha vencimiento (=fecha o "")
            "",                            # 17. Código impuesto
            "",                            # 18. Código grupo activo fijo
            "",                            # 19. Código activo fijo
            self.descripcion,              # 20. Descripción
            self.centro_costo,             # 21. Código centro/subcentro de costos
            self.debito,                   # 22. Débito
            self.credito,                  # 23. Crédito
            self.observaciones,            # 24. Observaciones
            "",                            # 25. Base gravable libro compras/ventas
            "",                            # 26. Base exenta libro compras/ventas
            "",                            # 27. Mes de cierre
        ]


def mapear_preasiento(
    preasiento: PreasientoContable,
    consecutivo_comprobante: int = 0,
    cuentas_vencimiento: frozenset = frozenset(),
) -> list[FilaSiigo]:
    """
    Convierte un PreasientoContable en una lista de FilaSiigo.

    Args:
        preasiento:              Preasiento contable a convertir.
        consecutivo_comprobante: Número de asiento (asignado por mapear_lote).
        cuentas_vencimiento:     Conjunto de códigos de cuenta que tienen
                                 "Maneja vencimientos" = "Con vencimiento en
                                 cartera" o "Con vencimiento en proveedores".
                                 Para esas cuentas se rellenan las cols 13-16.

    Returns:
        Lista de FilaSiigo, una por cada línea contable.
    """
    tipo_comp = SIIGO_CODIGOS_COMPROBANTE.get(preasiento.clasificacion, 0)

    fecha = (
        preasiento.fecha_emision.strftime("%d/%m/%Y")
        if preasiento.fecha_emision
        else ""
    )

    prefijo_doc = preasiento.prefijo or ""
    folio_doc   = preasiento.folio   or ""
    sep         = "-" if prefijo_doc else ""
    # Referencia del documento: identifica el asiento (clasificación, nº de
    # documento y tercero). Por decisión de negocio va en la columna
    # "Descripción" (col 20); la columna "Observaciones" (col 24) se deja vacía.
    referencia = (
        f"{preasiento.clasificacion.replace('_', ' ')} "
        f"{prefijo_doc}{sep}{folio_doc} "
        f"| {preasiento.tercero_nombre}"
    ).strip()

    filas: list[FilaSiigo] = []
    for linea in preasiento.lineas:
        if linea.es_pendiente:
            codigo_cuenta = ""
            descripcion   = f"[PENDIENTE] {referencia}"
        else:
            codigo_cuenta = linea.cuenta
            descripcion   = referencia

        # Cols 13-16: solo cuando la cuenta maneja vencimientos (y no es pendiente)
        tiene_vencimiento = (not linea.es_pendiente) and (linea.cuenta in cuentas_vencimiento)
        prefijo_siigo       = "CC"                          if tiene_vencimiento else ""
        folio_siigo         = str(consecutivo_comprobante)  if tiene_vencimiento else ""
        no_cuota            = "1"                           if tiene_vencimiento else ""
        fecha_vencimiento   = fecha                         if tiene_vencimiento else ""

        filas.append(FilaSiigo(
            tipo_comprobante=tipo_comp,
            consecutivo_comprobante=consecutivo_comprobante,
            fecha=fecha,
            codigo_cuenta=codigo_cuenta,
            nit_tercero=linea.tercero_nit or preasiento.tercero_nit or "",
            descripcion=descripcion,
            observaciones="",
            prefijo=prefijo_siigo,
            folio=folio_siigo,
            no_cuota=no_cuota,
            fecha_vencimiento=fecha_vencimiento,
            es_pendiente=linea.es_pendiente,
            debito=linea.debito,
            credito=linea.credito,
        ))

    return filas


def _construir_set_vencimiento(df_cuentas) -> frozenset:
    """
    Retorna el conjunto de códigos de cuenta que tienen vencimiento en cartera
    o en proveedores según el maestro de cuentas contables.

    Args:
        df_cuentas: DataFrame del maestro de cuentas (puede ser None).

    Returns:
        frozenset de códigos de cuenta con vencimiento.
    """
    if df_cuentas is None or df_cuentas.empty:
        return frozenset()
    if COL_CUENTAS_VENCIMIENTOS not in df_cuentas.columns:
        return frozenset()
    if COL_CUENTAS_CODIGO not in df_cuentas.columns:
        return frozenset()

    mask = df_cuentas[COL_CUENTAS_VENCIMIENTOS].str.strip().isin(_VALORES_VENCIMIENTO)
    return frozenset(df_cuentas.loc[mask, COL_CUENTAS_CODIGO].str.strip())


def mapear_lote(
    preasientos: list[PreasientoContable],
    incluir_pendientes: bool = True,
    df_cuentas=None,
) -> list[FilaSiigo]:
    """
    Convierte una lista de preasientos en filas SIIGO.

    Asigna un 'Consecutivo comprobante' con formato yyyymmNN por preasiento,
    donde yyyymm es el año y mes de la fecha de emisión y NN es el número
    correlativo (01, 02, …) dentro de ese mismo año-mes **y tipo de comprobante**.

    Ejemplo: primer FACTURA_COMPRA de enero 2026  → 20260101,
             primera NOMINA de enero 2026          → 20260101 (secuencia propia),
             segunda FACTURA_COMPRA de enero 2026  → 20260102.

    Reglas aplicadas:
    - Los preasientos se ordenan por fecha de emisión antes de numerar,
      de modo que los consecutivos respetan el orden cronológico aunque el
      archivo RADIAN no venga ordenado.
    - El contador es independiente por (tipo_comprobante, año-mes): dos
      tipos de comprobante distintos en el mismo mes tienen cada uno su
      propia secuencia que empieza en 01.

    Las columnas 13-16 (Prefijo, Consecutivo, No. cuota, Fecha vencimiento)
    se rellenan solo para las cuentas que en el maestro de cuentas tienen
    "Maneja vencimientos" = "Con vencimiento en cartera" o "Con vencimiento
    en proveedores".  En ese caso: Prefijo="CC", Consecutivo=consecutivo
    comprobante, No. cuota=1, Fecha vencimiento=fecha de elaboración.

    Args:
        preasientos:        Lista de preasientos a convertir.
        incluir_pendientes: Si es False, omite líneas con cuenta [PENDIENTE].
        df_cuentas:         DataFrame del maestro de cuentas contables
                            (Listado_de_Cuentas_Contables). Opcional;
                            si es None las cols 13-16 quedan vacías.

    Returns:
        Lista plana de FilaSiigo lista para exportar.
    """
    from datetime import datetime as _dt

    cuentas_vencimiento = _construir_set_vencimiento(df_cuentas)

    # Bug fix #2: ordenar por fecha antes de asignar consecutivos para
    # respetar el orden cronológico aunque el RADIAN no venga ordenado.
    _FECHA_MIN = _dt.min
    preasientos_ordenados = sorted(
        preasientos,
        key=lambda p: p.fecha_emision if p.fecha_emision else _FECHA_MIN,
    )

    filas: list[FilaSiigo] = []
    # Bug fix #1: clave compuesta (tipo_comprobante, yyyymm) → cada tipo tiene
    # su propio contador independiente dentro del mismo mes.
    contadores: dict[str, int] = {}

    for preasiento in preasientos_ordenados:
        tipo_comp = SIIGO_CODIGOS_COMPROBANTE.get(preasiento.clasificacion, 0)

        if preasiento.fecha_emision:
            mes = preasiento.fecha_emision.strftime("%Y%m")
        else:
            mes = "000000"

        # Clave única: tipo de comprobante + año-mes
        clave = f"{tipo_comp}_{mes}"
        contadores[clave] = contadores.get(clave, 0) + 1
        consecutivo = int(f"{mes}{contadores[clave]:02d}")

        for fila in mapear_preasiento(
            preasiento,
            consecutivo_comprobante=consecutivo,
            cuentas_vencimiento=cuentas_vencimiento,
        ):
            if not incluir_pendientes and fila.es_pendiente:
                continue
            filas.append(fila)

    return filas


def partir_en_chunks(filas: list[FilaSiigo], tamaño: int) -> list[list[FilaSiigo]]:
    """
    Divide una lista de filas en sublistas de máximo `tamaño` elementos.
    Respeta el límite de SIIGO de 500 filas por archivo.

    Args:
        filas:  Lista de FilaSiigo.
        tamaño: Número máximo de filas por chunk.

    Returns:
        Lista de listas, cada una con hasta `tamaño` filas.
    """
    if tamaño <= 0:
        raise ValueError("El tamaño del chunk debe ser mayor que cero.")
    return [filas[i : i + tamaño] for i in range(0, len(filas), tamaño)]
