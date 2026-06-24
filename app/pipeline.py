"""
Pipeline de procesamiento RADIAN (independiente de Flask).

Centraliza la orquestación que convierte un reporte RADIAN (.xlsx) en
preasientos contables, excepciones y un Excel de salida. Lo usan tanto la
interfaz web (`app/web/routes.py`) como la importación automática
(`app/radian_auto/`), de modo que ambos caminos comparten exactamente la misma
lógica de negocio.

Antes este código vivía dentro de `routes._ejecutar_pipeline`; se extrajo aquí
para poder ejecutarlo fuera de una petición web (CLI, scheduler, cron).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def ejecutar_pipeline(
    radian_path: str,
    terceros_path: str | None,
    cuentas_path: str | None,
    comprobantes_path: str | None,
    db: str,
    incluir_duplicados: bool,
    empresa,
    output_dir: str,
) -> dict:
    """Ejecuta el pipeline completo y retorna un dict con los resultados.

    Args:
        radian_path:       Ruta local al reporte RADIAN (.xlsx/.xls).
        terceros_path:     Ruta al maestro de terceros (o None).
        cuentas_path:      Ruta al plan de cuentas (o None).
        comprobantes_path: Ruta al catálogo de comprobantes (o None).
        db:                Ruta a la base de datos de la empresa.
        incluir_duplicados: Si False, descarta los CUFE ya registrados.
        empresa:           Empresa activa (obligatoria: aporta NIT y cuentas).
        output_dir:        Carpeta donde escribir el Excel generado.

    Returns:
        dict con n_docs, n_excepciones, preasientos, excepciones, excel_path y
        archivo_origen (la misma forma que consume la vista de resultados).
    """
    import pandas as pd
    from app.database import inicializar_db, registrar_documento
    from app import bitacora as bita
    from app.importador import (
        importar_radian, cargar_maestro_terceros,
        cargar_maestro_cuentas, cargar_maestro_comprobantes,
    )
    from app.clasificador import clasificar_lote
    from app.terceros import procesar_terceros_lote, aplicar_correcciones_lote
    from app.comprobantes import asignar_comprobantes_lote
    from app.impuestos import procesar_impuestos_lote
    from app.preasiento import generar_lote
    from app.validaciones import validar_preasiento_completo
    from app.exportador import exportar_excel
    from app.sugerencias import registrar_lote_confirmaciones

    inicializar_db(db)
    bita.limpiar_sesion()

    # 1. Importar
    df = importar_radian(radian_path, db_path=db)
    if not incluir_duplicados:
        df = df[~df["_duplicado"]].copy()
    if df.empty:
        raise ValueError("No hay documentos nuevos para procesar.")

    # 2-4. Maestros opcionales
    def _carga(fn, path):
        if not path:
            return None
        try:
            return fn(path)
        except Exception:
            return None

    df_terceros     = _carga(cargar_maestro_terceros, terceros_path)
    df_cuentas      = _carga(cargar_maestro_cuentas, cuentas_path)
    df_comprobantes = _carga(cargar_maestro_comprobantes, comprobantes_path)

    # 5-8. Pipeline (con NIT y cuentas propias de la empresa)
    df = clasificar_lote(df, nit_empresa=empresa.nit)
    df = procesar_terceros_lote(df, df_terceros if df_terceros is not None else pd.DataFrame())
    # Reaplicar correcciones de tercero aprendidas de procesamientos previos.
    df = aplicar_correcciones_lote(df, df_terceros, db)
    df = asignar_comprobantes_lote(df, df_comprobantes)
    df = procesar_impuestos_lote(df, cuentas_impuestos=empresa.cuentas_impuestos_efectivas())
    preasientos = generar_lote(
        df, df_comprobantes, db_path=db,
        cuentas_contraparte=empresa.cuentas_contraparte_efectivas(),
    )

    # 9. Validar
    excepciones = []
    for p in preasientos:
        errs = validar_preasiento_completo(p, df_cuentas, db)
        if errs:
            excepciones.append({
                "cufe": p.cufe,
                "tipo_documento": p.tipo_documento,
                "clasificacion": p.clasificacion,
                "tercero_nit": p.tercero_nit,
                "total": p.total,
                "errores": errs,
            })

    # 10. Registrar en BD
    for _, row in df.iterrows():
        try:
            registrar_documento(
                cufe=str(row.get("CUFE/CUDE", "")),
                tipo_documento=str(row.get("Tipo de documento", "")),
                clasificacion=str(row.get("clasificacion", "")),
                folio=str(row.get("Folio", "")),
                prefijo=str(row.get("Prefijo", "")),
                nit_emisor=str(row.get("NIT Emisor", "")),
                nombre_emisor=str(row.get("Nombre Emisor", "")),
                nit_receptor=str(row.get("NIT Receptor", "")),
                nombre_receptor=str(row.get("Nombre Receptor", "")),
                total=float(row.get("Total", 0.0) or 0.0),
                fecha_emision=row.get("Fecha Emisión"),
                archivo_origen=radian_path,
                db_path=db,
            )
        except Exception:
            logger.exception(
                "No se pudo registrar el documento CUFE=%s en la BD",
                row.get("CUFE/CUDE", ""),
            )

    # 11. Alimentar historial
    registrar_lote_confirmaciones(preasientos, db_path=db)

    # 12. Exportar Excel — ruta absoluta para que funcione desde cualquier CWD.
    ruta_excel = exportar_excel(
        preasientos=preasientos,
        excepciones=excepciones,
        bitacora=bita.obtener_registros_sesion(),
        output_path=output_dir,
        archivo_origen=radian_path,
    )

    # Serializar preasientos (sólo datos necesarios para la vista / snapshot)
    preasientos_data = []
    for p in preasientos:
        lineas = []
        for l in p.lineas:
            lineas.append({
                "numero_linea": l.numero_linea,
                "cuenta": l.cuenta,
                "descripcion_cuenta": l.descripcion_cuenta,
                "debito": l.debito,
                "credito": l.credito,
                "concepto": l.concepto,
                "es_pendiente": l.es_pendiente,
                "es_sugerida": getattr(l, "es_sugerida", False),
            })
        preasientos_data.append({
            "cufe": p.cufe[:30] + "…" if len(p.cufe) > 30 else p.cufe,
            "cufe_full": p.cufe,
            "clasificacion": p.clasificacion,
            "tipo_documento": p.tipo_documento,
            "codigo_comprobante": p.codigo_comprobante,
            "titulo_comprobante": p.titulo_comprobante,
            "base_gravable": p.base_gravable,
            "fecha_emision": p.fecha_emision.strftime("%d/%m/%Y") if p.fecha_emision else "",
            "folio": p.folio,
            "prefijo": p.prefijo,
            "tercero_nit": p.tercero_nit,
            "tercero_nombre": p.tercero_nombre,
            "tercero_encontrado": p.tercero_encontrado,
            "tercero_nit_original": getattr(p, "tercero_nit_original", "") or p.tercero_nit,
            "tercero_corregido": getattr(p, "tercero_corregido", False),
            "total": p.total,
            "cuadra": p.cuadra,
            "excepciones": p.excepciones,
            "lineas": lineas,
        })

    return {
        "n_docs": len(preasientos),
        "n_excepciones": len(excepciones),
        "preasientos": preasientos_data,
        "excepciones": excepciones,
        "excel_path": ruta_excel,
        "archivo_origen": radian_path,
    }
