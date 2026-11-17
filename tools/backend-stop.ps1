# tools\backend-start.ps1 ile baslatilan backend'i durdurur.
# Kullanim:  powershell -ExecutionPolicy Bypass -File tools\backend-stop.ps1 [-Port 3100]
param([int]$Port = 0)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ortak.ps1"

$cfg = Read-LocalEnv
if ($Port -eq 0) { $Port = if ($cfg.API_PORT) { [int]$cfg.API_PORT } else { 3100 } }

$pidFile = Join-Path $RunDir "backend-$Port.pid"
$targetPid = if (Test-Path $pidFile) { [int](Get-Content $pidFile | Select-Object -First 1) } else { 0 }

# PID dosyasi yoksa ya da eskiyse portu dinleyen dotnet sureci aranir (baska uygulamaya dokunulmaz).
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $p = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -eq 'dotnet') { $targetPid = $p.Id }
    elseif ($p) { throw "Port $Port'u dotnet olmayan bir surec ($($p.ProcessName)) dinliyor; dokunulmadi." }
}

if ($targetPid -gt 0 -and (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $targetPid -Force -Confirm:$false
    Write-Host "Backend durduruldu (port $Port, PID $targetPid)." -ForegroundColor Green
} else {
    Write-Host "Port $Port'ta calisan backend yok."
}
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
