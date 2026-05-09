# ===========================================================================
# uninstall_service.ps1 — Desinstala el servicio de Windows contable-auto
#
# REQUISITO: Ejecutar como Administrador
# ===========================================================================

param (
    [string]$ServiceName = "contable-auto"
)

$NssmPath = "C:\tools\nssm\nssm.exe"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Ejecuta este script como Administrador."
    exit 1
}

Write-Host "Deteniendo servicio '$ServiceName'..." -ForegroundColor Yellow
& $NssmPath stop $ServiceName

Write-Host "Removiendo servicio '$ServiceName'..." -ForegroundColor Yellow
& $NssmPath remove $ServiceName confirm

Write-Host "✅ Servicio '$ServiceName' desinstalado." -ForegroundColor Green
