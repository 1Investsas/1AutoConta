"""
Cliente REST de la API de SIIGO — Fase 3 (integración futura).

Este módulo implementa la conexión con la API de SIIGO Nube.
Requiere suscripción Premium y credenciales configuradas en .env:

    SIIGO_USERNAME=usuario@empresa.com
    SIIGO_ACCESS_KEY=tu_access_key_aqui

Referencia oficial: https://developers.siigo.com/

Uso básico:
    from app.siigo.api_client import SiigoClient

    client = SiigoClient()
    client.autenticar()
    resultado = client.crear_comprobante(preasiento)

ESTADO ACTUAL: Implementado y listo. Solo requiere credenciales activas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

from app.config import SIIGO_API_URL, SIIGO_USERNAME, SIIGO_ACCESS_KEY
from app.models import PreasientoContable
from app.siigo.mapeador import mapear_preasiento

logger = logging.getLogger(__name__)

_AUTH_ENDPOINT    = "/auth"
_JOURNALS_ENDPOINT = "/v1/journals"


class SiigoAuthError(Exception):
    """Error de autenticación con la API de SIIGO."""


class SiigoAPIError(Exception):
    """Error al llamar un endpoint de la API de SIIGO."""
    def __init__(self, status_code: int, mensaje: str):
        super().__init__(mensaje)
        self.status_code = status_code


class SiigoClient:
    """
    Cliente para la API REST de SIIGO Nube.

    El token de acceso tiene validez de 24 horas. El cliente lo renueva
    automáticamente antes de cada llamada si está próximo a vencer.
    """

    def __init__(
        self,
        username:   str | None = None,
        access_key: str | None = None,
        api_url:    str | None = None,
    ):
        self._username   = username   or SIIGO_USERNAME
        self._access_key = access_key or SIIGO_ACCESS_KEY
        self._api_url    = (api_url   or SIIGO_API_URL).rstrip("/")

        self._token:      Optional[str]      = None
        self._token_exp:  Optional[datetime] = None
        self._partner_id: Optional[str]      = None

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def autenticar(self) -> None:
        """
        Obtiene un token de acceso de la API de SIIGO.

        Raises:
            SiigoAuthError: Si las credenciales son inválidas o están vacías.
        """
        if not self._username or not self._access_key:
            raise SiigoAuthError(
                "Credenciales SIIGO no configuradas. "
                "Define SIIGO_USERNAME y SIIGO_ACCESS_KEY en el archivo .env."
            )

        url = f"{self._api_url}{_AUTH_ENDPOINT}"
        payload = {
            "username":   self._username,
            "access_key": self._access_key,
        }

        try:
            resp = requests.post(url, json=payload, timeout=15)
        except requests.RequestException as exc:
            raise SiigoAuthError(f"Error de red al autenticar con SIIGO: {exc}") from exc

        if resp.status_code != 200:
            raise SiigoAuthError(
                f"Autenticación fallida (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        self._token      = data.get("access_token")
        self._partner_id = data.get("token_type", "")   # SIIGO devuelve partner_id en algunos planes
        # El token dura 24 h; marcamos expiración con 5 min de margen
        self._token_exp  = datetime.utcnow() + timedelta(hours=23, minutes=55)
        logger.info("Autenticación SIIGO exitosa para usuario: %s", self._username)

    def _asegurar_token(self) -> None:
        """Re-autentica si el token está vencido o no existe."""
        if not self._token or (self._token_exp and datetime.utcnow() >= self._token_exp):
            self.autenticar()

    def _headers(self) -> dict[str, str]:
        self._asegurar_token()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }
        if self._partner_id:
            headers["Partner-Id"] = self._partner_id
        return headers

    # ------------------------------------------------------------------
    # Comprobantes contables (journals)
    # ------------------------------------------------------------------

    def crear_comprobante(self, preasiento: PreasientoContable) -> dict[str, Any]:
        """
        Crea un comprobante contable en SIIGO a partir de un PreasientoContable.

        Solo funciona si no hay líneas con cuenta [PENDIENTE].

        Args:
            preasiento: Preasiento con todas las cuentas resueltas.

        Returns:
            Respuesta JSON de SIIGO con el comprobante creado.

        Raises:
            ValueError:    Si el preasiento tiene cuentas pendientes.
            SiigoAPIError: Si la API retorna un error.
        """
        lineas_pendientes = [l for l in preasiento.lineas if l.es_pendiente]
        if lineas_pendientes:
            raise ValueError(
                f"El preasiento {preasiento.cufe[:20]}... tiene "
                f"{len(lineas_pendientes)} línea(s) con cuenta [PENDIENTE]. "
                "Resuelve las cuentas antes de enviar a SIIGO."
            )

        payload = _construir_payload_journal(preasiento)
        url     = f"{self._api_url}{_JOURNALS_ENDPOINT}"

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise SiigoAPIError(0, f"Error de red al crear comprobante: {exc}") from exc

        if resp.status_code not in (200, 201):
            raise SiigoAPIError(resp.status_code, resp.text)

        logger.info(
            "Comprobante SIIGO creado: CUFE=%s | HTTP %s",
            preasiento.cufe[:20],
            resp.status_code,
        )
        return resp.json()

    def crear_lote(
        self,
        preasientos: list[PreasientoContable],
        omitir_pendientes: bool = True,
    ) -> dict[str, Any]:
        """
        Envía múltiples preasientos a SIIGO.

        Args:
            preasientos:       Lista de preasientos a enviar.
            omitir_pendientes: Si True, omite documentos con cuentas pendientes
                               en lugar de interrumpir el proceso.

        Returns:
            Dict con claves:
                "enviados":  lista de CUFE enviados exitosamente.
                "omitidos":  lista de CUFE con cuentas pendientes (omitidos).
                "errores":   lista de dicts {"cufe", "error"} con fallos de API.
        """
        enviados: list[str] = []
        omitidos: list[str] = []
        errores:  list[dict] = []

        for p in preasientos:
            tiene_pendiente = any(l.es_pendiente for l in p.lineas)
            if tiene_pendiente:
                if omitir_pendientes:
                    omitidos.append(p.cufe)
                    logger.warning("Omitido (pendiente): %s", p.cufe[:30])
                    continue
                else:
                    errores.append({"cufe": p.cufe, "error": "Tiene cuentas [PENDIENTE]"})
                    continue

            try:
                self.crear_comprobante(p)
                enviados.append(p.cufe)
            except (SiigoAPIError, ValueError) as exc:
                errores.append({"cufe": p.cufe, "error": str(exc)})
                logger.error("Error enviando %s: %s", p.cufe[:30], exc)

        logger.info(
            "Lote SIIGO completado: %d enviados, %d omitidos, %d errores",
            len(enviados), len(omitidos), len(errores),
        )
        return {"enviados": enviados, "omitidos": omitidos, "errores": errores}

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def listar_comprobantes(
        self,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """
        Lista comprobantes contables registrados en SIIGO.

        Args:
            fecha_desde: Fecha inicio en formato YYYY-MM-DD (opcional).
            fecha_hasta: Fecha fin   en formato YYYY-MM-DD (opcional).
            page:        Número de página (paginación).
            page_size:   Registros por página.

        Returns:
            Respuesta JSON de SIIGO con la lista de comprobantes.
        """
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if fecha_desde:
            params["start_date"] = fecha_desde
        if fecha_hasta:
            params["end_date"] = fecha_hasta

        url = f"{self._api_url}{_JOURNALS_ENDPOINT}"
        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=15)
        except requests.RequestException as exc:
            raise SiigoAPIError(0, f"Error de red al listar comprobantes: {exc}") from exc

        if resp.status_code != 200:
            raise SiigoAPIError(resp.status_code, resp.text)

        return resp.json()


# ---------------------------------------------------------------------------
# Helpers de construcción de payload
# ---------------------------------------------------------------------------

def _construir_payload_journal(preasiento: PreasientoContable) -> dict[str, Any]:
    """
    Construye el payload JSON para POST /v1/journals según la API de SIIGO.

    Estructura de referencia:
        https://developers.siigo.com/docs/siigoapi/journal-entry/
    """
    from app.config import SIIGO_CODIGOS_COMPROBANTE

    fecha_str = (
        preasiento.fecha_emision.strftime("%Y-%m-%d")
        if preasiento.fecha_emision
        else datetime.utcnow().strftime("%Y-%m-%d")
    )

    prefijo = preasiento.prefijo or ""
    folio   = preasiento.folio   or ""
    sep     = "-" if prefijo else ""
    glosa = (
        f"{preasiento.clasificacion.replace('_', ' ')} "
        f"{prefijo}{sep}{folio} | {preasiento.tercero_nombre}"
    ).strip()

    items = []
    for linea in preasiento.lineas:
        movimiento = "Debit" if linea.debito > 0 else "Credit"
        valor      = linea.debito if linea.debito > 0 else linea.credito
        items.append({
            "account": {
                "code":     linea.cuenta,
                "movement": movimiento,
            },
            "customer": {
                "identification": linea.tercero_nit or preasiento.tercero_nit or "",
                "branch_office":  0,
            },
            "description": linea.concepto or glosa,
            "value":        round(valor, 2),
        })

    cod_comprobante = SIIGO_CODIGOS_COMPROBANTE.get(preasiento.clasificacion, 0)

    return {
        "document": {"id": cod_comprobante},
        "date":     fecha_str,
        "observations": glosa,
        "items":    items,
    }
