param(
    [switch]$UpdateLocks,
    [switch]$SkipFrontendBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Windows PowerShell renders a progress bar for every Invoke-WebRequest write, which
# costs far more than the transfer itself on the multi-megabyte runtime archives.
$ProgressPreference = "SilentlyContinue"

. (Join-Path $PSScriptRoot "common.ps1")
$paths = Initialize-ProjectEnvironment
$runtimeLock = Get-Content -LiteralPath (Join-Path $paths.ProjectRoot "runtime-lock.json") -Raw | ConvertFrom-Json

function Remove-LocalDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $safePath = Assert-ProjectPath -Path $Path -Label "Removal target"
    if (Test-Path -LiteralPath $safePath) {
        Remove-Item -LiteralPath $safePath -Recurse -Force
    }
}

function Get-VerifiedArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    Assert-ProjectPath -Path $Destination -Label "Download destination" | Out-Null
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null

    if (Test-Path -LiteralPath $Destination) {
        $existingHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $Sha256.ToLowerInvariant()) {
            return
        }
        Remove-Item -LiteralPath $Destination -Force
    }

    $part = "$Destination.part"
    if (Test-Path -LiteralPath $part) {
        Remove-Item -LiteralPath $part -Force
    }

    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $part -UseBasicParsing
    $actualHash = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $part -Force
        throw "SHA-256 mismatch for $Url. Expected $Sha256, got $actualHash"
    }
    Move-Item -LiteralPath $part -Destination $Destination
}

function Install-Uv {
    $uvExe = Join-Path $paths.Uv "uv.exe"
    if (Test-Path -LiteralPath $uvExe) {
        $versionOutput = (& $uvExe --version).Trim()
        if ($versionOutput -notmatch '^uv\s+([0-9]+\.[0-9]+\.[0-9]+)') {
            throw "Unable to parse installed uv version: $versionOutput"
        }
        $actualVersion = $Matches[1]
        if ($actualVersion -eq $runtimeLock.uv.version) {
            return $uvExe
        }
        throw @"
Installed uv version $actualVersion does not match runtime lock $($runtimeLock.uv.version).
This is expected after runtime-lock.json was updated. Remove the pinned runtime and re-run setup:
    Remove-Item -LiteralPath "$($paths.Uv)" -Recurse -Force
    .\setup.bat
"@
    }

    $archive = Join-Path $paths.ProjectRoot ".setup\downloads\$($runtimeLock.uv.asset)"
    Get-VerifiedArchive -Url $runtimeLock.uv.url -Sha256 $runtimeLock.uv.sha256 -Destination $archive

    $extract = Join-Path $paths.ProjectRoot ".setup\extract\uv"
    Remove-LocalDirectory -Path $extract
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
    $sourceExe = Get-ChildItem -LiteralPath $extract -Recurse -Filter "uv.exe" | Select-Object -First 1
    if (-not $sourceExe) {
        throw "uv.exe was not found in the verified archive"
    }
    New-Item -ItemType Directory -Path $paths.Uv -Force | Out-Null
    Copy-Item -LiteralPath $sourceExe.FullName -Destination $uvExe
    $uvx = Get-ChildItem -LiteralPath $extract -Recurse -Filter "uvx.exe" | Select-Object -First 1
    if ($uvx) {
        Copy-Item -LiteralPath $uvx.FullName -Destination (Join-Path $paths.Uv "uvx.exe")
    }
    Remove-LocalDirectory -Path $extract
    return $uvExe
}

function Install-Node {
    $nodeExe = Join-Path $paths.Node "node.exe"
    if (Test-Path -LiteralPath $nodeExe) {
        $actualVersion = (& $nodeExe --version) -replace '^v', ''
        if ($actualVersion -eq $runtimeLock.node.version) {
            return $nodeExe
        }
        throw @"
Installed Node version $actualVersion does not match runtime lock $($runtimeLock.node.version).
This is expected after runtime-lock.json was updated. Remove the pinned runtime and re-run setup:
    Remove-Item -LiteralPath "$($paths.Node)" -Recurse -Force
    .\setup.bat
"@
    }

    $archive = Join-Path $paths.ProjectRoot ".setup\downloads\$($runtimeLock.node.asset)"
    Get-VerifiedArchive -Url $runtimeLock.node.url -Sha256 $runtimeLock.node.sha256 -Destination $archive

    $extract = Join-Path $paths.ProjectRoot ".setup\extract\node"
    Remove-LocalDirectory -Path $extract
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
    $sourceExe = Get-ChildItem -LiteralPath $extract -Recurse -Filter "node.exe" | Select-Object -First 1
    if (-not $sourceExe) {
        throw "node.exe was not found in the verified archive"
    }
    $sourceRoot = $sourceExe.Directory.FullName
    New-Item -ItemType Directory -Path $paths.Node -Force | Out-Null
    Copy-Item -Path (Join-Path $sourceRoot "*") -Destination $paths.Node -Recurse -Force
    Remove-LocalDirectory -Path $extract
    return $nodeExe
}

foreach ($directory in @(
    $paths.Runtime,
    $paths.Models,
    $paths.Data,
    $paths.HfHome,
    $paths.TorchHome,
    $paths.UvCache,
    $paths.UvPython,
    $paths.PipCache,
    $paths.NpmCache
)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$uvExe = Install-Uv
$nodeExe = Install-Node

Write-Host "Installing pinned Python $($runtimeLock.python.version) into $($paths.UvPython)"
Invoke-Checked $uvExe python install $runtimeLock.python.version --no-bin --no-registry

$managedPython = Get-ChildItem -LiteralPath $paths.UvPython -Recurse -Filter "python.exe" |
    Where-Object { $_.Directory.Name -ne "Scripts" } |
    Select-Object -First 1
if (-not $managedPython) {
    throw "Managed Python was not found under $($paths.UvPython)"
}

$venvPython = Join-Path $paths.Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-Checked $uvExe venv --python $managedPython.FullName $paths.Venv
}

Push-Location $paths.ProjectRoot
try {
    $uvLock = Join-Path $paths.ProjectRoot "uv.lock"
    if ($UpdateLocks) {
        Invoke-Checked $uvExe lock
    }
    elseif (-not (Test-Path -LiteralPath $uvLock)) {
        throw "uv.lock is missing. Run setup.ps1 -UpdateLocks only when intentionally creating a new lock."
    }
    Invoke-Checked $uvExe sync --locked --group dev

    $frontend = Join-Path $paths.ProjectRoot "frontend"
    $npmCmd = Join-Path $paths.Node "npm.cmd"
    Push-Location $frontend
    try {
        $packageLock = Join-Path $frontend "package-lock.json"
        if ($UpdateLocks) {
            Invoke-Checked $npmCmd install --package-lock-only --no-audit --no-fund
        }
        elseif (-not (Test-Path -LiteralPath $packageLock)) {
            throw "frontend/package-lock.json is missing. Use -UpdateLocks only for an intentional lock update."
        }
        Invoke-Checked $npmCmd ci --no-audit --no-fund
        if (-not $SkipFrontendBuild) {
            Invoke-Checked $npmCmd run build
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

Invoke-Checked (Join-Path $paths.Venv "Scripts\python.exe") -c "from dataset_audit_studio.runtime import assert_runtime_isolated; assert_runtime_isolated(); print('Python isolation: OK')"
Write-Host "Setup completed with project-local runtimes only."
