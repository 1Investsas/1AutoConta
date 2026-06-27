#!/bin/bash
# Azure App Service startup script for contable-auto
# Python dependencies are bundled into ./vendor during the GitHub Actions
# deployment and added to sys.path in application.py (no server-side build).

# Provision the SQL Server ODBC driver only if Azure SQL is configured.
# pyodbc viaja empaquetado en ./vendor (ver requirements.txt), así que aquí solo
# nos ocupamos del driver de sistema, que vive fuera de /home y no se persiste.
if [ "$USE_SQLITE" = "false" ]; then
    # La imagen base de Python en Azure ya trae "ODBC Driver 18 for SQL Server".
    # Solo corremos el apt (lento, depende de red) cuando realmente falta.
    if odbcinst -q -d 2>/dev/null | grep -q "ODBC Driver 18 for SQL Server"; then
        echo "ODBC Driver 18 for SQL Server ya presente - se omite la instalación."
    else
        echo "Azure SQL mode - installing ODBC driver..."
        curl -s https://packages.microsoft.com/keys/microsoft.asc | apt-key add - 2>/dev/null
        curl -s https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list 2>/dev/null
        apt-get update -qq 2>/dev/null
        ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc-dev 2>/dev/null \
            || echo "WARNING: no se pudo instalar msodbcsql18; la conexión a Azure SQL puede fallar."
        echo "ODBC driver install complete."
    fi

    # pyodbc debería venir en ./vendor; solo lo instalamos si el wheel empaquetado
    # no es importable (p. ej. incompatibilidad de versión del runtime).
    if ! python -c "import pyodbc" 2>/dev/null; then
        echo "pyodbc no importable desde el bundle - instalando..."
        pip install pyodbc 2>/dev/null || echo "WARNING: no se pudo instalar pyodbc."
    fi
fi

# Start gunicorn - use PORT env var set by Azure
PORT="${PORT:-8000}"
exec gunicorn --bind=0.0.0.0:"$PORT" --timeout=600 --workers=2 --access-logfile=- --error-logfile=- "application:app"
