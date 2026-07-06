# ===========================================================================
# start.ps1 — Arranque manual de 1ContaBot (sin servicio Windows)
#
# Útil para pruebas o cuando no se quiere instalar como servicio.
# Ejecutar desde la raíz del proyecto.
# ===========================================================================

$ProjectDir = Split-Path -Parent $PSScriptRoot

Set-Location $ProjectDir

Write-Host ""
Write-Host "  1ContaBot — Arranque manual (Waitress)" -ForegroundColor Cyan
Write-Host "  Directorio: $ProjectDir"
Write-Host ""

python serve_prod.py
