Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "common.ps1")
$paths = Initialize-ProjectEnvironment

$python = Join-Path $paths.Venv "Scripts\python.exe"
$node = Join-Path $paths.Node "node.exe"
$npm = Join-Path $paths.Node "npm.cmd"
$uv = Join-Path $paths.Uv "uv.exe"

foreach ($binary in @($python, $node, $npm, $uv)) {
    if (-not (Test-Path -LiteralPath $binary)) {
        throw "Missing project-local runtime: $binary"
    }
    Assert-ProjectPath -Path $binary -Label "Runtime binary" | Out-Null
}

Invoke-Checked $python -c "from dataset_audit_studio.runtime import assert_runtime_isolated; assert_runtime_isolated()"

$nodePath = (& $node -p "process.execPath").Trim()
Assert-ProjectPath -Path $nodePath -Label "Node executable" | Out-Null

$npmCache = (& $npm config get cache).Trim()
if ([System.IO.Path]::GetFullPath($npmCache) -ne [System.IO.Path]::GetFullPath($paths.NpmCache)) {
    throw "npm cache is outside the project: $npmCache"
}

$npmUserConfig = (& $npm config get userconfig).Trim()
if ([System.IO.Path]::GetFullPath($npmUserConfig) -ne [System.IO.Path]::GetFullPath($paths.NpmUserConfig)) {
    throw "npm user config is outside the project: $npmUserConfig"
}

$uvCache = (& $uv cache dir).Trim()
if ([System.IO.Path]::GetFullPath($uvCache) -ne [System.IO.Path]::GetFullPath($paths.UvCache)) {
    throw "uv cache is outside the project: $uvCache"
}

[pscustomobject]@{
    Python = (& $python --version)
    PythonPath = $python
    Node = (& $node --version)
    NodePath = $nodePath
    Uv = (& $uv --version)
    UvCache = $uvCache
    NpmCache = $npmCache
    NpmUserConfig = $npmUserConfig
    PlaywrightBrowsers = $paths.PlaywrightBrowsers
    Models = $paths.Models
} | Format-List

Write-Host "Isolation verification: OK"
