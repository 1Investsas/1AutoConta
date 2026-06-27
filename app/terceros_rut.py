"""
Actualización del maestro de terceros a partir de RUTs de la DIAN.

Toma los datos leídos de uno o varios RUT (ver ``app.rut``) y los inserta o
actualiza en el archivo ``Listado_de_Terceros.xlsx`` —el mismo maestro que usa
el pipeline RADIAN para cruzar terceros—. El maestro sigue la estructura del
**Modelo de importación de terceros de Siigo Nube** (29 columnas, encabezados en
la fila 1); ver ``app.terceros_schema``.

El upsert:

- **Conserva el formato de las casillas del modelo de Siigo**: cada celda que se
  escribe queda con formato de **Texto** (``"@"``), de modo que las
  identificaciones, los dígitos de verificación y los códigos (país,
  departamento, ciudad, tipo de identificación, código postal…) nunca pierden
  los ceros a la izquierda ni se convierten a número/notación científica.
- Reconoce las columnas por su nombre (sin distinguir mayúsculas, tildes ni la
  anotación «(Obligatorio)»), de modo que funciona con el modelo de Siigo y, por
  compatibilidad, con la planilla antigua.
- Hace *match* por identificación (NIT/cédula, solo dígitos): si el tercero ya
  existe lo actualiza; si no, lo agrega.
- Si el archivo no existe todavía, crea uno nuevo con las 29 columnas del modelo
  de Siigo (encabezados en la fila 1) y todas las celdas en formato de texto.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from app import terceros_schema as esquema
from app.terceros_schema import (
    ANCHOS_SIIGO,
    COLUMNAS_SIIGO,
    FILA_ENCABEZADOS_SIIGO,
    aplicar_formato_texto,
    codigo_pais,
    codigo_tipo_identificacion,
    mapa_columnas,
    regimen_iva_siigo,
    solo_digitos,
    tipo_tercero_siigo,
)

logger = logging.getLogger(__name__)


def mapear_rut_a_tercero(rut: dict) -> dict:
    """Convierte el dict leído de un RUT en un tercero con campos del modelo Siigo.

    Las claves del resultado son los *campos canónicos* de
    ``app.terceros_schema`` (``identificacion``, ``razon_social``, ``nombres``,
    ``codigo_ciudad``…), listos para que ``actualizar_maestro_terceros`` los
    escriba en las columnas correspondientes.
    """
    es_natural = str(rut.get("tipo_persona", "")).lower() == "natural"

    nombres = " ".join(
        p for p in [rut.get("primer_nombre", ""), rut.get("otros_nombres", "")] if p
    ).strip()
    apellidos = " ".join(
        p for p in [rut.get("primer_apellido", ""), rut.get("segundo_apellido", "")] if p
    ).strip()

    return {
        "identificacion":      solo_digitos(rut.get("nit", "")),
        "dv":                  str(rut.get("dv", "") or ""),
        "codigo_sucursal":     "",
        "tipo_identificacion": codigo_tipo_identificacion(
                                   rut.get("tipo_identificacion", ""),
                                   es_natural=es_natural),
        "tipo":                tipo_tercero_siigo(es_natural),
        "razon_social":        rut.get("nombre", "") or rut.get("razon_social", ""),
        "nombres":             nombres if es_natural else "",
        "apellidos":           apellidos if es_natural else "",
        "nombre_comercial":    "" if es_natural else rut.get("nombre_comercial", ""),
        "direccion":           rut.get("direccion", ""),
        "codigo_pais":         codigo_pais(rut.get("pais", ""), rut.get("pais_codigo", "")),
        "codigo_departamento": solo_digitos(rut.get("departamento_codigo", "")),
        "codigo_ciudad":       solo_digitos(rut.get("ciudad_codigo", "")),
        "indicativo_telefono": "",
        "telefono":            rut.get("telefono", ""),
        "extension_telefono":  "",
        "regimen_iva":         regimen_iva_siigo(rut.get("responsable_iva", False)),
        "responsabilidad_fiscal": "R-99-PN,",
        "codigo_postal":       "",
        "contacto_nombres":    "",
        "contacto_apellidos":  "",
        "contacto_indicativo": "",
        "contacto_telefono":   "",
        "contacto_extension":  "",
        "correo":              rut.get("correo", ""),
        "otros":               "SI",
        "clientes":            "NO",
        "proveedor":           "NO",
        "estado":              "Activo",
    }


def _crear_libro_nuevo():
    """Crea un libro nuevo con la estructura del modelo de Siigo Nube.

    Encabezados en la fila 1, las 29 columnas en orden, anchos del modelo y
    **formato de texto en toda la hoja** (encabezado incluido) para que las
    identificaciones y códigos conserven los ceros a la izquierda.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Terceros"
    for col, nombre in enumerate(COLUMNAS_SIIGO, start=1):
        celda = ws.cell(row=FILA_ENCABEZADOS_SIIGO, column=col, value=nombre)
        aplicar_formato_texto(celda)
        letra = get_column_letter(col)
        ws.column_dimensions[letra].width = ANCHOS_SIIGO.get(nombre, 30.0)
    return wb, ws, FILA_ENCABEZADOS_SIIGO


def _abrir_o_crear(contenido: Optional[bytes]):
    """Devuelve ``(workbook, worksheet, fila_encabezados, creado_nuevo)``."""
    if contenido:
        wb = load_workbook(io.BytesIO(contenido))
        ws = wb.active
        fila_enc = esquema.detectar_fila_encabezados(ws)
        return wb, ws, fila_enc, False
    wb, ws, fila_enc = _crear_libro_nuevo()
    return wb, ws, fila_enc, True


def actualizar_maestro_terceros(
    terceros: list[dict],
    contenido: Optional[bytes] = None,
) -> tuple[bytes, dict]:
    """Inserta/actualiza terceros en el maestro y devuelve ``(bytes, resumen)``.

    Args:
        terceros:  Lista de terceros con los campos canónicos del modelo Siigo
                   (ver ``mapear_rut_a_tercero``).
        contenido: Bytes del ``Listado_de_Terceros.xlsx`` existente, o ``None``
                   para crear uno nuevo con la estructura del modelo de Siigo.

    Returns:
        Tupla ``(bytes_actualizados, resumen)``. ``resumen`` incluye
        ``agregados``, ``actualizados``, ``detalle`` (por tercero) y
        ``columnas`` (campos detectados en el archivo).
    """
    wb, ws, fila_enc, creado = _abrir_o_crear(contenido)
    columnas = mapa_columnas(ws, fila_enc)

    if "identificacion" not in columnas:
        # El archivo de terceros no tiene una columna de identificación. La causa
        # más común es haber subido otro maestro (p. ej. el Plan de Cuentas) en
        # la casilla de Terceros: damos un mensaje claro según lo que parezca ser.
        # El plan de cuentas/comprobantes trae sus encabezados en otra fila
        # (la 7), así que se revisan las primeras filas para clasificarlo.
        from app.maestros import clasificar_encabezados, ETIQUETA_MAESTRO
        clase = "desconocido"
        for fila in range(1, min(15, ws.max_row or 1) + 1):
            encabezados = [c.value for c in ws[fila] if c.value not in (None, "")]
            posible = clasificar_encabezados([str(e) for e in encabezados])
            if posible in ("cuentas", "comprobantes"):
                clase = posible
                break
        if clase in ("cuentas", "comprobantes"):
            etiqueta = ETIQUETA_MAESTRO.get(clase, clase)
            raise ValueError(
                f"El archivo guardado como «Listado de Terceros» parece ser el "
                f"«{etiqueta}». Ve a Configuraciones → Empresas → Maestros y sube "
                f"el archivo de terceros correcto en su casilla."
            )
        raise ValueError(
            "El maestro de terceros no tiene una columna de «Identificación» "
            "(NIT/Cédula) reconocible. Verifica que el archivo sea el modelo de "
            "importación de terceros de Siigo (encabezados en la primera fila)."
        )

    col_id = columnas["identificacion"]

    # Índice de terceros existentes: identificación (solo dígitos) → nº de fila.
    indice: dict[str, int] = {}
    ultima_fila = fila_enc
    for fila in range(fila_enc + 1, (ws.max_row or fila_enc) + 1):
        valor = ws.cell(row=fila, column=col_id).value
        ident = solo_digitos(valor)
        if ident:
            indice[ident] = fila
            ultima_fila = fila
        elif any(c.value not in (None, "") for c in ws[fila]):
            # Fila con datos pero sin identificación: la contamos para no
            # sobrescribirla al agregar nuevas filas.
            ultima_fila = fila

    agregados = actualizados = 0
    detalle: list[dict] = []

    for tercero in terceros:
        ident = solo_digitos(tercero.get("identificacion", ""))
        if not ident:
            detalle.append({"identificacion": "",
                            "nombre": tercero.get("razon_social", ""),
                            "accion": "omitido"})
            continue

        if ident in indice:
            fila = indice[ident]
            accion = "actualizado"
            actualizados += 1
        else:
            ultima_fila += 1
            fila = ultima_fila
            indice[ident] = fila
            accion = "agregado"
            agregados += 1

        for campo, col in columnas.items():
            valor = tercero.get(campo, "")
            # No sobrescribir un valor existente con uno vacío.
            if valor in (None, "") and accion == "actualizado":
                continue
            celda = ws.cell(row=fila, column=col, value=valor)
            # Conservar el formato de texto del modelo de Siigo en cada casilla.
            aplicar_formato_texto(celda)

        detalle.append({
            "identificacion": ident,
            "nombre": tercero.get("razon_social", ""),
            "accion": accion,
        })

    buffer = io.BytesIO()
    wb.save(buffer)
    resumen = {
        "agregados": agregados,
        "actualizados": actualizados,
        "creado": creado,
        "columnas": sorted(columnas.keys()),
        "fila_encabezados": fila_enc,
        "detalle": detalle,
    }
    logger.info(
        "Maestro de terceros actualizado: %d agregados, %d actualizados (nuevo=%s).",
        agregados, actualizados, creado,
    )
    return buffer.getvalue(), resumen
