#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Azure Infrastructure Setup — 1ContaBot
# ═══════════════════════════════════════════════════════════════════════════
#
# Ejecuta estos comandos en Azure Cloud Shell (https://shell.azure.com)
# o en una terminal con Azure CLI instalado y autenticado (az login).
#
# IMPORTANTE: Revisa y ajusta las variables antes de ejecutar.
# ═══════════════════════════════════════════════════════════════════════════

# ----- Variables — AJUSTAR SEGÚN TU ENTORNO -----
RESOURCE_GROUP="rg-1contabot"
LOCATION="westus2"                    # Región más cercana a Colombia
APP_NAME="1contabot"                  # → https://1contabot.azurewebsites.net
APP_SERVICE_PLAN="plan-1contabot"
SKU="B1"                             # Basic tier (~$13/mes)

SQL_SERVER_NAME="sql-1contabot"       # → sql-1contabot.database.windows.net
SQL_DB_NAME="contabledb"
SQL_ADMIN_USER="contable_admin"
SQL_ADMIN_PASS="CambiarEstaContraseña!2026"   # ⚠️ CAMBIAR antes de ejecutar

STORAGE_ACCOUNT="st1contabot"         # Solo letras minúsculas y números, 3-24 chars
STORAGE_CONTAINER="1contabot"

# Autenticación Entra (sección 7 — Fase 4)
BOOTSTRAP_ADMIN_EMAIL="admin@tuempresa.com"   # ⚠️ CAMBIAR: 1er admin global (cuenta Entra)

# Key Vault + observabilidad (secciones 8 y 9 — Fase 4)
KEYVAULT_NAME="kv-1contabot"          # Nombre GLOBALMENTE único, 3-24 chars
LOG_WORKSPACE="log-1contabot"
APPINSIGHTS_NAME="ai-1contabot"
ALERT_EMAIL="$BOOTSTRAP_ADMIN_EMAIL"  # Destinatario de las alertas
BUDGET_MENSUAL="60"                   # Presupuesto mensual (USD) para la alerta de costo

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
        SCM_DO_BUILD_DURING_DEPLOYMENT="false"
# Nota: las dependencias viajan empaquetadas en ./vendor desde GitHub Actions
# (ver .github/workflows), así que la build de Oryx en el servidor es
# redundante y solo alarga cada despliegue.

# ═══════════════════════════════════════════════════════════════════════════
# 6. CONFIGURAR STARTUP COMMAND
# ═══════════════════════════════════════════════════════════════════════════
echo "🔧 Configurando comando de inicio..."
# --always-on true: sin esto App Service APAGA el contenedor tras ~20 min sin
# tráfico y la siguiente visita paga un arranque en frío de 30-60 s (gunicorn +
# import de pandas desde el SMB de wwwroot). Disponible desde el plan B1.
az webapp config set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --startup-file "startup.sh" \
    --always-on true

# ═══════════════════════════════════════════════════════════════════════════
# 7. AUTENTICACIÓN — Microsoft Entra ID vía App Service Authentication (Fase 4)
# ═══════════════════════════════════════════════════════════════════════════
# Easy Auth valida el token OIDC en la plataforma e inyecta la identidad a la
# app en las cabeceras X-MS-CLIENT-PRINCIPAL* (app/authn.py, AUTH_MODE=entra).
# Se deja la acción para no-autenticados en AllowAnonymous porque /health y
# /radian/auto/cron deben seguir siendo públicos: la compuerta de login la
# aplica la propia app (redirige a /.auth/login/aad desde /login).
echo "🔐 Configurando autenticación con Microsoft Entra ID..."

TENANT_ID=$(az account show --query tenantId -o tsv)

# App registration (single-tenant) con la URI de callback de Easy Auth.
ENTRA_CLIENT_ID=$(az ad app create \
    --display-name "${APP_NAME}-auth" \
    --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "https://${APP_NAME}.azurewebsites.net/.auth/login/aad/callback" \
    --enable-id-token-issuance true \
    --query appId -o tsv)

# Secreto de cliente (rotarlo antes de que caduque; por defecto dura 1 año).
ENTRA_CLIENT_SECRET=$(az ad app credential reset \
    --id $ENTRA_CLIENT_ID \
    --display-name "easyauth" \
    --query password -o tsv)

# Activar App Service Authentication (authV2) con Entra como proveedor.
az webapp auth microsoft update \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --client-id $ENTRA_CLIENT_ID \
    --client-secret "$ENTRA_CLIENT_SECRET" \
    --issuer "https://login.microsoftonline.com/${TENANT_ID}/v2.0" \
    --yes

az webapp auth update \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --enabled true \
    --unauthenticated-client-action AllowAnonymous

# La app pasa a modo entra; ENTRA_TENANT_ID añade la validación de tenant y
# BOOTSTRAP_ADMIN_EMAIL da rol de admin global al primer administrador real
# (vaciar esta variable después del primer inicio de sesión).
az webapp config appsettings set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --settings \
        AUTH_MODE="entra" \
        ENTRA_TENANT_ID="$TENANT_ID" \
        BOOTSTRAP_ADMIN_EMAIL="$BOOTSTRAP_ADMIN_EMAIL"

# ═══════════════════════════════════════════════════════════════════════════
# 8. KEY VAULT + MANAGED IDENTITY (Fase 4)
# ═══════════════════════════════════════════════════════════════════════════
# Los secretos (cadena de conexión SQL, Storage, clave Flask) salen de los App
# Settings en texto plano y pasan a Key Vault; la app los lee mediante
# referencias @Microsoft.KeyVault resueltas con su Managed Identity, sin
# credenciales adicionales. Sección idempotente: puede reejecutarse.
echo "🔑 Configurando Key Vault + Managed Identity..."

az keyvault create \
    --name $KEYVAULT_NAME \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --enable-rbac-authorization true

KV_ID=$(az keyvault show --name $KEYVAULT_NAME \
    --resource-group $RESOURCE_GROUP --query id -o tsv)

# Managed Identity del App Service (system-assigned) + permiso de LECTURA de
# secretos sobre el vault.
PRINCIPAL_ID=$(az webapp identity assign \
    --name $APP_NAME --resource-group $RESOURCE_GROUP \
    --query principalId -o tsv)

az role assignment create \
    --assignee-object-id $PRINCIPAL_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Key Vault Secrets User" \
    --scope $KV_ID

# Quien ejecuta el script necesita poder ESCRIBIR los secretos (RBAC del vault).
YO=$(az ad signed-in-user show --query id -o tsv)
az role assignment create \
    --assignee-object-id $YO \
    --assignee-principal-type User \
    --role "Key Vault Secrets Officer" \
    --scope $KV_ID

# Valores actuales: si el App Service ya existe se migran tal cual (no se
# regeneran), si no, se usan los calculados en las secciones 3 y 5.
_setting() {
    az webapp config appsettings list --name $APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --query "[?name=='$1'].value | [0]" -o tsv 2>/dev/null
}
VAL_DB=$(_setting DATABASE_URL);   VAL_DB="${VAL_DB:-$SQL_CONN}"
VAL_ST=$(_setting AZURE_STORAGE_CONNECTION_STRING); VAL_ST="${VAL_ST:-$STORAGE_CONN}"
VAL_FK=$(_setting FLASK_SECRET_KEY); VAL_FK="${VAL_FK:-$(openssl rand -hex 32)}"

# Si los settings ya son referencias @Microsoft.KeyVault (reejecución), no
# hay nada que migrar: los secretos ya viven en el vault.
if [[ "$VAL_DB" == @Microsoft.KeyVault* ]]; then
    echo "   Los App Settings ya usan referencias de Key Vault; secretos sin cambios."
else
    az keyvault secret set --vault-name $KEYVAULT_NAME \
        --name "database-url" --value "$VAL_DB" -o none
    az keyvault secret set --vault-name $KEYVAULT_NAME \
        --name "storage-connection-string" --value "$VAL_ST" -o none
    az keyvault secret set --vault-name $KEYVAULT_NAME \
        --name "flask-secret-key" --value "$VAL_FK" -o none

    # Reemplazar los App Settings por referencias (sin versión: al rotar un
    # secreto en el vault la app toma el nuevo valor sin tocar la config).
    KV_URI="https://${KEYVAULT_NAME}.vault.azure.net"
    az webapp config appsettings set \
        --name $APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --settings \
            DATABASE_URL="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/database-url/)" \
            AZURE_STORAGE_CONNECTION_STRING="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/storage-connection-string/)" \
            FLASK_SECRET_KEY="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/flask-secret-key/)" \
        -o none
fi
# Nota: la asignación de roles tarda unos minutos en propagarse. Si tras el
# siguiente reinicio la app no arranca y en Configuración las referencias
# aparecen con error (icono rojo), espera 5-10 min y reinicia:
#   az webapp restart --name $APP_NAME --resource-group $RESOURCE_GROUP

# ═══════════════════════════════════════════════════════════════════════════
# 9. OBSERVABILIDAD — Application Insights + alertas + budget (Fase 4)
# ═══════════════════════════════════════════════════════════════════════════
# La telemetría de la app (peticiones, dependencias, excepciones) la envía el
# SDK azure-monitor-opentelemetry cuando APPLICATIONINSIGHTS_CONNECTION_STRING
# está definida (ver app/web/__init__.py). Las alertas usan métricas de la
# plataforma (funcionan aunque el SDK no esté).
echo "📈 Configurando observabilidad..."

# Permitir que az instale extensiones (application-insights) sin preguntar.
az config set extension.use_dynamic_install=yes_without_prompt 2>/dev/null

WS_ID=$(az monitor log-analytics workspace create \
    --resource-group $RESOURCE_GROUP \
    --workspace-name $LOG_WORKSPACE \
    --location $LOCATION \
    --query id -o tsv)

AI_CONN=$(az monitor app-insights component create \
    --app $APPINSIGHTS_NAME \
    --location $LOCATION \
    --resource-group $RESOURCE_GROUP \
    --workspace $WS_ID \
    --query connectionString -o tsv)

az webapp config appsettings set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --settings APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CONN" \
    -o none

# Grupo de acción: a quién avisan las alertas.
AG_ID=$(az monitor action-group create \
    --name "ag-${APP_NAME}" \
    --resource-group $RESOURCE_GROUP \
    --short-name "1contabot" \
    --action email admin "$ALERT_EMAIL" \
    --query id -o tsv)

WEBAPP_ID=$(az webapp show --name $APP_NAME \
    --resource-group $RESOURCE_GROUP --query id -o tsv)
PLAN_ID=$(az appservice plan show --name $APP_SERVICE_PLAN \
    --resource-group $RESOURCE_GROUP --query id -o tsv)

# Errores de servidor: más de 5 respuestas 5xx en 5 minutos.
az monitor metrics alert create \
    --name "alerta-http-5xx" \
    --resource-group $RESOURCE_GROUP \
    --scopes $WEBAPP_ID \
    --condition "total Http5xx > 5" \
    --window-size 5m --evaluation-frequency 5m \
    --action $AG_ID \
    --description "1ContaBot responde errores 5xx" -o none

# Lentitud: tiempo de respuesta promedio sobre 5 s durante 15 minutos.
az monitor metrics alert create \
    --name "alerta-tiempo-respuesta" \
    --resource-group $RESOURCE_GROUP \
    --scopes $WEBAPP_ID \
    --condition "avg HttpResponseTime > 5" \
    --window-size 15m --evaluation-frequency 5m \
    --action $AG_ID \
    --description "1ContaBot responde lento (promedio > 5 s)" -o none

# Saturación del plan: CPU sobre 85% durante 15 minutos.
az monitor metrics alert create \
    --name "alerta-cpu-plan" \
    --resource-group $RESOURCE_GROUP \
    --scopes $PLAN_ID \
    --condition "avg CpuPercentage > 85" \
    --window-size 15m --evaluation-frequency 5m \
    --action $AG_ID \
    --description "Plan de App Service saturado de CPU" -o none

# Budget mensual de costo (best-effort: algunos tipos de suscripción no lo
# soportan vía CLI; en ese caso créalo en el portal: Cost Management → Budgets).
az consumption budget create \
    --budget-name "budget-${APP_NAME}" \
    --amount $BUDGET_MENSUAL \
    --category cost \
    --time-grain monthly \
    --start-date "$(date +%Y-%m-01)" \
    --end-date "2030-01-01" \
    -o none 2>/dev/null \
    && echo "   Budget mensual de \$${BUDGET_MENSUAL} creado." \
    || echo "   ⚠️  No se pudo crear el budget vía CLI; créalo en el portal (Cost Management → Budgets)."

# ═══════════════════════════════════════════════════════════════════════════
# 10. RESUMEN
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
echo "  🔐 Entra auth:   app registration ${APP_NAME}-auth (tenant ${TENANT_ID})"
echo "                   Admin inicial: ${BOOTSTRAP_ADMIN_EMAIL} — tras su primer"
echo "                   login, vacía BOOTSTRAP_ADMIN_EMAIL en App Settings."
echo "  🔑 Key Vault:    ${KEYVAULT_NAME} (secretos: database-url,"
echo "                   storage-connection-string, flask-secret-key; la app los"
echo "                   lee con su Managed Identity vía referencias en App Settings)"
echo "  📈 Observervab.: App Insights ${APPINSIGHTS_NAME} + alertas 5xx/lentitud/CPU"
echo "                   → ${ALERT_EMAIL}"
echo "  🛡️ RLS:          la app crea/actualiza la política de Row-Level Security"
echo "                   automáticamente al arrancar contra Azure SQL (schema.py);"
echo "                   verifícala con scripts/verificar_azure.sh (sección 4)."
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
