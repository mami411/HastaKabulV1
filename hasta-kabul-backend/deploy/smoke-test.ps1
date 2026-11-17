# Hasta Kabul API - uctan uca duman testi
# Senaryonun 4-8. adimlarini gercek servislere karsi kosar.
# Kullanim:  pwsh -File deploy/smoke-test.ps1   (Windows PowerShell 5.1 ile de calisir)
# Onkosul:   docker compose up -d  (API http://localhost:3000) ya da tools\backend-start.ps1;
#            her kosudan once demo verisi sifirlanmali (-ResetDemo / --reset-demo).
# Ortam:     API_URL (varsayilan http://localhost:3000), SERVICE_KEY (yoksa .env ya da
#            ..\..\tools\hastakabul-local.env okunur).

$ErrorActionPreference = 'Stop'

$ApiUrl = if ($env:API_URL) { $env:API_URL } else { 'http://localhost:3000' }
$DemoTc = '10000000146'   # Seed'deki sabit demo hastasi; gorunen adi Turkce karakterli (Ayse H. degil)
$UnregisteredTc = '12345678950'   # bicimi gecerli (kontrol haneleri tutar) ama kayitli degil
$MalformedTc = '11111111111'      # 11 hane ama kontrol hanesi tutmaz -> 400, deneme sayilmaz

# SERVICE_KEY .env'den okunur; AI servislerinin taklit edilmesi icin gerekir.
$ServiceKey = $env:SERVICE_KEY
if (-not $ServiceKey -and (Test-Path '.env')) {
    $line = Get-Content '.env' | Where-Object { $_ -match '^SERVICE_KEY=' } | Select-Object -First 1
    if ($line) { $ServiceKey = $line.Substring('SERVICE_KEY='.Length).Trim() }
}
$localEnv = Join-Path $PSScriptRoot '..\..\tools\hastakabul-local.env'
if (-not $ServiceKey -and (Test-Path $localEnv)) {
    $line = Get-Content $localEnv | Where-Object { $_ -match '^SERVICE_KEY=' } | Select-Object -First 1
    if ($line) { $ServiceKey = $line.Substring('SERVICE_KEY='.Length).Trim() }
}
if (-not $ServiceKey) { throw 'SERVICE_KEY bulunamadi (.env, tools\hastakabul-local.env veya ortam degiskeni).' }

$script:step = 0
function Step([string]$name) {
    $script:step++
    Write-Host ''
    Write-Host ("[{0}] {1}" -f $script:step, $name) -ForegroundColor Cyan
}
function Ok([string]$message) { Write-Host "    OK  $message" -ForegroundColor Green }

# PowerShell 7 HttpResponseException, 5.1 WebException atar; ikisinden de kodu cikarir.
function Get-HttpStatus($errorRecord) {
    $response = $errorRecord.Exception.Response
    if ($null -eq $response) { return 0 }

    if ($response.StatusCode -is [int]) { return [int]$response.StatusCode }
    return [int]$response.StatusCode.value__
}

# Hata govdesini okur ({error:{code,message,requestId}} zarfi beklenir).
function Get-ErrorBody($errorRecord) {
    if ($errorRecord.ErrorDetails -and $errorRecord.ErrorDetails.Message) {
        try { return $errorRecord.ErrorDetails.Message | ConvertFrom-Json } catch { }
    }
    $response = $errorRecord.Exception.Response
    if ($null -eq $response) { return $null }
    try {
        $stream = $response.GetResponseStream()
        $stream.Position = 0
        $text = (New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)).ReadToEnd()
        return $text | ConvertFrom-Json
    } catch { return $null }
}

# Beklenen HTTP durumunu (ve verildiyse hata kodunu) dogrular; istek basarili olursa testi dusurur.
# Hata govdesi ortak zarfta olmalidir.
function Assert-HttpError {
    param(
        [int]$Expected,
        [string]$FailureMessage,
        [scriptblock]$Action,
        [string]$Code = ''
    )

    try {
        & $Action | Out-Null
    } catch {
        $status = Get-HttpStatus $_
        if ($status -ne $Expected) {
            throw "Beklenen HTTP $Expected, gelen $status. $($_.Exception.Message)"
        }
        $body = Get-ErrorBody $_
        if ($null -eq $body -or $null -eq $body.error -or -not $body.error.code -or -not $body.error.requestId) {
            throw "HTTP $status yaniti ortak hata zarfinda degil."
        }
        if ($Code -and $body.error.code -ne $Code) {
            throw "Beklenen kod $Code, gelen $($body.error.code)."
        }
        $script:LastError = $body.error
        return
    }

    throw $FailureMessage
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        $Body,
        [hashtable]$Headers = @{}
    )

    $uri = "$ApiUrl$Path"
    $headers = @{ 'X-Request-Id' = [guid]::NewGuid().ToString('N') } + $Headers

    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers
    }

    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers `
        -ContentType 'application/json; charset=utf-8' `
        -Body ($Body | ConvertTo-Json -Depth 6)
}

Step 'Saglik kontrolu'
$health = Invoke-Api GET '/health'
if ($health.status -ne 'ok') { throw "Beklenmeyen saglik yaniti: $($health.status)" }
Ok 'API ayakta'

Step 'Hekim listesi ve giris (dropdown + kisa omurlu JWT)'
$doctors = Invoke-Api GET '/api/v1/doctors'
if ($doctors.Count -lt 1) { throw 'Hekim listesi bos; seed calismamis olabilir.' }
Ok "$($doctors.Count) hekim listelendi"

Step 'Hata zarfi: JWT yok, bozuk govde, bilinmeyen yol'
Assert-HttpError 401 'Tokensiz istek kabul edildi.' { Invoke-Api GET '/api/v1/appointments' } -Code 'UNAUTHORIZED'
Ok "401 UNAUTHORIZED zarfta: $($script:LastError.message)"
Assert-HttpError 400 'Bos govde kabul edildi.' { Invoke-Api POST '/api/v1/kiosk/verify' @{} } -Code 'VALIDATION_FAILED'
Ok "400 VALIDATION_FAILED zarfta (eski ProblemDetails): $($script:LastError.message)"
Assert-HttpError 404 'Bilinmeyen yol 200 dondu.' { Invoke-Api GET '/api/v1/yok-boyle-bir-yol' } -Code 'NOT_FOUND'
Ok '404 NOT_FOUND zarfta'

Step 'Kiosk: TC dogrulama'
$verification = Invoke-Api POST '/api/v1/kiosk/verify' @{ tc = $DemoTc }
if (-not $verification.verified) { throw 'Dogrulama basarisiz.' }
if ($verification.displayName -notmatch '^\w+ \w\.$') {
    throw "displayName maskeli degil: $($verification.displayName)"
}
Ok "$($verification.displayName) - $($verification.appointment.clinicName), Oda $($verification.appointment.room)"

# Randevunun hekimi olarak giris yap: sonraki cagrilar bu token ile.
$appointmentId = $verification.appointment.appointmentId
$panelAppointment = $null
$token = $null
foreach ($doctor in $doctors) {
    $candidate = Invoke-Api POST '/api/v1/doctors/sign-in' @{ doctorId = $doctor.doctorId }
    $list = Invoke-Api GET '/api/v1/appointments' -Headers @{ Authorization = "Bearer $($candidate.token)" }
    $match = $list | Where-Object { $_.appointmentId -eq $appointmentId }
    if ($match) { $token = $candidate.token; $panelAppointment = $match; $doctorIdOfAppointment = $doctor.doctorId; break }
}
if (-not $token) { throw 'Randevunun hekimi bulunamadi.' }
$auth = @{ Authorization = "Bearer $token" }
Ok "Randevu hekim panelinde gorunuyor, durum: $($panelAppointment.status)"

Step 'Kiosk: "Geldim" onayi'
$checkIn = Invoke-Api POST '/api/v1/kiosk/check-in' @{ patientRef = $verification.patientRef }
if ($checkIn.status -ne 'CHECKED_IN') { throw "Beklenen CHECKED_IN, gelen $($checkIn.status)" }
Ok 'Durum CHECKED_IN (hekim paneline appointment.statusChanged yayildi)'

Step 'Gecersiz durum gecisi 409 donmeli'
Assert-HttpError 409 'Gecersiz gecis kabul edildi; durum makinesi calismiyor.' {
    Invoke-Api PATCH "/api/v1/appointments/$appointmentId/status" @{ status = 'COMPLETED' } -Headers $auth
}
Ok 'CHECKED_IN -> COMPLETED reddedildi (409 INVALID_STATUS_TRANSITION)'

Step 'Hekim: tani ve recete girisi'
$entry = Invoke-Api POST "/api/v1/appointments/$appointmentId/clinical-entries" `
    @{ diagnosis = 'Dispepsi'; medications = @('Lansoprazol') } -Headers $auth
Ok "Ozet kaydi olustu: $($entry.summaryStatus)"

$summaryId = $entry.summaryId
# Sadelestirme servisi taklidi: kayit PENDING ise geri cagri yapilir; servis kapaliysa API yedek ozete duser.
function Complete-Summary([string]$label) {
    $current = Invoke-Api GET "/api/v1/summaries/$summaryId" -Headers $auth
    if ($current.status -eq 'PENDING') {
        $callback = Invoke-Api PUT "/api/v1/summaries/$summaryId" @{
            diagnosisPlain = 'Mideniz fazla asit uretiyor; ilacinizi duzenli kullanirsaniz sikayetleriniz azalir.'
            medications    = @(@{ name = 'Lansoprazol'; purpose = 'Mide asidini azaltir.'; usage = 'Sabah ac karnina.' })
            warnings       = @('Ilaci kendi kararinizla birakmayin.')
            unknownTerms   = @()
            sourceIds      = @()
        } -Headers @{ 'X-Service-Key' = $ServiceKey }
        if ($callback.status -ne 'READY') { throw "Beklenen READY, gelen $($callback.status)" }
        Ok ($label + ': geri cagriyla READY (summary.ready yayildi)')
    } elseif ($current.status -eq 'READY') {
        Ok ($label + ': READY (yedek ozet ya da gercek servis)')
    } else {
        throw ($label + ': beklenmeyen ozet durumu ' + $current.status)
    }
}

Step 'AI servisi taklidi: ozet callback (X-Service-Key)'
Complete-Summary 'Ilk uretim'

Step 'Baska hekim ozeti okuyamamali'
$other = $doctors | Where-Object { $_.doctorId -ne $doctorIdOfAppointment } | Select-Object -First 1
$otherToken = (Invoke-Api POST '/api/v1/doctors/sign-in' @{ doctorId = $other.doctorId }).token
Assert-HttpError 403 'Baska hekim ozeti okudu.' {
    Invoke-Api GET "/api/v1/summaries/$summaryId" -Headers @{ Authorization = "Bearer $otherToken" }
} -Code 'FORBIDDEN'
Ok '403 FORBIDDEN'

Step 'Hekim reddi = yeniden olustur'
$rejected = Invoke-Api POST "/api/v1/summaries/$summaryId/reject" -Headers $auth
if ($rejected.status -ne 'REJECTED') { throw "Beklenen REJECTED, gelen $($rejected.status)" }
$after = Invoke-Api GET "/api/v1/summaries/$summaryId" -Headers $auth
if ($after.status -notin @('PENDING', 'READY')) { throw "Ret sonrasi beklenen PENDING/READY, gelen $($after.status)" }
Ok "Ret kaydedildi (REJECTED), ozet yeniden uretimde: $($after.status)"
Complete-Summary 'Yeniden uretim'

Step 'Servis anahtari olmadan callback reddedilmeli'
Assert-HttpError 401 'Anahtarsiz callback kabul edildi.' {
    Invoke-Api PUT "/api/v1/summaries/$summaryId" @{ diagnosisPlain = 'yetkisiz deneme' }
}
Ok 'Anahtarsiz istek 401 UNAUTHORIZED'

Step 'Portal: onaysiz ozet gorunmemeli'
$accessCode = '246802'
Assert-HttpError 409 'Onaysiz ozet portala dustu.' {
    Invoke-Api POST '/api/v1/portal/summary' @{ accessCode = $accessCode }
}
Ok 'Onaysiz ozet reddedildi (409 SUMMARY_NOT_APPROVED)'

Step 'Hekim onayi'
$decision = Invoke-Api POST "/api/v1/summaries/$summaryId/approve" -Headers $auth
if ($decision.status -ne 'APPROVED') { throw "Beklenen APPROVED, gelen $($decision.status)" }
Ok 'Ozet APPROVED'

Step 'Portal: onayli ozet'
$portal = Invoke-Api POST '/api/v1/portal/summary' @{ accessCode = $accessCode }
if (-not $portal.summary.diagnosisPlain) { throw 'Portal ozeti bos.' }
if ($portal.summary.PSObject.Properties.Name -contains 'patientRef') { throw 'Portal yaniti patientRef tasiyor.' }
Ok "$($portal.displayName): $($portal.summary.diagnosisPlain.Substring(0, [Math]::Min(60, $portal.summary.diagnosisPlain.Length)))..."

Step 'Sesli randevu: oturum, slot, randevu'
$session = Invoke-Api POST '/api/v1/voice-sessions'
$tomorrow = (Get-Date).AddDays(1).ToString('yyyy-MM-dd')
$slots = Invoke-Api GET "/api/v1/slots?clinic=dahiliye&date=$tomorrow" -Headers @{ 'X-Service-Key' = $ServiceKey }
if ($slots.Count -lt 1) { throw 'Yarin icin bos slot yok.' }
Ok "$($slots.Count) bos saat bulundu (ilk: $($slots[0].startTime))"

Assert-HttpError 400 'Bicimi gecersiz TC kabul edildi.' {
    Invoke-Api POST "/api/v1/voice-sessions/$($session.sessionId)/verify" @{ tc = $MalformedTc }
} -Code 'VALIDATION_FAILED'
$voiceVerify = Invoke-Api POST "/api/v1/voice-sessions/$($session.sessionId)/verify" @{ tc = $DemoTc }
if (-not $voiceVerify.verified) { throw 'Ses oturumu dogrulanamadi.' }
if ($voiceVerify.attempts -ne 0) { throw "Bicim hatasi ya da basarili dogrulama deneme sayildi: $($voiceVerify.attempts)" }
Ok "Bicim hatasi 400 (sayac artmadi), oturum dogrulandi (basarisiz deneme $($voiceVerify.attempts)/$($voiceVerify.maxAttempts))"

$history = Invoke-Api GET "/api/v1/voice-sessions/$($session.sessionId)/medical-history?limit=3" -Headers @{ 'X-Service-Key' = $ServiceKey }
if ($history.visits.Count -lt 2) { throw "Demo hastasinin en az 2 onayli gecmis muayenesi beklenirdi, gelen $($history.visits.Count)." }
Ok "Gecmis muayeneler: $(($history.visits | ForEach-Object { $_.clinicName }) -join ', ')"

$appointment = Invoke-Api POST '/api/v1/appointments' `
    @{ voiceSessionId = $session.sessionId; slotId = $slots[0].slotId } `
    -Headers @{ 'X-Service-Key' = $ServiceKey }
Ok "Randevu olusturuldu: $($appointment.scheduledAt) - $($appointment.doctorDisplayName)"

Step 'Ayni slot ikinci kez alinmamali'
$session2 = Invoke-Api POST '/api/v1/voice-sessions'
Invoke-Api POST "/api/v1/voice-sessions/$($session2.sessionId)/verify" @{ tc = $DemoTc } | Out-Null
Assert-HttpError 409 'Alinmis slot yeniden satildi.' {
    Invoke-Api POST '/api/v1/appointments' `
        @{ voiceSessionId = $session2.sessionId; slotId = $slots[0].slotId } `
        -Headers @{ 'X-Service-Key' = $ServiceKey }
}
Ok 'Dolu slot reddedildi (409 SLOT_TAKEN)'

Step 'Cakisan randevu: ayni saat baska poliklinik, ayni gun ayni poliklinik'
$karSlots = Invoke-Api GET "/api/v1/slots?clinic=kardiyoloji&date=$tomorrow" -Headers @{ 'X-Service-Key' = $ServiceKey }
$sameTime = $karSlots | Where-Object { $_.startTime -eq $slots[0].startTime } | Select-Object -First 1
if (-not $sameTime) { throw 'Kardiyolojide ayni saatte bos slot yok.' }
Assert-HttpError 409 'Ayni saate ikinci randevu verildi.' {
    Invoke-Api POST '/api/v1/appointments' @{ voiceSessionId = $session2.sessionId; slotId = $sameTime.slotId } `
        -Headers @{ 'X-Service-Key' = $ServiceKey }
} -Code 'APPOINTMENT_CONFLICT'
Assert-HttpError 409 'Ayni gun ayni poliklinige ikinci randevu verildi.' {
    Invoke-Api POST '/api/v1/appointments' @{ voiceSessionId = $session2.sessionId; slotId = $slots[$slots.Count - 1].slotId } `
        -Headers @{ 'X-Service-Key' = $ServiceKey }
} -Code 'APPOINTMENT_CONFLICT'
Ok 'Iki cakisma da 409 APPOINTMENT_CONFLICT'

Step 'Hatali TC ile deneme sayaci'
$session3 = Invoke-Api POST '/api/v1/voice-sessions'
$last = $null
for ($i = 1; $i -le 3; $i++) {
    $last = Invoke-Api POST "/api/v1/voice-sessions/$($session3.sessionId)/verify" @{ tc = $UnregisteredTc }
}
if (-not $last.handoff) { throw 'Uc denemeden sonra HANDOFF beklenirdi.' }
Ok 'Uc basarisiz (bicimi gecerli, kayitsiz) denemeden sonra HANDOFF'

Write-Host ''
Write-Host "Duman testi tamam: $script:step adim, hepsi gecti." -ForegroundColor Green
