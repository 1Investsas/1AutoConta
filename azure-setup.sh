#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Azure Infrastructure Setup — contable-auto (1Contigo)
# ═══════════════════════════════════════════════════════════════════════════
#
# Ejecuta estos comandos en Azure Cloud Shell (https://shell.azure.com)
# o en una terminal con Azure CLI instalado y autenticado (az login).
#
# IMPORTANTE: Revisa y ajusta las variables antes de ejecutar.
# ═══════════════════════════════════════════════════════════════════════════

# ----- Variables — AJUSTAR SEGÚN TU ENTORNO -----
RESOURCE_GROUP="rg-1contigo"
LOCATION="eastus"                    # Región más cercana a Colombia
APP_NAME="1Contigo"                  # → https://1contigo.azurewebsites.net
APP_SERVICE_PLAN="plan-1contigo"
SKU="B1"                             # Basic tier (~$13/mes)

SQL_SERVER_NAME="sql-1contigo"       # → sql-1contigo.database.windows.net
SQL_DB_NAME="contabledb"
SQL_ADMIN_USER="contable_admin"
SQL_ADMIN_PASS="CambiarEstaContraseña!2026"   # ⚠️ CAMBIAR antes de ejecutar

STORAGE_ACCOUNT="st1contigo"         # Solo letras minúsculas y números, 3-24 chars
STORAGE_CONTAINER="contable-auto"

# ═══════════════════════════════════════════════════════════════════════════
# 1. GRUPO DE RECURSOS
# ═══════════════════════════════════════════════════════════════════════════
echo "📦 Creando grupo de recursos..."
az group create \
    --name $RESOURCE_GROUP \
    --location $LOCATION

# ═══════════════════════════════════════════════════════════════════════════
# 2. AZURE SQL DATABASE
# ═══════════════════════════════════════════════════════════════════════════
echo "🗄️ Creando servidor SQL..."
az sql server create \
    --name $SQL_SERVER_NAME \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --admin-user $SQL_ADMIN_USER \
    --admin-password "$SQL_ADMIN_PASS"

echo "🔓 Permitiendo acceso desde servicios de Azure..."
az sql server firewall-rule create \
    --server $SQL_SERVER_NAME \
    --resource-group $RESOURCE_GROUP \
    --name AllowAzureServices \
    --start-ip-address 0.0.0.0 \
    --end-ip-address 0.0.0.0

echo "📊 Creando base de datos (Basic tier, ~$5/mes)..."
az sql db create \
    --server $SQL_SERVER_NAME \
    --resource-group $RESOURCE_GROUP \
    --name $SQL_DB_NAME \
    --edition Basic \
    --capacity 5 \
    --max-size 2GB

# ═══════════════════════════════════════════════════════════════════════════
# 3. AZURE BLOB STORAGE
# ═══════════════════════════════════════════════════════════════════════════
echo "📁 Creando cuenta de almacenamiento..."
az storage account create \
    --name $STORAGE_ACCOUNT \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --sku Standard_LRS \
    --kind StorageV2

echo "📂 Creando contenedor..."
STORAGE_KEY=$(az storage account keys list \
    --account-name $STORAGE_ACCOUNT \
    --resource-group $RESOURCE_GROUP \
    --query "[0].value" -o tsv)

az storage container create \
    --name $STORAGE_CONTAINER \
    --account-name $STORAGE_ACCOUNT \
    --account-key "$STORAGE_KEY"

# ═══════════════════════════════════════════════════════════════════════════
# 4. AZURE APP SERVICE (Python)
# ═══════════════════════════════════════════════════════════════════════════
echo "🖥️ Creando plan de App Service..."
az appservice plan create \
    --name $APP_SERVICE_PLAN \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --sku $SKU \
    --is-linux

echo "🚀 Creando App Service (Python 3.11)..."
az webapp create \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --plan $APP_SERVICE_PLAN \
    --runtime "PYTHON:3.11"

# ═══════════════════════════════════════════════════════════════════════════
# 5. CONFIGURAR VARIABLES DE ENTORNO
# ═══════════════════════════════════════════════════════════════════════════
echo "⚙️ Configurando variables de entorno..."

# Obtener connection strings
STORAGE_CONN=$(az storage account show-connection-string \
    --name $STORAGE_ACCOUNT \
    --resource-group $RESOURCE_GROUP \
    --query connectionString -o tsv)

SQL_CONN="Driver={ODBC Driver 18 for SQL Server};Server=tcp:${SQL_SERVER_NAME}.database.windows.net,1433;Database=${SQL_DB_NAME};Uid=${SQL_ADMIN_USER};Pwd=${SQL_ADMIN_PASS};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"

az webapp config appsettings set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --settings \
        USE_SQLITE="false" \
        DATABASE_URL="$SQL_CONN" \
        AZURE_STORAGE_CONNECTION_STRING="$STORAGE_CONN" \
        AZURE_STORAGE_CONTAINER="$STORAGE_CONTAINER" \
        NIT_EMPRESA="901331657" \
        NOMBRE_EMPRESA="1INVEST SAS" \
        FLASK_SECRET_KEY="$(openssl rand -hex 32)" \
        LOG_LEVEL="INFO" \
        SCM_DO_BUILD_DURING_DEPLOYMENT="true"

# ═══════════════════════════════════════════════════════════════════════════
# 6. CONFIGURAR STARTUP COMMAND
# ═══════════════════════════════════════════════════════════════════════════
echo "🔧 Configurando comando de inicio..."
az webapp config set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --startup-file "startup.sh"

# ═══════════════════════════════════════════════════════════════════════════
# 7. RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Infraestructura creada exitosamente"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  🌐 Web App:     https://${APP_NAME}.azurewebsites.net"
echo "  🗄️ SQL Server:  ${SQL_SERVER_NAME}.database.windows.net"
echo "  📊 Base datos:   ${SQL_DB_NAME}"
echo "  📁 Storage:      ${STORAGE_ACCOUNT}"
echo ""
echo "  📋 PRÓXIMOS PASOS:"
echo "  1. Sube el código a GitHub"
echo "  2. En GitHub repo → Settings → Secrets → Actions:"
echo "     Agrega AZURE_WEBAPP_PUBLISH_PROFILE con el contenido de:"
echo "     az webapp deployment list-publishing-profiles \\"
echo "         --name $APP_NAME \\"
echo "         --resource-group $RESOURCE_GROUP \\"
echo "         --xml"
echo ""
echo "  3. Sube archivos maestros a Blob Storage:"
echo "     az storage blob upload \\"
echo "         --account-name $STORAGE_ACCOUNT \\"
echo "         --container-name $STORAGE_CONTAINER \\"
echo "         --name 'data/Listado_de_Terceros.xlsx' \\"
echo "         --file './data/Listado_de_Terceros.xlsx'"
echo ""
echo "     (Repite para Listado_de_Cuentas_Contables.xlsx y"
echo "      Tipos_de_comprobante_contable.xlsx)"
echo ""
echo "═══════════════════════════════════════════════════════════════"
