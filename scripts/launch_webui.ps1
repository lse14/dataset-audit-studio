param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 7865
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$startScript = Join-Path $PSScriptRoot "start.ps1"
$url = "http://127.0.0.1:$Port"

function Get-ReportedProjectRoot {
    <#
        Reads runtime.project_root defensively. Under Set-StrictMode -Version Latest a
        plain $health.runtime.project_root throws when either level is absent, so the
        "no project root reported" case could never be handled as a value.
    #>
    param([Parameter(Mandatory = $true)][AllowNull()][object]$Health)

    if ($null -eq $Health) { return "" }
    $runtime = $Health.PSObject.Properties['runtime']
    if ($null -eq $runtime -or $null -eq $runtime.Value) { return "" }
    $root = $runtime.Value.PSObject.Properties['project_root']
    if ($null -eq $root) { return "" }
    return [string]$root.Value
}

function Get-WebUIState {
    <#
        Returns "ready" only for this project's WebUI. A different application that
        happens to answer /api/health on the same port must not be mistaken for ours,
        which is the same identity rule stop_webui.ps1 applies before it stops anything.
    #>
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 4
    }
    catch {
        return "absent"
    }

    $reportedRoot = Get-ReportedProjectRoot -Health $health
    if (-not $reportedRoot) {
        return "foreign"
    }
    $normalizedReportedRoot = [System.IO.Path]::GetFullPath($reportedRoot).TrimEnd('\')
    $normalizedProjectRoot = $projectRoot.TrimEnd('\')
    if ([string]::Equals(
            $normalizedReportedRoot,
            $normalizedProjectRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        return "ready"
    }
    return "foreign"
}

$state = Get-WebUIState
if ($state -eq "ready") {
    Start-Process $url
    exit 0
}
if ($state -eq "foreign") {
    throw "Port $Port already serves a different application or another checkout of this project. Start this WebUI on a free port, for example: .\start_webui.bat 7866"
}

$process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript, "-Port", $Port) `
    -WorkingDirectory $projectRoot `
    -PassThru

for ($attempt = 0; $attempt -lt 90; $attempt++) {
    Start-Sleep -Seconds 1
    $state = Get-WebUIState
    if ($state -eq "ready") {
        Start-Process $url
        exit 0
    }
    if ($state -eq "foreign") {
        throw "Port $Port was taken by a different application while this WebUI was starting."
    }
    # Report a failed start immediately instead of waiting out the full timeout on a
    # server window that has already exited with its error message.
    if ($process.HasExited) {
        throw "The WebUI window exited with code $($process.ExitCode) before the service became ready. Run .\scripts\start.ps1 -Port $Port directly to see the error, or run .\setup.bat if the environment is not installed yet."
    }
}

throw "WebUI did not become ready within 90 seconds. Check the Dataset Audit Studio WebUI window."
