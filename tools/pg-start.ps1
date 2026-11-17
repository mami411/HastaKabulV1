# Tasinabilir PostgreSQL 16'yi baslatir (sistem servisi/yonetici izni gerekmez).
# Ilk calistirmada tools\pgdata kumesini initdb ile olusturur, parolayi
# tools\hastakabul-local.env'den alir (yoksa uretir) ve hasta_kabul veritabanini acar.
# Kullanim:  powershell -ExecutionPolicy Bypass -File tools\pg-start.ps1
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ortak.ps1"

if (-not (Test-Path (Join-Path $PgBin 'pg_ctl.exe'))) {
    throw "PostgreSQL ikilileri yok: $PgBin. Resmi zip'i (postgresql-16.x-windows-x64-binaries.zip) tools\ altina acin."
}

$cfg  = Initialize-LocalEnv
$port = [int]$cfg.PG_PORT

if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) {
    Write-Host "Kume olusturuluyor: $PgData"
    $pwFile = Join-Path $RunDir 'pgpw.tmp'
    Set-Content -Path $pwFile -Value $cfg.PG_PASSWORD -Encoding ASCII -NoNewline
    try {
        & (Join-Path $PgBin 'initdb.exe') -D $PgData -U $cfg.PG_USER --pwfile=$pwFile `
            --auth=scram-sha-256 --encoding=UTF8 --locale=C | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "initdb basarisiz ($LASTEXITCODE)." }
    } finally {
        Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
    }
    # Yalniz bu makineden, compose'taki host portunda.
    Add-Content -Path (Join-Path $PgData 'postgresql.conf') -Encoding ASCII -Value @(
        '',
        '# HastaKabul yerel ayarlari (tools\pg-start.ps1)',
        "port = $port",
        "listen_addresses = 'localhost'",
        "timezone = 'UTC'",
        "log_timezone = 'UTC'"
    )
}

if (Test-PortListening $port) {
    $status = & (Join-Path $PgBin 'pg_ctl.exe') status -D $PgData 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host "PostgreSQL zaten calisiyor (port $port)."; }
    else { throw "Port $port baska bir surec tarafindan kullaniliyor." }
} else {
    # ShellExecute ile (gizli pencere): postgres bu kabugun cikti borusunu devralmaz,
    # boylece betik takilmaz ve sunucu kabuk kapansa da calismaya devam eder.
    $p = Start-Process -FilePath (Join-Path $PgBin 'pg_ctl.exe') -WindowStyle Hidden -PassThru -ArgumentList @(
        'start', '-D', "`"$PgData`"", '-l', "`"$(Join-Path $LogDir 'postgres.log')`"", '-w', '-t', '60')
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) { throw "pg_ctl start basarisiz; bkz. $LogDir\postgres.log" }
}

# Veritabani yoksa olustur (EF EnsureCreated da olusturur; bu adim duman testi vb. icin).
$env:PGPASSWORD = $cfg.PG_PASSWORD
try {
    $exists = & (Join-Path $PgBin 'psql.exe') -h localhost -p $port -U $cfg.PG_USER -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname = '$($cfg.PG_DATABASE)'"
    if ("$exists".Trim() -ne '1') {
        & (Join-Path $PgBin 'createdb.exe') -h localhost -p $port -U $cfg.PG_USER -E UTF8 $cfg.PG_DATABASE
        if ($LASTEXITCODE -ne 0) { throw 'createdb basarisiz.' }
        Write-Host "Veritabani olusturuldu: $($cfg.PG_DATABASE)"
    }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

Write-Host "PostgreSQL hazir: localhost:$port / $($cfg.PG_DATABASE)" -ForegroundColor Green
