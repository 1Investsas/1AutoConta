"""Conector Alegra (Colombia/Latam).

Autenticación: Basic Auth con correo:token sobre https://api.alegra.com/api/v1
Docs: https://developer.alegra.com/

config esperado:
{
  "email": "correo@empresa.com",
  "token": "token-generado-en-alegra",
  "base_url": "https://api.alegra.com/api/v1"   # opcional
}
"""
import calendar
from collections import defaultdict

import requests
from requests.auth import HTTPBasicAuth

from .base import ConectorContable, MovimientoContable

TIMEOUT = 30


class ConectorAlegra(ConectorContable):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = self.config.get(
            "base_url", "https://api.alegra.com/api/v1"
        ).rstrip("/")
        self.auth = HTTPBasicAuth(self.config["email"], self.config["token"])

    def probar_conexion(self) -> tuple[bool, str]:
        try:
            resp = requests.get(
                f"{self.base_url}/company", auth=self.auth, timeout=TIMEOUT
            )
            resp.raise_for_status()
            return True, f"Conexión con Alegra exitosa ({resp.json().get('name', '')})."
        except Exception as e:  # noqa: BLE001
            return False, f"Error autenticando con Alegra: {e}"

    def _paginado(self, path: str, params: dict) -> list[dict]:
        """Paginación estándar de Alegra (start/limit)."""
        resultados, start, limit = [], 0, 30
        while True:
            resp = requests.get(
                f"{self.base_url}{path}",
                params={**params, "start": start, "limit": limit},
                auth=self.auth,
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            lote = resp.json()
            if not lote:
                break
            resultados.extend(lote)
            if len(lote) < limit:
                break
            start += limit
        return resultados

    def obtener_movimientos(self, anio: int, mes: int) -> list[MovimientoContable]:
        """Agrega los comprobantes contables (journals) del mes por cuenta."""
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        desde = f"{anio}-{mes:02d}-01"
        hasta = f"{anio}-{mes:02d}-{ultimo_dia:02d}"

        journals = self._paginado(
            "/journals", {"date_afterOrNow": desde, "date_beforeOrNow": hasta}
        )

        acumulado: dict[str, dict] = defaultdict(lambda: {"nombre": "", "valor": 0.0})
        for j in journals:
            for entrada in j.get("entries", []):
                cuenta = entrada.get("account", {}) or {}
                codigo = str(cuenta.get("code") or cuenta.get("id") or "").strip()
                if not codigo:
                    continue
                debito = float(entrada.get("debit", 0) or 0)
                credito = float(entrada.get("credit", 0) or 0)
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
