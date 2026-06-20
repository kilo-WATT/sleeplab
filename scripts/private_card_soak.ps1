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
        if ($run.status -notin @("pending", "running")) { return $run }
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
        status = $Run.status; validation_status = $Run.validation_status
        importer_backend = $Run.importer_mode
        sessions_added = $Run.sessions_added_count; sessions_updated = $Run.sessions_updated_count
        sessions_skipped = $Run.sessions_skipped_count; imported_sessions = $Run.imported_session_count
        imported_events = $Run.imported_event_count; waveform_chunks = $Run.waveform_chunk_count
        warning_codes = @($Run.warnings | ForEach-Object { $_.code } | Where-Object { $_ } | Sort-Object -Unique)
        error_codes = @($Run.errors | ForEach-Object { $_.code } | Where-Object { $_ } | Sort-Object -Unique)
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

    $script:Base = $BaseUrl.TrimEnd('/')
    $script:Headers = @{}
    $script:Http = [Net.Http.HttpClient]::new()
    $script:Http.Timeout = [TimeSpan]::FromMinutes(10)
    $deadline = (Get-Date).AddMinutes(5); $health = $null
    do { try { $health = Invoke-RestMethod "$($script:Base)/health" } catch { Start-Sleep 3 } } while (-not $health -and (Get-Date) -lt $deadline)
    if (-not $health) { throw "SleepLab is not reachable at $($script:Base)." }
    $config = Invoke-Api GET "/config"
    if (-not $AllowLegacyFallback -and $config.resmed_import_backend -ne "cpap-parser") { throw "Running app uses the legacy backend." }
    if ($config.resmed_import_backend -eq "cpap-parser" -and -not $config.resmed_import_ready) { throw "cpap-parser is not ready." }

    if (-not $AccessToken) {
        if (-not $Email) { $Email = Read-Host "SleepLab login email" }
        if (-not $Password) { $Password = Read-Host "SleepLab login password" -AsSecureString }
        $plainPassword = [Net.NetworkCredential]::new("", $Password).Password
        $AccessToken = (Invoke-Api POST "/auth/login" @{ email = $Email; password = $plainPassword }).token
        $plainPassword = $null
    }
    $script:Headers.Authorization = "Bearer $AccessToken"
    $me = Invoke-Api GET "/auth/me"

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
    $before = Get-Counts $me.id $dbService
    Write-Host "Starting fresh parser-default import..."
    $fresh = Invoke-SourceImport $soakRoot
    $afterFresh = Get-Counts $me.id $dbService

    $sessions = @(Invoke-Api GET "/sessions/?page=1&per_page=600")
    $coveragePassed = $false; $eventPassed = $false; $fullNightPassed = $false
    foreach ($session in $sessions) {
        $detail = Invoke-Api GET "/sessions/$($session.id)"
        if ($detail.data_availability.import_backend -ne "cpap-parser" -or -not $detail.data_availability.full_night_flow_available) { continue }
        $coveragePassed = $detail.data_availability.event_waveform_source -eq "chunks"
        $signals = @(Invoke-Api GET "/sessions/$($session.id)/waveforms")
        if ($signals.signal_name -contains "flow_rate") {
            $signal = Invoke-Api GET "/sessions/$($session.id)/waveforms/flow_rate?max_points=500"
            $fullNightPassed = $signal.returned_sample_count -gt 0
        }
        $events = @(Invoke-Api GET "/sessions/$($session.id)/events")
        if ($events.Count) {
            $window = Invoke-Api GET "/sessions/$($session.id)/events/$($events[0].id)/window?waveform_downsample=10"
            $eventPassed = @($window.waveform.timestamps).Count -gt 0
        }
        if ($coveragePassed -and $eventPassed -and $fullNightPassed) { break }
    }

    Write-Host "Starting exact same-card re-import..."
    $second = Invoke-SourceImport $soakRoot
    $afterSecond = Get-Counts $me.id $dbService
    $history = @(Invoke-Api GET "/imports/runs?limit=100")
    $failures = [Collections.Generic.List[string]]::new()
    $manual = [Collections.Generic.List[string]]::new()
    if ($fresh.TriggerStatus -eq "unchanged") { $manual.Add("Fresh semantics were not exercised; rerun with -ResetSoakData on a disposable soak user/database.") }
    if ($fresh.Run.status -notin @("success", "unchanged")) { $failures.Add("Fresh import status: $($fresh.Run.status).") }
    if (-not $AllowLegacyFallback -and $fresh.Run.importer_mode -ne "cpap-parser") { $failures.Add("Fresh importer was not cpap-parser.") }
    if ($second.TriggerStatus -ne "unchanged" -and $second.Run.status -notin @("success", "unchanged")) { $failures.Add("Re-import status: $($second.Run.status).") }
    $reimportNoOp = $second.TriggerStatus -eq "unchanged" -or
        ([long]$second.Run.sessions_added_count -eq 0 -and
         [long]$second.Run.sessions_updated_count -eq 0 -and
         [long]$second.Run.sessions_skipped_count -gt 0)
    if (-not $reimportNoOp) { $failures.Add("Re-import did not clearly report unchanged/skipped no-op behavior.") }
    foreach ($name in @("sessions", "events", "waveform_chunks", "session_waveform")) {
        if ([long]$afterSecond.$name -ne [long]$afterFresh.$name) { $failures.Add("$name count changed on re-import.") }
    }
    if ([long]$afterFresh.waveform_chunks -le [long]$before.waveform_chunks -and $fresh.TriggerStatus -ne "unchanged") { $failures.Add("Fresh import added no waveform chunks.") }
    if ([long]$afterFresh.session_waveform -ne [long]$before.session_waveform) { $failures.Add("Parser import changed session_waveform row count.") }
    if (-not $coveragePassed) { $failures.Add("No night reported chunk-backed Event waveform coverage.") }
    if (-not $eventPassed) { $failures.Add("Event Inspector returned no usable waveform.") }
    if (-not $fullNightPassed) { $failures.Add("Full-night endpoint returned no usable flow data.") }
    $historyPassed = @($history | Where-Object { $_.importer_mode -eq "cpap-parser" -and $_.status -eq "success" }).Count -gt 0
    if (-not $historyPassed) { $failures.Add("Import History has no successful cpap-parser run.") }
    if (-not $sessions.Count) { $failures.Add("No imported sessions are visible.") }
    $decision = if ($failures.Count) { "FAIL" } elseif ($manual.Count) { "INCONCLUSIVE" } else { "PASS" }
    $report = [ordered]@{
        decision = $decision; generated_at = (Get-Date).ToString("o")
        git = [ordered]@{ branch = $branch; head = $head; tags_at_head = $tags; dirty = $dirty }
        app = [ordered]@{ base_url = $script:Base; backend = $config.resmed_import_backend; parser_ready = $config.resmed_import_ready }
        card_handling = $copyMode
        fresh_import = Get-SafeRunSummary $fresh.Run
        reimport = [ordered]@{ trigger_status = $second.TriggerStatus; summary = Get-SafeRunSummary $second.Run }
        counts = [ordered]@{ before = $before; after_fresh = $afterFresh; after_reimport = $afterSecond }
        checks = [ordered]@{
            sessions_visible = [bool]$sessions.Count; import_history = $historyPassed
            nightly_chunk_coverage = $coveragePassed; event_inspector = $eventPassed; full_night_waveform = $fullNightPassed
            reimport_reported_no_op = $reimportNoOp
            sessions_stable = [long]$afterSecond.sessions -eq [long]$afterFresh.sessions
            events_stable = [long]$afterSecond.events -eq [long]$afterFresh.events
            waveform_chunks_stable = [long]$afterSecond.waveform_chunks -eq [long]$afterFresh.waveform_chunks
            session_waveform_bloat = [long]$afterSecond.session_waveform -ne [long]$afterFresh.session_waveform
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
