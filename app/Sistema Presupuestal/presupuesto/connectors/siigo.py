"""Conector Siigo Nube (Colombia).

Autenticación: POST https://api.siigo.com/auth con {username, access_key}
y header Partner-Id. Devuelve access_token (Bearer) válido 24 h.
Docs: https://developers.siigo.com/docs/siigoapi

config esperado:
{
  "username": "correo@empresa.com",
  "access_key": "clave-generada-en-siigo",
  "partner_id": "1ContaBot",
  "base_url": "https://api.siigo.com"   # opcional
}
"""
import calendar
from collections import defaultdict

import requests

from .base import ConectorContable, MovimientoContable

TIMEOUT = 30


class ConectorSiigo(ConectorContable):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = self.config.get("base_url", "https://api.siigo.com").rstrip("/")
        self.partner_id = self.config.get("partner_id", "1ContaBot")
        self._token: str | None = None

    # ---------- Autenticación ----------
    def _autenticar(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            f"{self.base_url}/auth",
            json={
                "username": self.config["username"],
                "access_key": self.config["access_key"],
            },
            headers={"Partner-Id": self.partner_id},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._autenticar()}",
            "Partner-Id": self.partner_id,
        }

    def probar_conexion(self) -> tuple[bool, str]:
        try:
            self._autenticar()
            return True, "Conexión con Siigo exitosa."
        except Exception as e:  # noqa: BLE001
            return False, f"Error autenticando con Siigo: {e}"

    # ---------- Datos ----------
    def _paginado(self, path: str, params: dict) -> list[dict]:
        """Recorre la paginación estándar de Siigo (page/page_size)."""
        resultados, page = [], 1
        while True:
            resp = requests.get(
                f"{self.base_url}{path}",
                params={**params, "page": page, "page_size": 100},
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            resultados.extend(data.get("results", []))
            pagination = data.get("pagination", {})
            total = pagination.get("total_results", len(resultados))
            if len(resultados) >= total or not data.get("results"):
                break
            page += 1
        return resultados

    def obtener_movimientos(self, anio: int, mes: int) -> list[MovimientoContable]:
        """Agrega los comprobantes contables (journals) del mes por cuenta.

        Nota: según el plan de Siigo puede convenir usar el endpoint de
        balance de prueba en su lugar; la interfaz no cambia.
        """
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        desde = f"{anio}-{mes:02d}-01"
        hasta = f"{anio}-{mes:02d}-{ultimo_dia:02d}"

        journals = self._paginado(
            "/v1/journals", {"date_start": desde, "date_end": hasta}
        )

        acumulado: dict[str, dict] = defaultdict(lambda: {"nombre": "", "valor": 0.0})
        for j in journals:
            for item in j.get("items", []):
                cuenta = item.get("account", {})
                codigo = str(cuenta.get("code", "")).strip()
                if not codigo:
                    continue
                debito = float(item.get("debit", 0) or 0)
                credito = float(item.get("credit", 0) or 0)
                # Naturaleza según PUC: clase 4 (ingresos) crédito neto;
                # clases 5, 6, 7 débito neto. Otras clases: débito - crédito.
                if codigo.startswith("4"):
                    neto = credito - debito
                else:
                    neto = debito - credito
                acumulado[codigo]["nombre"] = cuenta.get("name", codigo)
                acumulado[codigo]["valor"] += neto

        return [
            MovimientoContable(
                codigo_cuenta=codigo,
                nombre_cuenta=info["nombre"],
                valor=round(info["valor"], 2),
                fecha=hasta,
            )
            for codigo, info in acumulado.items()
        ]
