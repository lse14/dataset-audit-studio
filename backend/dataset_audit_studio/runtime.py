from __future__ import annotations

import os
import site
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RuntimeIsolationError(RuntimeError):
    """Raised when the process can escape the project-local runtime boundary."""


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    venv: Path
    models: Path
    data: Path
    hf_home: Path
    torch_home: Path
    uv_cache: Path
    pip_cache: Path
    pip_config: Path
    npm_cache: Path
    npm_user_config: Path
    python_install: Path


def runtime_paths() -> RuntimePaths:
    cache_root = PROJECT_ROOT / "models" / ".cache"
    setup_root = PROJECT_ROOT / ".setup"
    return RuntimePaths(
        project_root=PROJECT_ROOT,
        venv=PROJECT_ROOT / ".venv",
        models=PROJECT_ROOT / "models",
        data=PROJECT_ROOT / "data",
        hf_home=cache_root / "huggingface",
        torch_home=cache_root / "torch",
        uv_cache=setup_root / "uv-cache",
        pip_cache=setup_root / "pip-cache",
        pip_config=PROJECT_ROOT / "scripts" / "pip.ini",
        npm_cache=setup_root / "npm-cache",
        npm_user_config=PROJECT_ROOT / "frontend" / ".npmrc",
        python_install=PROJECT_ROOT / ".runtime" / "python",
    )


def _normal(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_within_project(path: Path) -> bool:
    try:
        _normal(path).relative_to(_normal(PROJECT_ROOT))
    except ValueError:
        return False
    return True


def require_project_path(path: Path, label: str) -> None:
    if not is_within_project(path):
        raise RuntimeIsolationError(f"{label} escapes project root: {path}")


def expected_environment() -> dict[str, str]:
    paths = runtime_paths()
    return {
        "PYTHONNOUSERSITE": "1",
        "PIP_REQUIRE_VIRTUALENV": "1",
        "HF_HOME": str(paths.hf_home),
        "TORCH_HOME": str(paths.torch_home),
        "UV_CACHE_DIR": str(paths.uv_cache),
        "UV_PYTHON_INSTALL_DIR": str(paths.python_install),
        "UV_MANAGED_PYTHON": "1",
        "UV_NO_CONFIG": "1",
        "PIP_CACHE_DIR": str(paths.pip_cache),
        "PIP_CONFIG_FILE": str(paths.pip_config),
        "npm_config_cache": str(paths.npm_cache),
        "npm_config_userconfig": str(paths.npm_user_config),
    }


def assert_runtime_isolated() -> None:
    paths = runtime_paths()
    problems: list[str] = []

    if _normal(Path(sys.prefix)) != _normal(paths.venv):
        problems.append(f"sys.prefix is not project .venv: {sys.prefix}")
    if _normal(Path(sys.executable)).parent != _normal(paths.venv / "Scripts"):
        problems.append(f"Python executable is not project-local: {sys.executable}")
    if sys.prefix == sys.base_prefix:
        problems.append("Python is not running inside a virtual environment")
    if site.ENABLE_USER_SITE is not False:
        problems.append("Python user site-packages are enabled")
    if not is_within_project(Path(sys.base_prefix)):
        problems.append(f"base Python is outside the project: {sys.base_prefix}")

    for name, expected in expected_environment().items():
        actual = os.environ.get(name)
        if name in {
            "PYTHONNOUSERSITE",
            "PIP_REQUIRE_VIRTUALENV",
            "UV_MANAGED_PYTHON",
            "UV_NO_CONFIG",
        }:
            if actual != expected:
                problems.append(f"{name} must be {expected!r}, got {actual!r}")
            continue
        if actual is None or _normal(Path(actual)) != _normal(Path(expected)):
            problems.append(f"{name} must point inside the project: {expected}")

    if problems:
        detail = "\n- ".join(problems)
        raise RuntimeIsolationError(f"Runtime isolation check failed:\n- {detail}")


def ensure_runtime_directories() -> None:
    paths = runtime_paths()
    directories = (
        paths.models,
        paths.data,
        paths.hf_home,
        paths.torch_home,
        paths.uv_cache,
        paths.pip_cache,
        paths.npm_cache,
        paths.data / "tasks",
    )
    for directory in directories:
        require_project_path(directory, "runtime directory")
        directory.mkdir(parents=True, exist_ok=True)


def runtime_report() -> dict[str, object]:
    paths = runtime_paths()
    return {
        "isolated": True,
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "python_executable": str(Path(sys.executable).resolve()),
        "project_root": str(paths.project_root),
        "models_root": str(paths.models),
        "data_root": str(paths.data),
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
    }
