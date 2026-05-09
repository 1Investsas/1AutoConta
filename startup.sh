#!/bin/bash
# Azure App Service startup script

# Install ODBC Driver 18 if not present
if ! odbcinst -q -d -n 'ODBC Driver 18 for SQL Server' > /dev/null 2>&1; then
    echo 'Installing ODBC Driver 18...'
    curl -s https://packages.microsoft.com/keys/microsoft.asc | apt-key add - 2>/dev/null
    curl -s https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list
    apt-get update -qq
    ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc-dev 2>/dev/null || true
    echo 'ODBC Driver installed.'
fi

# Start gunicorn
exec gunicorn --bind=0.0.0.0:8000 --timeout=600 --workers=2 --access-logfile=- --error-logfile=- 'app:app'
