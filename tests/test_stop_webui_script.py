from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop_webui.ps1"


def run_stop_script_harness(
    tmp_path: Path, *, stop_process_fails: bool
) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / "stop-webui-harness.ps1"
    project_root = str(PROJECT_ROOT).replace("'", "''")
    stop_script = str(STOP_SCRIPT).replace("'", "''")
    stop_process_fails_literal = "$true" if stop_process_fails else "$false"
    harness.write_text(
        f"""
$script:stopped = $false
$script:stopProcessFails = {stop_process_fails_literal}
$script:root = [pscustomobject]@{{
    ProcessId = 4242
    ParentProcessId = 0
    Name = 'python.exe'
    CommandLine = 'python -m uvicorn dataset_audit_studio.main:app --host 127.0.0.1'
    CreationDate = [datetime]'2026-08-08T00:00:00Z'
}}
$script:child = [pscustomobject]@{{
    ProcessId = 4243
    ParentProcessId = 4242
    Name = 'python.exe'
    CommandLine = 'worker'
    CreationDate = [datetime]'2026-08-08T00:00:01Z'
}}

function Get-NetTCPConnection {{
    [CmdletBinding()]
    param([int]$LocalPort, [string]$State)
    if ($script:stopped) {{ return @() }}
    return @([pscustomobject]@{{ OwningProcess = 4242 }})
}}

function Get-CimInstance {{
    [CmdletBinding()]
    param([string]$ClassName, [string]$Filter)
    if ($Filter) {{ return $script:root }}
    return @($script:root, $script:child)
}}

function Invoke-RestMethod {{
    [CmdletBinding()]
    param([string]$Uri, [int]$TimeoutSec)
    return [pscustomobject]@{{ runtime = [pscustomobject]@{{ project_root = '{project_root}' }} }}
}}

function Read-Host {{ return 'STOP' }}

function Stop-Process {{
    [CmdletBinding()]
    param([object]$InputObject, [switch]$Force)
    if ($null -eq $InputObject -or $InputObject.Id -ne 4242) {{
        throw 'The listener must be stopped through its resolved process object.'
    }}
    if ($script:stopProcessFails) {{
        throw 'Stop-Process failed in the desktop host.'
    }}
    $script:stopped = $true
}}

Set-Item -Path Function:taskkill.exe -Value {{
    param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments)
    $script:stopped = $true
    $global:LASTEXITCODE = 0
    Write-Output 'taskkill fallback used'
}}

function Start-Sleep {{ param([int]$Seconds, [int]$Milliseconds) }}

function Get-Process {{
    [CmdletBinding()]
    param([int]$Id)
    if ($Id -eq 4242 -and -not $script:stopped) {{
        return [pscustomobject]@{{ Id = 4242 }}
    }}
    return $null
}}

. '{stop_script}'
""",
        encoding="utf-8",
    )

    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )



def test_stop_script_handles_a_worker_descendant(tmp_path: Path) -> None:
    result = run_stop_script_harness(tmp_path, stop_process_fails=False)

    assert result.returncode == 0, result.stderr
    expected_message = (
        "WebUI stopped, port 7865 released, "
        "and 1 worker child process(es) confirmed gone."
    )
    assert expected_message in result.stdout


def test_stop_script_falls_back_to_taskkill_when_stop_process_fails(tmp_path: Path) -> None:
    result = run_stop_script_harness(tmp_path, stop_process_fails=True)

    assert result.returncode == 0, result.stderr
    assert "taskkill fallback used" in result.stdout
