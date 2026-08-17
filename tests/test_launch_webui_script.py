from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_SCRIPT = PROJECT_ROOT / "scripts" / "launch_webui.ps1"


def test_start_webui_launcher_rebuilds_frontend_before_reusing_a_running_service() -> None:
    source = LAUNCH_SCRIPT.read_text(encoding="utf-8")

    build = "Invoke-Checked $npm --prefix frontend run build"
    state_check = "$state = Get-WebUIState"

    assert ". (Join-Path $PSScriptRoot \"common.ps1\")" in source
    assert '$npm = Join-Path $paths.Node "npm.cmd"' in source
    assert build in source
    assert state_check in source
    assert source.index(build) < source.index(state_check)
