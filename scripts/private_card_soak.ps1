[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CardPath,
    [string]$BaseUrl = "http://localhost:8080",
    [string]$CopyTo = "C:\tmp\sleeplab-private-card-soak\card",
    [switch]$SkipCopy,
    [switch]$ResetSoakData,
    [string]$OutputPath,
    [string]$AccessToken,
    [string]$Email,
    [SecureString]$Password,
    [switch]$AllowLegacyFallback,
    [int]$TimeoutMinutes = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$script:Http = $null
$script:Headers = @{}

function Resolve-NormalizedPath([string]$Path) {
    [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Path).Path).TrimEnd('\', '/')
}

function Test-PathInside([string]$Candidate, [string]$Parent) {
    $candidatePath = [IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $parentPath = [IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    $candidatePath.Equals($parentPath, [StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Get-OptionalProperty {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory)][string[]]$Names,
        [AllowNull()][object]$Default = $null
    )
    if ($null -eq $InputObject) { return $Default }
    foreach ($name in $Names) {
        if ($InputObject -is [Collections.IDictionary] -and $InputObject.Contains($name)) {
            return $InputObject[$name]
        }
        $property = $InputObject.PSObject.Properties[$name]
        if ($null -ne $property -and $null -ne $property.Value) { return $property.Value }
    }
    return $Default
}

function ConvertTo-FlatArray([AllowNull()][object]$InputObject) {
    if ($null -eq $InputObject) { return }
    @($InputObject) | ForEach-Object {
        if ($_ -is [Array]) { $_ | ForEach-Object { $_ } }
        else { $_ }
    }
}

function ConvertTo-OptionalBoolean([AllowNull()][object]$Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [bool]) { return $Value }
    if ($Value -is [string]) {
        if ($Value.Trim() -match '^(1|true|yes|on)$') { return $true }
        if ($Value.Trim() -match '^(0|false|no|off)$') { return $false }
        return $null
    }
    return [bool]$Value
}

function Normalize-BackendName([AllowNull()][object]$Value) {
    if ($null -eq $Value) { return $null }
    $normalized = ([string]$Value).Trim().ToLowerInvariant()
    if ($normalized -match 'cpap[-_ ]?parser|parser.*(default|recommended)|^(parser|recommended|default)$') { return "cpap-parser" }
    if ($normalized -match 'legacy|native') { return "legacy" }
    return $normalized
}

function Resolve-ApiBase([string]$RequestedBase) {
    $candidates = [Collections.Generic.List[string]]::new()
    $candidates.Add($RequestedBase.TrimEnd('/'))
    try {
        $runtimeConfig = Invoke-WebRequest -UseBasicParsing "$($RequestedBase.TrimEnd('/'))/config.js"
        if ($runtimeConfig.Content -match 'API_URL\s*:\s*["''](?<url>[^"'']+)["'']') {
            $candidates.Add($Matches.url.TrimEnd('/'))
        }
    } catch { }
    if ($RequestedBase -match '^https?://(localhost|127\.0\.0\.1)(:\d+)?$') {
        $candidates.Add("http://localhost:8000")
        $candidates.Add("http://127.0.0.1:8000")
    }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        try {
            $response = Invoke-RestMethod "$candidate/config"
            $displayTz = Get-OptionalProperty $response @("display_tz")
            $backend = Get-OptionalProperty $response @("resmed_import_backend", "importer_backend", "backend")
            $parserFlag = Get-OptionalProperty $response @("parser_enabled", "use_cpap_parser")
            if ($response -isnot [string] -and
                ($null -ne $displayTz -or $null -ne $backend -or $null -ne $parserFlag -or $response.PSObject.Properties.Count -gt 0)) {
                return [pscustomobject]@{ Base = $candidate; Config = $response }
            }
        } catch { }
    }
    return [pscustomobject]@{ Base = $RequestedBase.TrimEnd('/'); Config = $null }
}

function Invoke-Api([string]$Method, [string]$Path, [object]$Body = $null) {
    $args = @{ Method = $Method; Uri = "$($script:Base)/$($Path.TrimStart('/'))" }
    if ($script:Headers.Count) { $args.Headers = $script:Headers }
    if ($null -ne $Body) {
        $args.ContentType = "application/json"
        $args.Body = $Body | ConvertTo-Json -Depth 8
    }
    Invoke-RestMethod @args
}

function Wait-Import([string]$RunId) {
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        $run = Invoke-Api GET "/imports/runs/$RunId"
        $status = Get-OptionalProperty $run @("status", "import_status") "unknown"
        if ($status -notin @("pending", "running")) { return $run }
        Start-Sleep -Seconds 3
    }
    throw "Import did not finish within $TimeoutMinutes minutes."
}

function Send-SourceBatch([string]$UploadId, [IO.FileInfo[]]$Files, [string]$Root) {
    $content = [Net.Http.MultipartFormDataContent]::new()
    try {
        foreach ($file in $Files) {
            $rootPrefix = $Root.TrimEnd('\', '/')
            $relative = $file.FullName.Substring($rootPrefix.Length).TrimStart('\', '/').Replace('\', '/')
            $part = [Net.Http.StreamContent]::new([IO.File]::OpenRead($file.FullName))
            $part.Headers.ContentDisposition = [Net.Http.Headers.ContentDispositionHeaderValue]::new("form-data")
            $part.Headers.ContentDisposition.Name = '"files"'
            $part.Headers.ContentDisposition.FileName = '"' + $relative.Replace('"', '') + '"'
            [void]$content.Add($part)
        }
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, "$($script:Base)/upload/source/$UploadId/batch")
        $request.Content = $content
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $AccessToken)
        $response = $script:Http.SendAsync($request).GetAwaiter().GetResult()
        $payload = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "Upload batch failed (HTTP $([int]$response.StatusCode)): $payload" }
        $payload | ConvertFrom-Json
    } finally { $content.Dispose() }
}

function Invoke-SourceImport([string]$Root) {
    $upload = Invoke-Api POST "/upload/source/start" @{ root_name = "private-card-soak" }
    try {
        $files = @(Get-ChildItem -LiteralPath $Root -File -Recurse -Force | Where-Object {
            $_.FullName -notmatch '[\\/](System Volume Information|\$RECYCLE\.BIN)([\\/]|$)'
        })
        if (-not $files.Count) { throw "The card folder contains no uploadable files." }
        for ($offset = 0; $offset -lt $files.Count; $offset += 50) {
            $last = [Math]::Min($offset + 49, $files.Count - 1)
            [void](Send-SourceBatch $upload.upload_id $files[$offset..$last] $Root)
            Write-Host "Uploaded $([Math]::Min($offset + 50, $files.Count))/$($files.Count) private files (names suppressed)."
        }
        $plan = Invoke-Api POST "/upload/source/$($upload.upload_id)/inspect"
        $finish = Invoke-Api POST "/upload/source/$($upload.upload_id)/finish"
        $run = if ($finish.status -eq "unchanged") {
            Invoke-Api GET "/imports/runs/$($finish.import_run_id)"
        } else { Wait-Import $finish.import_run_id }
        [pscustomobject]@{ TriggerStatus = $finish.status; Run = $run; Plan = $plan }
    } catch {
        try { [void](Invoke-Api DELETE "/upload/source/$($upload.upload_id)") } catch { }
        throw
    }
}

function Get-Counts([string]$UserId, [string]$DbService) {
    if ($UserId -notmatch '^[0-9a-fA-F-]{36}$') { throw "Authenticated user id is invalid." }
    $sql = "SELECT json_build_object('sessions',(SELECT count(*) FROM sessions WHERE user_id='$UserId'::uuid),'events',(SELECT count(*) FROM session_events e JOIN sessions s ON s.id=e.session_id WHERE s.user_id='$UserId'::uuid),'waveform_chunks',(SELECT count(*) FROM waveform_chunks w JOIN sessions s ON s.id=w.session_id WHERE s.user_id='$UserId'::uuid),'session_waveform',(SELECT count(*) FROM session_waveform w JOIN sessions s ON s.id=w.session_id WHERE s.user_id='$UserId'::uuid),'import_runs',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT status,importer_mode AS importer_backend,count(*) AS count FROM import_runs WHERE user_id='$UserId'::uuid GROUP BY status,importer_mode ORDER BY status,importer_mode)x));"
    $raw = & docker compose exec -T $DbService psql -U cpap -d cpap -At -c $sql
    if ($LASTEXITCODE) { throw "Database count query failed through '$DbService'." }
    ($raw -join "") | ConvertFrom-Json
}

function Get-SafeRunSummary($Run) {
    [ordered]@{
        status = Get-OptionalProperty $Run @("status", "import_status") "unknown"
        validation_status = Get-OptionalProperty $Run @("validation_status") "unknown"
        importer_backend = Get-OptionalProperty $Run @("importer_mode", "importer_backend", "backend")
        sessions_added = Get-OptionalProperty $Run @("sessions_added_count")
        sessions_updated = Get-OptionalProperty $Run @("sessions_updated_count")
        sessions_skipped = Get-OptionalProperty $Run @("sessions_skipped_count")
        imported_sessions = Get-OptionalProperty $Run @("imported_session_count")
        imported_events = Get-OptionalProperty $Run @("imported_event_count")
        waveform_chunks = Get-OptionalProperty $Run @("waveform_chunk_count")
        warning_codes = @((Get-OptionalProperty $Run @("warnings") @()) | ForEach-Object { Get-OptionalProperty $_ @("code") } | Where-Object { $_ } | Sort-Object -Unique)
        error_codes = @((Get-OptionalProperty $Run @("errors") @()) | ForEach-Object { Get-OptionalProperty $_ @("code") } | Where-Object { $_ } | Sort-Object -Unique)
    }
}

Write-Warning "Private CPAP data, serials, PHI, reports, and generated fixtures must never be committed."
$repo = Resolve-NormalizedPath (Join-Path $PSScriptRoot "..")
$source = Resolve-NormalizedPath $CardPath
if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "CardPath must be a directory." }
if (Test-PathInside $source $repo) { throw "CardPath must be outside the repository." }
$copyTarget = [IO.Path]::GetFullPath($CopyTo)
if (Test-PathInside $copyTarget $repo) { throw "CopyTo must be outside the repository." }

Push-Location $repo
try {
    $branch = (& git branch --show-current).Trim()
    $head = (& git rev-parse --short HEAD).Trim()
    $tags = ((& git tag --points-at HEAD) -join ", ").Trim()
    $dirty = [bool](& git status --porcelain)
    if ($dirty) { Write-Warning "Working tree is dirty; ensure no private data is staged or untracked." }
    if ($branch -ne "develop/2.0") { throw "Expected develop/2.0; found '$branch'." }
    $flag = [Environment]::GetEnvironmentVariable("SLEEPLAB_USE_CPAP_PARSER")
    if (-not $AllowLegacyFallback -and $flag -match '^(0|false|no|off)$') { throw "SLEEPLAB_USE_CPAP_PARSER forces legacy fallback." }

    $services = @(& docker compose config --services)
    if ($LASTEXITCODE -or -not $services.Count) { throw "Docker Compose config is unavailable." }
    $dbService = @($services | Where-Object { $_ -match 'postgres|(^|[-_])db($|[-_])' })[0]
    $appService = @($services | Where-Object { $_ -match 'app|api|web' })[0]
    if (-not $dbService -or -not $appService) { throw "Could not discover app/database Compose services." }
    & docker compose up -d $dbService $appService
    if ($LASTEXITCODE) { throw "Docker Compose services did not start." }

    $resolvedApi = Resolve-ApiBase $BaseUrl
    $script:Base = $resolvedApi.Base
    $script:Headers = @{}
    $script:Http = [Net.Http.HttpClient]::new()
    $script:Http.Timeout = [TimeSpan]::FromMinutes(10)
    $deadline = (Get-Date).AddMinutes(5); $health = $null
    do { try { $health = Invoke-RestMethod "$($script:Base)/health" } catch { Start-Sleep 3 } } while (-not $health -and (Get-Date) -lt $deadline)
    if (-not $health) { throw "SleepLab is not reachable at $($script:Base)." }
    $config = if ($null -ne $resolvedApi.Config) { $resolvedApi.Config } else { try { Invoke-Api GET "/config" } catch { $null } }
    $configBackend = Normalize-BackendName (Get-OptionalProperty $config @("resmed_import_backend", "importer_backend", "backend"))
    $parserEnabled = ConvertTo-OptionalBoolean (Get-OptionalProperty $config @("parser_enabled", "use_cpap_parser"))
    $parserReady = ConvertTo-OptionalBoolean (Get-OptionalProperty $config @("resmed_import_ready", "cpap_parser_ready", "parser_ready", "cpap_parser_available"))
    if (-not $configBackend -and $null -ne $parserEnabled) { $configBackend = if ($parserEnabled) { "cpap-parser" } else { "legacy" } }
    $parserDefaultCheck = if ($configBackend -eq "cpap-parser" -and $parserReady -eq $true) { "PASS" }
        elseif ($configBackend -eq "legacy" -or $parserReady -eq $false) { "FAIL" }
        else { "INCONCLUSIVE" }
    Write-Host "Parser default/provenance preflight: $parserDefaultCheck"

    if (-not $AccessToken) {
        if (-not $Email) { $Email = Read-Host "SleepLab login email" }
        if (-not $Password) { $Password = Read-Host "SleepLab login password" -AsSecureString }
        $plainPassword = [Net.NetworkCredential]::new("", $Password).Password
        $AccessToken = (Invoke-Api POST "/auth/login" @{ email = $Email; password = $plainPassword }).token
        $plainPassword = $null
    }
    $script:Headers.Authorization = "Bearer $AccessToken"
    $me = Invoke-Api GET "/auth/me"
    $userId = Get-OptionalProperty $me @("id", "user_id")
    if (-not $userId) { throw "Authenticated user response did not include id or user_id." }

    if ($SkipCopy) { $soakRoot = $source; $copyMode = "source used directly" }
    else {
        if (Test-Path $copyTarget) {
            if (@(Get-ChildItem -LiteralPath $copyTarget -Force).Count) { throw "CopyTo must be empty; choose a clean private destination." }
        } else { [void](New-Item -ItemType Directory -Path $copyTarget -Force) }
        Write-Host "Copying private card (names suppressed)..."
        & robocopy $source $copyTarget /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP /XD "System Volume Information" '$RECYCLE.BIN'
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE." }
        $soakRoot = Resolve-NormalizedPath $copyTarget
        $copyMode = "copied outside repository"
    }

    if ($ResetSoakData) {
        Write-Warning "ResetSoakData deletes sessions, history, and machines for this user in this database."
        [void](Invoke-Api DELETE "/sessions/all?reset=true")
    }
    $before = Get-Counts $userId $dbService
    Write-Host "Starting fresh parser-default import..."
    $fresh = Invoke-SourceImport $soakRoot
    $afterFresh = Get-Counts $userId $dbService

    $sessions = @(ConvertTo-FlatArray (Invoke-Api GET "/sessions/?page=1&per_page=600"))
    $coveragePassed = $false; $eventPassed = $false; $fullNightPassed = $false
    $visibilityDiagnostics = [ordered]@{
        sessions_examined = 0; parser_backed_nights = 0; chunk_backed_nights = 0
        full_night_flow_candidates = 0; chunk_and_event_candidates = 0
    }
    foreach ($session in $sessions) {
        $visibilityDiagnostics.sessions_examined++
        $sessionId = Get-OptionalProperty $session @("id", "session_id")
        if (-not $sessionId) { continue }
        $detail = Invoke-Api GET "/sessions/$sessionId"
        $availability = Get-OptionalProperty $detail @("data_availability", "coverage")
        $nightBackend = Get-OptionalProperty $availability @("import_backend", "importer_backend", "backend")
        if ($nightBackend -ne "cpap-parser") { continue }
        $visibilityDiagnostics.parser_backed_nights++
        $waveformSource = Get-OptionalProperty $availability @("event_waveform_source", "waveform_source")
        if ($waveformSource -ne "chunks") { continue }
        $visibilityDiagnostics.chunk_backed_nights++
        $hasFullNight = Get-OptionalProperty $availability @("full_night_flow_available", "full_night_waveform_available") $false
        if (-not $hasFullNight) { continue }
        $visibilityDiagnostics.full_night_flow_candidates++
        $signals = @(ConvertTo-FlatArray (Invoke-Api GET "/sessions/$sessionId/waveforms"))
        $signalNames = @($signals | ForEach-Object { Get-OptionalProperty $_ @("signal_name", "name") })
        if ($signalNames -notcontains "flow_rate") { continue }
        $events = @(ConvertTo-FlatArray (Invoke-Api GET "/sessions/$sessionId/events"))
        if (-not $events.Count) { continue }
        $eventId = Get-OptionalProperty $events[0] @("id", "event_id")
        if (-not $eventId) { continue }
        $visibilityDiagnostics.chunk_and_event_candidates++

        $coveragePassed = $true
        $signal = Invoke-Api GET "/sessions/$sessionId/waveforms/flow_rate?max_points=500"
        $fullNightPassed = [long](Get-OptionalProperty $signal @("returned_sample_count", "sample_count") 0) -gt 0
        $window = Invoke-Api GET "/sessions/$sessionId/events/$eventId/window?waveform_downsample=10"
        $waveform = Get-OptionalProperty $window @("waveform")
        $eventPassed = @((Get-OptionalProperty $waveform @("timestamps") @())).Count -gt 0
        if ($eventPassed -and $fullNightPassed) { break }
    }

    Write-Host "Starting exact same-card re-import..."
    $second = Invoke-SourceImport $soakRoot
    $afterSecond = Get-Counts $userId $dbService
    $history = @(ConvertTo-FlatArray (Invoke-Api GET "/imports/runs?limit=100"))
    $failures = [Collections.Generic.List[string]]::new()
    $manual = [Collections.Generic.List[string]]::new()
    $freshStatus = Get-OptionalProperty $fresh.Run @("status", "import_status") "unknown"
    $freshBackend = Normalize-BackendName (Get-OptionalProperty $fresh.Run @("importer_mode", "importer_backend", "backend"))
    $secondStatus = Get-OptionalProperty $second.Run @("status", "import_status") "unknown"
    $secondAdded = [long](Get-OptionalProperty $second.Run @("sessions_added_count") 0)
    $secondUpdated = [long](Get-OptionalProperty $second.Run @("sessions_updated_count") 0)
    $secondSkipped = [long](Get-OptionalProperty $second.Run @("sessions_skipped_count") 0)
    $parserProvenanceStatus = if ($parserDefaultCheck -eq "PASS" -and $freshBackend -eq "cpap-parser") { "PASS" }
        elseif ($parserDefaultCheck -eq "FAIL" -or ($freshBackend -and $freshBackend -ne "cpap-parser")) { "FAIL" }
        else { "INCONCLUSIVE" }
    if ($parserProvenanceStatus -eq "FAIL" -and -not $AllowLegacyFallback) { $failures.Add("Parser default/provenance check failed.") }
    if ($parserProvenanceStatus -eq "FAIL" -and $AllowLegacyFallback) { $manual.Add("Parser default/provenance is not parser-backed because this was an explicit fallback test.") }
    if ($parserProvenanceStatus -eq "INCONCLUSIVE") { $manual.Add("Parser default/provenance could not be confirmed from config and import history fields.") }
    if ($fresh.TriggerStatus -eq "unchanged") { $manual.Add("Fresh semantics were not exercised; rerun with -ResetSoakData on a disposable soak user/database.") }
    if ($freshStatus -notin @("success", "unchanged")) { $failures.Add("Fresh import status: $freshStatus.") }
    if ($second.TriggerStatus -ne "unchanged" -and $secondStatus -notin @("success", "unchanged")) { $failures.Add("Re-import status: $secondStatus.") }
    $reimportNoOp = $second.TriggerStatus -eq "unchanged" -or
        ($secondAdded -eq 0 -and $secondUpdated -eq 0 -and $secondSkipped -gt 0)
    if (-not $reimportNoOp) { $failures.Add("Re-import did not clearly report unchanged/skipped no-op behavior.") }
    foreach ($name in @("sessions", "events", "waveform_chunks", "session_waveform")) {
        if ([long]$afterSecond.$name -ne [long]$afterFresh.$name) { $failures.Add("$name count changed on re-import.") }
    }
    if ([long]$afterFresh.waveform_chunks -le [long]$before.waveform_chunks -and $fresh.TriggerStatus -ne "unchanged") { $failures.Add("Fresh import added no waveform chunks.") }
    if ([long]$afterFresh.session_waveform -ne [long]$before.session_waveform) { $failures.Add("Parser import changed session_waveform row count.") }
    if (-not $coveragePassed) { $failures.Add("No night reported chunk-backed Event waveform coverage.") }
    if (-not $eventPassed) { $failures.Add("Event Inspector returned no usable waveform.") }
    if (-not $fullNightPassed) { $failures.Add("Full-night endpoint returned no usable flow data.") }
    $historyPassed = @($history | Where-Object {
        (Normalize-BackendName (Get-OptionalProperty $_ @("importer_mode", "importer_backend", "backend"))) -eq "cpap-parser" -and
        (Get-OptionalProperty $_ @("status", "import_status")) -eq "success"
    }).Count -gt 0
    if (-not $historyPassed) { $failures.Add("Import History has no successful cpap-parser run.") }
    if (-not $sessions.Count) { $failures.Add("No imported sessions are visible.") }
    $decision = if ($failures.Count) { "FAIL" } elseif ($manual.Count) { "INCONCLUSIVE" } else { "PASS" }
    Write-Host "Parser default/provenance: $parserProvenanceStatus"
    $warningCodes = @((Get-OptionalProperty $fresh.Run @("warnings") @()) | ForEach-Object { Get-OptionalProperty $_ @("code") } | Where-Object { $_ } | Sort-Object -Unique)
    $sessionWaveformNoBloat = [long]$before.session_waveform -eq [long]$afterFresh.session_waveform -and
        [long]$afterFresh.session_waveform -eq [long]$afterSecond.session_waveform
    $report = [ordered]@{
        decision = $decision; generated_at = (Get-Date).ToString("o")
        git = [ordered]@{ branch = $branch; head = $head; tags_at_head = $tags; dirty = $dirty }
        app = [ordered]@{ requested_base_url = $BaseUrl; api_base_url = $script:Base; backend = $configBackend; parser_ready = $parserReady }
        card_handling = $copyMode
        fresh_import = Get-SafeRunSummary $fresh.Run
        reimport = [ordered]@{ trigger_status = $second.TriggerStatus; summary = Get-SafeRunSummary $second.Run }
        counts = [ordered]@{ before = $before; after_fresh = $afterFresh; after_reimport = $afterSecond }
        checks = [ordered]@{
            parser_default_provenance = $parserProvenanceStatus
            import_storage_idempotency = [ordered]@{
                fresh_import_success = $freshStatus -eq "success"
                reimport_reported_no_op = $reimportNoOp
                sessions_stable = [long]$afterSecond.sessions -eq [long]$afterFresh.sessions
                events_stable = [long]$afterSecond.events -eq [long]$afterFresh.events
                waveform_chunks_stable = [long]$afterSecond.waveform_chunks -eq [long]$afterFresh.waveform_chunks
                session_waveform_no_bloat = $sessionWaveformNoBloat
            }
            api_waveform_visibility = [ordered]@{
                sessions_visible = [bool]$sessions.Count; import_history = $historyPassed
                nightly_chunk_coverage = $coveragePassed; event_inspector = $eventPassed
                full_night_waveform = $fullNightPassed
            }
        }
        visibility_diagnostics = $visibilityDiagnostics
        warnings = [ordered]@{
            codes = $warningCodes
            summary_only_day_present = $warningCodes -contains "resmed_summary_only_day"
            treated_as_failures = $false
        }
        manual_checks = @($manual); failures = @($failures)
    }
    Write-Host "`n=== SleepLab private-card soak: $decision ==="
    Write-Host ($report | ConvertTo-Json -Depth 10)
    if ($OutputPath) {
        $output = [IO.Path]::GetFullPath($OutputPath)
        if (Test-PathInside $output $repo) { throw "OutputPath must be outside the repository." }
        [void](New-Item -ItemType Directory -Path (Split-Path -Parent $output) -Force)
        $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $output -Encoding utf8
        Write-Host "Report saved outside the repository."
    }
    if ($decision -eq "FAIL") { exit 1 }
    if ($decision -eq "INCONCLUSIVE") { exit 2 }
} finally {
    if ($script:Http) { $script:Http.Dispose() }
    Pop-Location
}
