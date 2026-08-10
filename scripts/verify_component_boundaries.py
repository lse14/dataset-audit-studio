from __future__ import annotations

from pathlib import Path

from dataset_audit_studio.core.import_boundaries import find_component_import_violations

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "backend" / "dataset_audit_studio"


def main() -> int:
    violations = find_component_import_violations(PACKAGE_ROOT)
    if violations:
        for item in violations:
            relative = item.path.relative_to(PROJECT_ROOT)
            print(f"{relative}:{item.line}: {item.reason}: {item.imported}")
        return 1
    print("Component import boundaries: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

