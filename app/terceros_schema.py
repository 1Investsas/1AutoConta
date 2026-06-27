"""
Esquema del maestro de terceros — estructura «Modelo de importación Siigo Nube».

Este módulo es la **única fuente de verdad** de la estructura del maestro de
terceros del sistema. El maestro pasó de la antigua planilla «Búsqueda de
terceros» (11 columnas, encabezados en la fila 7) a la estructura mucho más
completa del **Modelo de importación de terceros de Siigo Nube**: 29 columnas
con los encabezados en la **fila 1** y los datos desde la fila 2.

Regla de oro — **formato de las casillas**
------------------------------------------
En el modelo de Siigo *todas* las celdas tienen formato de **Texto** (``"@"``).
Esto es imprescindible: las identificaciones, los dígitos de verificación y los
códigos DANE/ISO (país, departamento, ciudad, tipo de identificación, código
postal…) llevan ceros a la izquierda y no deben convertirse nunca a número
(perderían el cero o se mostrarían en notación científica). Por eso **cada vez**
que el sistema escribe o actualiza una celda del maestro, se conserva el formato
de texto del archivo (ver ``aplicar_formato_texto`` y el escritor en
``app.terceros_rut``).

El módulo expone además:

- ``COLUMNAS_SIIGO``: los 29 encabezados en orden, tal cual el modelo de Siigo.
- El mapa de *campos canónicos* → encabezado, con alias para reconocer las
  columnas en archivos reales (admite el modelo Siigo y la planilla antigua).
- Conversores a los códigos de Siigo (tipo de identificación, régimen de IVA,
  país…) para llenar la estructura nueva con información completa.
- Utilidades para detectar la fila de encabezados y mapear columnas, usadas por
  el lector (``app.importador``), el escritor (``app.terceros_rut``) y la
  validación de maestros (``app.maestros``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

# Formato de celda «Texto» de Excel. Conserva ceros a la izquierda y evita la
# notación científica en identificaciones y códigos.
FORMATO_TEXTO = "@"

# Fila (1-based de Excel) de los encabezados en el modelo de Siigo Nube.
FILA_ENCABEZADOS_SIIGO = 1

# Ancho por defecto de las columnas del modelo de Siigo (la de «Código Sucursal»
# es más angosta en el archivo original).
ANCHO_COLUMNA_DEFAULT = 30.0
ANCHO_COLUMNA_SUCURSAL = 10.0


# ---------------------------------------------------------------------------
# Definición de campos: (campo canónico, encabezado Siigo, ancho, alias)
# ---------------------------------------------------------------------------
# El orden de esta lista ES el orden de las columnas al crear un archivo nuevo
# (debe coincidir exactamente con el modelo de importación de Siigo Nube).
#
# Los ``alias`` (normalizados) se comparan contra los encabezados de un archivo
# existente para ubicar cada columna; incluyen tanto los del modelo nuevo como
# los de la planilla antigua, de modo que el upsert siga funcionando sobre
# archivos previos sin perder datos.
_CAMPOS: list[tuple[str, str, float, tuple[str, ...]]] = [
    ("identificacion", "Identificación (Obligatorio)", ANCHO_COLUMNA_DEFAULT,
     ("identificacion", "nit", "numero de identificacion", "numero identificacion",
      "nro identificacion", "documento", "cedula", "no identificacion",
      "numero de documento", "nit o cedula", "identificacion o nit")),
    ("dv", "Dígito de verificación", ANCHO_COLUMNA_DEFAULT,
     ("digito de verificacion", "digito verificacion", "dv", "digito")),
    ("codigo_sucursal", "Código Sucursal", ANCHO_COLUMNA_SUCURSAL,
     ("codigo sucursal", "sucursal", "cod sucursal")),
    ("tipo_identificacion", "Tipo identificación (Obligatorio)", ANCHO_COLUMNA_DEFAULT,
     ("tipo identificacion", "tipo de identificacion", "tipo de documento",
      "tipo documento", "tipo doc", "tipo id")),
    ("tipo", "Tipo (Obligatorio)", ANCHO_COLUMNA_DEFAULT,
     ("tipo", "tipo tercero", "tipo de tercero", "tipo persona")),
    ("razon_social", "Razón social (Obligatorio)", ANCHO_COLUMNA_DEFAULT,
     ("razon social", "nombre tercero", "nombre o razon social", "nombre completo",
      "tercero", "nombre del tercero", "razon social o nombre")),
    ("nombres", "Nombres del tercero (Obligatorio)", ANCHO_COLUMNA_DEFAULT,
     ("nombres del tercero", "nombres", "nombres tercero")),
    ("apellidos", "Apellidos del tercero (Obligatorio)", ANCHO_COLUMNA_DEFAULT,
     ("apellidos del tercero", "apellidos", "apellidos tercero")),
    ("nombre_comercial", "Nombre Comercial", ANCHO_COLUMNA_DEFAULT,
     ("nombre comercial",)),
    ("direccion", "Dirección", ANCHO_COLUMNA_DEFAULT,
     ("direccion", "direccion principal", "dir")),
    ("codigo_pais", "Código país", ANCHO_COLUMNA_DEFAULT,
     ("codigo pais", "pais", "cod pais")),
    ("codigo_departamento", "Código departamento/estado", ANCHO_COLUMNA_DEFAULT,
     ("codigo departamento/estado", "codigo departamento", "codigo estado",
      "departamento", "depto", "cod departamento")),
    ("codigo_ciudad", "Código ciudad", ANCHO_COLUMNA_DEFAULT,
     ("codigo ciudad", "codigo municipio", "cod ciudad")),
    ("indicativo_telefono", "Indicativo teléfono principal", ANCHO_COLUMNA_DEFAULT,
     ("indicativo telefono principal", "indicativo telefono", "indicativo")),
    ("telefono", "Teléfono principal", ANCHO_COLUMNA_DEFAULT,
     ("telefono principal", "telefono", "telefono 1", "tel", "celular", "movil")),
    ("extension_telefono", "Extensión teléfono principal", ANCHO_COLUMNA_DEFAULT,
     ("extension telefono principal", "extension telefono", "extension")),
    ("regimen_iva", "Tipo de régimen IVA", ANCHO_COLUMNA_DEFAULT,
     ("tipo de regimen iva", "regimen iva", "regimen", "responsabilidad iva",
      "responsable de iva")),
    ("responsabilidad_fiscal", "Código Responsabilidad fiscal", ANCHO_COLUMNA_DEFAULT,
     ("codigo responsabilidad fiscal", "responsabilidad fiscal")),
    ("codigo_postal", "Código Postal", ANCHO_COLUMNA_DEFAULT,
     ("codigo postal",)),
    ("contacto_nombres", "Nombres contacto principal", ANCHO_COLUMNA_DEFAULT,
     ("nombres contacto principal", "nombres contacto", "nombres del contacto")),
    ("contacto_apellidos", "Apellidos contacto principal", ANCHO_COLUMNA_DEFAULT,
     ("apellidos contacto principal", "apellidos contacto")),
    ("contacto_indicativo", "Indicativo teléfono contacto principal", ANCHO_COLUMNA_DEFAULT,
     ("indicativo telefono contacto principal", "indicativo contacto")),
    ("contacto_telefono", "Teléfono contacto principal", ANCHO_COLUMNA_DEFAULT,
     ("telefono contacto principal", "telefono contacto")),
    ("contacto_extension", "Extensión teléfono contacto principal", ANCHO_COLUMNA_DEFAULT,
     ("extension telefono contacto principal", "extension contacto")),
    ("correo", "Correo electrónico contacto principal", ANCHO_COLUMNA_DEFAULT,
     ("correo electronico contacto principal", "correo electronico", "correo",
      "email", "e-mail", "mail", "correo contacto")),
    ("otros", "Otros", ANCHO_COLUMNA_DEFAULT,
     ("otros",)),
    ("clientes", "Clientes", ANCHO_COLUMNA_DEFAULT,
     ("clientes", "cliente")),
    ("proveedor", "Proveedor", ANCHO_COLUMNA_DEFAULT,
     ("proveedor", "proveedores")),
    ("estado", "Estado", ANCHO_COLUMNA_DEFAULT,
     ("estado",)),
]

# Encabezados del modelo Siigo, en orden (para crear un archivo nuevo).
COLUMNAS_SIIGO: list[str] = [enc for _campo, enc, _ancho, _alias in _CAMPOS]

# Ancho de cada columna por encabezado.
ANCHOS_SIIGO: dict[str, float] = {enc: ancho for _c, enc, ancho, _a in _CAMPOS}

# Encabezado por defecto de cada campo canónico.
CAMPO_A_ENCABEZADO: dict[str, str] = {c: enc for c, enc, _ancho, _a in _CAMPOS}

# Campos obligatorios del modelo (los marcados «(Obligatorio)» en Siigo).
CAMPOS_OBLIGATORIOS: tuple[str, ...] = (
    "identificacion", "tipo_identificacion", "tipo", "razon_social",
)


def _normalizar_base(texto: object) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    s = str(texto or "").strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(s.split())


def normalizar_encabezado(texto: object) -> str:
    """Normaliza un encabezado para comparar columnas.

    Además de minúsculas/sin tildes, descarta las anotaciones entre paréntesis
    (p. ej. el «(Obligatorio)» del modelo de Siigo), de modo que
    ``"Identificación (Obligatorio)"`` y ``"Identificación"`` se reconozcan como
    la misma columna.
    """
    s = re.sub(r"\([^)]*\)", " ", str(texto or ""))
    return _normalizar_base(s)


# Mapa alias normalizado → campo canónico. Incluye el propio encabezado Siigo.
_ALIAS_A_CAMPO: dict[str, str] = {}
for _campo, _enc, _ancho, _aliases in _CAMPOS:
    _ALIAS_A_CAMPO[normalizar_encabezado(_enc)] = _campo
    for _a in _aliases:
        _ALIAS_A_CAMPO.setdefault(normalizar_encabezado(_a), _campo)

# Alias (normalizados) que identifican la columna de identificación del tercero.
ALIAS_IDENTIFICACION: frozenset[str] = frozenset(
    a for a, c in _ALIAS_A_CAMPO.items() if c == "identificacion"
)


def campo_de_encabezado(encabezado: object) -> Optional[str]:
    """Devuelve el campo canónico al que corresponde un encabezado, o ``None``."""
    return _ALIAS_A_CAMPO.get(normalizar_encabezado(encabezado))


def solo_digitos(valor: object) -> str:
    """Deja solo los dígitos de un valor (para identificaciones y códigos)."""
    return "".join(c for c in str(valor or "") if c.isdigit())


# ---------------------------------------------------------------------------
# Conversores a los códigos del modelo de Siigo
# ---------------------------------------------------------------------------

# Tipo de identificación: código corto del RUT → código de Siigo Nube.
_TIPO_ID_SIIGO: dict[str, str] = {
    "RC": "11",   # Registro civil
    "TI": "12",   # Tarjeta de identidad
    "CC": "13",   # Cédula de ciudadanía
    "TE": "21",   # Tarjeta de extranjería
    "CE": "22",   # Cédula de extranjería
    "NIT": "31",  # NIT
    "PA": "41",   # Pasaporte
    "PP": "41",
    "DE": "42",   # Documento de identificación del exterior
    "NITE": "50", # NIT de otro país
    "FOREIGN": "50",
    "NUIP": "91", # NUIP
}


def codigo_tipo_identificacion(tipo: object, *, es_natural: bool = False) -> str:
    """Traduce el tipo de identificación al código de Siigo (13, 31, 50…).

    Acepta tanto el código corto del RUT (``"CC"``, ``"NIT"``…) como un código de
    Siigo ya numérico (que se devuelve tal cual). Si no se reconoce, usa el
    valor por defecto según el tipo de persona (cédula para natural, NIT para
    jurídica).
    """
    t = str(tipo or "").strip().upper()
    if t.isdigit():
        return t
    if t in _TIPO_ID_SIIGO:
        return _TIPO_ID_SIIGO[t]
    return "13" if es_natural else "31"


def tipo_tercero_siigo(es_natural: bool) -> str:
    """Valor de la columna «Tipo (Obligatorio)»: ``Es persona`` / ``Empresa``."""
    return "Es persona" if es_natural else "Empresa"


def regimen_iva_siigo(valor: object) -> str:
    """Normaliza el régimen de IVA al texto del modelo de Siigo.

    Devuelve ``"2 - Responsable de IVA"`` o ``"0 - No responsable de IVA"``.
    Acepta un booleano (responsable sí/no) o un texto libre (p. ej.
    ``"Responsable de IVA"`` / ``"No responsable de IVA"``). Si no hay
    información (vacío), devuelve cadena vacía para no inventar un régimen.
    """
    if isinstance(valor, bool):
        return "2 - Responsable de IVA" if valor else "0 - No responsable de IVA"
    s = _normalizar_base(valor)
    if not s:
        return ""
    if s.startswith("2") or ("responsable" in s and "no responsable" not in s):
        return "2 - Responsable de IVA"
    if s.startswith("0") or "no responsable" in s:
        return "0 - No responsable de IVA"
    return ""


# País: código DIAN del RUT y/o nombre → código ISO-3166 alfa-3 que usa Siigo.
_PAIS_ISO3: dict[str, str] = {
    "colombia": "COL",
    "estados unidos": "USA",
    "estados unidos de america": "USA",
    "venezuela": "VEN",
    "mexico": "MEX",
    "espana": "ESP",
    "argentina": "ARG",
    "chile": "CHL",
    "peru": "PER",
    "ecuador": "ECU",
    "panama": "PAN",
    "brasil": "BRA",
    "israel": "ISR",
    "australia": "AUS",
    "canada": "CAN",
}
# Código numérico DIAN del país → ISO-3 (solo los más comunes).
_PAIS_DIAN_ISO3: dict[str, str] = {"169": "COL"}


def codigo_pais(nombre_o_codigo: object, codigo_dian: object = "") -> str:
    """Devuelve el código ISO-3 del país (``COL``, ``USA``…) que espera Siigo.

    Intenta, en orden: el nombre del país (``"COLOMBIA"`` → ``"COL"``), el código
    numérico DIAN del RUT (``"169"`` → ``"COL"``), o un valor que ya sea ISO-3.
    Por defecto, si no hay datos, asume Colombia (``"COL"``).
    """
    nombre = _normalizar_base(nombre_o_codigo)
    if nombre in _PAIS_ISO3:
        return _PAIS_ISO3[nombre]
    cod = solo_digitos(codigo_dian) or solo_digitos(nombre_o_codigo)
    if cod in _PAIS_DIAN_ISO3:
        return _PAIS_DIAN_ISO3[cod]
    bruto = str(nombre_o_codigo or "").strip().upper()
    if len(bruto) == 3 and bruto.isalpha():
        return bruto
    return "COL" if not nombre and not cod else (bruto if bruto.isalpha() else "")


# ---------------------------------------------------------------------------
# Detección de la fila de encabezados y mapeo de columnas (openpyxl)
# ---------------------------------------------------------------------------

def detectar_fila_encabezados(ws, *, max_scan: int = 15) -> int:
    """Ubica la fila (1-based) de encabezados del maestro de terceros.

    El modelo de Siigo trae los encabezados en la fila 1; la planilla antigua,
    en la fila 7. Se localiza buscando, en las primeras filas, una celda cuyo
    texto coincida con un alias de «Identificación».
    """
    limite = min(max_scan, ws.max_row or max_scan)
    for fila in range(1, limite + 1):
        for celda in ws[fila]:
            if normalizar_encabezado(celda.value) in ALIAS_IDENTIFICACION:
                return fila
    return FILA_ENCABEZADOS_SIIGO


def mapa_columnas(ws, fila_enc: int) -> dict[str, int]:
    """Construye ``{campo_canónico: índice_de_columna}`` desde la fila dada."""
    mapa: dict[str, int] = {}
    for celda in ws[fila_enc]:
        campo = campo_de_encabezado(celda.value)
        if campo and campo not in mapa:
            mapa[campo] = celda.column
    return mapa


def aplicar_formato_texto(celda) -> None:
    """Fija el formato de la celda en «Texto» (``"@"``), conservando el del modelo.

    Se llama cada vez que el escritor toca una celda del maestro para garantizar
    que las identificaciones y códigos nunca pierdan ceros a la izquierda ni se
    conviertan a número.
    """
    celda.number_format = FORMATO_TEXTO


def fila_encabezados_desde_valores(filas: Iterable[list]) -> int:
    """Índice (0-based) de la fila de encabezados dentro de una lista de filas.

    Igual que ``detectar_fila_encabezados`` pero trabajando sobre listas de
    valores (lo usa el lector basado en pandas). Devuelve 0 si no encuentra una
    columna de identificación reconocible (modelo nuevo: encabezados en la 1ª).
    """
    for idx, fila in enumerate(filas):
        for valor in fila:
            if normalizar_encabezado(valor) in ALIAS_IDENTIFICACION:
                return idx
    return 0
