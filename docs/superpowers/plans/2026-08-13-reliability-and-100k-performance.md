# Reliability + 100k Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix critical reliability bugs (picker host, AI `model_id`, frontend races/SSE) and improve 100k-scale throughput (export write-lock, batching, CF-only CLIP, audit UI) without new dependencies or product-contract changes.

**Architecture:** Four layers executed in order of risk: (1) correctness/hangs, (2) export/DB lock & I/O, (3) pipeline batch ceilings & CF-only, (4) audit UI remount/virtualization. Same-layer tasks may run in parallel subagents when file ownership does not overlap.

**Tech Stack:** Project-local Python `.venv`, FastAPI, SQLAlchemy/SQLite, Pydantic, Pytest, Ruff; frontend Vite/React/TypeScript, existing `@tanstack/react-virtual`, Node unit tests, Playwright where listed.

**Spec:** `docs/superpowers/specs/2026-08-13-reliability-and-100k-performance-design.md`

## Global Constraints

- Use only project-local Python, Node, models, and caches.
- Do not add Python/Node dependencies.
- Do not rewrite source images, saved human decisions, or existing export trees.
- Old direct `scoring.ai` missing `model_id` must remain UFD; new component defaults may stay Community Forensics.
- Community AI / semantic duplicates / character consistency stay `review_only` + human confirm.
- Windows picker remains Common Item Dialog; warmup failure must not block WebUI; shutdown must not leave orphan PowerShell.
- Unified export formats unchanged: `original|jpeg|png|webp`, JPEG white background, stem-collision keep-all, missing field → `original`.
- Risk rows without virtual offsets stay in normal document flow (no stacked-text regression).
- Do **not** git commit unless the user explicitly asks; leave working tree changes for the parent session.
- Do **not** push; do not add `docs/superpowers`, `MEMORY.md`, or `ROADMAP.md` to public release commits.
- Known baseline: `tests/test_r10_1_contract.py` may still expect general `embedding.semantic.enabled=False`; do not “fix” that unless this task owns that default.

## Parallelism

| Wave | Tasks (parallel OK if no file clash) |
| --- | --- |
| A | 1, 2, 3, 4 |
| B | 5, 6, 7 (7 after 5 if sharing `planner.py`/`service.py` — prefer 5→6 then 7, or single agent for 5+6) |
| C | 8, 9, 10 |
| D | 11, 12 |

## File Map

- Modify: `backend/dataset_audit_studio/workspace/windows_dialog.py` — picker host reliability
- Modify: `backend/dataset_audit_studio/main.py` — only if warmup/close wiring needs tweak (prefer keep)
- Modify: `tests/test_windows_dialog.py`, `tests/test_directory_selection_api.py`
- Modify: `backend/dataset_audit_studio/components/ai_detection/config.py` and/or `app/component_task_config.py` — missing `model_id` → UFD on materialize
- Modify/Create tests under `tests/` for AI materialization contract
- Modify: `frontend/src/hooks/useSelectedTaskData.ts`, `useTaskEventRefresh.ts`, `transport/taskEvents.ts`, `pages/ModelsPage.tsx`
- Modify: `frontend/tests/selectedTaskDataHook.test.mjs`, `taskEventRefreshHook.test.mjs`, `taskEventTransport.test.mjs`
- Modify: `backend/dataset_audit_studio/clustering/config.py` — semantic threshold default `0.92`
- Modify: `backend/dataset_audit_studio/clustering/repository.py` — default arg align
- Modify: `backend/dataset_audit_studio/export_runs/service.py`, `planner.py`, `executor.py`, `eligibility.py`
- Modify: `backend/dataset_audit_studio/export_runs/` (+ optional new small cache helper module)
- Modify: scoring/clip/ai asset resolution paths for CF-only
- Modify: component config Field `le=` ceilings (batch sizes)
- Modify: `backend/dataset_audit_studio/scanner/service.py`, `backend/dataset_audit_studio/app/modular_scoring.py` — Task 10 internal inference/write aggregation only; do not add public config fields
- Modify: `tests/test_scanner_integration.py`, `tests/test_modular_scoring.py` — Task 10 transaction, control, cache, checkpoint, and finite-value contracts
- Modify: `frontend/src/App.tsx`, audit pages for virtual list + remount keys
- Update local `MEMORY.md` / `ROADMAP.md` only after verification (parent session)

---

### Task 1: Add Windows picker host — stderr, READY timeout, orphan kill, close force

**Files:**
- Modify: `backend/dataset_audit_studio/workspace/windows_dialog.py`
- Modify: `tests/test_windows_dialog.py`
- Modify: `tests/test_directory_selection_api.py` (only if API lifecycle assertions need update)

**Interfaces:**
- Consumes: existing directory/file picker HTTP contract and one-shot PowerShell picker behavior
- Produces: new application-scoped `NativePickerHost.start/select/close`; internal `_start_locked` must never leave unreaped Popen on failure; `close` must return even if `select` holds the RLock

- [ ] **Step 1: Write failing tests**

Add/adjust tests that prove:
1. When READY never arrives, host raises and process is not left running (`poll() is not None` after failure).
2. `stderr=PIPE` is not used (or a drain exists); assert Popen kwargs / monkeypatch capture.
3. `close()` succeeds even if another thread holds `_lock` during a fake long `select` (or unit-test a `_force_close` path that kills by PID without requiring the same lock acquisition forever).

Example shape for READY timeout (adapt to existing fixtures):

```python
def test_start_ready_timeout_terminates_process(monkeypatch, tmp_path):
    # monkeypatch Popen to a fake whose stdout.readline blocks / returns non-READY
    # assert DirectoryDialogError and fake.terminated is True
    ...
```

- [ ] **Step 2: Run tests — expect FAIL**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_windows_dialog.py -q --basetemp=E:\Desktop\dataset-audit-studio\.tmp-pytest
```

- [ ] **Step 3: Implement minimal fix in `windows_dialog.py`**

Required behaviors:
- `stderr=subprocess.DEVNULL` (preferred) instead of `PIPE`.
- Wait for READY with timeout (e.g. `threading` + join, or read with deadline); on timeout `_terminate(process)` then raise.
- On any failure after successful `Popen` and before `self._process = process`, call `_terminate(process)`.
- `close()`: if lock not acquired within short timeout, terminate recorded PID / last known process so WebUI shutdown cannot hang forever. Keep QUIT-then-wait as happy path when lock is free.

- [ ] **Step 4: Re-run focused tests — expect PASS**

Also run: `tests/test_directory_selection_api.py` if warmup lifespan tests exist.

- [ ] **Step 5: Do not commit** (unless user asked)

---

### Task 2: AI missing `model_id` materializes as UFD

**Files:**
- Modify: `backend/dataset_audit_studio/app/component_task_config.py` (preferred: coerce before `model_validate` for `detect.ai`)
- Optionally adjust `components/ai_detection/config.py` only if needed without breaking **new-task CF default**
- Test: add/extend `tests/test_components_api.py` or `tests/test_profile_contracts.py` / dedicated `tests/test_ai_model_id_materialization.py`

**Interfaces:**
- Consumes: raw component config mappings
- Produces: materialized `scoring.ai.model_id == "universal_fake_detector_head"` when input omitted `model_id`; new configs that omit field at Pydantic default for **new** `AIDetectionConfig` may still be CF — fix must be at **legacy scoring / missing-key materialization** boundary

Critical distinction from spec:
- **Missing key in saved/raw config → UFD**
- **Explicit new component default when creating fresh detect.ai → CF allowed**

Recommended approach: in `component_task_config` validation loop for `detect.ai`, if `"model_id" not in raw_config`, insert `UFD_MODEL_ID` before `model_validate`. Keep `AIDetectionConfig.model_id` default as CF for true new objects that intentionally use defaults elsewhere.

- [ ] **Step 1: Failing test**

```python
def test_detect_ai_missing_model_id_materializes_as_ufd():
    # build minimal components payload with detect.ai enabled and config WITHOUT model_id
    # run materialize / normalize path used by production
    # assert scoring["ai"]["model_id"] == "universal_fake_detector_head"
```

Also assert a fresh config that **includes** explicit CF still stays CF.

- [ ] **Step 2: Run — expect FAIL** (likely gets CF today)

- [ ] **Step 3: Implement coerce-before-validate for missing key only**

- [ ] **Step 4: Run focused tests PASS + existing AI/community tests still green**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_model_id_materialization.py tests/test_components_api.py -q --basetemp=E:\Desktop\dataset-audit-studio\.tmp-pytest
```

- [ ] **Step 5: Do not commit**

---

### Task 3: Frontend task data race + SSE reopen + event types + Models submit busy

**Files:**
- Modify: `frontend/src/hooks/useSelectedTaskData.ts`
- Modify: `frontend/src/hooks/useTaskEventRefresh.ts`
- Modify: `frontend/src/transport/taskEvents.ts`
- Modify: `frontend/src/pages/ModelsPage.tsx`
- Modify: `frontend/tests/selectedTaskDataHook.test.mjs`
- Modify: `frontend/tests/taskEventRefreshHook.test.mjs`
- Modify: `frontend/tests/taskEventTransport.test.mjs`

**Interfaces:**
- `loadTaskData(taskId): Promise<number | null>` — must ignore stale completions
- `startTaskEventRefreshLifecycle` — must reopen SSE after a later successful `after`
- `openTaskEventStream` — must refresh on backend event types actually emitted (include at least `phase_process_ready`, `watermark_review_threshold_changed`, `rewrite_preview_confirmed`, `legacy_task_rejected`, or use a catch-all that does not disable fallback incorrectly)

- [ ] **Step 1: Extend unit tests to fail on current behavior**

`selectedTaskDataHook.test.mjs`: simulate two overlapping loads; older resolve last must not win.

`taskEventRefreshHook.test.mjs`: first `loadTaskData` returns `null`, fallback later returns sequence → `openStream` must be called.

`taskEventTransport.test.mjs`: assert listed backend event names are subscribed (or message handler covers them).

ModelsPage: if there is an existing test harness, assert primary submit `disabled` when `pickerBusy`; else add a small DOM/unit check or static assert in a presentation test that the JSX includes `pickerBusy` in the primary button disabled expression.

Primary button today:

```tsx
disabled={busy || !path.trim() || !base}
```

Change to:

```tsx
disabled={busy || pickerBusy || !path.trim() || !base}
```

- [ ] **Step 2: Run frontend unit tests for those files — expect FAIL**

```powershell
cd frontend; ..\.venv-node-path-or-project-node npm test -- --run selectedTaskDataHook taskEventRefreshHook taskEventTransport
```

Use the project’s documented Node (same as `scripts/test.ps1` / existing npm).

- [ ] **Step 3: Implement**

`useSelectedTaskData.ts` sketch:

```ts
const loadTaskData = useCallback(async (taskId: string): Promise<number | null> => {
  const requestId = ++requestIdRef.current
  try {
    const [...] = await Promise.all([...])
    if (requestId !== requestIdRef.current || taskId !== selectedTaskIdRef.current) return null
    // existing success writes
  } catch ...
}, [...])
```

Keep `selectedTaskId` in a ref updated by effect so stale guards see latest id.

`useTaskEventRefresh.ts`: if initial `after === null`, keep fallback refresh; when a refresh obtains non-null sequence and stream is null, call `openStream`.

`taskEvents.ts`: extend `taskEventTypes` to match backend emitters (grep `event_type=` / string literals in `jobs/service.py`).

- [ ] **Step 4: PASS unit tests**

- [ ] **Step 5: Do not commit**

---

### Task 4: Align semantic duplicate threshold default to 0.92

**Files:**
- Modify: `backend/dataset_audit_studio/clustering/config.py` (`semantic_duplicate_threshold` default `0.92`)
- Modify: `backend/dataset_audit_studio/clustering/repository.py` (signature default `0.92`)
- Modify/add tests that lock `0.92` and profile hierarchy `0.92`

- [ ] **Step 1: Failing test** asserting `ClusteringConfig().semantic_duplicate_threshold == 0.92` and repository default matches

- [ ] **Step 2: Run FAIL**

- [ ] **Step 3: Change defaults only** (do not change calibrated meaning claims; keep review_only)

- [ ] **Step 4: PASS** related clustering tests

- [ ] **Step 5: Do not commit**

---

### Task 5: Export create/preview — move encode out of write_session

**Files:**
- Modify: `backend/dataset_audit_studio/export_runs/service.py`
- Modify: `backend/dataset_audit_studio/export_runs/planner.py`
- Modify: `tests/test_export_runs.py` (and any planner unit tests)

**Interfaces:**
- Produces: `create`/`preview` no longer call `encode_export_image` inside `database.write_session()`
- Digest semantics unchanged (`preview_digest` still comparable)

Approach:
1. Split `_build_current` into (a) DB read of samples/settings → plan structure with source identities, (b) optional lock-free encode pass attaching output sha/size, (c) short write inserting run after digest check.
2. Delete `if False` dead branch around line 525 in `planner.py`; keep real preview digest path.

- [ ] **Step 1: Failing regression**

Prefer a monkeypatch test: wrap `encode_export_image` to assert `not in_write_session` flag set by monkeypatching `write_session` context.

Or: spy that during `write_session`, encode is never called.

- [ ] **Step 2: FAIL on current code**

- [ ] **Step 3: Implement split transactions**

Keep validation that needs consistency (task version, empty dir, output_key uniqueness) in short writes; do heavy encode outside.

- [ ] **Step 4: PASS export focused suite**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_export_runs.py -q --basetemp=E:\Desktop\dataset-audit-studio\.tmp-pytest
```

- [ ] **Step 5: Do not commit**

---

### Task 6: Export encode cache + batched executor heartbeats

**Files:**
- Create (optional): `backend/dataset_audit_studio/export_runs/transcode_cache.py`
- Modify: `planner.py` / publisher path / `executor.py`
- Tests: export run tests for cache hit reuse + heartbeat batching

**Interfaces:**
- Cache key: `source_path + mtime_ns/size + image_format`
- Miss → encode; hit → reuse bytes/hash
- Executor: update DB every N files or T seconds (default N=32–64)

- [ ] **Step 1: Tests** — cache hit avoids second encode; crash-restart still idempotent staging overwrite; progress advances in batches

- [ ] **Step 2: FAIL / implement / PASS**

- [ ] **Step 3: Do not commit**

Default cache memory cap: conservative (e.g. tens–low hundreds of MB). Optional higher cap via constant/env only if already patterned in codebase — do not add new deps or settings UI unless trivial constant.

---

### Task 7: Style eligibility SQL set filter

**Files:**
- Modify: `backend/dataset_audit_studio/export_runs/eligibility.py` (`_style_scope_identities`)
- Tests: export/eligibility/style identity digest stability

Keep digest payload identical for same member sets. Replace Python-side “load all then filter with sets” with SQL `NOT IN` / anti-join / `where(~Sample.id.in_(...))` composed in the query where practical. For large exclude sets, keep excluded ids as SQL subqueries (already partially subquery-friendly) instead of materializing all samples then filtering in Python.

- [ ] **Step 1: Test** same scope_hash before/after for a fixture with AI excludes + domain misses

- [ ] **Step 2: Implement query-side filters**

- [ ] **Step 3: PASS**

- [ ] **Step 4: Do not commit**

---

### Task 8: CF-only must not load CLIP + raise batch Field ceilings

**Files:**
- Audit/modify: `scoring/assets.py`, `scoring/torch_runtime.py`, `components/ai_detection/manifest.py`, modular component resolve paths
- Modify Field `le=` on: `components/clip_features/config.py`, `artist_style/config.py`, `semantic_embedding/config.py`, `scoring/config.py`, `scanner/config.py` as needed
- **Do not raise defaults** for new tasks beyond mild safe-tier; only raise maxima

- [ ] **Step 1: Test** CF-only enabled components → runtime assets exclude `openai_clip_vit_l14` and `clip_runtime is None`

- [ ] **Step 2: Close any remaining path that still pulls CLIP for CF-only**

- [ ] **Step 3: Raise `le` caps** (example targets — adjust to existing Field patterns): semantic/CLIP batch `le=128` or `256`; style `le=64`; scanner write batch `le` higher; keep defaults unchanged

- [ ] **Step 4: PASS** scoring/component tests; Gram weight 0 still skips VGG if already implemented (add regression if missing)

- [ ] **Step 5: Do not commit**

---

### Task 9: Clustering / semantic candidate — avoid full-task materialization

**Files:**
- Modify: `backend/dataset_audit_studio/clustering/repository.py` — ordered sample-ID loader and leaf-local candidate persistence
- Modify: `backend/dataset_audit_studio/clustering/shards.py` only if the existing verified shard loader cannot supply the needed rows
- Modify: `backend/dataset_audit_studio/app/modular_clustering.py` and `backend/dataset_audit_studio/clustering/service.py` — load current scope from registered shards instead of retaining the full task matrix when SAE is disabled
- Tests: modular clustering / semantic duplicate tests

- [ ] **Step 1: Identify code that loads all embeddings for the task when only leaf members are needed**

- [ ] **Step 2: Failing test or benchmark assertion** — leaf path only fetches member ids’ embeddings (mock/spy session.execute)

- [ ] **Step 3: Stream/fetch per leaf**

- [ ] **Step 4: PASS**; evidence remains `review_only`

- [ ] **Step 5: Do not commit**

---

### Task 10: Separate scan/scoring inference batches from DB write batches

**Files:**
- Modify: `backend/dataset_audit_studio/scanner/service.py` — aggregate decoded `ScannedMedia` DTOs and committed counter snapshots
- Modify: `backend/dataset_audit_studio/app/modular_scoring.py` — aggregate finite `SampleScore` DTOs and CLIP shard descriptors
- Test: `tests/test_scanner_integration.py`
- Test: `tests/test_modular_scoring.py`
- Test: `tests/test_scanner_foundations.py`, `tests/test_scoring_foundations.py`, `tests/test_cf_only_clip_and_batch_ceilings.py` — unchanged public/default config contract

**Interfaces:**
- Decode/inference boundaries remain `ScanConfig.batch_size` and `ScoringConfig.batch_size`; OCR's internal `recognition_batch_size` remains an OCR runtime detail.
- DB write/checkpoint aggregation is an internal derived policy, not a task/component schema field: `SCAN_WRITE_BATCH_TARGET = 256` and `SCORING_WRITE_BATCH_TARGET = 64`. For each run, derive the flush size as the smallest whole multiple of `batch_size` at least the target, or `batch_size` itself when it already exceeds the target. This keeps flushes on completed inference-batch boundaries and within the existing validated batch maximum.
- Do **not** add `write_batch_size` to `ScanConfig` or `ScoringConfig`. `ScanConfig.cache_payload()` currently serializes every model field into manifest/cache identity, and the component materializer currently projects only CLIP `batch_size` into `scoring`; a public field would change persisted task/config/schema contracts for a tuning detail.
- Pending scan values may contain only immutable `ScannedMedia` rows plus primitive counter snapshots. Pending non-CLIP scoring values may contain only `SampleScore`/dict/list/scalar persistence values after `_require_finite`; convert CLIP `FeatureShard` objects immediately to primitive `{cache_key, relative_path, sha256}` descriptors. Never retain PIL images, NumPy/Torch tensors, feature batches, model/runtime objects, or an open SQLAlchemy session across a write batch.
- One `TaskService.commit_batch` remains the sole atomic boundary for business rows, `PhaseCheckpoint`, task progress, lease renewal, component-run checkpoint, and graceful pause/terminate transition. `cursor.next_index`, scan `counts`, `inferred_samples`, `cached_samples`, `results_prepared`, and `component_complete` must describe only the rows/descriptors passed to that same `batch_writer`.
- Check control after each complete decode/inference batch. Inline/cooperative `PAUSING` or `TERMINATING` flushes the validated pending prefix through `commit_batch`; force termination or the existing subprocess supervisor may stop the child first, in which case pending memory is discarded and recovery starts at the last committed `next_index`. Do not modify `jobs/phase_process.py` or promise that a force-stopped child flushes memory.
- Keep the existing 30-second parent heartbeat for scoring subprocesses. For direct scanner/inline scoring execution, renew the 300-second lease with `TaskService.heartbeat` when 30 seconds elapse without a flush; heartbeat must not advance progress/checkpoint or mark buffered rows committed. No new heartbeat config/API is added.
- Preserve cache and identity semantics for all components: fully cached aesthetic/AI/OCR/watermark batches enter the same pending DTO buffer; inferred batches pass `_require_finite` before entering it; first non-CLIP flush alone may call repository `prepare=True`; terminal empty components still commit `results_prepared/component_complete` correctly.
- CLIP is the exceptional artifact path: every inference batch creates or reuses a distinct shard. A combined checkpoint must register every shard in a `feature_shards` list; when the list has one entry, also retain legacy `feature_shard`. `_registered_clip_shard` must read and deduplicate both forms so existing checkpoints remain resumable. An unregistered shard left by force stop may be reused only by the CLIP producer's existing `require_registered=False` path; consumers still require a committed matching descriptor.

- [ ] **Step 1: Lock the config boundary with failing/unchanged tests**

In `tests/test_scanner_foundations.py` and `tests/test_scoring_foundations.py`, assert defaults and serialized public fields are unchanged and contain no `write_batch_size`. Keep `tests/test_cf_only_clip_and_batch_ceilings.py` expectations (`ScanConfig().batch_size == 64`, `ScoringConfig().batch_size == 1`, existing maxima) unchanged.

These assertions should already pass; they are guard tests, not the red test. Their purpose is to prevent the implementation from solving batching by mutating saved task/config/cache identity.

- [ ] **Step 2: Write scanner red tests**

Extend `_queued_scan` in `tests/test_scanner_integration.py` only with an optional fixture-level way to exercise the internal target; do not expose it in task config. Add:

1. `test_scanner_groups_complete_decode_batches_into_one_atomic_write`: use five images, decode `batch_size=2`, spy `TaskService.commit_batch`, and assert one data flush at committed `next_index=5`, one `PhaseCheckpoint`, five `Sample` rows, and cursor counts equal the summary. The current loop produces three data checkpoints, so the red reason is `len(data_commits) == 3`, not `1`.
2. `test_scanner_pause_flushes_validated_prefix_and_resumes_from_committed_index`: request pause after the second decode batch but before the derived size is full; assert the cooperative flush commits exactly four rows with `next_index=4`, status becomes `paused`, and resume starts at 4 without rescanning the committed prefix. Current code pauses after the first inference batch (`next_index=2`), so the red reason proves aggregation/control behavior rather than a generic failure.
3. Keep `test_forced_termination_discards_uncommitted_scan_batch`: force termination must still leave zero rows/checkpoints. This distinguishes graceful flush from forced-stop discard.
4. Add a write-failure case by monkeypatching `upsert_scanned_batch` to raise: samples, checkpoint, progress, counters, and prepare/manifest effects must all roll back together.

- [ ] **Step 3: Run scanner red tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_scanner_integration.py tests/test_scanner_foundations.py -q --basetemp=.test-tmp\task10-scanner-red -p no:cacheprovider
```

Expected before implementation: new aggregation/control tests fail because `scanner/service.py` calls `commit_batch` at lines 205–216 once per decode batch. Existing forced termination and atomic rollback contracts must not be weakened to obtain red.

- [ ] **Step 4: Implement the minimal scanner buffer/flush helper**

Keep it private to `scanner/service.py`. Track separate pending and committed state:

```python
pending_items: list[ScannedMedia] = []
pending_end = committed_end = start_index
pending_counts = dict(committed_counts)
```

After each `executor.map` result, close all decoded media as today, append only returned `ScannedMedia`, update `pending_counts`, inspect control, and call one private `flush_pending(*, control_only=False)` when the derived item threshold is met, control is pending, or input is exhausted. The helper must snapshot tuples/dicts into closure defaults, call `prepare_scan`/`upsert_manifest_artifact` only on the first successful flush, call `upsert_scanned_batch`, then advance committed variables only after `commit_batch` returns. On `StaleWorkerToken`, report committed counters/index only and discard the pending list.

- [ ] **Step 5: Write modular scoring red tests for aggregation and component state**

Extend `tests/test_modular_scoring.py` with parametrized fake runtimes for `score.aesthetic_domain`, Community `detect.ai`, `evidence.ocr`, and `evidence.watermark`; use five samples and `batch_size=2`. For each non-CLIP component assert:

1. Runtime is called three times, while persistence/checkpoint is committed once for five ordered `SampleScore` values.
2. Mixed fully cached and inferred inference batches preserve exact `inferred_samples`/`cached_samples`; cache hits do not invoke that batch's runtime and do not disappear when grouped with inferred results.
3. The one committed cursor has `next_index=5`, `results_prepared=True`, `component_complete=True`; repository `prepare=True` occurs exactly once across the first non-CLIP component, and later component flushes do not clear earlier component evidence.
4. A `NaN`/infinite result in a later inference batch raises `Non-finite component output` before any value from that batch enters persistence. The last checkpoint and DB rows stop at the previously committed prefix (or zero when the target was not reached); the finite guard is not moved into the repository or weakened.
5. Cooperative pause after two inference batches commits the four-sample validated prefix and resume begins at `next_index=4`; force-stopped subprocess semantics remain last-checkpoint recovery.

Current red reasons: `ModularScoringComponentService.run` calls `commit_batch` at lines 337–347 once per inference batch, and each cursor marks only that batch's state.

- [ ] **Step 6: Write the CLIP multi-shard red test before changing the reader**

Use five samples with CLIP `batch_size=2`, run `feature.clip_l14`, then run aesthetic and UFD consumers. Assert the combined CLIP checkpoint contains three ordered, unique descriptors in `feature_shards`; both consumers load all three registered shards successfully. Tamper the first shard and assert the existing `changed after its checkpoint` failure still fires.

Current red reason: cursor construction at `app/modular_scoring.py:309` stores only one `feature_shard`, while `_registered_clip_shard` at `app/modular_scoring.py:531` reads only that singular descriptor; naively batching would register only the last shard.

- [ ] **Step 7: Run modular scoring red tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_modular_scoring.py tests/test_scoring_foundations.py tests/test_scoring_numerics.py -q --basetemp=.test-tmp\task10-scoring-red -p no:cacheprovider
```

Expected before implementation: new transaction-count, pause-prefix, and multi-shard assertions fail; existing cache, OCR non-finite normalization, and shard-tamper tests remain green.

- [ ] **Step 8: Implement the minimal scoring buffer and backward-compatible shard registry**

Keep private state inside `ModularScoringComponentService.run`: ordered pending scores, primitive pending shard descriptors, pending end index, and pending/committed inferred/cached counters. `_require_finite` stays immediately after runtime output creation and before append. A private flush helper constructs one committed cursor, passes only pending scores to `ScoringRepository.persist_batch`, and clears pending state only after `commit_batch` succeeds. Set `component_complete=True` only on the terminal committed flush; preserve the existing empty-component checkpoint. Update `_registered_clip_shard` through one descriptor iterator that accepts legacy `feature_shard` and new `feature_shards`, validates primitive field types, and rejects conflicting identities exactly as today.

- [ ] **Step 9: Add deterministic 100k transaction-count coverage and a measurement-only benchmark**

Do not create 100,000 image files or invoke real models. Add a parametrized pure scheduling/count test over 100,000 logical indices and a small real-SQLite integration check proving one flush equals one `PhaseCheckpoint`/`BEGIN IMMEDIATE` commit:

```python
@pytest.mark.parametrize(
    ("total", "inference_batch", "target", "expected_writes"),
    [
        (100_000, 64, 256, 391),
        (100_000, 1, 64, 1_563),
        (100_000, 256, 64, 391),
    ],
)
def test_100k_write_batch_schedule_has_deterministic_transaction_count(...):
    ...
```

The expected counts are `ceil(total / derived_flush_size)` and assert transaction/checkpoint cardinality, not throughput. For an optional local benchmark, reuse the same fake decode/runtime and temp SQLite fixture, accept `--samples 100000`, and print actual elapsed time, rows, checkpoints, and inferred batches for baseline (flush every inference batch) versus aggregated mode. Do not add a pass/fail time threshold or record a claimed speedup until fresh hardware measurements exist.

- [ ] **Step 10: Run focused and contract verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_scanner_foundations.py tests/test_scanner_integration.py tests/test_scoring_foundations.py tests/test_scoring_numerics.py tests/test_modular_scoring.py tests/test_cf_only_clip_and_batch_ceilings.py tests/test_task_service.py tests/test_component_runs.py -q --basetemp=.test-tmp\task10-focused -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check backend/dataset_audit_studio/scanner/service.py backend/dataset_audit_studio/app/modular_scoring.py tests/test_scanner_integration.py tests/test_modular_scoring.py
git diff --check -- backend/dataset_audit_studio/scanner/service.py backend/dataset_audit_studio/app/modular_scoring.py tests/test_scanner_integration.py tests/test_modular_scoring.py
```

Acceptance requires: fewer `commit_batch` calls than inference batches for the default 100k schedules; exact final rows/counters; atomic rollback; graceful-prefix resume; forced-stop discard; parent/direct heartbeat distinction; finite-only persistence; CLIP/aesthetic/AI/OCR/watermark cache identity and evidence unchanged; `results_prepared` exactly once; terminal `component_complete`; and no public config/schema/default/dependency change.

- [ ] **Step 11: Do not commit**

**Risks and non-goals:** Larger write batches reduce transaction count but can lengthen each SQLite `BEGIN IMMEDIATE` hold and enlarge the recomputation window after force stop. The initial 256/64 targets therefore require measured reporting before retuning. This task does not bulk-rewrite `ScoringRepository`/scanner SQL, migrate checkpoints, change `TaskService`, alter model batch sizes, or guarantee a latency/throughput number.

---

### Task 11: Audit pages remount keys + Models busy (if not done in Task 3)

**Files:**
- Modify: `frontend/src/App.tsx` — add `key={selectedTaskId ?? 'none'}` to `RisksPage`, `DuplicatesPage`, `AestheticsPage` (Style already has it)

- [ ] **Step 1: Unit/presentation test or E2E assert keys exist / remount clears selection** (follow existing frontend test style)

- [ ] **Step 2: Implement keys**

- [ ] **Step 3: PASS**

- [ ] **Step 4: Do not commit**

---

### Task 12: Virtualize audit page lists (Risks first)

**Files:**
- Modify: `frontend/src/pages/RisksPage.tsx` (then Duplicates/Aesthetics/Style if time)
- Modify: `frontend/src/styles.css` if needed for virtual row positioning
- Modify: `frontend/tests/riskListLayout.test.mjs` (must keep non-overlap / document-flow rule for non-offset rows)
- Pattern reference: `frontend/src/pages/ProgressPage.tsx`, `ReviewsPage.tsx`

- [ ] **Step 1: Extend layout test** so virtual rows with offsets may use transform; stacked `.risk-row` without offsets must not regress

- [ ] **Step 2: Implement `useVirtualizer` for the risk list container**

- [ ] **Step 3: PASS unit tests + production build**

```powershell
cd frontend; npm test; npm run build
```

- [ ] **Step 4: Do not commit**

---

### Task 13: Parent verification wave + MEMORY/ROADMAP

**Owner:** parent session (not a drive-by subagent)

- [ ] Run Wave A–D focused suites after merges of task branches/worktrees
- [ ] Record results in `MEMORY.md` / `ROADMAP.md`
- [ ] Full backend pytest note including known R10.1 baseline
- [ ] Ask user before any git commit/push

---

## Spec coverage checklist

| Spec item | Task |
| --- | --- |
| Picker stderr/READY/orphan/close | 1 |
| AI missing model_id → UFD | 2 |
| loadTaskData race / SSE reopen / events / Models busy | 3 |
| Semantic threshold 0.92 | 4 |
| Export encode outside write lock | 5 |
| Encode cache + heartbeat batch | 6 |
| Style eligibility SQL | 7 |
| CF-only no CLIP + batch ceilings | 8 |
| Clustering leaf streaming | 9 |
| Scan/scoring inference-write batch separation and default-compatible derived policy | 10 Steps 1–5, 8 |
| Pause/terminate/heartbeat/resume and atomic checkpoint semantics | 10 Steps 2–5, 8, 10 |
| CLIP multi-shard registry plus aesthetic/UFD consumers | 10 Steps 6–8 |
| Aesthetic/AI/OCR/watermark cache, finite guard, results preparation/completion | 10 Steps 5, 7–10 |
| 100k SQLite transaction-count assertion without invented timing | 10 Step 9 |
| Audit remount keys | 11 |
| Virtualize audit lists | 12 |
| Verification docs | 13 |

## Self-review notes

- No intentional TBD placeholders.
- Commit steps intentionally disabled per user/public-release rules.
- Tasks 5–6 share export files — prefer sequential or one agent.
- Tasks 3 and 11 both touch frontend; Task 11 is App-only if Task 3 skips App remount (per spec §4).
- Task 10 deliberately keeps write aggregation internal: public `write_batch_size` would change scan cache/config identity and component schemas without evidence that users need that control.
- Task 10 does not alter the force-stop supervisor. Cooperative inline control flushes a validated prefix; process termination discards uncommitted memory and resumes from the last atomic checkpoint.
