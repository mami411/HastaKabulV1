# Tasinabilir PostgreSQL'i durdurur (veri tools\pgdata'da kalir).
# Kullanim:  powershell -ExecutionPolicy Bypass -File tools\pg-stop.ps1
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ortak.ps1"

& (Join-Path $PgBin 'pg_ctl.exe') status -D $PgData *> $null
if ($LASTEXITCODE -ne 0) { Write-Host 'PostgreSQL zaten durmus.'; return }

& (Join-Path $PgBin 'pg_ctl.exe') stop -D $PgData -m fast -w | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'pg_ctl stop basarisiz.' }
Write-Host 'PostgreSQL durduruldu.' -ForegroundColor Green
