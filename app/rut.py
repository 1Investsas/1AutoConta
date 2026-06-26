"""
Lectura del RUT de la DIAN (formato PDF) para alimentar el maestro de terceros.

El RUT (Registro Único Tributario) es un formulario de la DIAN con casillas
numeradas y posiciones fijas. Este módulo extrae los datos del tercero desde
la **primera hoja** del PDF —que contiene la identificación y la ubicación—
tanto para **persona jurídica** como para **persona natural**.

El extractor es **posicional**: ubica cada valor por su coordenada dentro del
formulario (que es estable porque la DIAN lo genera siempre con el mismo
diseño), lo que es más robusto que leer el texto en orden lineal (donde
etiquetas y valores se entremezclan).

Uso:
    from app.rut import parsear_rut_pdf
    datos = parsear_rut_pdf("RUT.pdf")
    # datos = {"nit": "901331657", "dv": "7", "tipo_persona": "juridica",
    #          "nombre": "1 INVERSIONES ESTRATEGICAS ...", ...}
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class RUTParseError(Exception):
    """Error al leer un PDF de RUT (no es un RUT, está dañado o ilegible)."""


# ---------------------------------------------------------------------------
# Geometría del formulario RUT (hoja principal · 612 x 792 pt)
# ---------------------------------------------------------------------------
# Cada casilla del RUT ocupa una posición fija. Definimos las filas por su
# coordenada vertical (``top``) y las columnas por su rango horizontal (``x``).
# Los valores se rellenan en una fila ligeramente por debajo de su etiqueta.
# Tolerancia vertical para agrupar las palabras de una misma fila de valores.
_TOL = 6.0

# Filas de valores (coordenada ``top`` aproximada del texto rellenado).
_FILA_NIT       = 180   # casillas 5 (NIT) y 6 (DV) + 12 (dirección seccional)
_FILA_DOC       = 216   # casillas 24 (tipo contrib.), 25 (tipo doc), 26 (nº id)
_FILA_NOMBRES   = 264   # casillas 31-34 (apellidos y nombres) — persona natural
_FILA_RAZON     = 288   # casilla 35 (razón social) — persona jurídica
_FILA_COMERCIAL = 312   # casillas 36 (nombre comercial) y 37 (sigla)
_FILA_UBICACION = 348   # casillas 38-40 (país / departamento / ciudad)
_FILA_DIRECCION = 373   # casilla 41 (dirección principal)
_FILA_CORREO    = 384   # casilla 42 (correo electrónico)
_FILA_TELEFONO  = 398   # casillas 44 (teléfono 1) y 45 (teléfono 2)


def _palabra_en(palabras: list[dict], top: float, x_lo: float, x_hi: float) -> list[dict]:
    """Devuelve las palabras cuya posición cae dentro de la banda indicada.

    Una palabra pertenece a la banda si su ``top`` está dentro de ``±_TOL`` de
    la fila buscada y el centro horizontal de la palabra está en ``[x_lo, x_hi)``.
    El resultado se ordena de izquierda a derecha.
    """
    out = []
    for w in palabras:
        centro = (w["x0"] + w["x1"]) / 2.0
        if abs(w["top"] - top) <= _TOL and x_lo <= centro < x_hi:
            out.append(w)
    out.sort(key=lambda w: w["x0"])
    return out


def _texto(palabras: list[dict], descartar_codigos: bool = False) -> str:
    """Une el texto de las palabras dadas (ya ordenadas) en una sola cadena.

    Si ``descartar_codigos`` es True, ignora las palabras que son un único
    dígito: en el RUT esos dígitos sueltos son los **códigos** de las casillas
    (país, departamento, ciudad…) que acompañan al texto y que no forman parte
    del nombre legible.
    """
    partes = []
    for w in palabras:
        t = w["text"].strip()
        if not t:
            continue
        if descartar_codigos and len(t) == 1 and t.isdigit():
            continue
        partes.append(t)
    return " ".join(partes).strip()


def _digitos(palabras: list[dict]) -> str:
    """Concatena los dígitos de las palabras dadas (ya ordenadas por x)."""
    return "".join(w["text"].strip() for w in palabras if w["text"].strip().isdigit())


def parsear_rut_words(palabras: list[dict], texto_completo: str = "") -> dict:
    """Extrae los datos del tercero desde las palabras de la hoja principal.

    Args:
        palabras:        Lista de palabras con coordenadas (formato de
                         ``pdfplumber.Page.extract_words``: claves ``text``,
                         ``x0``, ``x1``, ``top``).
        texto_completo:  Texto plano completo de la página (para detectar
                         responsabilidades como la de IVA). Opcional.

    Returns:
        Diccionario normalizado con los datos del tercero.

    Raises:
        RUTParseError: Si no se reconoce un NIT válido en el documento.
    """
    # --- Casilla 5/6: NIT + dígito de verificación -------------------------
    # Los dígitos del NIT y el DV son palabras de un solo carácter en la franja
    # izquierda de la fila. El último dígito es el DV; el resto, el NIT.
    nit_dv = _digitos(_palabra_en(palabras, _FILA_NIT, 78, 205))
    if len(nit_dv) < 2:
        raise RUTParseError(
            "No se reconoció el NIT en el documento. "
            "Verifica que sea un PDF del RUT de la DIAN."
        )
    nit, dv = nit_dv[:-1], nit_dv[-1]

    # --- Casilla 12: dirección seccional ----------------------------------
    direccion_seccional = _texto(
        _palabra_en(palabras, _FILA_NIT, 200, 445), descartar_codigos=True
    )

    # --- Casilla 24: tipo de contribuyente (jurídica / natural) ------------
    tipo_contribuyente = _texto(_palabra_en(palabras, _FILA_DOC, 20, 175))
    es_natural = "natural" in tipo_contribuyente.lower()
    es_juridica = "jur" in tipo_contribuyente.lower()
    # Si la casilla no es legible, se infiere por la presencia de razón social.
    if not es_natural and not es_juridica:
        razon_tmp = _texto(_palabra_en(palabras, _FILA_RAZON, 20, 600))
        es_juridica = bool(razon_tmp)
        es_natural = not es_juridica
    tipo_persona = "natural" if es_natural else "juridica"

    datos: dict = {
        "nit": nit,
        "dv": dv,
        "tipo_persona": tipo_persona,
        "tipo_contribuyente": tipo_contribuyente,
        "direccion_seccional": direccion_seccional,
        "razon_social": "",
        "primer_apellido": "",
        "segundo_apellido": "",
        "primer_nombre": "",
        "otros_nombres": "",
        "nombre_comercial": "",
        "sigla": "",
    }

    if tipo_persona == "natural":
        # --- Casilla 25: tipo de documento --------------------------------
        tipo_doc = _texto(
            _palabra_en(palabras, _FILA_DOC, 184, 285), descartar_codigos=True
        )
        datos["tipo_documento"] = tipo_doc
        datos["tipo_identificacion"] = _mapear_tipo_id(tipo_doc, es_natural=True)
        # --- Casillas 31-34: apellidos y nombres --------------------------
        datos["primer_apellido"]  = _texto(_palabra_en(palabras, _FILA_NOMBRES, 20, 155))
        datos["segundo_apellido"] = _texto(_palabra_en(palabras, _FILA_NOMBRES, 155, 282))
        datos["primer_nombre"]    = _texto(_palabra_en(palabras, _FILA_NOMBRES, 282, 407))
        datos["otros_nombres"]    = _texto(_palabra_en(palabras, _FILA_NOMBRES, 407, 600))
        datos["nombre"] = " ".join(
            p for p in [
                datos["primer_apellido"], datos["segundo_apellido"],
                datos["primer_nombre"], datos["otros_nombres"],
            ] if p
        ).strip()
    else:
        # --- Casilla 35: razón social -------------------------------------
        datos["razon_social"] = _texto(_palabra_en(palabras, _FILA_RAZON, 20, 600))
        # --- Casillas 36/37: nombre comercial y sigla ---------------------
        datos["nombre_comercial"] = _texto(_palabra_en(palabras, _FILA_COMERCIAL, 20, 340))
        datos["sigla"]            = _texto(_palabra_en(palabras, _FILA_COMERCIAL, 340, 600))
        datos["tipo_documento"] = "NIT"
        datos["tipo_identificacion"] = "NIT"
        datos["nombre"] = datos["razon_social"]

    # --- Casillas 38-40: ubicación (país / departamento / ciudad) ----------
    datos["pais"]        = _texto(_palabra_en(palabras, _FILA_UBICACION, 20, 165),
                                  descartar_codigos=True)
    datos["departamento"] = _texto(_palabra_en(palabras, _FILA_UBICACION, 195, 370),
                                   descartar_codigos=True)
    datos["ciudad"]      = _texto(_palabra_en(palabras, _FILA_UBICACION, 388, 560),
                                  descartar_codigos=True)

    # --- Casilla 41: dirección principal -----------------------------------
    datos["direccion"] = _texto(_palabra_en(palabras, _FILA_DIRECCION, 20, 600))

    # --- Casilla 42: correo electrónico ------------------------------------
    correo = ""
    for w in _palabra_en(palabras, _FILA_CORREO, 95, 600):
        if "@" in w["text"]:
            correo = w["text"].strip()
            break
    datos["correo"] = correo

    # --- Casillas 44/45: teléfonos -----------------------------------------
    # Las bandas arrancan después del dígito de la etiqueta ("Teléfono 1" tiene
    # un "1" en x≈237 y "Teléfono 2" un "2" en x≈434) para no capturarlo.
    tel1 = _digitos(_palabra_en(palabras, _FILA_TELEFONO, 255, 397))
    tel2 = _digitos(_palabra_en(palabras, _FILA_TELEFONO, 450, 600))
    datos["telefono1"] = tel1
    datos["telefono2"] = tel2
    datos["telefono"]  = tel1 or tel2

    # --- Régimen de IVA (best-effort, desde el texto completo) -------------
    datos["responsable_iva"] = _detectar_responsable_iva(texto_completo)
    datos["regimen_iva"] = (
        "Responsable de IVA" if datos["responsable_iva"] else "No responsable de IVA"
    )

    return datos


# Tipos de documento del RUT → código corto usado en el maestro de terceros.
_TIPOS_ID = [
    ("cédula de ciud",  "CC"),
    ("cedula de ciud",  "CC"),
    ("ciudadan",        "CC"),
    ("cédula de extr",  "CE"),
    ("extranjer",       "CE"),
    ("pasaporte",       "PA"),
    ("tarjeta de ident","TI"),
    ("nit",             "NIT"),
]


def _mapear_tipo_id(tipo_doc: str, es_natural: bool) -> str:
    """Traduce el tipo de documento del RUT al código corto (CC, CE, NIT…)."""
    t = (tipo_doc or "").lower()
    for fragmento, codigo in _TIPOS_ID:
        if fragmento in t:
            return codigo
    return "CC" if es_natural else "NIT"


def _detectar_responsable_iva(texto_completo: str) -> bool:
    """Detecta si el RUT incluye la responsabilidad de IVA (casilla 48)."""
    if not texto_completo:
        return False
    t = texto_completo.lower()
    # En el RUT la responsabilidad de IVA aparece como
    # "48 - Impuesto sobre las ventas - IVA".
    return bool(re.search(r"impuesto sobre las ventas", t)) or "- iva" in t


def parsear_rut_pdf(filepath: str) -> dict:
    """Lee un PDF de RUT de la DIAN y devuelve los datos del tercero.

    Solo se procesa la **primera hoja** (identificación + ubicación).

    Args:
        filepath: Ruta local al PDF del RUT.

    Returns:
        Diccionario con los datos del tercero (ver ``parsear_rut_words``).

    Raises:
        RUTParseError: Si el PDF no se puede leer o no parece un RUT.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RUTParseError(
            "Falta la dependencia 'pdfplumber' para leer PDFs del RUT. "
            "Instálala con: pip install pdfplumber"
        ) from exc

    try:
        with pdfplumber.open(filepath) as pdf:
            if not pdf.pages:
                raise RUTParseError("El PDF del RUT no tiene páginas.")
            page = pdf.pages[0]
            palabras = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            texto = page.extract_text() or ""
    except RUTParseError:
        raise
    except Exception as exc:
        raise RUTParseError(f"No se pudo leer el PDF del RUT: {exc}") from exc

    datos = parsear_rut_words(palabras, texto_completo=texto)
    logger.info(
        "RUT leído: NIT=%s-%s tipo=%s nombre=%s",
        datos.get("nit"), datos.get("dv"),
        datos.get("tipo_persona"), datos.get("nombre"),
    )
    return datos
