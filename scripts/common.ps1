Set-StrictMode -Version Latest

$script:ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Assert-ProjectPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$Label = "Path"
    )

    $rootPrefix = $script:ProjectRoot.TrimEnd('\') + '\'
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes project root: $fullPath"
    }
    return $fullPath
}

function Initialize-ProjectEnvironment {
    $paths = [ordered]@{
        ProjectRoot = $script:ProjectRoot
        Runtime = Join-Path $script:ProjectRoot ".runtime"
        Venv = Join-Path $script:ProjectRoot ".venv"
        Models = Join-Path $script:ProjectRoot "models"
        Data = Join-Path $script:ProjectRoot "data"
        HfHome = Join-Path $script:ProjectRoot "models\.cache\huggingface"
        TorchHome = Join-Path $script:ProjectRoot "models\.cache\torch"
        UvCache = Join-Path $script:ProjectRoot ".setup\uv-cache"
        UvPython = Join-Path $script:ProjectRoot ".runtime\python"
        PipCache = Join-Path $script:ProjectRoot ".setup\pip-cache"
        PipConfig = Join-Path $script:ProjectRoot "scripts\pip.ini"
        NpmCache = Join-Path $script:ProjectRoot ".setup\npm-cache"
        NpmUserConfig = Join-Path $script:ProjectRoot "frontend\.npmrc"
        PlaywrightBrowsers = Join-Path $script:ProjectRoot ".setup\playwright-browsers"
        Node = Join-Path $script:ProjectRoot ".runtime\node"
        Uv = Join-Path $script:ProjectRoot ".runtime\uv"
    }

    foreach ($entry in $paths.GetEnumerator()) {
        if ($entry.Key -ne "ProjectRoot") {
            Assert-ProjectPath -Path $entry.Value -Label $entry.Key | Out-Null
        }
    }

    $env:PYTHONNOUSERSITE = "1"
    $env:PIP_REQUIRE_VIRTUALENV = "1"
    $env:HF_HOME = $paths.HfHome
    $env:TORCH_HOME = $paths.TorchHome
    $env:UV_CACHE_DIR = $paths.UvCache
    $env:UV_PYTHON_INSTALL_DIR = $paths.UvPython
    $env:UV_PROJECT_ENVIRONMENT = $paths.Venv
    $env:UV_MANAGED_PYTHON = "1"
    $env:UV_NO_CONFIG = "1"
    $env:PIP_CACHE_DIR = $paths.PipCache
    $env:PIP_CONFIG_FILE = $paths.PipConfig
    $env:npm_config_cache = $paths.NpmCache
    $env:npm_config_userconfig = $paths.NpmUserConfig
    $env:PLAYWRIGHT_BROWSERS_PATH = $paths.PlaywrightBrowsers
    $env:PYTHONPATH = ""

    $localBins = @(
        (Join-Path $paths.Venv "Scripts"),
        $paths.Node,
        $paths.Uv
    )
    $env:PATH = ($localBins + @($env:PATH)) -join [System.IO.Path]::PathSeparator

    return [pscustomobject]$paths
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}
