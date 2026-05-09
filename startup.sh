#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Startup script para Azure App Service (Linux)
# ═══════════════════════════════════════════════════════════════════════════
# Azure App Service ejecuta este script al iniciar el contenedor.
# Instala el driver ODBC (si no existe) y arranca gunicorn.

# Instalar ODBC Driver 18 si no está presente
if ! odbcinst -q -d -n "ODBC Driver 18 for SQL Server" > /dev/null 2>&1; then
    echo "Instalando ODBC Driver 18 for SQL Server..."
    curl -s https://packages.microsoft.com/keys/microsoft.asc | apt-key add - 2>/dev/null
    curl -s https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list
    apt-get update -qq
    ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc-dev
    echo "ODBC Driver instalado."
fi

# Arrancar gunicorn
echo "Iniciando contable-auto con gunicorn..."
gunicorn \
    --bind=0.0.0.0:8000 \
    --timeout=600 \
    --workers=2 \
    --access-logfile=- \
    --error-logfile=- \
    "app.web:create_app()"
