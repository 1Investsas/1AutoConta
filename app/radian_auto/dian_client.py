"""
Cliente HTTP del portal RADIAN de la DIAN (catalogo-vpfe.dian.gov.co).

Implementa el flujo de autenticación por token temporal descrito en la
documentación del portal y la descarga del reporte RADIAN:

    1. `solicitar_token(...)`  → la DIAN envía un correo con un enlace de acceso.
    2. (el correo se lee con `app.radian_auto.email_token`)
    3. `activar_sesion(auth_url)` → se abre el enlace y queda una sesión activa.
    4. `descargar_reporte(...)` → se descarga el Excel del catálogo RADIAN.

Estructura del enlace de acceso (Fase 4 del flujo de autenticación):

    {portal}/User/AuthToken?pk=<CEDULA>%7C<NIT_EMPRESA>&rk=<NIT_CON_DV>&token=<UUID>

Notas de implementación
-----------------------
Las piezas deterministas y verificables están completas y probadas:
- El dígito de verificación del NIT (algoritmo oficial de la DIAN).
- La construcción y el parseo del enlace de acceso (`AuthToken`).

Los detalles HTTP del portal (la ruta exacta del formulario de ingreso y la del
endpoint de descarga, además de los nombres de los campos) NO están publicados
y pueden cambiar. Por eso son **configurables** (vía `DianConfig` / variables de
entorno) en lugar de estar incrustados: así se calibran contra el portal real
sin tocar el código. Los valores por defecto siguen la convención observada del
portal (ASP.NET MVC) y deben confirmarse en producción.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import requests

from app.config import DIAN_PORTAL_URL

logger = logging.getLogger(__name__)

# Pesos del algoritmo oficial de la DIAN para el dígito de verificación del NIT.
# Se aplican de derecha a izquierda sobre los dígitos del NIT (sin DV).
_PESOS_DV = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)

_AUTH_TOKEN_PATH = "/User/AuthToken"
_TIMEOUT = 30

# El portal de la DIAN está detrás de un WAF que rechaza (HTTP 403) las
# peticiones que no parecen venir de un navegador real. Por eso el cliente se
# presenta con un juego de cabeceras de navegador. (No fijamos Accept-Encoding:
# se deja que `requests` anuncie solo lo que sabe descomprimir —gzip/deflate—,
# evitando recibir brotli sin poder decodificarlo.)
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS = {
    "User-Agent": _DEFAULT_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": (
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def _headers_navegacion(sec_fetch_site: str) -> dict:
    """Cabeceras `Sec-Fetch-*` propias de una navegación de página (GET de un
    enlace/descarga). Se combinan por petición con las de la sesión."""
    return {
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": sec_fetch_site,
        "Sec-Fetch-User": "?1",
    }


class DianError(Exception):
    """Error genérico al interactuar con el portal de la DIAN."""


class DianAuthError(DianError):
    """Error de autenticación con el portal de la DIAN."""


class DianDownloadError(DianError):
    """Error al descargar el reporte RADIAN."""


# ---------------------------------------------------------------------------
# Utilidades deterministas (NIT, dígito de verificación, enlace de acceso)
# ---------------------------------------------------------------------------

def _solo_digitos(valor: str) -> str:
    """Deja únicamente los dígitos de un identificador (quita puntos/guiones)."""
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def calcular_digito_verificacion(nit: str) -> int:
    """Calcula el dígito de verificación de un NIT según el algoritmo de la DIAN.

    Ejemplo: ``calcular_digito_verificacion("901331657") == 7`` (1INVEST SAS).

    Raises:
        ValueError: si el NIT no contiene dígitos o es demasiado largo.
    """
    digitos = _solo_digitos(nit)
    if not digitos:
        raise ValueError("NIT vacío o sin dígitos para calcular el DV.")
    if len(digitos) > len(_PESOS_DV):
        raise ValueError(f"NIT demasiado largo para el algoritmo de DV: {nit!r}")

    # Recorrer de derecha a izquierda multiplicando por los pesos.
    suma = sum(int(d) * peso for d, peso in zip(reversed(digitos), _PESOS_DV))
    residuo = suma % 11
    return residuo if residuo < 2 else 11 - residuo


def nit_con_dv(nit: str) -> str:
    """Retorna el NIT con su dígito de verificación concatenado (parámetro `rk`).

    Ejemplo: ``nit_con_dv("901331657") == "9013316577"``.
    """
    digitos = _solo_digitos(nit)
    return f"{digitos}{calcular_digito_verificacion(digitos)}"


def construir_pk(nit_representante: str, nit_empresa: str) -> str:
    """Construye el parámetro `pk` (`CEDULA|NIT_EMPRESA`) sin codificar.

    El separador es la tubería ``|``; al ir en una URL se codifica como ``%7C``
    (lo hace `construir_auth_url`).
    """
    return f"{_solo_digitos(nit_representante)}|{_solo_digitos(nit_empresa)}"


def construir_auth_url(
    token: str,
    nit_representante: str,
    nit_empresa: str,
    portal_url: str = DIAN_PORTAL_URL,
) -> str:
    """Construye el enlace de acceso `AuthToken` a partir de sus componentes.

    Útil para pruebas y para reconstruir el enlace si solo se conoce el token.
    En el flujo normal el enlace llega completo en el correo y se usa tal cual.
    """
    params = {
        "pk": construir_pk(nit_representante, nit_empresa),
        "rk": nit_con_dv(nit_empresa),
        "token": token,
    }
    # quote_via=quote para que `|` → %7C (urlencode usa quote_plus por defecto).
    query = urlencode(params, quote_via=quote)
    return f"{portal_url.rstrip('/')}{_AUTH_TOKEN_PATH}?{query}"


def _nombre_desde_content_disposition(valor: str) -> str:
    """Extrae el nombre de archivo de un encabezado Content-Disposition."""
    if not valor:
        return ""
    # filename*=UTF-8''nombre.xlsx  ó  filename="nombre.xlsx"
    m = re.search(r"filename\*=(?:[^']*'')?([^;\r\n]+)", valor, re.IGNORECASE)
    if not m:
        m = re.search(r'filename="?([^";\r\n]+)"?', valor, re.IGNORECASE)
    if not m:
        return ""
    nombre = m.group(1).strip().strip('"')
    return unquote(nombre)


def parsear_auth_url(url: str) -> dict:
    """Extrae ``{pk, rk, token}`` de un enlace de acceso `AuthToken`.

    Acepta tanto la URL completa como una cadena que la contenga. Retorna un dict
    vacío si la URL no trae los parámetros esperados.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    datos = {k: qs[k][0] for k in ("pk", "rk", "token") if qs.get(k)}
    return datos


# ---------------------------------------------------------------------------
# Cliente del portal
# ---------------------------------------------------------------------------

class DianClient:
    """Cliente con sesión persistente para el portal RADIAN de la DIAN.

    Mantiene las cookies entre pasos (solicitar token → activar sesión →
    descargar) usando un único ``requests.Session``.
    """

    def __init__(
        self,
        portal_url: str = DIAN_PORTAL_URL,
        *,
        login_path: str = "/",
        login_fields: Optional[dict] = None,
        descarga_path: str = "/Document/DownloadZipFiles",
        descarga_params: Optional[dict] = None,
        session: Optional[requests.Session] = None,
        timeout: int = _TIMEOUT,
        user_agent: str = "",
        extra_headers: Optional[dict] = None,
    ):
        self.portal_url = portal_url.rstrip("/")
        self.login_path = login_path
        self.login_fields = login_fields or {}
        self.descarga_path = descarga_path
        self.descarga_params = descarga_params or {}
        self.timeout = timeout
        self.session = session or requests.Session()
        # Presentarse como un navegador real: el WAF de la DIAN devuelve 403 a
        # los clientes que no lo parecen. user_agent / extra_headers permiten
        # calibrar si el portal endurece el filtro, sin tocar el código.
        self.session.headers.update(_BROWSER_HEADERS)
        if user_agent.strip():
            self.session.headers["User-Agent"] = user_agent.strip()
        if extra_headers:
            self.session.headers.update(extra_headers)
        self._autenticado = False
        # Nombre de archivo sugerido por la última descarga (Content-Disposition).
        self.ultimo_archivo: str = ""

    # ------------------------------------------------------------------
    # Paso 1 — solicitar el token (dispara el correo de la DIAN)
    # ------------------------------------------------------------------

    def solicitar_token(
        self,
        tipo_identificacion: str,
        nit_representante: str,
        nit_empresa: str,
    ) -> None:
        """Envía las credenciales para que la DIAN genere y envíe el token.

        Hace un POST al formulario de ingreso del portal con el perfil
        «Empresa». Tras un POST válido, la DIAN envía un correo con el enlace de
        acceso al representante legal registrado en el RUT.

        Los nombres de los campos del formulario son configurables (`login_fields`)
        porque no están publicados; los valores por defecto siguen la convención
        del portal y deben confirmarse contra el portal real.

        Raises:
            DianAuthError: si la solicitud de red falla.
        """
        # Mapa de campos por defecto → puede sobreescribirse vía login_fields.
        defaults = {
            "campo_tipo_id": "typeDocument",
            "campo_nit_representante": "documentNumber",
            "campo_nit_empresa": "companyDocumentNumber",
            "perfil": "1",  # 1 = Empresa
            "campo_perfil": "profile",
        }
        cfg = {**defaults, **self.login_fields}

        payload = {
            cfg["campo_tipo_id"]: tipo_identificacion,
            cfg["campo_nit_representante"]: _solo_digitos(nit_representante),
            cfg["campo_nit_empresa"]: _solo_digitos(nit_empresa),
            cfg["campo_perfil"]: cfg["perfil"],
        }
        url = f"{self.portal_url}{self.login_path}"
        try:
            resp = self.session.post(url, data=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise DianAuthError(
                f"No se pudo solicitar el token a la DIAN ({url}): {exc}"
            ) from exc
        logger.info(
            "Solicitud de token enviada a la DIAN (empresa NIT=%s). HTTP %s",
            _solo_digitos(nit_empresa), resp.status_code,
        )

    # ------------------------------------------------------------------
    # Paso 3 — activar la sesión con el enlace recibido por correo
    # ------------------------------------------------------------------

    def activar_sesion(self, auth_url: str) -> None:
        """Abre el enlace de acceso para establecer la sesión autenticada.

        Args:
            auth_url: URL `AuthToken` completa recibida en el correo de la DIAN.

        Raises:
            DianAuthError: si el enlace es inválido, expiró o la red falla.
        """
        datos = parsear_auth_url(auth_url)
        if "token" not in datos:
            raise DianAuthError(
                "El enlace de acceso no contiene un token válido: "
                f"{auth_url[:80]}…"
            )
        try:
            # Sec-Fetch-Site=none: navegación de nivel superior (como abrir el
            # enlace desde el correo), no una petición incrustada.
            resp = self.session.get(
                auth_url,
                timeout=self.timeout,
                allow_redirects=True,
                headers=_headers_navegacion("none"),
            )
        except requests.RequestException as exc:
            raise DianAuthError(
                f"No se pudo conectar con el portal de la DIAN: {exc}"
            ) from exc

        if resp.status_code == 403:
            # El portal bloqueó la petición. Causas habituales, en orden:
            raise DianAuthError(
                "El portal de la DIAN rechazó el acceso (HTTP 403 Forbidden). "
                "Las causas más frecuentes son: (1) el token ya expiró —vence a "
                "los 60 minutos—, así que genera uno nuevo y pégalo de inmediato; "
                "(2) el enlace ya se abrió antes (es de un solo uso); o (3) la "
                "petición sale desde una IP fuera de Colombia o el portal la "
                "tomó como automatizada (filtro antibot/WAF). Si el token es "
                "reciente y de un solo uso, ejecuta la importación desde una red "
                "en Colombia."
            )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise DianAuthError(
                "No se pudo activar la sesión con el enlace de la DIAN "
                f"(HTTP {resp.status_code}): {exc}"
            ) from exc

        # El portal devuelve un error visible cuando el token expiró o es inválido.
        cuerpo = (resp.text or "").lower()
        if "token" in cuerpo and ("expir" in cuerpo or "inválid" in cuerpo or "invalid" in cuerpo):
            raise DianAuthError(
                "El token de la DIAN expiró o es inválido; reinicia el flujo."
            )
        self._autenticado = True
        logger.info("Sesión DIAN activada correctamente (token=%s).", datos.get("token", "")[:8])

    @property
    def autenticado(self) -> bool:
        return self._autenticado

    # ------------------------------------------------------------------
    # Paso 4 — descargar el reporte RADIAN
    # ------------------------------------------------------------------

    def descargar_reporte(
        self,
        fecha_desde: str,
        fecha_hasta: str,
    ) -> bytes:
        """Descarga el reporte RADIAN del rango de fechas y retorna sus bytes.

        Args:
            fecha_desde: fecha inicial (YYYY-MM-DD).
            fecha_hasta: fecha final (YYYY-MM-DD).

        Returns:
            Contenido binario del archivo descargado (.xlsx / .zip).

        Raises:
            DianAuthError:     si la sesión no está activa.
            DianDownloadError: si la descarga falla o no retorna un archivo.
        """
        if not self._autenticado:
            raise DianAuthError(
                "No hay sesión DIAN activa. Llama a activar_sesion() primero."
            )

        params = {
            "startDate": fecha_desde,
            "endDate": fecha_hasta,
            **self.descarga_params,
        }
        url = f"{self.portal_url}{self.descarga_path}"
        try:
            # Same-origin: ya estamos dentro del portal tras activar la sesión.
            resp = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                headers=_headers_navegacion("same-origin"),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise DianDownloadError(
                f"No se pudo descargar el reporte RADIAN ({url}): {exc}"
            ) from exc

        contenido = resp.content or b""
        ctype = resp.headers.get("Content-Type", "")
        # Un HTML de respuesta casi siempre indica que la descarga no procedió
        # (sesión vencida, parámetros incorrectos, etc.).
        if not contenido or "text/html" in ctype:
            raise DianDownloadError(
                "La descarga del reporte RADIAN no devolvió un archivo "
                f"(Content-Type={ctype!r}, {len(contenido)} bytes). "
                "Revisa la configuración del endpoint de descarga."
            )
        self.ultimo_archivo = _nombre_desde_content_disposition(
            resp.headers.get("Content-Disposition", "")
        )
        logger.info(
            "Reporte RADIAN descargado: %d bytes (%s).", len(contenido), ctype
        )
        return contenido
