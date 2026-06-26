"""
Lectura del certificado bancario de Bancolombia (PDF) para registrar las
cuentas bancarias de un tercero al que se le realizan pagos.

El certificado bancario es una constancia que el banco emite a nombre de un
titular (persona **jurídica** o **natural**) en la que enumera los productos
financieros (cuentas) que tiene con la entidad. Este módulo extrae de ese PDF:

- El **banco** que expide la constancia (p. ej. ``BANCOLOMBIA S.A.``).
- El **titular** de la(s) cuenta(s) y su documento (NIT para jurídica, cédula
  para natural).
- La(s) **cuenta(s)**: tipo de producto, número, fecha de apertura y estado.

La lectura es **basada en texto**: el certificado de Bancolombia se genera con
un texto limpio y ordenado, de modo que un puñado de expresiones regulares es
más robusto y simple que un extractor posicional. El mismo diseño sirve para
persona jurídica y natural —solo cambia el tipo de documento del titular—.

Uso:
    from app.certificado_bancario import parsear_certificado_pdf
    datos = parsear_certificado_pdf("Certificado_Bancario.pdf")
    # datos = {"banco": "BANCOLOMBIA S.A.", "titular": "CHICA BOTERO SAS",
    #          "tipo_documento": "NIT", "numero_documento": "900669897",
    #          "tipo_persona": "juridica",
    #          "cuentas": [{"tipo_producto": "CUENTA DE AHORROS",
    #                       "numero_cuenta": "55116315903",
    #                       "fecha_apertura": "2013/11/26", "estado": "ACTIVA"}]}
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class CertificadoBancarioError(Exception):
    """Error al leer un certificado bancario (no lo es, está dañado o ilegible)."""


# ---------------------------------------------------------------------------
# Expresiones regulares
# ---------------------------------------------------------------------------
# Banco que expide la constancia: "BANCOLOMBIA S.A. se permite informar que ...".
_RE_BANCO = re.compile(
    r"^\s*(?P<banco>.+?)\s+se permite informar",
    re.IGNORECASE | re.MULTILINE,
)

# Titular + documento: "... informar que <NOMBRE> identificado(a) con <TIPO> <Nº>".
# Se usa DOTALL porque el número del documento puede quedar en la línea siguiente
# (el texto del PDF envuelve la línea tras el tipo de documento).
_RE_TITULAR = re.compile(
    r"informar que\s+(?P<nombre>.+?)\s+identificad[oa]\(a\)\s+con\s+"
    r"(?P<tipo>[A-Za-zÁÉÍÓÚÑ\.]+)\s+(?P<numero>[\d\.\,]+)",
    re.IGNORECASE | re.DOTALL,
)

# Línea de producto de la tabla:
#   "CUENTA DE AHORROS 55116315903 2013/11/26 ACTIVA"
# El número de fecha (aaaa/mm/dd) es muy específico, por lo que evita falsos
# positivos con otras líneas del documento (teléfonos, NITs, etc.).
_RE_PRODUCTO = re.compile(
    r"^(?P<tipo>.+?)\s+(?P<numero>\d{5,})\s+"
    r"(?P<fecha>\d{4}/\d{2}/\d{2})\s+(?P<estado>[A-Za-zÁÉÍÓÚÑ]+)\s*$",
    re.MULTILINE,
)


# Tipo de documento textual → código corto usado en el maestro de terceros.
_TIPOS_DOC = [
    ("nit", "NIT"),
    ("cédula de extr", "CE"),
    ("cedula de extr", "CE"),
    ("c.e", "CE"),
    ("cédula de ciud", "CC"),
    ("cedula de ciud", "CC"),
    ("c.c", "CC"),
    ("cc", "CC"),
    ("pasaporte", "PA"),
    ("pa", "PA"),
    ("tarjeta de ident", "TI"),
    ("ti", "TI"),
    ("ce", "CE"),
]


def _solo_digitos(valor: object) -> str:
    """Deja solo los dígitos de un identificador (igual que el importador)."""
    return "".join(c for c in str(valor or "") if c.isdigit())


def _normalizar_tipo_doc(tipo: str) -> str:
    """Traduce el tipo de documento del certificado al código corto (NIT, CC, CE…)."""
    t = (tipo or "").strip().lower().rstrip(".")
    for fragmento, codigo in _TIPOS_DOC:
        if t == fragmento or t.startswith(fragmento):
            return codigo
    return (tipo or "").strip().upper()


def parsear_certificado_texto(texto: str) -> dict:
    """Extrae banco, titular y cuentas desde el texto plano del certificado.

    Args:
        texto: Texto completo de la primera página del certificado.

    Returns:
        Diccionario normalizado con ``banco``, ``titular``, ``tipo_documento``,
        ``numero_documento``, ``tipo_persona`` y ``cuentas`` (lista).

    Raises:
        CertificadoBancarioError: Si no se reconoce el titular o ninguna cuenta.
    """
    texto = texto or ""

    m_tit = _RE_TITULAR.search(texto)
    if not m_tit:
        raise CertificadoBancarioError(
            "No se reconoció el titular de la cuenta. "
            "Verifica que sea un certificado bancario de Bancolombia en PDF."
        )

    # El nombre puede arrastrar saltos de línea: normalizar espacios.
    titular = " ".join(m_tit.group("nombre").split()).strip()
    tipo_documento = _normalizar_tipo_doc(m_tit.group("tipo"))
    numero_documento = _solo_digitos(m_tit.group("numero"))
    tipo_persona = "juridica" if tipo_documento == "NIT" else "natural"

    m_banco = _RE_BANCO.search(texto)
    banco = " ".join(m_banco.group("banco").split()).strip() if m_banco else ""

    cuentas: list[dict] = []
    for m in _RE_PRODUCTO.finditer(texto):
        cuentas.append({
            "tipo_producto": " ".join(m.group("tipo").split()).strip(),
            "numero_cuenta": m.group("numero").strip(),
            "fecha_apertura": m.group("fecha").strip(),
            "estado": m.group("estado").strip().upper(),
        })

    if not cuentas:
        raise CertificadoBancarioError(
            "No se reconoció ninguna cuenta en el certificado. "
            "Verifica que el PDF incluya la tabla de productos del banco."
        )

    return {
        "banco": banco,
        "titular": titular,
        "tipo_documento": tipo_documento,
        "numero_documento": numero_documento,
        "tipo_persona": tipo_persona,
        "cuentas": cuentas,
    }


def parsear_certificado_pdf(filepath: str) -> dict:
    """Lee un PDF de certificado bancario y devuelve sus datos.

    Solo se procesa la **primera página** (titular + tabla de productos).

    Args:
        filepath: Ruta local al PDF del certificado.

    Returns:
        Diccionario con los datos del certificado (ver ``parsear_certificado_texto``).

    Raises:
        CertificadoBancarioError: Si el PDF no se puede leer o no parece un
            certificado bancario.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise CertificadoBancarioError(
            "Falta la dependencia 'pdfplumber' para leer el certificado bancario. "
            "Instálala con: pip install pdfplumber"
        ) from exc

    try:
        with pdfplumber.open(filepath) as pdf:
            if not pdf.pages:
                raise CertificadoBancarioError("El PDF del certificado no tiene páginas.")
            # ``x_tolerance`` bajo: el certificado usa un interletrado muy ajustado
            # en los párrafos de texto y con la tolerancia por defecto (3) pdfplumber
            # pega las palabras ("BANCOLOMBIAS.A.sepermite..."). Con ~1.5 recupera los
            # espacios sin partir palabras, y la tabla de productos sigue intacta.
            texto = pdf.pages[0].extract_text(x_tolerance=1.5) or ""
    except CertificadoBancarioError:
        raise
    except Exception as exc:
        raise CertificadoBancarioError(
            f"No se pudo leer el PDF del certificado bancario: {exc}"
        ) from exc

    datos = parsear_certificado_texto(texto)
    logger.info(
        "Certificado bancario leído: banco=%s titular=%s doc=%s-%s cuentas=%d",
        datos.get("banco"), datos.get("titular"),
        datos.get("tipo_documento"), datos.get("numero_documento"),
        len(datos.get("cuentas", [])),
    )
    return datos
