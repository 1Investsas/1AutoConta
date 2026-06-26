"""
Configuración por empresa de la importación automática de RADIAN.

Cada empresa guarda sus propios datos de acceso a la DIAN y al buzón de correo
donde llega el token, además del horario de la importación diaria. La
configuración se persiste como JSON en la columna ``dian_config`` de la tabla
``empresas`` (BD de sistema), igual que el resto de overrides de la empresa.

Seguridad: la contraseña del correo (contraseña de aplicación) puede guardarse
aquí para comodidad, pero se recomienda definirla en la variable de entorno
``DIAN_EMAIL_PASSWORD`` (que tiene prioridad) para no almacenarla en la BD.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app import config
from app.radian_auto.email_token import ImapConfig

# Tipos de identificación del representante legal (códigos de la DIAN).
TIPOS_IDENTIFICACION: dict[str, str] = {
    "13": "Cédula de ciudadanía",
    "22": "Cédula de extranjería",
    "41": "Pasaporte",
    "12": "Tarjeta de identidad",
    "50": "NIT de otro país",
}

TIPO_ID_DEFAULT = "13"


@dataclass
class DianConfig:
    """Configuración de acceso automático a RADIAN para una empresa."""

    habilitado: bool = False
    # Credenciales del portal
    tipo_identificacion: str = TIPO_ID_DEFAULT
    nit_representante: str = ""
    # NIT de la empresa en la DIAN; si está vacío se usa el NIT de la empresa.
    nit_empresa: str = ""
    # Buzón de correo (IMAP) donde llega el token de la DIAN
    email_user: str = ""
    email_password: str = ""
    imap_host: str = ""
    imap_port: int = 0
    email_carpeta: str = "INBOX"
    # Programación de la importación diaria
    hora: str = ""              # "HH:MM" (24h); vacío → RADIAN_HORA_DEFAULT
    dias_atras: int = 1         # cuántos días hacia atrás descargar
    # Overrides avanzados del portal (rutas/campos del formulario). Vacío =
    # usar los valores por defecto del cliente.
    login_path: str = ""
    descarga_path: str = ""

    # ------------------------------------------------------------------
    # (De)serialización
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, datos: dict | None) -> "DianConfig":
        if not datos:
            return cls()
        campos = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        limpio = {k: v for k, v in datos.items() if k in campos}
        return cls(**limpio)

    def to_dict(self) -> dict:
        return asdict(self)

    # ------------------------------------------------------------------
    # Valores efectivos (configuración + variables de entorno)
    # ------------------------------------------------------------------

    def nit_empresa_efectivo(self, empresa) -> str:
        """NIT de la empresa a usar (override de la config o el de la empresa)."""
        return (self.nit_empresa or "").strip() or empresa.nit

    def hora_efectiva(self) -> str:
        return (self.hora or "").strip() or config.RADIAN_HORA_DEFAULT

    def _email_user_efectivo(self) -> str:
        return (self.email_user or "").strip() or config.DIAN_EMAIL_USER

    def _email_password_efectivo(self) -> str:
        # La variable de entorno tiene prioridad para no depender de la BD.
        return config.DIAN_EMAIL_PASSWORD or self.email_password

    def imap_config(self) -> ImapConfig:
        """Construye la configuración IMAP efectiva para leer el token."""
        return ImapConfig(
            host=(self.imap_host or "").strip() or config.DIAN_IMAP_HOST,
            port=self.imap_port or config.DIAN_IMAP_PORT,
            usuario=self._email_user_efectivo(),
            password=self._email_password_efectivo(),
            carpeta=(self.email_carpeta or "INBOX").strip() or "INBOX",
            remitente=config.DIAN_EMAIL_REMITENTE,
            asunto=config.DIAN_EMAIL_ASUNTO,
        )

    def client_kwargs(self) -> dict:
        """kwargs específicos del portal para construir un ``DianClient``."""
        kwargs: dict = {}
        if self.login_path.strip():
            kwargs["login_path"] = self.login_path.strip()
        if self.descarga_path.strip():
            kwargs["descarga_path"] = self.descarga_path.strip()
        return kwargs

    def puede_solicitar(self) -> bool:
        """True si hay datos para solicitar el token (flujo manual con enlace).

        No requiere correo: basta el representante legal. El enlace se pega a mano.
        """
        return bool(self.nit_representante.strip())

    def configurado(self) -> bool:
        """True si hay datos mínimos para una importación 100% automática (IMAP)."""
        return bool(
            self.nit_representante.strip()
            and self._email_user_efectivo()
            and self._email_password_efectivo()
        )

    def faltantes(self) -> list[str]:
        """Lista legible de datos mínimos que faltan por configurar."""
        faltan = []
        if not self.nit_representante.strip():
            faltan.append("NIT del representante legal")
        if not self._email_user_efectivo():
            faltan.append("correo (usuario)")
        if not self._email_password_efectivo():
            faltan.append("contraseña de aplicación del correo")
        return faltan
