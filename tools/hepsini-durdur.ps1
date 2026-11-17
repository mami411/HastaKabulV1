# hepsini-baslat.ps1 ile acilan her seyi ters sirayla durdurur:
# kiosk (Vite 5173) -> ai-voice (8002) -> ai-simplify (8001) -> backend (3000) -> PostgreSQL.
# Yalniz beklenen sureci durdurur (5173'te node, 8001/8002'de python, API portunda dotnet);
# portu baska bir uygulama tutuyorsa dokunmaz ve uyarir. Ollama'ya dokunmaz.
#   powershell -ExecutionPolicy Bypass -File C:\Dev\HastaKabul\tools\hepsini-durdur.ps1
#   ... -ViteKalsin     # kiosk gelistirme sunucusunu acik birak
#   ... -VeritabaniKalsin
param(
    [int]$ApiPort = 3000,
    [switch]$ViteKalsin,
    [switch]$VeritabaniKalsin
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ortak.ps1"

function Stop-PortSureci([string]$ad, [int]$port, [string[]]$beklenen) {
    $conns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($conns.Count -eq 0) { Write-Host "$ad ($port) zaten kapali."; return }
    foreach ($procId in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        if ($beklenen -notcontains $p.ProcessName) {
            Write-Warning "$ad ($port): '$($p.ProcessName)' (PID $procId) beklenen surec degil; dokunulmadi."
            continue
        }
        # Ust surec (venv baslaticisi / cmd sarmalayicisi) cocuk kapaninca kendiliginden biter.
        Stop-Process -Id $procId -Force -Confirm:$false
        Write-Host "$ad durduruldu ($port, PID $procId)." -ForegroundColor Green
    }
}

if (-not $ViteKalsin) { Stop-PortSureci 'Uygulama (Vite)' 5173 @('node') }
Stop-PortSureci 'ai-voice'    8002 @('python', 'python3')
Stop-PortSureci 'ai-simplify' 8001 @('python', 'python3')

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ToolsDir 'backend-stop.ps1') -Port $ApiPort
if (-not $VeritabaniKalsin) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ToolsDir 'pg-stop.ps1')
}
Write-Host 'Durduruldu.' -ForegroundColor Green
