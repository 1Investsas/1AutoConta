"""
Importador del extracto bancario CSV.

Estructura del CSV del banco (sin encabezados, separado por comas):
  Col A (0): Número de cuenta bancaria        ej. "551-000068-95"
  Col B (1): Código interno banco             ej. 551, 99, 976
  Col C (2): (vacía)
  Col D (3): Fecha yyyymmdd                   ej. 20260131
  Col E (4): (vacía)
  Col F (5): Valor (+débito al banco, -crédito al banco)
  Col G (6): Código interno detalle           ej. 2999, 3339
  Col H (7): Descripción del movimiento
  Col I (8): (cero, ignorar)

Lógica 4x1000:
  El código 3339 identifica el Impuesto de Gobierno 4x1000. Siempre
  corresponde a un egreso del mismo día. Se enlaza automáticamente con
  su movimiento padre buscando el egreso cuyo 0.4 % coincida con el valor
  del impuesto (tolerancia ±$0.50).

Consolidación de intereses de ahorros:
  Todos los movimientos cuya descripción coincida exactamente con
  BANCO_DESC_INTERESES_AHORROS (por defecto "ABONO INTERESES AHORROS") y que
  pertenezcan al mismo mes calendario se fusionan en un único movimiento
  cuya fecha es el último día del mes, conservando la cuenta bancaria,
  el código de detalle y demás campos del primer movimiento del grupo.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd

from app.config import BANCO_CODIGO_4X1000, BANCO_CODIGOS_BANCARIOS, BANCO_DESC_INTERESES_AHORROS, BANCO_DESC_BANCARIOS

logger = logging.getLogger(__name__)

_TOLERANCIA_4X1000 = Decimal("0.50")   # pesos de tolerancia en el match por redondeo
_TASA_4X1000       = Decimal("0.004")


def _es_movimiento_bancario(codigo_detalle: str, descripcion: str) -> bool:
    """
    Retorna True si el movimiento siempre debe usar el NIT del banco como tercero.

    Un movimiento es bancario si:
      - Su código de detalle pertenece a BANCO_CODIGOS_BANCARIOS, ó
      - Su descripción contiene alguno de los patrones en BANCO_DESC_BANCARIOS
        (IMPTO GOBIERNO 4X1000, ABONO INTERESES AHORROS, CUOTA MANEJO TRJ DEB, …)
    """
    if codigo_detalle in BANCO_CODIGOS_BANCARIOS:
        return True
    desc_upper = descripcion.strip().upper()
    return any(patron in desc_upper for patron in BANCO_DESC_BANCARIOS)


@dataclass
class MovimientoBanco:
    """Representa una línea del CSV del banco."""
    idx: int                         # posición en la lista final (0-based, ordenado)
    cuenta_banco_num: str            # "551-000068-95"
    codigo_banco: str                # "551", "99", etc.
    fecha: date
    valor: Decimal                   # + = entra dinero, - = sale dinero
    codigo_detalle: str              # "2999", "3339", etc.
    descripcion: str
    es_4x1000: bool = False
    es_bancario: bool = False    # True cuando el banco es siempre la contraparte
    idx_padre: Optional[int] = None  # si es 4x1000, idx del movimiento padre


# Delimitadores que se prueban cuando el configurado no permite leer el CSV.
_DELIMITADORES_FALLBACK = (",", ";", "\t", "|")


def _leer_dataframe_banco(path: str | Path, fmt: dict, max_col: int) -> pd.DataFrame:
    """
    Lee el CSV con el delimitador configurado; si falla, intenta detectarlo.

    Un delimitador mal configurado en la empresa (p. ej. "." — que aparece en
    los decimales y en descripciones como "S.A") hace que pandas parta las
    filas de forma inconsistente ("Expected N fields... saw M") o que no haya
    columnas suficientes para las posiciones configuradas. En ambos casos se
    reintenta con los delimitadores habituales y se acepta el primero que
    produzca al menos `max_col + 1` columnas.
    """
    def _leer(delim: str) -> pd.DataFrame:
        return pd.read_csv(
            str(path),
            header=None,
            sep=delim,
            skiprows=int(fmt["filas_encabezado"]),
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=True,
        )

    delim_cfg = str(fmt["delimitador"] or ",")
    error_cfg: Optional[Exception] = None
    try:
        df = _leer(delim_cfg)
        if df.shape[1] > max_col:
            return df
    except pd.errors.ParserError as exc:
        error_cfg = exc

    for candidato in _DELIMITADORES_FALLBACK:
        if candidato == delim_cfg:
            continue
        try:
            df = _leer(candidato)
        except pd.errors.ParserError:
            continue
        if df.shape[1] > max_col:
            logger.warning(
                "El delimitador configurado (%r) no permite leer el extracto "
                "(%s); se usó %r detectado automáticamente. Revisa el formato "
                "del banco en la configuración de la empresa.",
                delim_cfg, error_cfg or "columnas insuficientes", candidato,
            )
            return df

    raise ValueError(
        f"No se pudo interpretar el extracto con el delimitador configurado "
        f"({delim_cfg!r}) ni con los delimitadores habituales (, ; tab |). "
        f"Verifica el campo «Delimitador» del formato del extracto bancario "
        f"en la configuración de la empresa."
    ) from error_cfg


def leer_csv_banco(path: str | Path, formato: Optional[dict] = None) -> list[MovimientoBanco]:
    """
    Lee el CSV del banco y retorna lista de MovimientoBanco.

    Pasos:
    1. Lee el CSV (sin encabezados por defecto).
    2. Filtra filas vacías.
    3. Parsea y ordena por fecha ascendente.
    4. Reasigna índices consecutivos.
    5. Enlaza los movimientos 4x1000 con su egreso padre.

    Args:
        path:    Ruta al archivo CSV del banco.
        formato: Formato del extracto (ver app.empresas.FORMATO_BANCO_DEFAULT).
                 Permite ajustar delimitador, filas de encabezado, posiciones
                 de columnas, formato de fecha y separadores numéricos por
                 empresa/banco. Si es None se usa el formato por defecto.

    Returns:
        Lista de MovimientoBanco ordenada por fecha ascendente.
    """
    from app.empresas import FORMATO_BANCO_DEFAULT
    fmt = {**FORMATO_BANCO_DEFAULT, **(formato or {})}

    col_cuenta  = int(fmt["col_cuenta"])
    col_cod_bco = int(fmt["col_codigo_banco"])
    col_fecha   = int(fmt["col_fecha"])
    col_valor   = int(fmt["col_valor"])
    col_detalle = int(fmt["col_codigo_detalle"])
    col_desc    = int(fmt["col_descripcion"])

    # La descripción es opcional (len(row) > col_desc más abajo); las demás
    # columnas sí deben existir para poder interpretar el movimiento.
    max_col = max(col_cuenta, col_cod_bco, col_fecha, col_valor, col_detalle)
    df = _leer_dataframe_banco(path, fmt, max_col)


    sep_miles   = fmt["separador_miles"]
    sep_decimal = fmt["separador_decimal"]

    # Descartar filas cuya columna de cuenta sea vacía
    df = df[df.iloc[:, col_cuenta].str.strip() != ""].reset_index(drop=True)

    movimientos: list[MovimientoBanco] = []
    for raw_idx, row in df.iterrows():
        try:
            cuenta_banco_num = str(row.iloc[col_cuenta]).strip()
            codigo_banco     = str(row.iloc[col_cod_bco]).strip()
            fecha_str        = str(row.iloc[col_fecha]).strip()
            valor_str        = str(row.iloc[col_valor]).strip()
            codigo_detalle   = str(row.iloc[col_detalle]).strip()
            descripcion      = str(row.iloc[col_desc]).strip() if len(row) > col_desc else ""

            if sep_miles:
                valor_str = valor_str.replace(sep_miles, "")
            if sep_decimal != ".":
                valor_str = valor_str.replace(sep_decimal, ".")

            fecha = datetime.strptime(fecha_str, fmt["formato_fecha"]).date()
            valor = Decimal(valor_str)

            es_4x1000 = (codigo_detalle == BANCO_CODIGO_4X1000)
            movimientos.append(MovimientoBanco(
                idx=int(raw_idx),
                cuenta_banco_num=cuenta_banco_num,
                codigo_banco=codigo_banco,
                fecha=fecha,
                valor=valor,
                codigo_detalle=codigo_detalle,
                descripcion=descripcion,
                es_4x1000=es_4x1000,
                es_bancario=_es_movimiento_bancario(codigo_detalle, descripcion),
            ))
        except Exception as exc:
            logger.warning("Fila %s ignorada: %s", raw_idx, exc)

    # Ordenar cronológicamente (ascendente), preservando orden original del CSV
    # como desempate (el CSV suele venir en orden inverso)
    movimientos.sort(key=lambda m: (m.fecha, -m.idx))

    # Consolidar intereses de ahorros: agrupar por mes en un solo movimiento
    movimientos = _consolidar_intereses_ahorros(movimientos)

    # Reasignar índices consecutivos después del sort y la consolidación
    for i, m in enumerate(movimientos):
        m.idx = i

    _enlazar_4x1000(movimientos)

    return movimientos


def _consolidar_intereses_ahorros(
    movimientos: list[MovimientoBanco],
) -> list[MovimientoBanco]:
    """
    Consolida los movimientos "ABONO INTERESES AHORROS" del mismo mes en uno solo.

    Para cada mes calendario, todos los movimientos cuya descripción coincida
    exactamente con BANCO_DESC_INTERESES_AHORROS se suman y se reemplazan por
    un único MovimientoBanco con:
      - fecha  = último día del mes
      - valor  = suma de todos los valores del grupo
      - descripcion, cuenta_banco_num, codigo_banco, codigo_detalle y
        es_bancario tomados del primer movimiento del grupo

    Los movimientos que no son de intereses se conservan intactos. El resultado
    se devuelve ordenado por fecha ascendente.
    """
    from collections import defaultdict

    # Separar intereses del resto usando coincidencia flexible (contains,
    # ignorando mayúsculas y espacios extra). Así se capturan variaciones
    # menores que pueda generar el banco en el extracto.
    patron = BANCO_DESC_INTERESES_AHORROS.strip().upper()
    intereses: list[MovimientoBanco] = []
    resto: list[MovimientoBanco] = []
    for m in movimientos:
        if patron in m.descripcion.strip().upper():
            intereses.append(m)
        else:
            resto.append(m)

    if not intereses:
        logger.info(
            "_consolidar_intereses_ahorros: no se encontraron movimientos con \"%s\" "
            "— nada que consolidar.",
            patron,
        )
        return movimientos

    # Agrupar intereses por (cuenta_banco_num, año, mes)
    grupos: dict[tuple, list[MovimientoBanco]] = defaultdict(list)
    for m in intereses:
        clave = (m.cuenta_banco_num, m.fecha.year, m.fecha.month)
        grupos[clave].append(m)

    consolidados: list[MovimientoBanco] = []
    for (cuenta, anio, mes), grupo in grupos.items():
        # Último día del mes como fecha del movimiento consolidado
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        fecha_fin_mes = date(anio, mes, ultimo_dia)

        total = sum(m.valor for m in grupo)
        primero = grupo[0]

        consolidados.append(MovimientoBanco(
            idx=primero.idx,           # se reasignará después
            cuenta_banco_num=primero.cuenta_banco_num,
            codigo_banco=primero.codigo_banco,
            fecha=fecha_fin_mes,
            valor=total,
            codigo_detalle=primero.codigo_detalle,
            descripcion=primero.descripcion,
            es_4x1000=False,
            es_bancario=primero.es_bancario,
            idx_padre=None,
        ))
        logger.info(
            "Intereses consolidados: cuenta=%s %02d/%d — %d movimientos sumados → %s",
            cuenta, mes, anio, len(grupo), total,
        )

    # Reunir y reordenar
    resultado = resto + consolidados
    resultado.sort(key=lambda m: m.fecha)
    return resultado


def _enlazar_4x1000(movimientos: list[MovimientoBanco]) -> None:
    """
    Enlaza cada movimiento 4x1000 (código 3339) con su egreso padre.

    Para cada 4x1000 busca en el mismo día el egreso (valor negativo, no-4x1000)
    cuyo 0.4 % se aproxime más al valor del impuesto (dentro de la tolerancia).
    Si hay varios egresos usa el de mejor match.
    Si no hay match en el mismo día, el 4x1000 queda huérfano (idx_padre=None).
    """
    from collections import defaultdict

    por_fecha: dict[date, list[MovimientoBanco]] = defaultdict(list)
    for m in movimientos:
        por_fecha[m.fecha].append(m)

    for grupo in por_fecha.values():
        impuestos = [m for m in grupo if m.es_4x1000]
        egresos   = [m for m in grupo if not m.es_4x1000 and m.valor < 0]

        if not impuestos or not egresos:
            continue

        usados: set[int] = set()
        for imp in impuestos:
            monto_imp  = abs(imp.valor)
            mejor: Optional[MovimientoBanco] = None
            mejor_diff: Optional[Decimal]   = None

            for eg in egresos:
                if eg.idx in usados:
                    continue
                esperado = abs(eg.valor) * _TASA_4X1000
                diff     = abs(esperado - monto_imp)
                if mejor_diff is None or diff < mejor_diff:
                    mejor_diff = diff
                    mejor      = eg

            if mejor is not None and mejor_diff <= _TOLERANCIA_4X1000:
                imp.idx_padre = mejor.idx
                usados.add(mejor.idx)
            elif egresos:
                # Fallback: usar el primer egreso disponible del día
                disponibles = [e for e in egresos if e.idx not in usados]
                if disponibles:
                    imp.idx_padre = disponibles[0].idx
                else:
                    imp.idx_padre = egresos[0].idx


# ---------------------------------------------------------------------------
# Serialización para sesión Flask
# ---------------------------------------------------------------------------

def a_dict(m: MovimientoBanco) -> dict:
    """Convierte un MovimientoBanco a dict serializable en sesión Flask."""
    return {
        "idx":             m.idx,
        "cuenta_banco_num": m.cuenta_banco_num,
        "codigo_banco":    m.codigo_banco,
        "fecha":           m.fecha.isoformat(),
        "valor":           str(m.valor),
        "codigo_detalle":  m.codigo_detalle,
        "descripcion":     m.descripcion,
        "es_4x1000":       m.es_4x1000,
        "es_bancario":     m.es_bancario,
        "idx_padre":       m.idx_padre,
    }


def desde_dict(d: dict) -> MovimientoBanco:
    """Reconstruye un MovimientoBanco desde el dict de sesión."""
    return MovimientoBanco(
        idx=d["idx"],
        cuenta_banco_num=d["cuenta_banco_num"],
        codigo_banco=d["codigo_banco"],
        fecha=date.fromisoformat(d["fecha"]),
        valor=Decimal(d["valor"]),
        codigo_detalle=d["codigo_detalle"],
        descripcion=d["descripcion"],
        es_4x1000=d["es_4x1000"],
        es_bancario=d.get("es_bancario", False),
        idx_padre=d.get("idx_padre"),
    )
