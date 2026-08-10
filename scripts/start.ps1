param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 7865
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "common.ps1")
$paths = Initialize-ProjectEnvironment
$python = Join-Path $paths.Venv "Scripts\python.exe"
$url = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project environment is missing. Run .\scripts\setup.ps1 first."
}

Push-Location $paths.ProjectRoot
try {
    Invoke-Checked $python -c "from dataset_audit_studio.runtime import assert_runtime_isolated; assert_runtime_isolated()"
    try {
        $Host.UI.RawUI.WindowTitle = "Dataset Audit Studio WebUI - $url - Closing this window stops the WebUI"
    }
    catch {
        # Some non-console PowerShell hosts cannot set a window title.
    }
    Write-Host ""
    Write-Host "Dataset Audit Studio WebUI"
    Write-Host "Address: $url"
    Write-Host "Keep this PowerShell window open while using the WebUI."
    Write-Host "Closing the browser does not stop the service."
    Write-Host "Closing this window stops the server and Worker, and releases port $Port."
    Write-Host ""
    Invoke-Checked $python -m uvicorn dataset_audit_studio.main:app --app-dir backend --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
