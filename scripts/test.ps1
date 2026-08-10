param(
    # Supply-chain checks reach out to registry.npmjs.org and huggingface.co, so they are
    # opt-in rather than part of the default offline run. Run them whenever uv.lock,
    # frontend/package-lock.json or the model registry changes.
    [switch]$Online
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "common.ps1")
$paths = Initialize-ProjectEnvironment

# verify-isolation.ps1 reports failure by throwing, which propagates out of this call and
# aborts the run, so there is no exit code left to inspect here.
& (Join-Path $PSScriptRoot "verify-isolation.ps1")

$python = Join-Path $paths.Venv "Scripts\python.exe"
$npm = Join-Path $paths.Node "npm.cmd"
$testTempRoot = Assert-ProjectPath -Path (Join-Path $paths.ProjectRoot ".test-tmp") -Label "Test temporary root"
$pytestBaseTemp = Assert-ProjectPath -Path (
    Join-Path $testTempRoot ([guid]::NewGuid().ToString("N"))
) -Label "Pytest temporary directory"

New-Item -ItemType Directory -Path $testTempRoot -Force | Out-Null
New-Item -ItemType Directory -Path $pytestBaseTemp -Force | Out-Null

Push-Location $paths.ProjectRoot
try {
    Invoke-Checked $python -m ruff check backend tests scripts
    Invoke-Checked $python scripts\verify_component_boundaries.py
    Invoke-Checked $python scripts\generate_third_party_report.py --check
    Invoke-Checked -FilePath $python -Arguments @(
        "-m",
        "pytest",
        "--basetemp",
        $pytestBaseTemp,
        "-p",
        "no:cacheprovider"
    )
    Invoke-Checked $npm --prefix frontend run test
    Invoke-Checked $npm --prefix frontend run build
    Invoke-Checked $npm --prefix frontend run test:e2e

    if ($Online) {
        Write-Host "Running supply-chain verification against upstream registries."
        Invoke-Checked $python scripts\verify_npm_lock.py
        Invoke-Checked $python scripts\verify_model_registry.py
    }
    else {
        Write-Host "Skipped supply-chain verification (offline run). Use .\scripts\test.ps1 -Online after changing uv.lock, frontend/package-lock.json or the model registry."
    }
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $pytestBaseTemp) {
        Remove-Item -LiteralPath $pytestBaseTemp -Recurse -Force -ErrorAction Stop
    }
}
