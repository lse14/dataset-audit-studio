from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_application_module_cold_imports_without_circular_dependency() -> None:
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    backend_path = str(project_root / "backend")
    environment["PYTHONPATH"] = backend_path + os.pathsep + environment.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", "import dataset_audit_studio.main"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
