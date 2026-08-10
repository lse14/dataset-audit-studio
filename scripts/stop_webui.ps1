param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 7865
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$url = "http://127.0.0.1:$Port"

function Get-WebUIListenerProcess {
    $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)

    if ($processIds.Count -eq 0) {
        return $null
    }
    if ($processIds.Count -ne 1) {
        throw "Port $Port has multiple listening processes. Refusing to stop an ambiguous listener."
    }

    $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($processIds[0])"
    if ($null -eq $process) {
        throw "Could not identify the listener on port $Port."
    }
    return $process
}

function Get-DescendantProcess {
    <#
        Walks the process tree below $Root breadth-first.

        A child is only accepted when it was created at or after its parent, because
        Windows recycles process IDs: a long-lived unrelated process can otherwise
        claim a dead PID that some current process still lists as its parent.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [object]$Root
    )

    $all = @(Get-CimInstance -ClassName Win32_Process)
    $byParent = @{}
    foreach ($process in $all) {
        $parentId = [int]$process.ParentProcessId
        if (-not $byParent.ContainsKey($parentId)) {
            $byParent[$parentId] = [System.Collections.ArrayList]::new()
        }
        [void]$byParent[$parentId].Add($process)
    }

    $descendants = [System.Collections.ArrayList]::new()
    $seen = @{ [int]$Root.ProcessId = $true }
    $pending = [System.Collections.Queue]::new()
    $pending.Enqueue($Root)

    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        $parentId = [int]$parent.ProcessId
        if (-not $byParent.ContainsKey($parentId)) {
            continue
        }
        foreach ($child in $byParent[$parentId]) {
            $childId = [int]$child.ProcessId
            if ($seen.ContainsKey($childId)) {
                continue
            }
            if ($null -ne $child.CreationDate -and $null -ne $parent.CreationDate -and
                $child.CreationDate -lt $parent.CreationDate) {
                # Recycled parent PID, not a real child.
                continue
            }
            $seen[$childId] = $true
            [void]$descendants.Add($child)
            $pending.Enqueue($child)
        }
    }
    # The leading comma stops PowerShell from unrolling the result: an unwrapped empty
    # array would come back as $null, and $null.Count throws under Set-StrictMode.
    return , $descendants.ToArray()
}

function Get-LiveProcessIds {
    param([Parameter(Mandatory = $true)][int[]]$ProcessIds)

    $live = @()
    foreach ($processId in $ProcessIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            $live += $processId
        }
    }
    return , [int[]]$live
}

function Get-ReportedProjectRoot {
    <#
        Reads runtime.project_root defensively. Under Set-StrictMode -Version Latest a
        plain $health.runtime.project_root throws when either level is absent, so the
        "no project root reported" refusal below could never actually be reached.
    #>
    param([Parameter(Mandatory = $true)][AllowNull()][object]$Health)

    if ($null -eq $Health) { return "" }
    $runtime = $Health.PSObject.Properties['runtime']
    if ($null -eq $runtime -or $null -eq $runtime.Value) { return "" }
    $root = $runtime.Value.PSObject.Properties['project_root']
    if ($null -eq $root) { return "" }
    return [string]$root.Value
}

function Assert-ProjectWebUI {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Process
    )

    $commandLine = [string]$Process.CommandLine
    if ($commandLine -notmatch '(?i)(?:^|\s)dataset_audit_studio\.main:app(?:\s|$)') {
        throw "Port $Port is owned by PID $($Process.ProcessId), but it is not this project's WebUI Uvicorn process. Refusing to stop it."
    }

    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 4
    }
    catch {
        throw "The listener on port $Port did not pass this project's health check. Refusing to stop it. $($_.Exception.Message)"
    }

    $reportedRoot = Get-ReportedProjectRoot -Health $health
    if (-not $reportedRoot) {
        throw "The health response did not report a project root. Refusing to stop it."
    }
    $normalizedReportedRoot = [System.IO.Path]::GetFullPath($reportedRoot).TrimEnd('\')
    $normalizedProjectRoot = $projectRoot.TrimEnd('\')
    if (-not [string]::Equals(
            $normalizedReportedRoot,
            $normalizedProjectRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "The listener on port $Port belongs to '$normalizedReportedRoot', not '$normalizedProjectRoot'. Refusing to stop it."
    }
}

function Stop-VerifiedProcess {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        Stop-Process -InputObject $process -ErrorAction Stop
        return
    }
    catch {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return
        }
        Write-Warning "Stop-Process failed for verified WebUI PID $ProcessId. Falling back to taskkill. $($_.Exception.Message)"
        & taskkill.exe /PID $ProcessId /T /F
        if ($LASTEXITCODE -ne 0 -and $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "taskkill could not stop verified WebUI PID $ProcessId (exit code $LASTEXITCODE)."
        }
    }
}

$listener = Get-WebUIListenerProcess
if ($null -eq $listener) {
    Write-Host "No listener found on port $Port. Nothing was stopped."
    exit 0
}

Assert-ProjectWebUI -Process $listener

# Phase and scoring work runs in spawned multiprocessing children. They are not daemons:
# only the parent's supervision loop terminates them. Snapshot the tree before the parent
# dies so the orphans can be reaped afterwards instead of being left holding VRAM and the
# SQLite database.
$descendants = Get-DescendantProcess -Root $listener
$descendantIds = [int[]]@($descendants | ForEach-Object { [int]$_.ProcessId })

Write-Host "Verified Dataset Audit Studio WebUI on $url (PID $($listener.ProcessId))."
if ($descendantIds.Count -gt 0) {
    Write-Host "Worker child processes that will be stopped with it:"
    foreach ($child in $descendants) {
        Write-Host "  PID $($child.ProcessId)  $($child.Name)"
    }
}
Write-Warning "If a task is running, stop it from the WebUI first when possible. Stopping the server can interrupt in-flight work; committed checkpoints remain available for recovery."
$confirmation = Read-Host "Type STOP to stop this WebUI"
if ($confirmation -cne "STOP") {
    Write-Host "Cancelled. The WebUI is still running."
    exit 0
}

Stop-VerifiedProcess -ProcessId $listener.ProcessId

$portReleased = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 250
    if ($null -eq (Get-WebUIListenerProcess)) {
        $portReleased = $true
        break
    }
}
if (-not $portReleased) {
    throw "The listener on port $Port did not exit within 10 seconds."
}

# The parent is gone, so its supervision loop can no longer terminate the children.
# Give them a short grace period to notice the broken queue, then terminate the survivors.
$orphans = @()
if ($descendantIds.Count -gt 0) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $orphans = Get-LiveProcessIds -ProcessIds $descendantIds
        if ($orphans.Count -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    foreach ($orphanId in $orphans) {
        Write-Host "Terminating orphaned worker child PID $orphanId."
        Stop-Process -Id $orphanId -Force -ErrorAction SilentlyContinue
    }
}

$remaining = @()
if ($descendantIds.Count -gt 0) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remaining = Get-LiveProcessIds -ProcessIds $descendantIds
        if ($remaining.Count -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
}
if ($remaining.Count -gt 0) {
    throw "The WebUI stopped and port $Port was released, but these worker child processes are still running and may still hold GPU memory or the database: $($remaining -join ', '). Stop them manually before starting the WebUI again."
}

Write-Host "WebUI stopped, port $Port released, and $($descendantIds.Count) worker child process(es) confirmed gone."
exit 0
