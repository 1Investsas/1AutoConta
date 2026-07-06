#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Verificación de infraestructura Azure — 1ContaBot (solo lectura)
# ═══════════════════════════════════════════════════════════════════════════
#
# Verifica el estado de la migración a cuentas oficiales y de la Fase 4:
#   1. App Settings oficiales (USE_SQLITE, DATABASE_URL, Storage, auth Entra…)
#   2. App Service Authentication (Easy Auth) con Entra ID
#   3. Key Vault + Managed Identity
#   4. Row-Level Security en Azure SQL (imprime el T-SQL de chequeo)
#   5. Observabilidad: Application Insights, alertas, budgets
#
# Uso (en Azure Cloud Shell o con az login hecho):
#   bash scripts/verificar_azure.sh [APP_NAME] [RESOURCE_GROUP]
#
# No modifica nada: todos los comandos son de consulta.
# ═══════════════════════════════════════════════════════════════════════════

APP_NAME="${1:-1contabot}"
RESOURCE_GROUP="${2:-rg-1contabot}"

OK="✅"; FALTA="❌"; AVISO="⚠️ "

echo "═══════════════════════════════════════════════════════════════"
echo "  Verificando: app=$APP_NAME  rg=$RESOURCE_GROUP"
echo "  Suscripción: $(az account show --query name -o tsv 2>/dev/null || echo 'SIN LOGIN — ejecuta az login')"
echo "═══════════════════════════════════════════════════════════════"

# ───────────────────────────────────────────────────────────────────────────
# 1. APP SETTINGS OFICIALES
# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "── 1. App Settings ──────────────────────────────────────────────"

SETTINGS_JSON=$(az webapp config appsettings list \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" -o json 2>/dev/null)

if [ -z "$SETTINGS_JSON" ]; then
    echo "$FALTA No se pudo leer el App Service '$APP_NAME' en '$RESOURCE_GROUP'."
    echo "   Verifica el nombre/grupo o los permisos de la cuenta."
    exit 1
fi

valor() { echo "$SETTINGS_JSON" | python3 -c "
import json,sys
d={s['name']:s['value'] for s in json.load(sys.stdin)}
print(d.get('$1',''))"; }

# check NOMBRE [valor_esperado]  — con valor esperado compara; sin él, solo exige no-vacío
check() {
    local nombre="$1" esperado="$2" v
    v=$(valor "$nombre")
    if [ -z "$v" ]; then
        echo "$FALTA $nombre — NO configurado"
    elif [ -n "$esperado" ] && [ "${v,,}" != "${esperado,,}" ]; then
        echo "$AVISO $nombre = '$v' (esperado: '$esperado')"
    else
        echo "$OK $nombre"
    fi
}

echo "· Base de datos y storage:"
check USE_SQLITE false
check DATABASE_URL
check AZURE_STORAGE_CONNECTION_STRING
check AZURE_STORAGE_CONTAINER

echo "· Identidad de la empresa:"
check NIT_EMPRESA
check NOMBRE_EMPRESA

echo "· Flask / despliegue:"
check FLASK_SECRET_KEY
check SCM_DO_BUILD_DURING_DEPLOYMENT false
check LOG_LEVEL

echo "· Autenticación Entra (Fase 4):"
check AUTH_MODE entra
check ENTRA_TENANT_ID

BOOT=$(valor BOOTSTRAP_ADMIN_EMAIL)
if [ -n "$BOOT" ]; then
    echo "$AVISO BOOTSTRAP_ADMIN_EMAIL sigue definido ('$BOOT') — vaciarlo tras el primer login del admin"
else
    echo "$OK BOOTSTRAP_ADMIN_EMAIL vacío (correcto post-bootstrap)"
fi

# DATABASE_URL no debe apuntar al servidor SQL de prueba
DBURL=$(valor DATABASE_URL)
case "$DBURL" in
    *sql-1contabot.database.windows.net*)
        echo "$AVISO DATABASE_URL apunta al servidor de PRUEBA (sql-1contabot). Si este es el entorno oficial con otro server, revisar." ;;
esac

echo "· Comando de inicio / always-on:"
CFG=$(az webapp config show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --query "{startup:appCommandLine, alwaysOn:alwaysOn}" -o json)
echo "  $CFG   (esperado: startup.sh, alwaysOn=true)"

# ───────────────────────────────────────────────────────────────────────────
# 2. EASY AUTH (App Service Authentication)
# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "── 2. App Service Authentication (Entra ID) ─────────────────────"
# Según la versión del CLI, la respuesta viene plana o envuelta en 'properties'.
az webapp auth show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    -o json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
p = d.get('properties', d)
plat = (p.get('platform') or {}).get('enabled')
accion = (p.get('globalValidation') or {}).get('unauthenticatedClientAction')
aad = ((p.get('identityProviders') or {}).get('azureActiveDirectory') or {}).get('enabled')
if plat is None and accion is None and aad is None:
    # Respuesta en formato v1 (authV2 no configurado vía este endpoint)
    plat = p.get('enabled')
    accion = p.get('unauthenticatedClientAction')
    prov = (p.get('defaultProvider') or '').lower()
    aad = ('aad' in prov or 'activedirectory' in prov) or None
print(f'  {\"✅\" if plat else \"❌\"} Easy Auth habilitado: {plat}')
print(f'  {\"✅\" if accion == \"AllowAnonymous\" else \"⚠️ \"} Acción no-autenticados: {accion} (esperado: AllowAnonymous)')
print(f'  {\"✅\" if aad else \"❌\"} Proveedor Entra ID (AAD) activo: {aad}')
"

# ───────────────────────────────────────────────────────────────────────────
# 3. KEY VAULT + MANAGED IDENTITY
# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "── 3. Key Vault + Managed Identity ──────────────────────────────"

KV=$(az keyvault list --resource-group "$RESOURCE_GROUP" --query "[].name" -o tsv 2>/dev/null)
if [ -n "$KV" ]; then
    echo "$OK Key Vault en el grupo: $KV"
else
    echo "$FALTA No hay Key Vault en '$RESOURCE_GROUP'"
fi

MI=$(az webapp identity show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --query principalId -o tsv 2>/dev/null)
if [ -n "$MI" ]; then
    echo "$OK Managed Identity asignada (principalId: $MI)"
else
    echo "$FALTA El App Service NO tiene Managed Identity (az webapp identity assign)"
fi

# ¿Los secretos se leen desde Key Vault? (referencias @Microsoft.KeyVault en settings)
NREF=$(echo "$SETTINGS_JSON" | python3 -c "
import json,sys
print(sum(1 for s in json.load(sys.stdin) if '@Microsoft.KeyVault' in (s.get('value') or '')))")
if [ "$NREF" -gt 0 ]; then
    echo "$OK $NREF App Setting(s) usan referencias a Key Vault"
else
    echo "$FALTA Ningún App Setting usa referencias @Microsoft.KeyVault — los secretos (DATABASE_URL, storage, FLASK_SECRET_KEY) están en texto plano en la configuración"
fi

# ───────────────────────────────────────────────────────────────────────────
# 4. ROW-LEVEL SECURITY (Azure SQL)
# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "── 4. Row-Level Security en Azure SQL ───────────────────────────"
echo "  Ejecuta este T-SQL contra la base de datos (Query editor del portal o sqlcmd):"
cat <<'SQL'
    -- Políticas de seguridad RLS activas (esperado: al menos 1 sobre las tablas con empresa_id)
    SELECT p.name AS politica, p.is_enabled,
           OBJECT_NAME(sp.target_object_id) AS tabla
    FROM sys.security_policies p
    LEFT JOIN sys.security_predicates sp ON sp.object_id = p.object_id;
SQL
echo "  → Si no devuelve filas: RLS NO está configurado. La app crea la política"
echo "  automáticamente al arrancar contra Azure SQL (app/database/schema.py);"
echo "  si falta, revisa los logs de arranque (permiso ALTER ANY SECURITY POLICY)."

# ───────────────────────────────────────────────────────────────────────────
# 5. OBSERVABILIDAD
# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "── 5. Observabilidad ────────────────────────────────────────────"

# Se consulta como recurso genérico para no depender de la extensión
# 'application-insights' (en Cloud Shell pide confirmación y bloquea el script).
AI=$(az resource list --resource-group "$RESOURCE_GROUP" \
    --resource-type "microsoft.insights/components" \
    --query "[].name" -o tsv 2>/dev/null)
if [ -n "$AI" ]; then
    echo "$OK Application Insights: $AI"
else
    echo "$FALTA No hay Application Insights en '$RESOURCE_GROUP'"
fi

AIKEY=$(valor APPLICATIONINSIGHTS_CONNECTION_STRING)
if [ -n "$AIKEY" ]; then
    echo "$OK APPLICATIONINSIGHTS_CONNECTION_STRING configurado en la app"
else
    echo "$FALTA La app no tiene APPLICATIONINSIGHTS_CONNECTION_STRING"
fi

NALERT=$(az monitor metrics alert list --resource-group "$RESOURCE_GROUP" \
    --query "length(@)" -o tsv 2>/dev/null)
if [ -n "$NALERT" ] && [ "$NALERT" -gt 0 ]; then
    echo "$OK $NALERT regla(s) de alerta de métricas"
else
    echo "$FALTA Sin reglas de alerta (errores 5xx, CPU, disponibilidad…)"
fi

SUB=$(az account show --query id -o tsv)
NBUD=$(az consumption budget list --query "length(@)" -o tsv 2>/dev/null)
if [ -n "$NBUD" ] && [ "$NBUD" -gt 0 ]; then
    echo "$OK $NBUD budget(s) de costo en la suscripción $SUB"
else
    echo "$FALTA Sin budgets de costo configurados"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Fin de la verificación."
echo "═══════════════════════════════════════════════════════════════"
