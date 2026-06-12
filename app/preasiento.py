"""
Generación de preasientos contables por documento electrónico.

Construye la lista de líneas contables (débitos y créditos) para cada
tipo de documento según las reglas de negocio definidas.

Convenciones:
- La línea de contraparte principal (CxC, CxP, etc.) siempre es la primera.
- La línea de base gravable va en segundo lugar y puede quedar [PENDIENTE].
- Las líneas de impuestos van después, una por cada impuesto > 0.
"""

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from app.config import CUENTAS_CONTRAPARTE
from app.models import LineaContable, PreasientoContable
from app.comprobantes import asignar_comprobante

logger = logging.getLogger(__name__)

CUENTA_PENDIENTE = "[PENDIENTE]"


def _linea(
    cufe: str,
    numero: int,
    cuenta: str,
    descripcion: str,
    debito: float,
    credito: float,
    concepto: str,
    tercero_nit: str,
    tercero_nombre: str,
) -> LineaContable:
    """Crea un objeto LineaContable con validación básica."""
    es_pendiente = cuenta == CUENTA_PENDIENTE
    return LineaContable(
        cufe=cufe,
        numero_linea=numero,
        cuenta=cuenta,
        descripcion_cuenta=descripcion,
        debito=round(float(debito), 2),
        credito=round(float(credito), 2),
        concepto=concepto,
        tercero_nit=tercero_nit,
        tercero_nombre=tercero_nombre,
        es_pendiente=es_pendiente,
    )


def _generar_lineas_factura_compra(
    cufe: str,
    tercero_nit: str,
    tercero_nombre: str,
    total: float,
    base_gravable: float,
    impuestos: list[dict],
    cuenta_contraparte: str = "22050501",
) -> list[LineaContable]:
    """
    Estructura del asiento para Factura de Compra:
    1. 22050501 Proveedores nacionales  → CRÉDITO Total
    2. [PENDIENTE] Gasto/Costo          → DÉBITO Base gravable
    3. Impuestos no-retención           → DÉBITO valor impuesto
    4. Retenciones                      → CRÉDITO valor retención
    """
    lineas = []
    n = 1

    lineas.append(_linea(cufe, n, cuenta_contraparte, "Proveedores nacionales",
                         0.0, total, "CxP Proveedor", tercero_nit, tercero_nombre))
    n += 1

    lineas.append(_linea(cufe, n, CUENTA_PENDIENTE, "Gasto/Costo",
                         base_gravable, 0.0, "Base gravable", tercero_nit, tercero_nombre))
    n += 1

    for imp in impuestos:
        if imp["es_retencion"]:
            lineas.append(_linea(cufe, n, imp["cuenta_sugerida"] or CUENTA_PENDIENTE,
                                 imp["nombre_impuesto"],
                                 0.0, imp["valor"],
                                 f"Ret. practicada {imp['nombre_impuesto']}",
                                 tercero_nit, tercero_nombre))
        else:
            lineas.append(_linea(cufe, n, imp["cuenta_sugerida"] or CUENTA_PENDIENTE,
                                 imp["nombre_impuesto"],
                                 imp["valor"], 0.0,
                                 imp["nombre_impuesto"],
                                 tercero_nit, tercero_nombre))
        n += 1

    return lineas


def _generar_lineas_factura_venta(
    cufe: str,
    tercero_nit: str,
    tercero_nombre: str,
    total: float,
    base_gravable: float,
    impuestos: list[dict],
    cuenta_contraparte: str = "13050501",
) -> list[LineaContable]:
    """
    Estructura del asiento para Factura de Venta:
    1. 13050501 CxC Clientes      → DÉBITO Total
    2. [PENDIENTE] Ingreso        → CRÉDITO Base gravable
    3. Impuestos no-retención     → CRÉDITO valor impuesto
    4. Retenciones a favor        → DÉBITO valor retención
    """
    lineas = []
    n = 1

    lineas.append(_linea(cufe, n, cuenta_contraparte, "CxC Clientes",
                         total, 0.0, "CxC Cliente", tercero_nit, tercero_nombre))
    n += 1

    lineas.append(_linea(cufe, n, CUENTA_PENDIENTE, "Ingreso",
                         0.0, base_gravable, "Base gravable", tercero_nit, tercero_nombre))
    n += 1

    for imp in impuestos:
        if imp["es_retencion"]:
            lineas.append(_linea(cufe, n, imp["cuenta_sugerida"] or CUENTA_PENDIENTE,
                                 imp["nombre_impuesto"],
                                 imp["valor"], 0.0,
                                 f"Ret. a favor {imp['nombre_impuesto']}",
                                 tercero_nit, tercero_nombre))
        else:
            lineas.append(_linea(cufe, n, imp["cuenta_sugerida"] or CUENTA_PENDIENTE,
                                 imp["nombre_impuesto"],
                                 0.0, imp["valor"],
                                 imp["nombre_impuesto"],
                                 tercero_nit, tercero_nombre))
        n += 1

    return lineas


def _generar_lineas_documento_soporte(
    cufe: str,
    tercero_nit: str,
    tercero_nombre: str,
    total: float,
    base_gravable: float,
    impuestos: list[dict],
    cuenta_contraparte: str = "22100501",
) -> list[LineaContable]:
    """
    Estructura del asiento para Documento Soporte:
    1. 22100501 Proveedores exterior → CRÉDITO Total
    2. [PENDIENTE] Gasto             → DÉBITO Base gravable
    3. Impuestos si aplican
    """
    lineas = []
    n = 1

    lineas.append(_linea(cufe, n, cuenta_contraparte, "Proveedores exterior/no obligados",
                         0.0, total, "CxP No obligado", tercero_nit, tercero_nombre))
    n += 1

    lineas.append(_linea(cufe, n, CUENTA_PENDIENTE, "Gasto",
                         base_gravable, 0.0, "Base gravable", tercero_nit, tercero_nombre))
    n += 1

    for imp in impuestos:
        if imp["es_retencion"]:
            lineas.append(_linea(cufe, n, imp["cuenta_sugerida"] or CUENTA_PENDIENTE,
                                 imp["nombre_impuesto"],
                                 0.0, imp["valor"],
                                 f"Ret. {imp['nombre_impuesto']}",
                                 tercero_nit, tercero_nombre))
        else:
            lineas.append(_linea(cufe, n, imp["cuenta_sugerida"] or CUENTA_PENDIENTE,
                                 imp["nombre_impuesto"],
                                 imp["valor"], 0.0,
                                 imp["nombre_impuesto"],
                                 tercero_nit, tercero_nombre))
        n += 1

    return lineas


def _generar_lineas_nomina(
    cufe: str,
    tercero_nit: str,
    tercero_nombre: str,
    total: float,
    impuestos: list[dict],
    cuenta_contraparte: str = "25050501",
) -> list[LineaContable]:
    """
    Estructura del asiento para Pago de Nómina Individual (RADIAN):
    El RADIAN refleja el PAGO de nómina, no la causación.
    1. 25050501 Salarios por pagar  → DÉBITO Total  (cancelación CxP)
    2. [PENDIENTE] Cuenta disponible → CRÉDITO Total (banco/caja, selección manual)
    """
    lineas = []
    n = 1

    lineas.append(_linea(cufe, n, cuenta_contraparte, "Salarios por pagar",
                         total, 0.0, "Pago nómina", tercero_nit, tercero_nombre))
    n += 1

    lineas.append(_linea(cufe, n, CUENTA_PENDIENTE, "Cuenta disponible (banco/caja)",
                         0.0, total, "Cuenta disponible", tercero_nit, tercero_nombre))
    n += 1

    return lineas


_GENERADORES = {
    "FACTURA_COMPRA": _generar_lineas_factura_compra,
    "DOCUMENTO_SOPORTE": _generar_lineas_documento_soporte,
    "NOTA_CREDITO_COMPRA": _generar_lineas_factura_compra,
    "NOTA_DEBITO_COMPRA": _generar_lineas_factura_compra,
    "FACTURA_VENTA": _generar_lineas_factura_venta,
    "NOTA_CREDITO_VENTA": _generar_lineas_factura_venta,
    "NOTA_DEBITO_VENTA": _generar_lineas_factura_venta,
}


def generar_preasiento(
    documento: dict,
    tercero: dict,
    impuestos: list[dict],
    base_gravable: float,
    clasificacion: str,
    df_comprobantes: Optional[pd.DataFrame] = None,
    cuentas_contraparte: Optional[dict] = None,
) -> PreasientoContable:
    """
    Genera el preasiento contable completo para un documento.

    Args:
        documento:       Diccionario con los campos del documento (fila RADIAN).
        tercero:         Diccionario {'nit': ..., 'nombre': ...} del tercero.
        impuestos:       Lista de impuestos del documento (de separar_impuestos).
        base_gravable:   Base gravable calculada.
        clasificacion:   Clasificación del documento.
        df_comprobantes: Maestro de comprobantes para resolver título (opcional).

    Returns:
        Objeto PreasientoContable con todas las líneas.
    """
    cufe = str(documento.get("CUFE/CUDE", ""))
    total = float(documento.get("Total", 0.0) or 0.0)
    tercero_nit = tercero.get("nit", "")
    tercero_nombre = tercero.get("nombre", "")

    comp = asignar_comprobante(clasificacion, df_comprobantes)

    # Cuenta de contrapartida: override de la empresa o default global
    cuentas = {**CUENTAS_CONTRAPARTE, **(cuentas_contraparte or {})}
    cuenta_cp = cuentas.get(clasificacion, "")

    # Seleccionar generador de líneas según clasificación
    generador = _GENERADORES.get(clasificacion)

    if clasificacion == "NOMINA":
        lineas = _generar_lineas_nomina(cufe, tercero_nit, tercero_nombre, total,
                                        impuestos, cuenta_cp or "25050501")
    elif generador is not None:
        lineas = generador(cufe, tercero_nit, tercero_nombre, total, base_gravable,
                           impuestos, cuenta_cp) if cuenta_cp else generador(
                               cufe, tercero_nit, tercero_nombre, total, base_gravable, impuestos)
    else:
        # Para SIN_CLASIFICAR u otros: generamos solo líneas pendientes
        lineas = [
            _linea(cufe, 1, CUENTA_PENDIENTE, "Sin clasificar",
                   total, 0.0, "Revisar clasificación", tercero_nit, tercero_nombre),
            _linea(cufe, 2, CUENTA_PENDIENTE, "Sin clasificar",
                   0.0, total, "Revisar clasificación", tercero_nit, tercero_nombre),
        ]

    # Verificar cuadre
    total_debito = sum(l.debito for l in lineas)
    total_credito = sum(l.credito for l in lineas)
    cuadra = abs(total_debito - total_credito) < 0.01

    excepciones = []
    if not cuadra:
        msg = f"No cuadra: débitos={total_debito:.2f}, créditos={total_credito:.2f}"
        excepciones.append(msg)
        logger.warning("CUFE %s: %s", cufe, msg)

    pendientes = [l for l in lineas if l.es_pendiente]
    if pendientes:
        excepciones.append(f"{len(pendientes)} línea(s) con cuenta [PENDIENTE]")

    fecha_emision = documento.get("Fecha Emisión")
    if isinstance(fecha_emision, str):
        try:
            fecha_emision = datetime.fromisoformat(fecha_emision)
        except ValueError:
            fecha_emision = None

    return PreasientoContable(
        cufe=cufe,
        tipo_documento=str(documento.get("Tipo de documento", "")),
        clasificacion=clasificacion,
        codigo_comprobante=comp["codigo"],
        titulo_comprobante=comp["titulo"],
        fecha_emision=fecha_emision,
        folio=str(documento.get("Folio", "")),
        prefijo=str(documento.get("Prefijo", "")),
        tercero_nit=tercero_nit,
        tercero_nombre=tercero_nombre,
        tercero_encontrado=bool(documento.get("tercero_encontrado", False)),
        total=total,
        base_gravable=base_gravable,
        lineas=lineas,
        cuadra=cuadra,
        excepciones=excepciones,
    )


def generar_lote(
    df: pd.DataFrame,
    df_comprobantes: Optional[pd.DataFrame] = None,
    db_path: Optional[str] = None,
    cuentas_contraparte: Optional[dict] = None,
) -> list[PreasientoContable]:
    """
    Genera los preasientos para todo el DataFrame procesado.

    El DataFrame debe tener las columnas: clasificacion, tercero_nit,
    tercero_nombre, tercero_encontrado, _impuestos, _base_gravable.

    Args:
        df:               DataFrame RADIAN enriquecido.
        df_comprobantes:  Maestro de comprobantes (opcional).
        db_path:          Ruta a la BD SQLite para enriquecer con sugerencias
                          (opcional, Fase 2). Si es None, se omite el motor.

    Returns:
        Lista de PreasientoContable, uno por cada fila del DataFrame.
    """
    preasientos = []

    for _, row in df.iterrows():
        clasificacion = str(row.get("clasificacion", "SIN_CLASIFICAR"))
        tercero = {
            "nit": str(row.get("tercero_nit", "")),
            "nombre": str(row.get("tercero_nombre", "")),
        }
        impuestos = row.get("_impuestos", [])
        base_gravable = float(row.get("_base_gravable", 0.0))

        preasiento = generar_preasiento(
            documento=row.to_dict(),
            tercero=tercero,
            impuestos=impuestos,
            base_gravable=base_gravable,
            clasificacion=clasificacion,
            df_comprobantes=df_comprobantes,
            cuentas_contraparte=cuentas_contraparte,
        )
        preasientos.append(preasiento)

    logger.info("Preasientos generados: %d", len(preasientos))

    # --- Fase 2: enriquecer con sugerencias del historial ---
    if db_path:
        from app.sugerencias import enriquecer_con_sugerencias
        preasientos = enriquecer_con_sugerencias(preasientos, db_path)

    return preasientos

