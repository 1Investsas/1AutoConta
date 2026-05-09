#!/bin/bash
# Azure App Service startup script for contable-auto

# Install ODBC Driver + pyodbc only if Azure SQL is configured
if [ "$USE_SQLITE" = "false" ]; then
    echo "Azure SQL mode - installing ODBC driver..."
    curl -s https://packages.microsoft.com/keys/microsoft.asc | apt-key add - 2>/dev/null
    curl -s https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list 2>/dev/null
    apt-get update -qq 2>/dev/null
    ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc-dev 2>/dev/null || true
    pip install pyodbc 2>/dev/null || true
    echo "ODBC setup complete."
fi

# Start gunicorn - use PORT env var set by Azure
PORT="${PORT:-8000}"
exec gunicorn --bind=0.0.0.0:"$PORT" --timeout=600 --workers=2 --access-logfile=- --error-logfile=- "app:app"
