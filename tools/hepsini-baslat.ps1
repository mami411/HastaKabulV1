# HastaKabul'un tamamini sirayla acar: PostgreSQL -> backend (3000) -> ai-simplify (8001)
# -> ai-voice (8002) -> kiosk (Vite 5173). Zaten calisan (ve saglikli) bileseni yeniden baslatmaz.
# Makine yeniden basladiktan sonra tek komut:
#   powershell -ExecutionPolicy Bypass -File C:\Dev\HastaKabul\tools\hepsini-baslat.ps1
#   ... -ResetDemo   # backend acilirken demo verisini sifirlar (backend zaten aciksa yeniden baslatir);
#                    # idempotent, ai-simplify'a dokunmaz (KB kimlikleri sabit, onbellek gecerli kalir)
#   ... -SadeYenile  # (-ResetDemo ile) ai-simplify'i da yeniden baslatir; yalniz seed KB METNI degistiyse gerekir
#   ... -NoBuild     # backend'i yeniden yayinlamadan son ciktiyla acar
#   ... -ViteYok     # kiosk gelistirme sunucusunu acma
# Durdurma: tools\hepsini-durdur.ps1. Loglar: tools\logs\ (PostgreSQL, backend, Vite) ve
# hasta-kabul-ai\logs\ (sade-servisi.*.log, ses-servisi.*.log). Windows PowerShell 5.1 ile calisir.
param(
    [int]$ApiPort = 3000,
    [switch]$ResetDemo,
    [switch]$SadeYenile,
    [switch]$NoBuild,
    [switch]$ViteYok
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ortak.ps1"

$root     = Split-Path $ToolsDir -Parent
$aiDir    = Join-Path $root 'hasta-kabul-ai'
$aiLogDir = Join-Path $aiDir 'logs'
$feDir    = Join-Path $root 'hasta-kabul-frontend'
$py       = Join-Path $aiDir '.venv\Scripts\python.exe'
if (-not (Test-Path $aiLogDir)) { New-Item -ItemType Directory -Path $aiLogDir | Out-Null }

function Test-Health([string]$url) {
    try { $null = Invoke-RestMethod -Uri $url -TimeoutSec 3; return $true } catch { return $false }
}

function Wait-Health([string]$ad, [string]$url, [int]$saniye, $proc) {
    $deadline = (Get-Date).AddSeconds($saniye)
    while ((Get-Date) -lt $deadline) {
        if (Test-Health $url) { return }
        if ($proc -and $proc.HasExited) { break }
        Start-Sleep -Milliseconds 800
    }
    throw "$ad acilmadi ($url)."
}

# cmd /c ile gizli pencerede; surec bu kabugun borularini devralmaz, kabuk kapansa da calisir.
function Start-Arkaplan([string]$dizin, [string]$komut, [string]$log) {
    return Start-Process -FilePath $env:ComSpec -WorkingDirectory $dizin -WindowStyle Hidden -PassThru `
        -ArgumentList "/s /c `"$komut >> `"$log`" 2>&1`""   # /s: dis tirnaklar soyulur, ic tirnaklar kalir
}

function Write-Adim([string]$m) { Write-Host "== $m" -ForegroundColor Cyan }

# Calisan surec, kaynak koddan ESKI mi? (2026-11-16 kok nedeni: kod degistikten sonra "zaten calisiyor"
# denip eski ses servisi birakildi; yeni 409 kodlari sistem hatasi sanildi.) Kaynaklardaki en yeni
# degisiklik surecin baslama zamanindan sonraysa $true.
function Test-SurecEski([int]$port, [string[]]$dizinler, [string[]]$desenler) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { return $false }
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if (-not $p -or -not $p.StartTime) { return $false }
    $enYeni = Get-ChildItem -Path $dizinler -Recurse -File -Include $desenler -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\(bin|obj|__pycache__|\.venv)\\' } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    return [bool]($enYeni -and $enYeni.LastWriteTime -gt $p.StartTime)
}

function Stop-PortPython([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { return }
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like 'python*') {
        Stop-Process -Id $p.Id -Force -Confirm:$false
        $deadline = (Get-Date).AddSeconds(10)
        while ((Test-PortListening $port) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    }
}

# 1) PostgreSQL
Write-Adim 'PostgreSQL'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ToolsDir 'pg-start.ps1')
if ($LASTEXITCODE -ne 0) { throw 'pg-start basarisiz.' }

# 2) Backend
Write-Adim "Backend ($ApiPort)"
$apiUp = Test-Health "http://localhost:$ApiPort/health"
if ($apiUp -and -not $ResetDemo -and -not $NoBuild -and
    (Test-SurecEski $ApiPort @((Join-Path $root 'hasta-kabul-backend\src')) @('*.cs', '*.csproj', 'appsettings*.json'))) {
    Write-Host 'Backend kodu calisan surecten yeni; yeniden derlenip baslatiliyor.' -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ToolsDir 'backend-stop.ps1') -Port $ApiPort
    $apiUp = $false
}
if ($apiUp -and $ResetDemo) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ToolsDir 'backend-stop.ps1') -Port $ApiPort
    $apiUp = $false
}
if ($apiUp) {
    Write-Host "Backend zaten calisiyor: http://localhost:$ApiPort"
} else {
    # 127.0.0.1: ai-simplify yalniz IPv4 dinler; 'localhost' once ::1'e gider, reddedilince .NET
    # Windows'ta ~2 sn bekleyip IPv4'e duser (her yeni baglantida klinik giris +2 sn).
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $ToolsDir 'backend-start.ps1'),
           '-Port', $ApiPort, '-SimplifyUrl', 'http://127.0.0.1:8001')
    if ($ResetDemo) { $a += '-ResetDemo' }
    if ($NoBuild)   { $a += '-NoBuild' }
    & powershell @a
    if ($LASTEXITCODE -ne 0) { throw 'backend-start basarisiz.' }
}

# KB kimlikleri sabit (DemoDataSeeder.KnowledgeBaseId) ve sifirlama ayni kayitlari uretir; ai-simplify'in
# KB onbellegi gecerli kalir, yeniden baslatmaya gerek yoktur. Seed KB metni degistiyse -SadeYenile.
if ($ResetDemo -and $SadeYenile) {
    $c8001 = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c8001) {
        $p = Get-Process -Id $c8001.OwningProcess -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -like 'python*') {
            Stop-Process -Id $p.Id -Force -Confirm:$false
            Write-Host 'ai-simplify KB onbellegi icin yeniden baslatiliyor.'
            Start-Sleep -Seconds 1
        }
    }
}

# 3-4) AI servisleri (.env: HASTAKABUL_API_URL ve SERVICE_KEY backend ile ayni olmali)
foreach ($svc in @(
        @{ Ad = 'ai-simplify'; Modul = 'sade_servisi'; Port = 8001; Log = 'sade-servisi' },
        @{ Ad = 'ai-voice';    Modul = 'ses_servisi';  Port = 8002; Log = 'ses-servisi' })) {
    Write-Adim "$($svc.Ad) ($($svc.Port))"
    $url = "http://127.0.0.1:$($svc.Port)/health"
    $kaynak = @((Join-Path $aiDir $svc.Modul), (Join-Path $aiDir 'ortak'), (Join-Path $aiDir '.env'))
    if ((Test-PortListening $svc.Port) -and (Test-SurecEski $svc.Port $kaynak @('*.py', '.env'))) {
        Write-Host "$($svc.Ad): kod/ayar calisan surecten yeni; yeniden baslatiliyor." -ForegroundColor Yellow
        Stop-PortPython $svc.Port
    }
    if (Test-PortListening $svc.Port) {
        Wait-Health $svc.Ad $url 30 $null
        Write-Host "$($svc.Ad) zaten calisiyor."
        continue
    }
    $log = Join-Path $aiLogDir "$($svc.Log).out.log"
    $p = Start-Arkaplan $aiDir "`"$py`" -m $($svc.Modul)" $log
    Wait-Health $svc.Ad $url 120 $p
    Write-Host "$($svc.Ad) hazir: $url (log: $log)" -ForegroundColor Green
}

# 4b) Isitma (hasta verisi YOK; sabit cumleler). Soguk LLM ilk cagrida ~3,5 sn, ilk STT ~0,6 sn surer;
# demo sirasinda ilk sesli tur yavas olmasin diye model bellege alinir. Hata akisi durdurmaz.
Write-Adim 'Isitma (LLM, STT/TTS)'
try {
    $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8002/health' -TimeoutSec 5
    $model = $h.llm.model
    if ($h.llm.etkin -and $model -and (Test-PortListening 11434)) {
        $govde = @{ model = $model; prompt = ''; keep_alive = '60m' } | ConvertTo-Json
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -Body $govde `
            -ContentType 'application/json' -TimeoutSec 90
        Write-Host "LLM bellekte: $model"
    }
    $wav = Join-Path $env:TEMP 'hk-isitma.wav'
    Invoke-WebRequest -Uri 'http://127.0.0.1:8002/tts' -Method Post -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes('{"text":"Merhaba"}')) -OutFile $wav -UseBasicParsing -TimeoutSec 30
    $null = & curl.exe -s -m 30 -F "file=@$wav;type=audio/wav" 'http://127.0.0.1:8002/stt'
    Remove-Item $wav -ErrorAction SilentlyContinue
    Write-Host 'STT/TTS isitildi.'
} catch {
    Write-Warning "Isitma atlandi: $($_.Exception.Message)"
}

# 5) Kiosk (Vite)
if (-not $ViteYok) {
    Write-Adim 'Uygulama (Vite 5173)'
    # Vite 'localhost'u IPv6 (::1) uzerinde dinler; Test-PortListening yalniz 127.0.0.1'e bakar.
    $viteAcik = { [bool](Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) }
    if (& $viteAcik) {
        Write-Host 'Vite zaten calisiyor: http://localhost:5173'
    } else {
        $log = Join-Path $LogDir 'vite-5173.log'
        $p = Start-Arkaplan $feDir 'npm run dev -- --port 5173 --strictPort' $log
        $deadline = (Get-Date).AddSeconds(60)
        while (-not (& $viteAcik) -and (Get-Date) -lt $deadline -and -not $p.HasExited) { Start-Sleep -Milliseconds 800 }
        if (-not (& $viteAcik)) { throw "Vite acilmadi; bkz. $log" }
        Write-Host "Vite hazir: http://localhost:5173 (log: $log)" -ForegroundColor Green
    }
}

Write-Host ''
Write-Host "Hepsi hazir: PostgreSQL $((Read-LocalEnv).PG_PORT), API http://localhost:$ApiPort, ai-simplify 8001, ai-voice 8002$(if (-not $ViteYok) { ', uygulama http://localhost:5173' })" -ForegroundColor Green
