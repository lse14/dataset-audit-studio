from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = PROJECT_ROOT / "frontend" / "package-lock.json"
DEFAULT_REGISTRY = "https://registry.npmjs.org"


def load_locked_packages(lock_path: Path) -> dict[tuple[str, str], str]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: dict[tuple[str, str], str] = {}

    for package_path, metadata in lock.get("packages", {}).items():
        if "node_modules/" not in package_path:
            continue
        version = metadata.get("version")
        integrity = metadata.get("integrity")
        if not version or not integrity:
            continue

        name = package_path.rsplit("node_modules/", 1)[1]
        key = (name, version)
        previous = packages.setdefault(key, integrity)
        if previous != integrity:
            raise ValueError(f"Conflicting integrity values for {name}@{version}")

    if not packages:
        raise ValueError(f"No npm packages with integrity values found in {lock_path}")
    return packages


def fetch_official_integrity(
    package: tuple[str, str], registry: str, attempts: int = 3
) -> tuple[tuple[str, str], str]:
    name, version = package
    package_name = quote(name, safe="@")
    package_version = quote(version, safe="")
    url = f"{registry.rstrip('/')}/{package_name}/{package_version}"
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "dataset-audit-studio/0.1"},
    )

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                metadata: dict[str, Any] = json.load(response)
            integrity = metadata.get("dist", {}).get("integrity")
            if not integrity:
                raise ValueError(f"Official metadata has no integrity value: {name}@{version}")
            return package, integrity
        except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt)

    raise RuntimeError(f"Failed to read official metadata for {name}@{version}: {last_error}")


def verify(lock_path: Path, registry: str, workers: int) -> None:
    locked = load_locked_packages(lock_path)
    mismatches: list[str] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_official_integrity, package, registry): package
            for package in locked
        }
        for future in as_completed(futures):
            package = futures[future]
            try:
                (_, official_integrity) = future.result()
            except Exception as error:  # noqa: BLE001 - all failures must be reported together
                errors.append(str(error))
                continue

            expected = locked[package]
            if official_integrity != expected:
                name, version = package
                mismatches.append(
                    f"{name}@{version}: lock={expected}, official={official_integrity}"
                )

    if errors or mismatches:
        for message in sorted(errors):
            print(f"ERROR: {message}", file=sys.stderr)
        for message in sorted(mismatches):
            print(f"MISMATCH: {message}", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"Verified {len(locked)} locked npm package versions against {registry}; "
        "all integrity values match."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare package-lock integrity with npm metadata")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    if args.workers < 1 or args.workers > 32:
        parser.error("--workers must be between 1 and 32")
    verify(args.lock.resolve(), args.registry, args.workers)


if __name__ == "__main__":
    main()
