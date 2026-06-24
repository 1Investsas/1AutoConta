"""
Importación automática de RADIAN desde el portal de la DIAN.

Este subpaquete implementa el flujo que permite a la app descargar el reporte
RADIAN (`https://catalogo-vpfe.dian.gov.co`) de forma automática y diaria, sin
que un usuario tenga que entrar al portal y subir el Excel manualmente.

Módulos:
- ``dian_client``   : cliente HTTP del portal (autenticación por token + descarga).
- ``email_token``   : lectura del correo de la DIAN para extraer el enlace de acceso.
- ``config_dian``   : configuración por empresa (credenciales, correo, horario).
- ``auto_importador``: orquesta el flujo completo y lo conecta con el pipeline.
- ``scheduler``     : disparo diario (hilo en segundo plano) y utilidades de cron.
"""
