from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "dataset_audit_studio"
COMPONENT_PREFIX = f"{PACKAGE}.components"
CORE_FORBIDDEN_PREFIXES = (
    COMPONENT_PREFIX,
    f"{PACKAGE}.app",
    f"{PACKAGE}.clustering",
    f"{PACKAGE}.export",
    f"{PACKAGE}.latent",
    f"{PACKAGE}.metrics",
    f"{PACKAGE}.reviews",
    f"{PACKAGE}.scanner",
    f"{PACKAGE}.scoring",
    f"{PACKAGE}.style",
    f"{PACKAGE}.workspace",
)
OWNED_SERVICE_COMPONENTS = {
    Path("export/service.py"): "dataset_export",
}


@dataclass(frozen=True)
class ImportBoundaryViolation:
    path: Path
    line: int
    imported: str
    reason: str


def _module_package(relative_path: Path, prefix: str) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()
    return ".".join((prefix, *parts))


def _imports(path: Path, relative_path: Path, prefix: str) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_package(relative_path, prefix)
    values: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                name = "." * node.level + module
                try:
                    module = importlib.util.resolve_name(name, package)
                except (ImportError, ValueError):
                    module = name
            values.append((node.lineno, module))
    return tuple(values)


def find_component_import_violations(package_root: Path) -> tuple[ImportBoundaryViolation, ...]:
    package_root = package_root.resolve(strict=True)
    components_root = package_root / "components"
    core_root = package_root / "core"
    violations: list[ImportBoundaryViolation] = []

    for path in sorted(components_root.rglob("*.py")):
        relative = path.relative_to(components_root)
        if len(relative.parts) < 2 or "__pycache__" in relative.parts:
            continue
        current_component = relative.parts[0]
        for line, imported in _imports(path, relative, COMPONENT_PREFIX):
            if imported == COMPONENT_PREFIX:
                violations.append(
                    ImportBoundaryViolation(
                        path,
                        line,
                        imported,
                        "component imports the components package root",
                    )
                )
                continue
            prefix = f"{COMPONENT_PREFIX}."
            if imported.startswith(prefix):
                target = imported[len(prefix) :].split(".", 1)[0]
                if target != current_component:
                    violations.append(
                        ImportBoundaryViolation(
                            path,
                            line,
                            imported,
                            f"component {current_component} imports component {target}",
                        )
                    )
                continue
            core_prefix = f"{PACKAGE}.core"
            if imported == core_prefix or imported.startswith(f"{core_prefix}."):
                continue
            if imported == PACKAGE or imported.startswith(f"{PACKAGE}."):
                violations.append(
                    ImportBoundaryViolation(
                        path,
                        line,
                        imported,
                        f"component {current_component} imports a non-core project package",
                    )
                )

    for path in sorted(core_root.rglob("*.py")):
        relative = path.relative_to(core_root)
        if "__pycache__" in relative.parts:
            continue
        for line, imported in _imports(path, relative, f"{PACKAGE}.core"):
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in CORE_FORBIDDEN_PREFIXES
            ):
                violations.append(
                    ImportBoundaryViolation(
                        path,
                        line,
                        imported,
                        "core imports a business or composition package",
                    )
                )

    for relative, owner in OWNED_SERVICE_COMPONENTS.items():
        path = package_root / relative
        if not path.is_file():
            continue
        for line, imported in _imports(path, relative, PACKAGE):
            prefix = f"{COMPONENT_PREFIX}."
            if not imported.startswith(prefix):
                continue
            target = imported[len(prefix) :].split(".", 1)[0]
            if target != owner:
                violations.append(
                    ImportBoundaryViolation(
                        path,
                        line,
                        imported,
                        f"service owned by {owner} imports component {target}",
                    )
                )
    return tuple(violations)
