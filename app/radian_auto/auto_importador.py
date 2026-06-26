"""
Orquestador de la importación automática de RADIAN.

Encadena todo el flujo, sin depender de Flask, para que pueda ejecutarse desde
la web, la CLI o el scheduler:

    1. Solicita el token a la DIAN (dispara el correo).
    2. Espera y lee el correo para obtener el enlace de acceso.
    3. Activa la sesión y descarga el reporte RADIAN del rango de fechas.
    4. Guarda el archivo y lo procesa con el pipeline estándar.
    5. Registra la importación (queda visible en el módulo «Importaciones»).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app import storage as store
from app.radian_auto.dian_client import DianClient, DianError
from app.radian_auto.email_token import EmailTokenError, esperar_enlace_token

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class ResultadoAuto:
    """Resultado de un intento de importación automática."""

    empresa_id: str
    ok: bool
    mensaje: str
    importacion_id: Optional[int] = None
    n_docs: int = 0
    n_excepciones: int = 0
    archivo_ref: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "empresa_id": self.empresa_id,
            "ok": self.ok,
            "mensaje": self.mensaje,
            "importacion_id": self.importacion_id,
            "n_docs": self.n_docs,
            "n_excepciones": self.n_excepciones,
            "archivo_ref": self.archivo_ref,
            "error": self.error,
        }


def _rutas_maestros(emp) -> tuple[str, str, str]:
    """Resuelve las rutas de los 3 maestros de la empresa (como en la web)."""
    rutas = []
    for nombre in (
        "Listado_de_Terceros.xlsx",
        "Listado_de_Cuentas_Contables.xlsx",
        "Tipos_de_comprobante_contable.xlsx",
    ):
        try:
            ruta = emp.ruta_maestro(nombre)
        except FileNotFoundError:
            ruta = str(_PROJECT_ROOT / emp.data_category / nombre)
        rutas.append(ruta)
    return tuple(rutas)  # type: ignore[return-value]


def _rango_fechas(dias_atras: int, hoy: Optional[date] = None) -> tuple[str, str]:
    """Retorna (fecha_desde, fecha_hasta) en formato YYYY-MM-DD."""
    hoy = hoy or date.today()
    desde = hoy - timedelta(days=max(0, dias_atras))
    return desde.isoformat(), hoy.isoformat()


def _extension_archivo(nombre_sugerido: str) -> str:
    """Extensión a usar para guardar el reporte (.xlsx por defecto)."""
    ext = Path(nombre_sugerido or "").suffix.lower()
    return ext if ext in (".xlsx", ".xls", ".zip") else ".xlsx"


def importar_empresa(
    empresa,
    *,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    incluir_duplicados: bool = False,
    client: Optional[DianClient] = None,
) -> ResultadoAuto:
    """Ejecuta la importación automática de RADIAN para una empresa.

    Args:
        empresa:            Empresa a procesar (con su `DianConfig`).
        fecha_desde/hasta:  Rango YYYY-MM-DD; por defecto los últimos
                            `dian.dias_atras` días.
        incluir_duplicados: Reprocesar CUFE ya registrados.
        client:             Cliente DIAN inyectable (para pruebas).

    Returns:
        ResultadoAuto con el desenlace (nunca lanza: encapsula el error).
    """
    from app.database import (
        inicializar_db, registrar_importacion, actualizar_importacion,
    )
    from app.pipeline import ejecutar_pipeline

    dcfg = empresa.dian()
    if not dcfg.configurado():
        faltan = ", ".join(dcfg.faltantes())
        return ResultadoAuto(
            empresa.id, False,
            f"Configuración DIAN incompleta para {empresa.nombre}: falta {faltan}.",
            error="config_incompleta",
        )

    nit_empresa = dcfg.nit_empresa_efectivo(empresa)
    desde, hasta = (fecha_desde, fecha_hasta)
    if not desde or not hasta:
        desde, hasta = _rango_fechas(dcfg.dias_atras)

    cliente = client or DianClient(**dcfg.client_kwargs())
    marca = datetime.now(timezone.utc)

    try:
        logger.info("[%s] Solicitando token a la DIAN…", empresa.id)
        cliente.solicitar_token(
            dcfg.tipo_identificacion, dcfg.nit_representante, nit_empresa,
        )

        logger.info("[%s] Esperando el correo del token…", empresa.id)
        enlace = esperar_enlace_token(dcfg.imap_config(), no_antes_de=marca)

        logger.info("[%s] Activando sesión DIAN…", empresa.id)
        cliente.activar_sesion(enlace)

        logger.info("[%s] Descargando reporte RADIAN %s → %s…", empresa.id, desde, hasta)
        contenido = cliente.descargar_reporte(desde, hasta)
    except (DianError, EmailTokenError) as exc:
        logger.warning("[%s] Falló la descarga automática: %s", empresa.id, exc)
        return ResultadoAuto(empresa.id, False, str(exc), error=type(exc).__name__)

    # Guardar el archivo descargado (aislado por empresa) y registrar la importación.
    ext = _extension_archivo(cliente.ultimo_archivo)
    nombre = f"RADIAN_{hasta}_{uuid.uuid4().hex[:8]}{ext}"
    archivo_ref = store.save_file(contenido, empresa.upload_category, nombre)

    db = empresa.db_path
    inicializar_db(db)
    imp_id = registrar_importacion(
        archivo_nombre=nombre, archivo_ref=archivo_ref, db_path=db,
    )

    try:
        radian_path = store.load_file(archivo_ref)
        terceros, cuentas, comprobantes = _rutas_maestros(empresa)
        resultado = ejecutar_pipeline(
            radian_path, terceros, cuentas, comprobantes,
            db, incluir_duplicados, empresa,
            output_dir=str(_PROJECT_ROOT / "output"),
        )
        resultado["importacion_id"] = imp_id
        _persistir(imp_id, resultado, "procesada", db)
        logger.info(
            "[%s] Importación automática #%s: %d documentos, %d excepciones.",
            empresa.id, imp_id, resultado["n_docs"], resultado["n_excepciones"],
        )
        return ResultadoAuto(
            empresa.id, True,
            f"Importados {resultado['n_docs']} documentos "
            f"({resultado['n_excepciones']} con excepciones).",
            importacion_id=imp_id,
            n_docs=resultado["n_docs"],
            n_excepciones=resultado["n_excepciones"],
            archivo_ref=archivo_ref,
        )
    except Exception as exc:  # noqa: BLE001 - se reporta y se guarda el archivo
        logger.exception("[%s] Error procesando el reporte automático", empresa.id)
        actualizar_importacion(imp_id, estado="error", error=str(exc), db_path=db)
        return ResultadoAuto(
            empresa.id, False,
            f"Se descargó el reporte pero falló el procesamiento: {exc}",
            importacion_id=imp_id, archivo_ref=archivo_ref, error="pipeline",
        )


def solicitar_token(empresa, *, client: Optional[DianClient] = None) -> None:
    """Pide a la DIAN que genere y envíe el token (correo al representante legal).

    Pensado para el flujo manual: el usuario pulsa «Solicitar token», recibe el
    correo y luego pega el enlace. Requiere los datos del representante legal.
    """
    dcfg = empresa.dian()
    if not dcfg.nit_representante.strip():
        raise DianError(
            "Configura el tipo y el NIT del representante legal para solicitar el token."
        )
    cliente = client or DianClient(**dcfg.client_kwargs())
    cliente.solicitar_token(
        dcfg.tipo_identificacion,
        dcfg.nit_representante,
        dcfg.nit_empresa_efectivo(empresa),
    )


def descargar_con_enlace(
    empresa,
    auth_url: str,
    *,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    client: Optional[DianClient] = None,
) -> tuple[str, str, tuple[str, str]]:
    """Activa la sesión con un enlace pegado y descarga el reporte RADIAN.

    Flujo manual (mientras no haya buzón/certificado para leer el correo solo):
    el usuario pega el enlace `AuthToken` recibido por correo y la app activa la
    sesión, descarga el reporte y lo guarda.

    Returns:
        (archivo_ref, nombre_archivo, (fecha_desde, fecha_hasta))

    Raises:
        DianError: si el enlace no es válido o falla la descarga.
    """
    from app.radian_auto.dian_client import parsear_auth_url

    if not parsear_auth_url(auth_url).get("token"):
        raise DianError("El enlace pegado no es un enlace de acceso válido de la DIAN.")

    dcfg = empresa.dian()
    desde, hasta = (fecha_desde, fecha_hasta)
    if not desde or not hasta:
        desde, hasta = _rango_fechas(dcfg.dias_atras)

    cliente = client or DianClient(**dcfg.client_kwargs())
    cliente.activar_sesion(auth_url)
    contenido = cliente.descargar_reporte(desde, hasta)

    ext = _extension_archivo(cliente.ultimo_archivo)
    nombre = f"RADIAN_{hasta}_{uuid.uuid4().hex[:8]}{ext}"
    archivo_ref = store.save_file(contenido, empresa.upload_category, nombre)
    return archivo_ref, nombre, (desde, hasta)


def _persistir(imp_id: int, datos: dict, estado: str, db: str) -> None:
    """Guarda el snapshot durable de la importación (como hace la web)."""
    from app.database import actualizar_importacion
    try:
        actualizar_importacion(
            imp_id,
            estado=estado,
            n_docs=int(datos.get("n_docs", 0) or 0),
            n_excepciones=int(datos.get("n_excepciones", 0) or 0),
            excel_ref=datos.get("excel_path") or None,
            preasientos_json=json.dumps(datos, ensure_ascii=False),
            db_path=db,
        )
    except Exception:
        logger.exception("No se pudo persistir el snapshot de la importación %s", imp_id)


def importar_todas(
    *, solo_habilitadas: bool = True, incluir_duplicados: bool = False,
) -> list[ResultadoAuto]:
    """Ejecuta la importación automática para todas las empresas configuradas.

    Args:
        solo_habilitadas: si True, solo procesa empresas con la importación
                          automática activada (`dian.habilitado`).
    """
    from app.empresas import listar_empresas

    resultados: list[ResultadoAuto] = []
    for emp in listar_empresas():
        dcfg = emp.dian()
        if solo_habilitadas and not dcfg.habilitado:
            continue
        if not dcfg.configurado():
            continue
        resultados.append(
            importar_empresa(emp, incluir_duplicados=incluir_duplicados)
        )
    return resultados
