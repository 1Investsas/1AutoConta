"""
Motor de aprendizaje generalizado (machine learning) para prediligenciar
la información de los distintos módulos.

A diferencia del motor de sugerencias de RADIAN (app/sugerencias.py), que
aprende por combinación exacta (clasificación, NIT, tipo de línea), este motor
aprende de CUALQUIER texto digitado en el sistema (descripción del extracto
bancario, concepto de caja, nombre del tercero, …) y predice el valor de un
campo (cuenta contable, NIT del tercero, …) combinando dos memorias:

1. **Patrones exactos** (tabla ``patrones_aprendidos``): el texto normalizado
   completo → valor confirmado, con contador de usos. Máxima precisión cuando
   el mismo texto vuelve a aparecer (p. ej. la misma descripción del banco
   cada mes).

2. **Clasificador de texto** (tabla ``tokens_aprendidos``): un Naive Bayes
   multinomial sobre los tokens del texto. Generaliza a textos NUNCA vistos:
   si "PAGO NOMINA ELECTRONICA ACME" se contabilizó en la 51050501, una nueva
   descripción "PAGO NOMINA BANCOLOMBIA" hereda la predicción por los tokens
   compartidos (PAGO, NOMINA).

El conocimiento se guarda por **módulo** ('banco', 'caja', 'radian', …) y por
**campo** ('cuenta', 'nit_tercero', …). Existe además el módulo especial
``general``: allí se deposita el conocimiento importado de archivos externos
(exportes del programa de contabilidad, p. ej. SIIGO) y sirve de *fallback*
para todos los módulos. Orden de consulta de ``predecir()``:

    exacto(módulo) → exacto(general) → texto(módulo) → texto(general)

Todo es por empresa (cada empresa tiene su BD SQLite; en Azure SQL se aísla
por ``empresa_id``). Sin dependencias nuevas: puro Python + la BD existente.

Funciones públicas:
    aprender()          → registra una observación (texto → valor).
    aprender_lote()     → registra muchas observaciones en una sola conexión.
    predecir()          → predice el valor de un campo para un texto.
    predecir_campos()   → predice varios campos de una vez para un texto.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from app.config import DB_PATH
from app.database import (
    obtener_patrones_exactos,
    obtener_tokens_aprendidos,
    registrar_aprendizaje_lote,
    totales_tokens_por_valor,
)

logger = logging.getLogger(__name__)

# Módulo especial: conocimiento importado de fuentes externas, usado como
# fallback por todos los módulos.
MODULO_GENERAL = "general"

# Marcador de cuenta sin asignar (nunca se aprende).
_PENDIENTE = "[PENDIENTE]"

# Umbral mínimo de confianza para que una predicción por texto se sugiera.
UMBRAL_CONFIANZA = 0.40

# Suavizado de Laplace del Naive Bayes.
_ALFA = 1.0

# Longitud máxima del contexto exacto (límite NVARCHAR(400) en Azure SQL).
_MAX_CONTEXTO = 300

# Palabras sin señal en textos contables/bancarios colombianos.
_STOPWORDS = frozenset({
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "O", "U", "A", "EN",
    "POR", "PARA", "CON", "SIN", "AL", "SE", "SU", "SUS", "UN", "UNA",
    "LO", "QUE", "ES", "MES", "NO", "SI",
})


# ---------------------------------------------------------------------------
# Normalización y tokenización
# ---------------------------------------------------------------------------

def normalizar_texto(texto: str) -> str:
    """
    Normaliza un texto libre para usarlo como clave de aprendizaje.

    Mayúsculas sin tildes, solo letras/dígitos, espacios colapsados y SIN
    grupos de solo dígitos (números de factura, fechas y consecutivos cambian
    en cada documento y romperían la coincidencia exacta).
    """
    if not texto:
        return ""
    # Quitar tildes/diacríticos (NFD descompone y se filtran las marcas).
    plano = unicodedata.normalize("NFD", str(texto))
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    plano = plano.upper()
    # Todo lo que no sea letra/dígito → espacio.
    plano = re.sub(r"[^A-Z0-9Ñ]+", " ", plano)
    # Grupos de solo dígitos fuera (varían por documento).
    plano = re.sub(r"\b\d+\b", " ", plano)
    return " ".join(plano.split())[:_MAX_CONTEXTO]


def tokenizar(texto: str) -> list[str]:
    """
    Tokens únicos y con señal de un texto (ya normalizado o no).

    Filtra stopwords y tokens de un solo carácter; conserva el orden de
    aparición. La unicidad evita que una palabra repetida domine el conteo.
    """
    normalizado = normalizar_texto(texto)
    vistos: list[str] = []
    for token in normalizado.split():
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in vistos:
            vistos.append(token)
    return vistos


# ---------------------------------------------------------------------------
# Resultado de una predicción
# ---------------------------------------------------------------------------

@dataclass
class Prediccion:
    """Valor predicho para un campo, con su nivel de confianza y origen."""
    valor: str
    confianza: float        # 0.0 – 1.0
    origen: str             # 'exacto' (mismo texto) | 'texto' (Naive Bayes)
    modulo: str             # módulo del que salió el conocimiento
    usos: int = 0           # evidencia acumulada detrás de la predicción

    def a_dict(self) -> dict:
        """Serializa para respuestas JSON de la web."""
        return {
            "valor": self.valor,
            "confianza": round(self.confianza, 3),
            "origen": self.origen,
            "modulo": self.modulo,
            "usos": self.usos,
        }


# ---------------------------------------------------------------------------
# Aprendizaje
# ---------------------------------------------------------------------------

def _observacion(modulo: str, campo: str, texto: str, valor: str,
                 peso: int = 1) -> Optional[dict]:
    """Convierte (texto → valor) en una observación persistible, o None."""
    valor = str(valor or "").strip()
    if not valor or valor == _PENDIENTE:
        return None
    contexto = normalizar_texto(texto)
    if not contexto:
        return None
    return {
        "modulo": modulo,
        "campo": campo,
        "contexto": contexto,
        "valor": valor[:300],
        "tokens": tokenizar(contexto),
        "peso": max(1, int(peso)),
    }


def aprender(
    modulo: str,
    campo: str,
    texto: str,
    valor: str,
    db_path: str = DB_PATH,
    peso: int = 1,
) -> bool:
    """
    Registra que para `texto` el usuario confirmó `valor` en `campo`.

    Alimenta el patrón exacto y las frecuencias de tokens. Los valores vacíos
    o '[PENDIENTE]' se ignoran.

    Returns:
        True si la observación se registró.
    """
    obs = _observacion(modulo, campo, texto, valor, peso)
    if not obs:
        return False
    registrar_aprendizaje_lote([obs], db_path)
    logger.debug("Aprendido %s/%s: '%s' → %s", modulo, campo,
                 obs["contexto"][:40], obs["valor"])
    return True


def aprender_lote(
    observaciones: list[dict],
    db_path: str = DB_PATH,
) -> int:
    """
    Registra muchas observaciones en UNA conexión (rápido para entrenamiento).

    Cada observación: {'modulo', 'campo', 'texto', 'valor', 'peso'(opcional)}.

    Returns:
        Número de observaciones válidas registradas.
    """
    preparadas = []
    for o in observaciones:
        obs = _observacion(
            o.get("modulo", ""), o.get("campo", ""),
            o.get("texto", ""), o.get("valor", ""), o.get("peso", 1),
        )
        if obs:
            preparadas.append(obs)
    if not preparadas:
        return 0
    return registrar_aprendizaje_lote(preparadas, db_path)


# ---------------------------------------------------------------------------
# Predicción
# ---------------------------------------------------------------------------

def _predecir_exacto(modulo: str, campo: str, contexto: str,
                     db_path: str) -> Optional[Prediccion]:
    """Predicción por patrón exacto: el valor más confirmado para el contexto."""
    patrones = obtener_patrones_exactos(modulo, campo, contexto, db_path)
    if not patrones:
        return None
    total = sum(p["usos"] for p in patrones)
    mejor = patrones[0]
    return Prediccion(
        valor=mejor["valor"],
        confianza=mejor["usos"] / total if total else 0.0,
        origen="exacto",
        modulo=modulo,
        usos=mejor["usos"],
    )


def _predecir_texto(modulo: str, campo: str, tokens: list[str],
                    db_path: str, umbral: float) -> Optional[Prediccion]:
    """
    Predicción por clasificador de texto (Naive Bayes multinomial).

    P(valor | tokens) ∝ P(valor) · Π P(token | valor), con suavizado de
    Laplace. La confianza es el posterior (normalizado entre los valores
    candidatos) multiplicado por la cobertura (fracción de tokens del texto
    que el valor ganador ya conoce): así una coincidencia de un solo token
    entre muchos no pasa el umbral, y el motor puede caer al conocimiento
    general, donde quizá haya una coincidencia más completa.
    """
    if not tokens:
        return None
    filas = obtener_tokens_aprendidos(modulo, campo, tokens, db_path)
    if not filas:
        return None

    totales = totales_tokens_por_valor(modulo, campo, db_path)
    por_valor: dict[str, int] = totales["por_valor"]
    vocabulario = max(1, totales["vocabulario"])
    n_total = sum(por_valor.values()) or 1

    # usos por (valor, token) solo de los tokens consultados.
    conteo: dict[str, dict[str, int]] = {}
    for f in filas:
        conteo.setdefault(f["valor"], {})[f["token"]] = int(f["usos"])

    puntajes: dict[str, float] = {}
    for valor, tokens_valor in conteo.items():
        total_valor = por_valor.get(valor, 0)
        puntaje = math.log(max(total_valor, 1) / n_total)  # prior
        for token in tokens:
            usos = tokens_valor.get(token, 0)
            puntaje += math.log(
                (usos + _ALFA) / (total_valor + _ALFA * vocabulario)
            )
        puntajes[valor] = puntaje

    # Softmax numéricamente estable → posterior normalizado.
    tope = max(puntajes.values())
    expo = {v: math.exp(p - tope) for v, p in puntajes.items()}
    suma = sum(expo.values()) or 1.0
    mejor_valor = max(expo, key=expo.get)
    posterior = expo[mejor_valor] / suma

    coincidentes = sum(1 for t in tokens if conteo[mejor_valor].get(t, 0) > 0)
    cobertura = coincidentes / len(tokens)
    confianza = posterior * cobertura

    if coincidentes == 0 or confianza < umbral:
        return None
    return Prediccion(
        valor=mejor_valor,
        confianza=confianza,
        origen="texto",
        modulo=modulo,
        usos=sum(conteo[mejor_valor].values()),
    )


def predecir(
    modulo: str,
    campo: str,
    texto: str,
    db_path: str = DB_PATH,
    umbral: float = UMBRAL_CONFIANZA,
) -> Optional[Prediccion]:
    """
    Predice el valor de `campo` para un `texto`, o None si no hay confianza.

    Orden de consulta (del más preciso al más general):
      1. Patrón exacto del módulo.
      2. Patrón exacto del conocimiento general (importado de otras fuentes).
      3. Clasificador de texto del módulo.
      4. Clasificador de texto del conocimiento general.
    """
    contexto = normalizar_texto(texto)
    if not contexto:
        return None

    modulos = [modulo] + ([MODULO_GENERAL] if modulo != MODULO_GENERAL else [])
    for mod in modulos:
        pred = _predecir_exacto(mod, campo, contexto, db_path)
        if pred:
            return pred

    tokens = tokenizar(contexto)
    for mod in modulos:
        pred = _predecir_texto(mod, campo, tokens, db_path, umbral)
        if pred:
            return pred
    return None


def predecir_campos(
    modulo: str,
    texto: str,
    campos: list[str],
    db_path: str = DB_PATH,
    umbral: float = UMBRAL_CONFIANZA,
) -> dict[str, Prediccion]:
    """
    Predice varios campos de una vez para el mismo texto.

    Returns:
        Dict {campo: Prediccion} solo con los campos que tuvieron predicción.
    """
    resultado: dict[str, Prediccion] = {}
    for campo in campos:
        pred = predecir(modulo, campo, texto, db_path, umbral)
        if pred:
            resultado[campo] = pred
    return resultado
