"""
Validación del *tipo* de un archivo maestro.

Los tres maestros de una empresa (terceros, plan de cuentas y comprobantes) son
todos archivos ``.xlsx`` con un nombre parecido, por lo que es fácil subir uno
en la casilla equivocada (p. ej. el Plan de Cuentas en la casilla de Terceros).
Cuando eso pasa, el módulo de Terceros «parece usar el plan de cuentas»: al
descargar el maestro de terceros se obtiene en realidad el de cuentas.

Este módulo mira los **encabezados de la fila 7** y clasifica el archivo por sus
columnas, para poder rechazar archivos puestos en la casilla equivocada con un
mensaje claro.
"""

from __future__ import annotations

import io
import logging
import unicodedata
from typing import Optional

from app.config import FILA_ENCABEZADOS_MAESTROS

logger = logging.getLogger(__name__)


def _normalizar(texto: object) -> str:
    """Minúsculas, sin tildes y espacios colapsados (para comparar encabezados)."""
    s = str(texto or "").strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(s.split())


# Encabezados (normalizados) que identifican a un tercero (NIT/cédula/nombre).
_ID_TERCERO = {
    "identificacion", "nit", "numero de identificacion", "numero identificacion",
    "nro identificacion", "no identificacion", "documento", "numero de documento",
    "cedula", "nit o cedula", "identificacion o nit", "nombre tercero",
}
# Encabezados (normalizados) propios del plan de cuentas contables.
_FIRMA_CUENTAS = {"nivel agrupacion", "naturaleza"}
# Encabezados (normalizados) propios del catálogo de comprobantes.
_FIRMA_COMPROBANTES = {"tipo de comprobante", "tipo comprobante", "comprobante"}

# Etiquetas legibles por tipo (para los mensajes de error).
ETIQUETA_MAESTRO = {
    "terceros": "Listado de Terceros",
    "cuentas": "Plan de Cuentas Contables",
    "comprobantes": "Tipos de comprobante contable",
}


def leer_encabezados(contenido: bytes) -> list[str]:
    """Lee los encabezados (fila 7) de un Excel maestro a partir de sus bytes.

    Devuelve la lista de nombres de columna, o ``[]`` si el archivo no se puede
    leer (en cuyo caso la validación es permisiva: no bloquea la subida).
    """
    try:
        import pandas as pd
        df = pd.read_excel(
            io.BytesIO(contenido), header=FILA_ENCABEZADOS_MAESTROS, nrows=0
        )
        return [str(c).strip() for c in df.columns]
    except Exception:
        logger.debug("No se pudieron leer los encabezados del maestro.", exc_info=True)
        return []


def clasificar_encabezados(encabezados: list[str]) -> str:
    """Clasifica un maestro por sus encabezados.

    Returns:
        ``"terceros"``, ``"cuentas"``, ``"comprobantes"`` o ``"desconocido"``.
    """
    n = {_normalizar(h) for h in encabezados if h}
    tiene_id_tercero = bool(n & _ID_TERCERO)
    es_cuentas = bool(n & _FIRMA_CUENTAS) or ("codigo" in n and "activo" in n)
    es_comprobantes = bool(n & _FIRMA_COMPROBANTES)

    # El identificador de tercero manda: un maestro de terceros nunca trae
    # "Nivel agrupación"; el plan de cuentas nunca trae una columna de NIT.
    if tiene_id_tercero and not es_cuentas:
        return "terceros"
    if es_cuentas and not tiene_id_tercero:
        return "cuentas"
    if es_comprobantes:
        return "comprobantes"
    if tiene_id_tercero:
        return "terceros"
    if es_cuentas:
        return "cuentas"
    return "desconocido"


def clasificar_maestro(contenido: bytes) -> str:
    """Clasifica el archivo maestro (bytes) por sus encabezados."""
    return clasificar_encabezados(leer_encabezados(contenido))


def validar_maestro(tipo_esperado: str, contenido: bytes) -> Optional[str]:
    """Valida que un archivo corresponde al tipo de maestro esperado.

    Args:
        tipo_esperado: ``"terceros"``, ``"cuentas"`` o ``"comprobantes"``.
        contenido:     Bytes del archivo subido.

    Returns:
        ``None`` si el archivo es del tipo esperado (o no se puede determinar);
        un mensaje de error claro si el archivo es de **otro** tipo de maestro.
    """
    encabezados = leer_encabezados(contenido)
    if not encabezados:
        # No se pudo leer: no bloquear (fail-open) para no rechazar archivos
        # válidos por una incompatibilidad puntual de lectura.
        return None

    clase = clasificar_encabezados(encabezados)
    if clase == "desconocido" or clase == tipo_esperado:
        return None

    esperado = ETIQUETA_MAESTRO.get(tipo_esperado, tipo_esperado)
    encontrado = ETIQUETA_MAESTRO.get(clase, clase)
    return (
        f"El archivo que subiste en la casilla «{esperado}» parece ser en realidad "
        f"el «{encontrado}» (por sus columnas en la fila 7). Verifica que estás "
        f"subiendo el archivo correcto en cada casilla."
    )
