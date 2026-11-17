# HastaKabul backend'ini yerel PostgreSQL'e (tools\pg-start.ps1) bagli olarak arka planda baslatir.
# Derlenmis cikti tools\run\api-<port>\ altina yayinlanir; boylece 'dotnet build/test' calisan
# surecin DLL'lerine takilmaz. Makinede yalniz .NET 10 runtime varsa DOTNET_ROLL_FORWARD=Major kullanilir.
#
# Kullanim:
#   powershell -ExecutionPolicy Bypass -File tools\backend-start.ps1                 # API_PORT (3100)
#   powershell -ExecutionPolicy Bypass -File tools\backend-start.ps1 -Port 3000 -SimplifyUrl http://127.0.0.1:8001
#   ... -ResetDemo     # acilista demo verisini sifirlayip yeniden uretir (--reset-demo)
#   ... -NoBuild       # yeniden yayinlamadan son ciktiyi calistirir
#   ... -Database hasta_kabul_test   # ayri veritabani (deneme ornegi; yoksa EnsureCreated olusturur)
param(
    [int]$Port = 0,
    [string]$SimplifyUrl = '',
    [string]$Database = '',
    [switch]$ResetDemo,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ortak.ps1"

$cfg = Initialize-LocalEnv
if ($Port -eq 0) { $Port = [int]$cfg.API_PORT }
if (-not $SimplifyUrl) { $SimplifyUrl = $cfg.SIMPLIFY_URL }
if (-not $Database) { $Database = $cfg.PG_DATABASE }

if (Test-PortListening $Port) { throw "Port $Port zaten kullaniliyor. Once tools\backend-stop.ps1 -Port $Port." }
if (-not (Test-PortListening ([int]$cfg.PG_PORT))) { throw "PostgreSQL ($($cfg.PG_PORT)) calismiyor. Once tools\pg-start.ps1." }

$env:DOTNET_ROLL_FORWARD = 'Major'
$outDir = Join-Path $RunDir "api-$Port"

if (-not $NoBuild -or -not (Test-Path (Join-Path $outDir 'HastaKabul.Api.dll'))) {
    Write-Host "Yayinlaniyor: $outDir"
    & dotnet publish (Join-Path $BackendDir 'src\HastaKabul.Api\HastaKabul.Api.csproj') -c Debug -o $outDir --nologo -v q -nodeReuse:false -p:UseSharedCompilation=false | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'dotnet publish basarisiz.' }
}

# Ayarlar yalniz bu surecin ortamina verilir; appsettings*.json dosyalari degismez.
# Ortam degiskenleri appsettings.Local.json dahil tum json kaynaklarini ezer.
$envVars = @{
    ASPNETCORE_ENVIRONMENT     = 'Development'
    ASPNETCORE_URLS            = "http://localhost:$Port"
    ConnectionStrings__Default = "Host=localhost;Port=$($cfg.PG_PORT);Database=$Database;Username=$($cfg.PG_USER);Password=$($cfg.PG_PASSWORD)"
    Database__AutoMigrate      = 'true'
    Database__UsePgvector      = 'false'   # tasinabilir kurulumda pgvector yok; embedding real[]
    ServiceKey                 = $cfg.SERVICE_KEY
    Simplify__ServiceKey       = $cfg.SERVICE_KEY
    Simplify__BaseUrl          = $SimplifyUrl
    Demo__Seed                 = 'true'
}
# Surec makinenin kulturuyle (Turkce Windows'ta tr-TR) calisir; poliklinik/KB eslesmesi
# kulturden bagimsizdir (Domain/Common/TurkishText.cs). Eski oturumdan kalmis olabilecek
# degismez kultur ayari temizlenir.
Remove-Item Env:\DOTNET_SYSTEM_GLOBALIZATION_INVARIANT -ErrorAction SilentlyContinue

foreach ($k in $envVars.Keys) { Set-Item -Path "Env:\$k" -Value $envVars[$k] }

# ShellExecute (gizli pencere) + cmd yonlendirmesi: surec bu kabugun borularini devralmaz,
# kabuk kapansa da calisir; cikti log dosyasina gider.
$log = Join-Path $LogDir "backend-$Port.log"
$dll = Join-Path $outDir 'HastaKabul.Api.dll'
$extra = if ($ResetDemo) { ' --reset-demo' } else { '' }
$proc = Start-Process -FilePath $env:ComSpec -WorkingDirectory $outDir -WindowStyle Hidden -PassThru `
    -ArgumentList "/c dotnet `"$dll`"$extra >> `"$log`" 2>&1"

$deadline = (Get-Date).AddSeconds(90)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 2
        if ($h.status -eq 'ok') { $ok = $true; break }
    } catch { }
    if ($proc.HasExited) { break }
}
if (-not $ok) { throw "Backend acilmadi; bkz. $log" }

# Dinleyen surecin PID'i (cmd sarmalayicisinin degil) durdurma icin saklanir.
$listenerPid = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess
Set-Content -Path (Join-Path $RunDir "backend-$Port.pid") -Value $listenerPid -Encoding ASCII
Write-Host "Backend hazir: http://localhost:$Port (PID $listenerPid, DB: $Database, Simplify: $SimplifyUrl, log: $log)" -ForegroundColor Green
