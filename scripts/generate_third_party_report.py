from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "docs" / "THIRD_PARTY_DEPENDENCIES.json"
LICENSE_PREFIXES = ("license", "licence", "notice", "copying")
UNRESOLVED_LICENSE = "SEE_INSTALLED_LICENSE_FILES"


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _requirement_name(value: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", value.strip())
    if match is None:
        raise ValueError(f"Cannot parse dependency requirement: {value}")
    return _canonical_name(match.group())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        raise RuntimeError(f"Dependency notice escapes the project: {path}") from None


def _license_files(paths: list[Path]) -> list[dict[str, Any]]:
    records = []
    for path in sorted(set(paths), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        records.append(
            {
                "path": _project_relative(path),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
        )
    return records


def _python_license(metadata: importlib.metadata.PackageMetadata) -> str:
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    classifiers = sorted(
        value.removeprefix("License :: ")
        for value in metadata.get_all("Classifier") or ()
        if value.startswith("License :: ")
    )
    if classifiers:
        return "; ".join(classifiers)
    raw = " ".join((metadata.get("License") or "").split())
    if raw and len(raw) <= 200:
        return raw
    return UNRESOLVED_LICENSE if raw else "UNKNOWN"


def _python_source(metadata: importlib.metadata.PackageMetadata, name: str, version: str) -> str:
    project_urls: dict[str, str] = {}
    for item in metadata.get_all("Project-URL") or ():
        if "," not in item:
            continue
        label, url = item.split(",", 1)
        project_urls[label.strip().casefold()] = url.strip()
    for label in ("source", "repository", "homepage", "documentation", "changelog"):
        if project_urls.get(label):
            return project_urls[label]
    homepage = (metadata.get("Home-page") or "").strip()
    return homepage or f"https://pypi.org/project/{name}/{version}/"


def _python_packages(pyproject: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = {_requirement_name(value) for value in pyproject["project"]["dependencies"]}
    dev = {
        _requirement_name(value)
        for value in pyproject.get("dependency-groups", {}).get("dev", ())
    }
    rows: list[dict[str, Any]] = []
    found: set[str] = set()
    for distribution in importlib.metadata.distributions():
        metadata = distribution.metadata
        name = metadata.get("Name") or ""
        canonical = _canonical_name(name)
        if canonical == "dataset-audit-studio":
            continue
        found.add(canonical)
        notices = []
        for item in distribution.files or ():
            if any(part.casefold().startswith(LICENSE_PREFIXES) for part in item.parts):
                notices.append(Path(distribution.locate_file(item)))
        scope = "runtime" if canonical in runtime else "dev" if canonical in dev else "transitive"
        rows.append(
            {
                "name": name,
                "version": distribution.version,
                "scope": scope,
                "license": _python_license(metadata),
                "source": _python_source(metadata, name, distribution.version),
                "notice_files": _license_files(notices),
            }
        )
    missing = sorted((runtime | dev) - found)
    if missing:
        raise RuntimeError(f"Direct Python dependencies are not installed: {missing}")
    rows.sort(key=lambda item: (_canonical_name(item["name"]), item["version"]))
    return rows


def _npm_packages(package_json: dict[str, Any], lock: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = {_canonical_name(value) for value in package_json.get("dependencies", {})}
    dev = {_canonical_name(value) for value in package_json.get("devDependencies", {})}
    rows = []
    found: set[str] = set()
    for key, package in lock.get("packages", {}).items():
        if not key or "node_modules/" not in key:
            continue
        name = key.rsplit("node_modules/", 1)[1]
        canonical = _canonical_name(name)
        found.add(canonical)
        package_root = PROJECT_ROOT / "frontend" / Path(key)
        notices = [
            path
            for path in package_root.iterdir()
            if path.is_file() and path.name.casefold().startswith(LICENSE_PREFIXES)
        ] if package_root.is_dir() else []
        scope = "runtime" if canonical in runtime else "dev" if canonical in dev else "transitive"
        version = str(package.get("version", ""))
        rows.append(
            {
                "name": name,
                "version": version,
                "scope": scope,
                "license": str(package.get("license", "UNKNOWN")),
                "source": package.get("resolved")
                or f"https://www.npmjs.com/package/{name}/v/{version}",
                "integrity": package.get("integrity"),
                "notice_files": _license_files(notices),
            }
        )
    missing = sorted((runtime | dev) - found)
    if missing:
        raise RuntimeError(f"Direct NPM dependencies are not installed: {missing}")
    rows.sort(key=lambda item: (_canonical_name(item["name"]), item["version"]))
    return rows


def _model_assets(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for model in registry["models"]:
        source = model["source"]
        rows.append(
            {
                "id": model["id"],
                "revision": source.get("revision"),
                "license": source["license"],
                "source": source["homepage"],
                "remote_code_allowed": source["remote_code_allowed"],
                "files": [
                    {
                        "path": item["path"],
                        "sha256": item["sha256"],
                        "size": item["size"],
                    }
                    for item in model["files"]
                ],
            }
        )
    rows.sort(key=lambda item: item["id"])
    return rows


def build_report() -> dict[str, Any]:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_json = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text())
    package_lock = json.loads((PROJECT_ROOT / "frontend" / "package-lock.json").read_text())
    registry = json.loads(
        (PROJECT_ROOT / "backend" / "dataset_audit_studio" / "model_adapters" / "registry.json")
        .read_text(encoding="utf-8")
    )
    report = {
        "schema_version": 1,
        "python": _python_packages(pyproject),
        "npm": _npm_packages(package_json, package_lock),
        "models": _model_assets(registry),
    }
    # SEE_INSTALLED_LICENSE_FILES means the metadata carried no usable license expression
    # either, so it only passes the gate when the shipped notice files can be read instead.
    unknown = [
        f"python:{item['name']}"
        for item in report["python"]
        if item["license"] == "UNKNOWN"
        or (item["license"] == UNRESOLVED_LICENSE and not item["notice_files"])
    ] + [
        f"npm:{item['name']}"
        for item in report["npm"]
        if item["license"] == "UNKNOWN"
    ] + [
        f"model:{item['id']}"
        for item in report["models"]
        if not item["license"]
    ]
    if unknown:
        raise RuntimeError(f"Dependencies without a declared license: {unknown}")
    return report


def _render(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the locked dependency license report")
    parser.add_argument("--check", action="store_true", help="fail when the report is stale")
    args = parser.parse_args()
    rendered = _render(build_report())
    if args.check:
        if not REPORT_PATH.is_file() or REPORT_PATH.read_text(encoding="utf-8") != rendered:
            print(f"Third-party dependency report is stale: {REPORT_PATH}", file=sys.stderr)
            return 1
        print("Third-party dependency report: OK")
        return 0
    REPORT_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
