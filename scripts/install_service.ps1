# ===========================================================================
# install_service.ps1 — Instala contable-auto como Servicio de Windows
#
# REQUISITOS:
#   1. Ejecutar como Administrador
#   2. NSSM instalado en C:\tools\nssm\nssm.exe  (o actualizar $NssmPath)
#      Descarga: https://nssm.cc/download
#   3. Si NSSM no está, el script lo descargará automáticamente
# ===========================================================================

param (
    [string]$ServiceName = "contable-auto",
    [string]$DisplayName = "contable-auto — Sistema Contable 1INVEST",
    [string]$Port = "5000"
)

# ── Rutas ──────────────────────────────────────────────────────────────────
$ProjectDir = Split-Path -Parent $PSScriptRoot          # raíz del proyecto
$Python     = (Get-Command python).Source                # python del entorno actual
$Script     = Join-Path $ProjectDir "serve_prod.py"
$LogDir     = Join-Path $ProjectDir "logs"
$NssmPath   = "C:\tools\nssm\nssm.exe"

# ── Verificar que se ejecuta como Administrador ────────────────────────────
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Ejecuta este script como Administrador."
    exit 1
}

# ── Descargar NSSM si no existe ─────────────────────────────────────────────
if (-not (Test-Path $NssmPath)) {
    Write-Host "NSSM no encontrado. Descargando..." -ForegroundColor Yellow
    $NssmZip = "$env:TEMP\nssm.zip"
    $NssmDir = "C:\tools\nssm"

    Invoke-WebRequest -Uri "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip" -OutFile $NssmZip
    Expand-Archive -Path $NssmZip -DestinationPath "$env:TEMP\nssm_extracted" -Force

    New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null
    Copy-Item "$env:TEMP\nssm_extracted\nssm-2.24-101-g897c7ad\win64\nssm.exe" $NssmPath
    Write-Host "NSSM instalado en $NssmPath" -ForegroundColor Green
}

# ── Crear directorio de logs ───────────────────────────────────────────────
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# ── Detener e instalar servicio ────────────────────────────────────────────
Write-Host "Instalando servicio '$ServiceName'..." -ForegroundColor Cyan

& $NssmPath stop    $ServiceName 2>$null
& $NssmPath remove  $ServiceName confirm 2>$null

& $NssmPath install $ServiceName $Python $Script

# Configuración del servicio
& $NssmPath set $ServiceName AppDirectory      $ProjectDir
& $NssmPath set $ServiceName DisplayName       $DisplayName
& $NssmPath set $ServiceName Description       "Servidor WSGI de producción para el sistema contable-auto de 1INVEST SAS"
& $NssmPath set $ServiceName Start             SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout         (Join-Path $LogDir "service.log")
& $NssmPath set $ServiceName AppStderr         (Join-Path $LogDir "service_error.log")
& $NssmPath set $ServiceName AppRotateFiles    1
& $NssmPath set $ServiceName AppRotateBytes    10485760   # rotar al llegar a 10 MB

# Reiniciar automáticamente si falla
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppRestartDelay 5000

# Iniciar el servicio
& $NssmPath start $ServiceName

Write-Host ""
Write-Host "✅ Servicio '$ServiceName' instalado y arrancado." -ForegroundColor Green
Write-Host "   App disponible en: http://localhost:$Port" -ForegroundColor Green
Write-Host "   Logs en:           $LogDir" -ForegroundColor Gray
Write-Host ""
Write-Host "Comandos útiles:" -ForegroundColor Gray
Write-Host "   Detener:   sc stop $ServiceName"
Write-Host "   Iniciar:   sc start $ServiceName"
Write-Host "   Estado:    sc query $ServiceName"
Write-Host "   Desinstalar: .\scripts\uninstall_service.ps1"
