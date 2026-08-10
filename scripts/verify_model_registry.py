from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY  # noqa: E402

USER_AGENT = "DatasetAuditStudio/0.1 model-registry-verifier"
# Files up to this size are always hashed. Larger ones are only hashed with --allow-large,
# because a full pass downloads every model weight in the registry.
SMALL_FILE_LIMIT = 2 * 1024 * 1024


@dataclass
class Outcome:
    """Separates files whose digest was actually recomputed from files that were not.

    The previous version counted a URL that merely contained the first eight hex
    characters of the expected digest as verified. That is a 32-bit substring match, not
    an integrity check, and reporting it as "verified" overstated what had been proven.
    """

    hashed: int = 0
    unverified: list[str] = field(default_factory=list)

    def extend(self, other: Outcome) -> None:
        self.hashed += other.hashed
        self.unverified.extend(other.unverified)

    @property
    def total(self) -> int:
        return self.hashed + len(self.unverified)


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.load(response)


def _download_sha256(url: str, expected_size: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            if size > expected_size:
                raise RuntimeError(
                    f"Response is larger than the registered size for {url}: "
                    f"expected {expected_size}, already read {size}"
                )
    if size != expected_size:
        raise RuntimeError(f"Size mismatch for {url}: expected {expected_size}, got {size}")
    return digest.hexdigest()


def _head_size(url: str) -> int:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        method="HEAD",
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        raw = response.headers.get("Content-Length")
    if raw is None:
        raise RuntimeError(f"HEAD response has no Content-Length: {url}")
    return int(raw)


def _verify_huggingface(model, *, allow_large: bool) -> Outcome:
    assert model.source.repository is not None
    assert model.source.revision is not None
    api_url = (
        f"https://huggingface.co/api/models/{model.source.repository}/revision/"
        f"{model.source.revision}?blobs=true"
    )
    metadata = _request_json(api_url)
    if metadata.get("sha") != model.source.revision:
        raise RuntimeError(f"Revision mismatch for {model.id}")
    upstream_license = str(dict(metadata.get("cardData") or {}).get("license", ""))
    if upstream_license.casefold() != model.source.license.casefold():
        raise RuntimeError(
            f"License mismatch for {model.id}: {upstream_license} != {model.source.license}"
        )
    siblings = {item["rfilename"]: item for item in metadata.get("siblings", [])}
    outcome = Outcome()
    for file in model.files:
        upstream = siblings.get(file.path)
        if upstream is None:
            raise RuntimeError(f"Missing upstream file for {model.id}: {file.path}")
        if int(upstream.get("size", -1)) != file.size:
            raise RuntimeError(f"Size mismatch for {model.id}/{file.path}")
        lfs = dict(upstream.get("lfs") or {})
        upstream_sha = lfs.get("sha256")
        if upstream_sha is None:
            # Not stored in LFS, so the API carries no digest and the only way to check
            # is to hash the bytes. Skipping is a gap in coverage, not a passing result.
            if file.size > SMALL_FILE_LIMIT and not allow_large:
                outcome.unverified.append(
                    f"{model.id}/{file.path}: no upstream LFS digest and "
                    f"{file.size} bytes exceeds the {SMALL_FILE_LIMIT} byte download limit "
                    "(re-run with --allow-large to hash it)"
                )
                continue
            upstream_sha = _download_sha256(DEFAULT_REGISTRY.file_url(model, file), file.size)
        if upstream_sha != file.sha256:
            raise RuntimeError(f"SHA-256 mismatch for {model.id}/{file.path}")
        outcome.hashed += 1
    print(
        f"verified huggingface {model.id}: {outcome.hashed} hashed, "
        f"{len(outcome.unverified)} unverified"
    )
    return outcome


def _verify_direct(model, *, allow_large: bool) -> Outcome:
    outcome = Outcome()
    for file in model.files:
        url = DEFAULT_REGISTRY.file_url(model, file)
        if model.source.kind == "github":
            assert model.source.revision is not None
            if f"/{model.source.revision}/" not in url:
                raise RuntimeError(f"GitHub URL is not pinned for {model.id}/{file.path}")
        if _head_size(url) != file.size:
            raise RuntimeError(f"HEAD size mismatch for {model.id}/{file.path}")
        if file.size <= SMALL_FILE_LIMIT or allow_large:
            digest = _download_sha256(url, file.size)
            if digest != file.sha256:
                raise RuntimeError(f"SHA-256 mismatch for {model.id}/{file.path}")
            outcome.hashed += 1
        else:
            outcome.unverified.append(
                f"{model.id}/{file.path}: {file.size} bytes exceeds the "
                f"{SMALL_FILE_LIMIT} byte download limit; only the pinned URL and "
                "Content-Length were checked (re-run with --allow-large to hash it)"
            )
    print(
        f"verified direct {model.id}: {outcome.hashed} hashed, "
        f"{len(outcome.unverified)} unverified"
    )
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the pinned model registry upstream")
    parser.add_argument(
        "--allow-large",
        action="store_true",
        help="download and hash files above the small-file limit instead of reporting them "
        "as unverified (downloads every registered model weight)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when any file could not be hashed",
    )
    args = parser.parse_args()

    total = Outcome()
    for model in DEFAULT_REGISTRY.all():
        if model.source.kind == "huggingface":
            total.extend(_verify_huggingface(model, allow_large=args.allow_large))
        else:
            total.extend(_verify_direct(model, allow_large=args.allow_large))

    models = len(DEFAULT_REGISTRY.all())
    if total.unverified:
        print(f"\n{len(total.unverified)} file(s) were NOT digest-verified:", file=sys.stderr)
        for message in sorted(total.unverified):
            print(f"  UNVERIFIED: {message}", file=sys.stderr)

    print(
        f"Model registry check: {models} models, {total.total} files, "
        f"{total.hashed} digest-verified, {len(total.unverified)} unverified, "
        f"digest {DEFAULT_REGISTRY.digest}"
    )
    if total.unverified and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
