"""
Lectura del correo de la DIAN para extraer el enlace de acceso (token).

Tras solicitar el token, la DIAN envía un correo (remitente
``facturacionelectronica@dian.gov.co``, asunto «Token Acceso DIAN») con un
enlace de la forma::

    {portal}/User/AuthToken?pk=...&rk=...&token=...

Este módulo se conecta por IMAP al buzón del representante legal, localiza ese
correo y extrae el enlace. Funciona con Gmail (usando una **contraseña de
aplicación**) y con cualquier proveedor IMAP estándar.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional

from app.config import (
    DIAN_EMAIL_ASUNTO,
    DIAN_EMAIL_ESPERA_SEG,
    DIAN_EMAIL_INTERVALO_SEG,
    DIAN_EMAIL_REMITENTE,
    DIAN_IMAP_HOST,
    DIAN_IMAP_PORT,
)

logger = logging.getLogger(__name__)

# Captura el enlace AuthToken aunque venga rodeado de comillas o etiquetas HTML.
_RE_AUTH_URL = re.compile(
    r"https?://[^\s\"'<>]*?/User/AuthToken\?[^\s\"'<>]+",
    re.IGNORECASE,
)


class EmailTokenError(Exception):
    """Error al leer el correo del token de la DIAN."""


@dataclass
class ImapConfig:
    """Parámetros de conexión IMAP para leer el correo del token."""

    host: str = DIAN_IMAP_HOST
    port: int = DIAN_IMAP_PORT
    usuario: str = ""
    password: str = ""
    carpeta: str = "INBOX"
    remitente: str = DIAN_EMAIL_REMITENTE
    asunto: str = DIAN_EMAIL_ASUNTO


def _decode(valor: Optional[str]) -> str:
    """Decodifica un encabezado MIME (asunto/remitente) a texto plano."""
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except Exception:
        return valor


def _extraer_cuerpo(msg: email.message.Message) -> str:
    """Retorna el cuerpo del correo (prefiere texto plano; cae a HTML)."""
    partes_texto, partes_html = [], []
    if msg.is_multipart():
        for parte in msg.walk():
            if parte.get_content_maintype() == "multipart":
                continue
            if parte.get("Content-Disposition", "").startswith("attachment"):
                continue
            ctype = parte.get_content_type()
            try:
                payload = parte.get_payload(decode=True)
            except Exception:
                continue
            if not payload:
                continue
            charset = parte.get_content_charset() or "utf-8"
            texto = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                partes_texto.append(texto)
            elif ctype == "text/html":
                partes_html.append(texto)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            texto = payload.decode(charset, errors="replace")
            (partes_html if msg.get_content_type() == "text/html" else partes_texto).append(texto)

    return "\n".join(partes_texto) or "\n".join(partes_html)


def extraer_enlace_token(cuerpo: str) -> Optional[str]:
    """Extrae el enlace de acceso `AuthToken` del cuerpo de un correo.

    Maneja entidades HTML (p. ej. ``&amp;`` → ``&``) para que el enlace quede
    utilizable tal cual. Retorna None si no se encuentra.
    """
    if not cuerpo:
        return None
    match = _RE_AUTH_URL.search(unescape(cuerpo))
    return match.group(0) if match else None


def _buscar_uids(conn: imaplib.IMAP4_SSL, cfg: ImapConfig, desde: datetime) -> list[bytes]:
    """Busca UIDs de correos del token recibidos desde `desde` (criterio IMAP)."""
    # SINCE usa granularidad de día; el filtrado fino por hora se hace después.
    criterios = [
        "FROM", f'"{cfg.remitente}"',
        "SUBJECT", f'"{cfg.asunto}"',
        "SINCE", desde.strftime("%d-%b-%Y"),
    ]
    typ, data = conn.uid("search", None, *criterios)
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fecha_msg(msg: email.message.Message) -> Optional[datetime]:
    """Fecha del correo en UTC (o None si no se puede parsear)."""
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def obtener_enlace_token(
    cfg: ImapConfig,
    *,
    no_antes_de: Optional[datetime] = None,
) -> Optional[str]:
    """Busca una sola vez el correo del token y retorna su enlace (o None).

    Args:
        cfg:         configuración IMAP.
        no_antes_de: si se indica, ignora correos anteriores a esta marca
                     (para no usar un token viejo de una corrida previa).

    Raises:
        EmailTokenError: si falla la conexión o autenticación IMAP.
    """
    if not cfg.usuario or not cfg.password:
        raise EmailTokenError(
            "Faltan credenciales de correo (usuario/contraseña) para leer el "
            "token de la DIAN."
        )

    no_antes_de = no_antes_de or (datetime.now(timezone.utc) - timedelta(minutes=10))
    try:
        conn = imaplib.IMAP4_SSL(cfg.host, cfg.port)
    except OSError as exc:
        raise EmailTokenError(f"No se pudo conectar al servidor IMAP {cfg.host}: {exc}") from exc

    try:
        try:
            conn.login(cfg.usuario, cfg.password)
        except imaplib.IMAP4.error as exc:
            raise EmailTokenError(
                "Autenticación IMAP fallida. Si usas Gmail, genera una "
                "contraseña de aplicación y actívala en la configuración."
            ) from exc

        conn.select(cfg.carpeta, readonly=True)
        uids = _buscar_uids(conn, cfg, no_antes_de.astimezone(timezone.utc))
        # Revisar del más reciente al más antiguo.
        for uid in reversed(uids):
            typ, data = conn.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not data or not data[0]:
                continue
            msg = email.message_from_bytes(data[0][1])
            fecha = _fecha_msg(msg)
            if fecha and fecha < no_antes_de:
                continue
            enlace = extraer_enlace_token(_extraer_cuerpo(msg))
            if enlace:
                logger.info("Enlace de token encontrado en correo del %s.", fecha)
                return enlace
        return None
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def esperar_enlace_token(
    cfg: ImapConfig,
    *,
    no_antes_de: Optional[datetime] = None,
    espera_seg: int = DIAN_EMAIL_ESPERA_SEG,
    intervalo_seg: int = DIAN_EMAIL_INTERVALO_SEG,
    _sleep=time.sleep,
) -> str:
    """Sondea el buzón hasta encontrar el correo del token o agotar el tiempo.

    Args:
        cfg:           configuración IMAP.
        no_antes_de:   marca de tiempo mínima del correo (por defecto: ahora).
        espera_seg:    tiempo total máximo de espera.
        intervalo_seg: intervalo entre sondeos.

    Returns:
        El enlace de acceso `AuthToken`.

    Raises:
        EmailTokenError: si no llega el correo dentro de la ventana de espera.
    """
    inicio = time.monotonic()
    no_antes_de = no_antes_de or datetime.now(timezone.utc)
    intentos = 0
    while True:
        intentos += 1
        enlace = obtener_enlace_token(cfg, no_antes_de=no_antes_de)
        if enlace:
            return enlace
        if time.monotonic() - inicio >= espera_seg:
            raise EmailTokenError(
                f"No llegó el correo del token de la DIAN tras {espera_seg}s "
                f"({intentos} intento(s)). Verifica el buzón y las credenciales."
            )
        _sleep(intervalo_seg)
