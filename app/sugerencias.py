"""
Motor de sugerencias de cuentas contables basado en historial (FASE 2).

Aprende de cada procesamiento previo: registra qué cuenta contable se usó
para cada combinación (clasificación, NIT tercero, tipo de línea) y la sugiere
automáticamente la próxima vez que aparezca la misma combinación.

Funciones públicas:
    sugerir_cuenta()              → Consulta el historial para una tripleta.
    registrar_confirmacion()      → Incrementa el contador de una cuenta.
    enriquecer_con_sugerencias()  → Aplica sugerencias a una lista de preasientos.
"""

import logging
from typing import Optional

from app.config import DB_PATH
from app.database import obtener_historial_cuenta, actualizar_historial_cuenta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapa: concepto de línea → tipo_linea normalizado usado como clave de historial
# ---------------------------------------------------------------------------
_CONCEPTO_A_TIPO: dict[str, str] = {
    "Base gravable":            "base",
    "Gasto/Costo":              "base",
    "Gasto":                    "base",
    "Gasto de nómina":          "base",
    "Gasto nómina":             "base",
    "Ingreso":                  "base",
    "IVA":                      "iva",
    "ICA":                      "ica",
    "IC":                       "ic",
    "INC":                      "inc",
    "Timbre":                   "timbre",
    "INC Bolsas":               "inc_bolsas",
    "IN Carbono":               "in_carbono",
    "IN Combustibles":          "in_combustibles",
    "IC Datos":                 "ic_datos",
    "ICL":                      "icl",
    "INPP":                     "inpp",
    "IBUA":                     "ibua",
    "ICUI":                     "icui",
    "Rete IVA":                 "rete_iva",
    "Rete Renta":               "rete_renta",
    "Rete ICA":                 "rete_ica",
}


def _tipo_linea_desde_concepto(concepto: str) -> str:
    """
    Deriva el tipo_linea canónico desde el texto del concepto de la línea.

    Por ejemplo 'Base gravable' → 'base', 'IVA' → 'iva'.
    Si el concepto no está mapeado, usa el propio texto en minúsculas como clave.
    """
    # Busca coincidencia exacta primero, luego parcial (para retenciones con prefijo)
    if concepto in _CONCEPTO_A_TIPO:
        return _CONCEPTO_A_TIPO[concepto]
    for key, val in _CONCEPTO_A_TIPO.items():
        if key in concepto:
            return val
    return concepto.lower().replace(" ", "_")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def sugerir_cuenta(
    clasificacion: str,
    nit_tercero: str,
    tipo_linea: str,
    db_path: str = DB_PATH,
) -> Optional[str]:
    """
    Retorna la cuenta contable sugerida para la tripleta dada, o None.

    Consulta historial_cuentas ordenado por usos DESC y retorna la cuenta
    con más confirmaciones pasadas. Si no hay historial, retorna None.

    Args:
        clasificacion: Clasificación del documento (e.g. 'FACTURA_COMPRA').
        nit_tercero:   NIT del emisor o receptor principal.
        tipo_linea:    Tipo canónico de línea (e.g. 'base', 'iva', 'rete_renta').
        db_path:       Ruta a la base de datos SQLite.

    Returns:
        Código de cuenta contable (str) o None si no hay historial.
    """
    cuenta = obtener_historial_cuenta(clasificacion, nit_tercero, tipo_linea, db_path)
    if cuenta:
        logger.debug(
            "Sugerencia: %s/%s/%s → %s",
            clasificacion, nit_tercero, tipo_linea, cuenta,
        )
    return cuenta


def registrar_confirmacion(
    clasificacion: str,
    nit_tercero: str,
    tipo_linea: str,
    cuenta: str,
    db_path: str = DB_PATH,
) -> None:
    """
    Registra (o incrementa) la confirmación de una cuenta en el historial.

    Llama a actualizar_historial_cuenta() que hace un UPSERT: inserta el
    registro si no existe o incrementa `usos` si ya existe.

    Args:
        clasificacion: Clasificación del documento.
        nit_tercero:   NIT del tercero.
        tipo_linea:    Tipo canónico de línea.
        cuenta:        Código de cuenta contable confirmada.
        db_path:       Ruta a la base de datos SQLite.
    """
    if not cuenta or cuenta == "[PENDIENTE]":
        return  # nunca registrar cuentas pendientes

    actualizar_historial_cuenta(clasificacion, nit_tercero, tipo_linea, cuenta, db_path)
    logger.debug(
        "Confirmación registrada: %s/%s/%s → %s",
        clasificacion, nit_tercero, tipo_linea, cuenta,
    )


def _texto_aprendizaje(preasiento) -> str:
    """Texto que describe el documento para el motor de aprendizaje por texto.

    Combina la clasificación y el nombre del tercero: así el motor puede
    generalizar a terceros NUNCA vistos con nombres similares (p. ej. dos
    empresas de "TRANSPORTES ..." suelen ir a la misma cuenta de fletes).
    """
    return f"{preasiento.clasificacion} {preasiento.tercero_nombre or ''}"


def enriquecer_con_sugerencias(
    preasientos: list,
    db_path: str = DB_PATH,
) -> list:
    """
    Reemplaza cuentas [PENDIENTE] con sugerencias del historial.

    Recorre todas las líneas de cada preasiento. Cuando encuentra una línea
    con es_pendiente=True y existe una sugerencia en el historial para la
    tripleta (clasificacion, tercero_nit, tipo_linea), reemplaza la cuenta
    '[PENDIENTE]' por la cuenta sugerida y marca la línea como es_sugerida=True.

    Si el historial exacto no conoce la tripleta (tercero nuevo), consulta el
    motor de aprendizaje generalizado (app/aprendizaje.py), que predice por
    similitud de texto sobre la clasificación y el nombre del tercero e incluye
    el conocimiento importado de fuentes externas.

    Las líneas que NO tienen cuenta pendiente NO son modificadas.

    Args:
        preasientos: Lista de PreasientoContable generados por preasiento.py.
        db_path:     Ruta a la base de datos SQLite.

    Returns:
        La misma lista de preasientos, con las líneas pendientes enriquecidas.
    """
    from app import aprendizaje

    total_sugeridas = 0

    for preasiento in preasientos:
        for linea in preasiento.lineas:
            if not linea.es_pendiente:
                continue

            tipo_linea = _tipo_linea_desde_concepto(linea.concepto)
            cuenta = sugerir_cuenta(
                clasificacion=preasiento.clasificacion,
                nit_tercero=preasiento.tercero_nit,
                tipo_linea=tipo_linea,
                db_path=db_path,
            )

            if not cuenta:
                # Fallback: motor de aprendizaje generalizado (predice por texto
                # para terceros nuevos y usa el conocimiento importado).
                pred = aprendizaje.predecir(
                    "radian", f"cuenta_{tipo_linea}",
                    _texto_aprendizaje(preasiento), db_path,
                )
                if pred:
                    cuenta = pred.valor

            if cuenta:
                linea.cuenta = cuenta
                linea.es_pendiente = False
                linea.es_sugerida = True
                total_sugeridas += 1
                logger.debug(
                    "CUFE %s línea %d: [PENDIENTE] → %s (sugerencia)",
                    preasiento.cufe[:20], linea.numero_linea, cuenta,
                )

        # Recalcular excepciones: quitar aviso de [PENDIENTE] si ya no quedan
        pendientes_restantes = [l for l in preasiento.lineas if l.es_pendiente]
        preasiento.excepciones = [
            e for e in preasiento.excepciones
            if "PENDIENTE" not in e
        ]
        if pendientes_restantes:
            preasiento.excepciones.append(
                f"{len(pendientes_restantes)} línea(s) con cuenta [PENDIENTE]"
            )

    if total_sugeridas:
        logger.info(
            "Motor de sugerencias: %d línea(s) enriquecida(s) automáticamente.",
            total_sugeridas,
        )

    return preasientos


def registrar_lote_confirmaciones(
    preasientos: list,
    db_path: str = DB_PATH,
) -> int:
    """
    Registra en el historial todas las líneas con cuenta real (no pendiente,
    no sugerida) de la lista de preasientos.

    Esto "alimenta" el motor con cada procesamiento exitoso: tanto el
    historial exacto (tabla historial_cuentas) como el motor de aprendizaje
    generalizado (texto de clasificación + nombre del tercero → cuenta).

    Args:
        preasientos: Lista de PreasientoContable ya procesados.
        db_path:     Ruta a la base de datos SQLite.

    Returns:
        Número de confirmaciones registradas.
    """
    from app import aprendizaje

    total = 0
    observaciones = []
    for preasiento in preasientos:
        for linea in preasiento.lineas:
            # Solo registrar cuentas reales (no pendientes, no sugeridas)
            if linea.es_pendiente or getattr(linea, "es_sugerida", False):
                continue
            if not linea.cuenta or linea.cuenta == "[PENDIENTE]":
                continue

            tipo_linea = _tipo_linea_desde_concepto(linea.concepto)
            registrar_confirmacion(
                clasificacion=preasiento.clasificacion,
                nit_tercero=preasiento.tercero_nit,
                tipo_linea=tipo_linea,
                cuenta=linea.cuenta,
                db_path=db_path,
            )
            observaciones.append({
                "modulo": "radian",
                "campo": f"cuenta_{tipo_linea}",
                "texto": _texto_aprendizaje(preasiento),
                "valor": linea.cuenta,
            })
            total += 1

    if observaciones:
        try:
            aprendizaje.aprender_lote(observaciones, db_path)
        except Exception:
            logger.exception("No se pudo alimentar el motor de aprendizaje.")

    logger.info("Historial actualizado con %d confirmación(es).", total)
    return total
