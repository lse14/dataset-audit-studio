# Technical Screening Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the confirmed shared technical-quality defaults to all built-in profiles, replace outer-ring border scoring with continuous opposing-strip detection, and prevent v1/v2 technical-evidence duplication during rescans.

**Architecture:** `MetricThresholds` remains the single configuration source, so all three profiles inherit the same new-task defaults through existing profile materialization. Metrics v2 computes black and white borders independently from qualifying continuous edge scanlines, and repository replacement deletes every historical `technical_metrics_v%` row before inserting the new scan output.

**Tech Stack:** Python 3.11 project-local `.venv`, Pydantic, NumPy, Pillow, OpenCV, SQLAlchemy, Pytest, Ruff.

---

## File Map

- Modify: `backend/dataset_audit_studio/scanner/config.py` - shared threshold defaults for `ScanConfig`.
- Modify: `backend/dataset_audit_studio/scanner/metrics.py` - technical metrics v2, explicit source, and paired-border calculation.
- Modify: `backend/dataset_audit_studio/scanner/types.py` - require an explicit evidence source.
- Modify: `backend/dataset_audit_studio/scanner/repository.py` - remove every historical technical-metric source on scan-output replacement.
- Modify: `tests/test_profile_contracts.py` - assert all three profiles materialize the confirmed scan defaults.
- Modify: `tests/test_scanner_foundations.py` - deterministic in-memory border and metric-version regressions.
- Modify: `tests/test_scanner_integration.py` - regression for v1 evidence cleanup while preserving unrelated evidence.
- Modify: `ROADMAP.md`, `RULES.md`, `MEMORY.md` - mark verified completion and record exact commands/results after testing.

## Execution Record

- Task 1 completed with a red-green profile-materialization regression.
- Task 2 completed with red-green continuous-border and v2-source regressions.
- Task 3 completed with a red-green historical-technical-evidence cleanup regression.
- Task 4 completed: focused Pytest reported `25 passed, 1 skipped`, Ruff passed, and full backend Pytest reached `100%` with one existing skip.
- Per-task commits were intentionally skipped because the active worktree contains unrelated user changes, including one test file touched by this task. No unrelated change was staged or reverted.

### Task 1: Shared Default Thresholds

**Files:**
- Modify: `tests/test_profile_contracts.py:138-154`
- Modify: `backend/dataset_audit_studio/scanner/config.py:11-20`

- [ ] **Step 1: Write the failing profile-default regression**

Add this test after `test_preset_profile_specs_and_materialization_remain_stable`:

```python
def test_builtin_profiles_share_calibrated_technical_metric_defaults() -> None:
    from dataset_audit_studio.app.profile_materialization import materialize_profile

    expected = {
        "minimum_rgb_entropy": 2.5,
        "maximum_black_ratio": 0.90,
        "maximum_white_ratio": 0.90,
        "minimum_laplacian_variance": 16.0,
        "maximum_high_frequency_ratio": 0.32,
        "maximum_border_ratio": 0.03,
        "maximum_blockiness": 0.35,
        "minimum_luminance_std": 10.0,
    }

    for profile in ("artist_concept", "character_concept", "general"):
        components = materialize_profile(profile)["components"]
        assert components["media.scan"]["config"]["thresholds"] == expected
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_profile_contracts.py::test_builtin_profiles_share_calibrated_technical_metric_defaults -q
```

Expected: FAIL because the current defaults still include `0.92`, `25.0`, `0.65`, `0.25`, and `8.0`.

- [ ] **Step 3: Change only the confirmed defaults**

In `MetricThresholds`, set the defaults exactly as follows:

```python
minimum_rgb_entropy: float = Field(default=2.5, ge=0, le=8)
maximum_black_ratio: float = Field(default=0.90, ge=0, le=1)
maximum_white_ratio: float = Field(default=0.90, ge=0, le=1)
minimum_laplacian_variance: float = Field(default=16.0, ge=0)
maximum_high_frequency_ratio: float = Field(default=0.32, ge=0, le=1)
maximum_border_ratio: float = Field(default=0.03, ge=0, le=1)
maximum_blockiness: float = Field(default=0.35, ge=0)
minimum_luminance_std: float = Field(default=10.0, ge=0)
```

Do not add per-profile overrides: `materialize_profile()` already uses `ScanConfig` for each built-in profile.

- [ ] **Step 4: Re-run the focused test and verify it passes**

Run the command from Step 2.

Expected: `1 passed`.

- [ ] **Step 5: Commit this isolated source-and-test change**

```powershell
git add -- backend/dataset_audit_studio/scanner/config.py tests/test_profile_contracts.py
git commit -m "feat: tune technical metric defaults"
```

### Task 2: Technical Metrics v2 and Continuous Opposing Borders

**Files:**
- Modify: `tests/test_scanner_foundations.py:1-121`
- Modify: `backend/dataset_audit_studio/scanner/metrics.py:14,61-70,112-126,220-224`
- Modify: `backend/dataset_audit_studio/scanner/types.py:26-33`

- [ ] **Step 1: Write failing in-memory border and evidence-source tests**

Extend imports in `tests/test_scanner_foundations.py`:

```python
from PIL import Image, ImageDraw, ImageFilter
from dataset_audit_studio.scanner.metrics import (
    METRICS_ALGORITHM_VERSION,
    calculate_metrics,
    perceptual_hashes,
    pixel_sha256,
)
from dataset_audit_studio.scanner.types import MetricEvidence
```

Add these helpers and tests:

```python
def _metric_map(image: Image.Image) -> dict[str, MetricEvidence]:
    return {metric.code: metric for metric in calculate_metrics(image, ScanConfig())}


def test_border_metrics_require_continuous_opposing_strips() -> None:
    black_bars = Image.new("RGB", (200, 100), (128, 128, 128))
    black_draw = ImageDraw.Draw(black_bars)
    black_draw.rectangle((0, 0, 199, 3), fill=(0, 0, 0))
    black_draw.rectangle((0, 96, 199, 99), fill=(0, 0, 0))
    black = _metric_map(black_bars)
    assert float(black["black_border_ratio"].value) == pytest.approx(0.08)
    assert black["black_border_ratio"].severity == "medium"
    assert float(black["white_border_ratio"].value) == 0.0

    white_bars = Image.new("RGB", (200, 100), (128, 128, 128))
    white_draw = ImageDraw.Draw(white_bars)
    white_draw.rectangle((0, 0, 3, 99), fill=(255, 255, 255))
    white_draw.rectangle((196, 0, 199, 99), fill=(255, 255, 255))
    white = _metric_map(white_bars)
    assert float(white["white_border_ratio"].value) == pytest.approx(0.04)
    assert white["white_border_ratio"].severity == "medium"
    assert float(white["black_border_ratio"].value) == 0.0

    thin_bars = Image.new("RGB", (200, 100), (128, 128, 128))
    thin_draw = ImageDraw.Draw(thin_bars)
    thin_draw.line((0, 0, 199, 0), fill=(0, 0, 0))
    thin_draw.line((0, 99, 199, 99), fill=(0, 0, 0))
    thin = _metric_map(thin_bars)
    assert float(thin["black_border_ratio"].value) == pytest.approx(0.02)
    assert thin["black_border_ratio"].severity == "info"


def test_border_metrics_reject_single_or_noncontinuous_edge_backgrounds() -> None:
    single = Image.new("RGB", (200, 100), (128, 128, 128))
    ImageDraw.Draw(single).rectangle((0, 0, 199, 3), fill=(0, 0, 0))
    single_metrics = _metric_map(single)
    assert float(single_metrics["black_border_ratio"].value) == 0.0
    assert single_metrics["black_border_ratio"].severity == "info"

    irregular = Image.new("RGB", (200, 100), (128, 128, 128))
    irregular_draw = ImageDraw.Draw(irregular)
    irregular_draw.rectangle((0, 0, 197, 3), fill=(0, 0, 0))
    irregular_draw.rectangle((0, 96, 197, 99), fill=(0, 0, 0))
    irregular_metrics = _metric_map(irregular)
    assert float(irregular_metrics["black_border_ratio"].value) == 0.0
    assert irregular_metrics["black_border_ratio"].severity == "info"

    solid_black = _metric_map(Image.new("RGB", (200, 100), (0, 0, 0)))
    assert float(solid_black["black_border_ratio"].value) == 0.0
    assert solid_black["black_pixel_ratio"].severity == "medium"


def test_technical_metrics_use_v2_as_source_and_metadata() -> None:
    metrics = calculate_metrics(Image.new("RGB", (32, 32), "gray"), ScanConfig())
    assert METRICS_ALGORITHM_VERSION == "technical_metrics_v2"
    assert {metric.source for metric in metrics} == {METRICS_ALGORITHM_VERSION}
    assert {
        metric.metadata["algorithm_version"] for metric in metrics
    } == {METRICS_ALGORITHM_VERSION}
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_scanner_foundations.py -q
```

Expected: FAIL because v1 scores the outer ring, `technical_metrics_v1` is still the source, and `MetricEvidence` still supplies a v1 default.

- [ ] **Step 3: Implement the v2 metric source and paired-strip calculation**

Replace the version and outer-ring helper in `metrics.py` with the following focused helpers:

```python
METRICS_ALGORITHM_VERSION = "technical_metrics_v2"
PIXEL_HASH_VERSION = "rgba8_display_v1"

_BORDER_SCANLINE_COVERAGE = 0.995
_BORDER_MINIMUM_DEPTH_RATIO = 0.005
_BORDER_INTERIOR_COVERAGE_DROP = 0.60


def _edge_strip_depth(coverages: np.ndarray) -> int:
    depth = 0
    for coverage in coverages:
        if coverage < _BORDER_SCANLINE_COVERAGE:
            break
        depth += 1
    minimum_depth = max(1, math.ceil(len(coverages) * _BORDER_MINIMUM_DEPTH_RATIO))
    if depth < minimum_depth or depth >= len(coverages):
        return 0
    if float(coverages[:depth].mean()) - float(coverages[depth]) < _BORDER_INTERIOR_COVERAGE_DROP:
        return 0
    return depth


def _opposing_strip_ratio(coverages: np.ndarray) -> float:
    start_depth = _edge_strip_depth(coverages)
    end_depth = _edge_strip_depth(coverages[::-1])
    if not start_depth or not end_depth or start_depth + end_depth >= len(coverages):
        return 0.0
    return float((start_depth + end_depth) / len(coverages))


def _paired_border_ratio(mask: np.ndarray) -> float:
    horizontal = _opposing_strip_ratio(mask.mean(axis=1))
    vertical = _opposing_strip_ratio(mask.mean(axis=0))
    return max(horizontal, vertical)


def _border_ratios(gray: np.ndarray) -> tuple[float, float]:
    return (
        _paired_border_ratio(gray <= 12),
        _paired_border_ratio(gray >= 243),
    )
```

In `_metric`, pass `source=METRICS_ALGORITHM_VERSION` to `MetricEvidence`. In `types.py`, change the dataclass field to `source: str` without a default, so every producer explicitly chooses its source. `scanner/service.py` already supplies `source="scanner"` and needs no behavior change.

- [ ] **Step 4: Re-run the focused tests and verify they pass**

Run the command from Step 2.

Expected: all tests in `tests/test_scanner_foundations.py` pass, including the existing blur review-only assertion.

- [ ] **Step 5: Commit this isolated source-and-test change**

```powershell
git add -- backend/dataset_audit_studio/scanner/metrics.py backend/dataset_audit_studio/scanner/types.py tests/test_scanner_foundations.py
git commit -m "fix: require paired continuous image borders"
```

### Task 3: Remove Historical Technical Evidence on Rescan

**Files:**
- Modify: `tests/test_scanner_integration.py:1-25,258-299,302-389`
- Modify: `backend/dataset_audit_studio/scanner/repository.py:3,19-23,56-68`

- [ ] **Step 1: Write the failing v1-cleanup regression**

Extend imports in `tests/test_scanner_integration.py`:

```python
from dataclasses import replace

from dataset_audit_studio.scanner.metrics import METRICS_ALGORITHM_VERSION
from dataset_audit_studio.scanner.types import MetricEvidence, ScannedMedia
```

Add this test after `test_incremental_reuse_move_and_layered_invalidation`:

```python
def test_rescan_replaces_all_historical_technical_metric_evidence(
    database: Database, task_service: TaskService
) -> None:
    task = task_service.create_task(
        name="technical metric cleanup",
        source_root="E:\\source",
        output_root=None,
        config=_scan_config(),
    )
    original = _scanned("sample.png", "1" * 64, "a" * 64)
    _upsert(database, task.id, original, prepare=True)

    with database.write_session() as session:
        sample = session.scalar(select(Sample).where(Sample.task_id == task.id))
        assert sample is not None
        for source in ("scanner", "technical_metrics_v0", "technical_metrics_v1"):
            session.add(
                Evidence(
                    task_id=task.id,
                    sample_id=sample.id,
                    code=source,
                    source=source,
                    value_json=1.0,
                    value_number=1.0,
                    threshold_json=None,
                    threshold_number=None,
                    metadata_json={},
                    severity="info",
                    review_only=False,
                    bbox_json=None,
                    algorithm_version=source,
                )
            )
        session.add(
            Evidence(
                task_id=task.id,
                sample_id=sample.id,
                code="aesthetic",
                source="aesthetic_model",
                value_json=1.0,
                value_number=1.0,
                threshold_json=None,
                threshold_number=None,
                metadata_json={},
                severity="info",
                review_only=False,
                bbox_json=None,
                algorithm_version="aesthetic_v1",
            )
        )

    v2_metric = MetricEvidence(
        code="rgb_entropy",
        value=7.0,
        threshold=2.5,
        severity="info",
        review_only=False,
        source=METRICS_ALGORITHM_VERSION,
        metadata={"algorithm_version": METRICS_ALGORITHM_VERSION},
    )
    _upsert(database, task.id, replace(original, evidence=(v2_metric,)), prepare=False)

    with database.read_session() as session:
        rows = session.scalars(
            select(Evidence)
            .where(Evidence.task_id == task.id)
            .order_by(Evidence.source, Evidence.code)
        ).all()

    assert [(row.source, row.code) for row in rows] == [
        ("aesthetic_model", "aesthetic"),
        (METRICS_ALGORITHM_VERSION, "rgb_entropy"),
    ]
```

- [ ] **Step 2: Run the focused regression and verify it fails**

Run:

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_scanner_integration.py::test_rescan_replaces_all_historical_technical_metric_evidence -q
```

Expected: FAIL because the existing replacement query removes `scanner` and v2 only, leaving `technical_metrics_v0` and `technical_metrics_v1` rows.

- [ ] **Step 3: Replace the exact-source cleanup with source-family cleanup**

In `repository.py`, import `or_` and remove `SCAN_EVIDENCE_SOURCES`. Replace the evidence delete condition with:

```python
session.execute(
    delete(Evidence).where(
        Evidence.sample_id == sample.id,
        or_(
            Evidence.source == "scanner",
            Evidence.source.like("technical_metrics_v%"),
        ),
    )
)
```

This intentionally leaves `aesthetic_model`, model outputs, and human decisions untouched.

- [ ] **Step 4: Re-run the focused regression and verify it passes**

Run the command from Step 2.

Expected: `1 passed`; only `aesthetic_model` and the newly inserted v2 metric remain.

- [ ] **Step 5: Commit this isolated source-and-test change**

```powershell
git add -- backend/dataset_audit_studio/scanner/repository.py tests/test_scanner_integration.py
git commit -m "fix: clear obsolete technical metric evidence"
```

### Task 4: Verify, Record, and Hand Off

**Files:**
- Modify: `ROADMAP.md`
- Modify: `RULES.md`
- Modify: `MEMORY.md`

- [ ] **Step 1: Run the combined focused regression set**

Run:

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_profile_contracts.py tests/test_scanner_foundations.py tests/test_scanner_integration.py -q
```

Expected: PASS with no collection errors.

- [ ] **Step 2: Run Ruff on the affected backend and tests**

Run:

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
& (Join-Path $paths.Venv 'Scripts\python.exe') -m ruff check backend/dataset_audit_studio/scanner tests/test_profile_contracts.py tests/test_scanner_foundations.py tests/test_scanner_integration.py
```

Expected: exit code 0.

- [ ] **Step 3: Run the full backend suite in a project-local temporary directory**

Run:

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
$testTemp = Join-Path $paths.ProjectRoot '.test-tmp\technical-screening-defaults'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
try {
    & (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest --basetemp $testTemp -p no:cacheprovider -q
    $exitCode = $LASTEXITCODE
}
finally {
    if (Test-Path -LiteralPath $testTemp) {
        Remove-Item -LiteralPath $testTemp -Recurse -Force -ErrorAction Stop
    }
}
exit $exitCode
```

Expected: full backend Pytest passes, with only any pre-existing documented skip allowed.

- [ ] **Step 4: Update the project records with observed verification evidence**

Change the technical-screening Roadmap items to `[x]` only after the commands above succeed. Record exact pass counts and any skips in `ROADMAP.md` and `MEMORY.md`; retain the scope rules in `RULES.md` unchanged unless verification exposes a necessary new constraint.

- [ ] **Step 5: Inspect the final diff and commit only task-owned files**

Run:

```powershell
git diff --check
git status --short
```

Stage only the files listed in this plan that belong to this task. Do not stage unrelated dirty files. If `ROADMAP.md`, `RULES.md`, or `MEMORY.md` contain prior uncommitted work, leave them unstaged while preserving the appended technical-screening entries.

Commit the remaining task-owned code and tests only if no earlier task commit already includes them:

```powershell
git add -- backend/dataset_audit_studio/scanner/config.py backend/dataset_audit_studio/scanner/metrics.py backend/dataset_audit_studio/scanner/types.py backend/dataset_audit_studio/scanner/repository.py tests/test_profile_contracts.py tests/test_scanner_foundations.py tests/test_scanner_integration.py
git commit -m "fix: calibrate technical screening metrics"
```
