#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Reparación Fase 4 — 1ContaBot
# ═══════════════════════════════════════════════════════════════════════════
#
# Repara lo que dejó pendiente la ejecución de azure-setup.sh del 2026-07-06:
#   1. Registra el proveedor Microsoft.KeyVault (causa raíz del fallo) y
#      completa la sección 8: Key Vault + Managed Identity + secretos +
#      referencias @Microsoft.KeyVault en App Settings.
#   2. ROTA la contraseña del admin de Azure SQL: la reejecución completa del
#      setup la dejó con el placeholder del repositorio (público).
#   3. Corrige los App Settings pisados con placeholders:
#      BOOTSTRAP_ADMIN_EMAIL queda VACÍO (el admin real ya tiene su rol en BD).
#   4. Corrige el destinatario de las alertas (quedó admin@tuempresa.com) y
#      asegura APPLICATIONINSIGHTS_CONNECTION_STRING + las 3 alertas.
#   5. Endurece el Storage (TLS 1.2 mínimo) y reinicia la app.
#
# Uso (Azure Cloud Shell):  bash scripts/reparar_fase4.sh
# Reejecutable sin peligro. NO reejecutes azure-setup.sh completo: sus
# secciones 1-7 ya están aplicadas y pisan valores con placeholders.
# ═══════════════════════════════════════════════════════════════════════════

APP_NAME="${APP_NAME:-1contabot}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-1contabot}"
LOCATION="${LOCATION:-westus2}"                 # región real de los recursos
KEYVAULT_NAME="${KEYVAULT_NAME:-kv-1contabot}"
APP_SERVICE_PLAN="${APP_SERVICE_PLAN:-plan-1contabot}"
SQL_SERVER_NAME="${SQL_SERVER_NAME:-sql-1contabot}"
SQL_DB_NAME="${SQL_DB_NAME:-contabledb}"
SQL_ADMIN_USER="${SQL_ADMIN_USER:-contable_admin}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-st1contabot}"
LOG_WORKSPACE="${LOG_WORKSPACE:-log-1contabot}"
APPINSIGHTS_NAME="${APPINSIGHTS_NAME:-ai-1contabot}"
ALERT_EMAIL="${ALERT_EMAIL:-gerencia@1inversionesestrategicas.com}"

echo "═══ 1/6 · Registrando el proveedor Microsoft.KeyVault ═══"
az provider register --namespace Microsoft.KeyVault --wait
echo "   Estado: $(az provider show --namespace Microsoft.KeyVault --query registrationState -o tsv)"

echo ""
echo "═══ 2/6 · Rotando la contraseña del admin SQL ═══"
# La reejecución del setup dejó la contraseña del placeholder del repo
# (pública). Se genera una nueva fuerte y se reconstruye DATABASE_URL.
NEW_SQL_PASS="$(openssl rand -hex 20)aK!"
az sql server update \
    --name $SQL_SERVER_NAME \
    --resource-group $RESOURCE_GROUP \
    --admin-password "$NEW_SQL_PASS" -o none \
    && echo "   Contraseña rotada." \
    || { echo "❌ No se pudo rotar la contraseña SQL; abortando."; exit 1; }

SQL_CONN="Driver={ODBC Driver 18 for SQL Server};Server=tcp:${SQL_SERVER_NAME}.database.windows.net,1433;Database=${SQL_DB_NAME};Uid=${SQL_ADMIN_USER};Pwd=${NEW_SQL_PASS};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"

echo ""
echo "═══ 3/6 · Key Vault + Managed Identity + secretos ═══"
az keyvault create \
    --name $KEYVAULT_NAME \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --enable-rbac-authorization true -o none

KV_ID=$(az keyvault show --name $KEYVAULT_NAME \
    --resource-group $RESOURCE_GROUP --query id -o tsv)
if [ -z "$KV_ID" ]; then
    echo "❌ El Key Vault no existe tras intentar crearlo; abortando."
    echo "   Si el nombre '$KEYVAULT_NAME' está tomado globalmente, elige otro"
    echo "   (KEYVAULT_NAME=kv-otro bash scripts/reparar_fase4.sh)."
    exit 1
fi
echo "   Key Vault OK: $KV_ID"

PRINCIPAL_ID=$(az webapp identity assign \
    --name $APP_NAME --resource-group $RESOURCE_GROUP \
    --query principalId -o tsv)
echo "   Managed Identity: $PRINCIPAL_ID"

az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Key Vault Secrets User" \
    --scope "$KV_ID" -o none 2>/dev/null || echo "   (rol de la app ya asignado)"

YO=$(az ad signed-in-user show --query id -o tsv)
az role assignment create \
    --assignee-object-id "$YO" \
    --assignee-principal-type User \
    --role "Key Vault Secrets Officer" \
    --scope "$KV_ID" -o none 2>/dev/null || echo "   (rol del operador ya asignado)"

# La propagación RBAC puede tardar; reintentar la escritura del primer secreto.
echo "   Guardando secretos (espera propagación RBAC si hace falta)..."
for intento in 1 2 3 4 5 6; do
    if az keyvault secret set --vault-name $KEYVAULT_NAME \
        --name "database-url" --value "$SQL_CONN" -o none 2>/dev/null; then
        break
    fi
    echo "   ...RBAC aún propagando (intento $intento), reintento en 20 s"
    sleep 20
done

STORAGE_CONN=$(az storage account show-connection-string \
    --name $STORAGE_ACCOUNT --resource-group $RESOURCE_GROUP \
    --query connectionString -o tsv)
az keyvault secret set --vault-name $KEYVAULT_NAME \
    --name "storage-connection-string" --value "$STORAGE_CONN" -o none

# La clave Flask actual es un aleatorio recién generado por el setup: se
# conserva (moverla al vault no invalida nada que no esté ya invalidado).
FLASK_KEY=$(az webapp config appsettings list --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query "[?name=='FLASK_SECRET_KEY'].value | [0]" -o tsv)
case "$FLASK_KEY" in
    ""|@Microsoft.KeyVault*) FLASK_KEY="$(openssl rand -hex 32)";;
esac
az keyvault secret set --vault-name $KEYVAULT_NAME \
    --name "flask-secret-key" --value "$FLASK_KEY" -o none
echo "   Secretos guardados: database-url, storage-connection-string, flask-secret-key"

echo ""
echo "═══ 4/6 · App Settings: referencias al vault + limpiar placeholders ═══"
KV_URI="https://${KEYVAULT_NAME}.vault.azure.net"
az webapp config appsettings set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --settings \
        DATABASE_URL="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/database-url/)" \
        AZURE_STORAGE_CONNECTION_STRING="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/storage-connection-string/)" \
        FLASK_SECRET_KEY="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/flask-secret-key/)" \
        BOOTSTRAP_ADMIN_EMAIL="" \
    -o none
echo "   Referencias configuradas y BOOTSTRAP_ADMIN_EMAIL vaciado."

echo ""
echo "═══ 5/6 · Observabilidad: correo real + alertas ═══"
az config set extension.use_dynamic_install=yes_without_prompt 2>/dev/null

AI_CONN=$(az monitor app-insights component show \
    --app $APPINSIGHTS_NAME --resource-group $RESOURCE_GROUP \
    --query connectionString -o tsv 2>/dev/null)
if [ -z "$AI_CONN" ]; then
    echo "   App Insights no existía; creándolo..."
    WS_ID=$(az monitor log-analytics workspace create \
        --resource-group $RESOURCE_GROUP \
        --workspace-name $LOG_WORKSPACE \
        --location $LOCATION --query id -o tsv)
    AI_CONN=$(az monitor app-insights component create \
        --app $APPINSIGHTS_NAME --location $LOCATION \
        --resource-group $RESOURCE_GROUP --workspace "$WS_ID" \
        --query connectionString -o tsv)
fi
az webapp config appsettings set --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --settings APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CONN" -o none
echo "   APPLICATIONINSIGHTS_CONNECTION_STRING configurado."

# Recrear el grupo de acción corrige el correo placeholder (mismo nombre = update).
AG_ID=$(az monitor action-group create \
    --name "ag-${APP_NAME}" \
    --resource-group $RESOURCE_GROUP \
    --short-name "1contabot" \
    --action email admin "$ALERT_EMAIL" \
    --query id -o tsv)
echo "   Alertas → $ALERT_EMAIL"

WEBAPP_ID=$(az webapp show --name $APP_NAME \
    --resource-group $RESOURCE_GROUP --query id -o tsv)
PLAN_ID=$(az appservice plan show --name $APP_SERVICE_PLAN \
    --resource-group $RESOURCE_GROUP --query id -o tsv)

az monitor metrics alert create --name "alerta-http-5xx" \
    --resource-group $RESOURCE_GROUP --scopes "$WEBAPP_ID" \
    --condition "total Http5xx > 5" \
    --window-size 5m --evaluation-frequency 5m --action "$AG_ID" \
    --description "1ContaBot responde errores 5xx" -o none
az monitor metrics alert create --name "alerta-tiempo-respuesta" \
    --resource-group $RESOURCE_GROUP --scopes "$WEBAPP_ID" \
    --condition "avg HttpResponseTime > 5" \
    --window-size 15m --evaluation-frequency 5m --action "$AG_ID" \
    --description "1ContaBot responde lento (promedio > 5 s)" -o none
az monitor metrics alert create --name "alerta-cpu-plan" \
    --resource-group $RESOURCE_GROUP --scopes "$PLAN_ID" \
    --condition "avg CpuPercentage > 85" \
    --window-size 15m --evaluation-frequency 5m --action "$AG_ID" \
    --description "Plan de App Service saturado de CPU" -o none
echo "   3 alertas de métricas aseguradas."

echo ""
echo "═══ 6/6 · Endurecer Storage (TLS 1.2) y reiniciar la app ═══"
az storage account update --name $STORAGE_ACCOUNT \
    --resource-group $RESOURCE_GROUP --min-tls-version TLS1_2 -o none \
    && echo "   Storage: TLS mínimo 1.2."
az webapp restart --name $APP_NAME --resource-group $RESOURCE_GROUP
echo "   App reiniciada."

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Reparación aplicada."
echo "  · Si la app no arranca de inmediato: las referencias de Key Vault"
echo "    pueden tardar 5-10 min en resolverse (propagación RBAC)."
echo "    Reintenta:  az webapp restart --name $APP_NAME --resource-group $RESOURCE_GROUP"
echo "  · Verifica todo con:  bash scripts/verificar_azure.sh"
echo "  · El budget de costo no lo soporta esta suscripción vía CLI:"
echo "    créalo en el portal → Cost Management → Budgets."
echo "═══════════════════════════════════════════════════════════════"
