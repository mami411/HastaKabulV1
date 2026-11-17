# Ortak yardimcilar: diger betikler bunu dot-source eder ( . "$PSScriptRoot\_ortak.ps1" ).
# Yerel gelistirme degerleri (veritabani parolasi, servis anahtari) tools\hastakabul-local.env
# dosyasinda durur; dosya ilk calistirmada rastgele degerlerle uretilir ve paylasilmaz.
# Windows PowerShell 5.1 ile calisir.

$script:ToolsDir    = $PSScriptRoot
$script:PgHome      = Join-Path $ToolsDir 'pgsql'
$script:PgBin       = Join-Path $PgHome 'bin'
$script:PgData      = Join-Path $ToolsDir 'pgdata'
$script:LogDir      = Join-Path $ToolsDir 'logs'
$script:RunDir      = Join-Path $ToolsDir 'run'
$script:LocalEnv    = Join-Path $ToolsDir 'hastakabul-local.env'
$script:BackendDir  = Join-Path (Split-Path $ToolsDir -Parent) 'hasta-kabul-backend'

foreach ($d in @($LogDir, $RunDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

function New-RandomSecret([int]$bytes = 24) {
    $buffer = New-Object byte[] $bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    # URL/baglanti dizesi icin guvenli karakterler.
    return ([Convert]::ToBase64String($buffer)).Replace('+', 'A').Replace('/', 'B').Replace('=', '')
}

# KEY=VALUE satirlari; # ile baslayan satirlar yorum.
function Read-LocalEnv {
    $values = @{}
    if (Test-Path $LocalEnv) {
        foreach ($line in Get-Content -Path $LocalEnv -Encoding UTF8) {
            $t = $line.Trim()
            if ($t -eq '' -or $t.StartsWith('#')) { continue }
            $i = $t.IndexOf('=')
            if ($i -lt 1) { continue }
            $values[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
        }
    }
    return $values
}

# Eksik anahtarlari varsayilan/rastgele degerle tamamlar ve dosyayi yazar.
function Initialize-LocalEnv {
    $values = Read-LocalEnv
    $defaults = [ordered]@{
        PG_PORT      = '5433'
        PG_USER      = 'postgres'
        PG_DATABASE  = 'hasta_kabul'
        PG_PASSWORD  = $null          # rastgele
        API_PORT     = '3100'
        SERVICE_KEY  = $null          # rastgele; AI servislerinin SERVICE_KEY'i ile ayni olmali
        SIMPLIFY_URL = 'http://localhost:8001'
    }
    $changed = -not (Test-Path $LocalEnv)
    foreach ($key in $defaults.Keys) {
        if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($values[$key])) {
            $values[$key] = if ($null -eq $defaults[$key]) { New-RandomSecret } else { $defaults[$key] }
            $changed = $true
        }
    }
    if ($changed) {
        $lines = @(
            '# HastaKabul yerel gelistirme degerleri (tools\*.ps1 okur). Paylasmayin, depoya koymayin.',
            '# SERVICE_KEY: backend ServiceKey + Simplify:ServiceKey; AI servislerine ayni deger verilmeli.'
        )
        foreach ($key in $values.Keys | Sort-Object) { $lines += "$key=$($values[$key])" }
        Set-Content -Path $LocalEnv -Value $lines -Encoding ASCII
    }
    return $values
}

function Test-PortListening([int]$port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(500)) { return $false }
        $c.EndConnect($ar)
        return $true
    } catch { return $false } finally { $c.Close() }
}
