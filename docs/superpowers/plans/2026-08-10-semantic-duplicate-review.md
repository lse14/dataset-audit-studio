# Semantic Duplicate Review Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task in the assigned Terra/max thread. Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch subagents.

**Goal:** Generate deterministic, leaf-cluster-scoped SigLIP2 semantic duplicate candidates and expose them through the existing human duplicate-review workflow without adding automatic exclusion.

**Architecture:** Extend the existing `cluster.hierarchy` component because it already owns the verified embeddings and leaf memberships. Persist `duplicate_semantic` review-only evidence in the same scope transaction as cluster nodes, reuse the existing duplicate audit API/UI, and keep automatic export deduplication limited to exact/visual evidence.

**Tech Stack:** Python 3.11 project-local `.venv`, NumPy, FAISS, SQLAlchemy, Pydantic, Pytest, Ruff, existing React/TypeScript duplicate audit UI.

---

## File Map

- Modify: `backend/dataset_audit_studio/clustering/types.py` - add semantic member scores to the internal duplicate-group result.
- Modify: `backend/dataset_audit_studio/clustering/dedupe.py` - stable semantic group keys and strongest direct-neighbor similarity per member.
- Modify: `backend/dataset_audit_studio/components/cluster_hierarchy/config.py` - expose the configurable semantic threshold.
- Modify: `backend/dataset_audit_studio/app/component_task_config.py` - map the component threshold into the compatibility clustering config.
- Modify: `backend/dataset_audit_studio/clustering/repository.py` - persist/replace leaf-scoped semantic evidence with embedding provenance.
- Modify: `backend/dataset_audit_studio/app/modular_clustering.py` - pass threshold and verified embedding identity through the active component path.
- Modify: `backend/dataset_audit_studio/clustering/service.py` - keep the compatibility clustering runner behavior aligned.
- Modify: `tests/test_clustering_foundations.py` - algorithm, stable-key, score, and leaf-boundary unit contracts.
- Modify: `tests/test_modular_clustering.py` - production-path persistence, provenance, cleanup, review, and export behavior.
- Modify: `tests/test_clustering_service.py` - compatibility runner threshold identity and evidence cleanup behavior.
- Verify without product changes: `tests/test_duplicate_group_audit.py`, `frontend/tests/duplicateAutoSelection.test.mjs`, and the existing semantic duplicate E2E path.
- Update after fresh verification: `ROADMAP.md`, `RULES.md`, `MEMORY.md` - local internal records only; never stage or commit these files.

## Fixed Boundaries

- The default candidate threshold is `0.985`; it is configurable but uncalibrated.
- Compare only members of the same persisted leaf node.
- Write evidence only; never create an automatic `ReviewDecision`.
- Keep `exclude_exact_visual_duplicates` and `_DUPLICATE_CODES` unchanged.
- Do not add dependencies, model downloads, caption handling, source-image mutation, long-tail sampling, VLM labeling, or SAE work.
- Long-tail and SAE plans are indefinitely deferred and must not be implemented in this thread.

### Task 1: Make Semantic Groups Stable and Auditable

**Files:**
- Modify: `tests/test_clustering_foundations.py:174-211`
- Modify: `backend/dataset_audit_studio/clustering/types.py:63-69`
- Modify: `backend/dataset_audit_studio/clustering/dedupe.py:70-154`

- [ ] **Step 1: Write the failing stable-key and member-score regression**

Extend the existing duplicate-layer test with a separate focused test:

```python
def test_semantic_duplicate_groups_use_stable_ids_and_record_direct_scores() -> None:
    embeddings = np.asarray(
        [
            [1.0, 0.0],
            [0.9999, 0.01],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )

    first = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("sample-a", "sample-b", "sample-c"),
    )
    repeated = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("sample-a", "sample-b", "sample-c"),
    )
    other_scope = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("sample-x", "sample-y", "sample-z"),
    )

    assert len(first) == 1
    assert first[0].group_key == repeated[0].group_key
    assert first[0].group_key != other_scope[0].group_key
    assert first[0].member_indices == (0, 1)
    assert first[0].member_scores == pytest.approx((0.9999, 0.9999), abs=1e-3)
```

Add a validation assertion:

```python
with pytest.raises(ValueError, match="stable keys"):
    semantic_duplicate_groups(
        (0, 1),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("only-one",),
    )
```

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
. .\scripts\common.ps1
$paths = Initialize-ProjectEnvironment
$env:PYTHONPATH = 'backend'
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_clustering_foundations.py::test_semantic_duplicate_groups_use_stable_ids_and_record_direct_scores -q
```

Expected: FAIL because `stable_keys` and `member_scores` do not exist.

- [ ] **Step 3: Extend the internal result without changing exact/visual behavior**

Change `DuplicateGroup` to:

```python
@dataclass(frozen=True)
class DuplicateGroup:
    kind: str
    group_key: str
    member_indices: tuple[int, ...]
    representative_index: int
    member_scores: tuple[float | None, ...] = ()
```

In `dedupe.py`, add a stable string-key helper:

```python
def _stable_group_key(kind: str, members: tuple[str, ...]) -> str:
    payload = f"{kind}:" + "\0".join(sorted(members))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]
```

Extend `semantic_duplicate_groups()` with the optional keyword-only input:

```python
stable_keys: tuple[str, ...] | None = None,
```

Validate it against the embedding row count:

```python
if stable_keys is not None and len(stable_keys) != len(embeddings):
    raise ValueError("Semantic duplicate stable keys do not match embedding rows")
```

Retain FAISS distances and build strongest direct-neighbor scores while preserving connected-component grouping:

```python
limits, similarities, neighbors = index.range_search(matrix, threshold)
union = _UnionFind(indices)
best_scores: dict[int, float] = {}
for local_left in range(len(indices)):
    for position in range(limits[local_left], limits[local_left + 1]):
        local_right = int(neighbors[position])
        if local_right == local_left:
            continue
        left = indices[local_left]
        right = indices[local_right]
        similarity = max(-1.0, min(1.0, float(similarities[position])))
        best_scores[left] = max(best_scores.get(left, float("-inf")), similarity)
        best_scores[right] = max(best_scores.get(right, float("-inf")), similarity)
        if local_right > local_left:
            union.union(left, right)
```

Build the semantic groups explicitly so stable IDs affect only semantic keys:

```python
groups = _as_groups("semantic", union.groups(), rank)
return tuple(
    DuplicateGroup(
        kind=group.kind,
        group_key=(
            _stable_group_key(
                "semantic",
                tuple(stable_keys[index] for index in group.member_indices),
            )
            if stable_keys is not None
            else group.group_key
        ),
        member_indices=group.member_indices,
        representative_index=group.representative_index,
        member_scores=tuple(best_scores[index] for index in group.member_indices),
    )
    for group in groups
)
```

Do not change exact or pHash/colorhash grouping.

- [ ] **Step 4: Run the focused and existing duplicate algorithm tests**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_clustering_foundations.py -q
```

Expected: all tests in the module PASS.

- [ ] **Step 5: Commit the isolated algorithm change**

```powershell
git add -- backend/dataset_audit_studio/clustering/types.py backend/dataset_audit_studio/clustering/dedupe.py tests/test_clustering_foundations.py
git commit -m "feat: retain semantic duplicate similarity evidence"
```

### Task 2: Expose the Leaf Semantic Threshold

**Files:**
- Modify: `tests/test_modular_clustering.py:71-103`
- Modify: `backend/dataset_audit_studio/components/cluster_hierarchy/config.py:8-21`
- Modify: `backend/dataset_audit_studio/app/component_task_config.py:289-310`

- [ ] **Step 1: Write a failing component-materialization test**

Add `from pydantic import ValidationError` to the test imports, then add beside the existing modular clustering config helpers:

```python
def test_hierarchy_materializes_semantic_duplicate_threshold() -> None:
    components = materialize_profile("general")["components"]
    components["embedding.semantic"]["enabled"] = True
    components["cluster.hierarchy"]["enabled"] = True
    components["cluster.hierarchy"]["config"]["semantic_duplicate_threshold"] = 0.992

    materialized = ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )

    assert materialized["components"]["cluster.hierarchy"]["config"][
        "semantic_duplicate_threshold"
    ] == 0.992
    assert materialized["clustering"]["semantic_duplicate_threshold"] == 0.992

    with pytest.raises(ValidationError):
        HierarchyConfig(semantic_duplicate_threshold=0.799)
    with pytest.raises(ValidationError):
        HierarchyConfig(semantic_duplicate_threshold=1.001)
```

- [ ] **Step 2: Run the test and verify it fails**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_modular_clustering.py::test_hierarchy_materializes_semantic_duplicate_threshold -q
```

Expected: FAIL because `HierarchyConfig` forbids the unknown field or the compatibility config remains hard-coded to `0.985`.

- [ ] **Step 3: Add the one configuration field and map it**

In `HierarchyConfig` add:

```python
semantic_duplicate_threshold: float = Field(default=0.985, ge=0.8, le=1.0)
```

In `ComponentTaskConfigMaterializer._clustering_config()`, replace the hard-coded value with:

```python
"semantic_duplicate_threshold": hierarchy["semantic_duplicate_threshold"],
```

Do not add a second frontend constant or a separate enable flag. Existing component schema generation and existing frontend labels own the UI.

- [ ] **Step 4: Verify defaults, explicit values, and validation**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_modular_clustering.py::test_hierarchy_materializes_semantic_duplicate_threshold tests/test_components_api.py -q
```

Expected: PASS. In the same test, call `HierarchyConfig(semantic_duplicate_threshold=0.799)` and `HierarchyConfig(semantic_duplicate_threshold=1.001)` under `pytest.raises(ValidationError)` so both bounds are explicit and independently verified.

- [ ] **Step 5: Commit the configuration contract**

```powershell
git add -- backend/dataset_audit_studio/components/cluster_hierarchy/config.py backend/dataset_audit_studio/app/component_task_config.py tests/test_modular_clustering.py
git commit -m "feat: configure semantic duplicate candidates"
```

### Task 3: Persist Leaf-Scoped Review Evidence

**Files:**
- Modify: `tests/test_modular_clustering.py`
- Modify: `backend/dataset_audit_studio/clustering/repository.py:38-41,274-451`

- [ ] **Step 1: Write a failing repository-level production contract**

Add a modular clustering integration test using deterministic fake embeddings with these properties:

```python
rows = np.asarray(
    [
        [1.0, 0.0, 0.0],
        [0.9999, 0.01, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.9999, 0.01],
    ],
    dtype=np.float32,
)
```

Persist one explicit four-member root and two child leaves, `(0, 1)` and `(2, 3)`, in one scope by monkeypatching only `modular_clustering.hierarchical_clusters`. Use `ClusterPlanNode` values whose child `parent_key` matches the root and whose leaf keys are `test:leaf-a` and `test:leaf-b`. Run the real `embedding.semantic` and `cluster.hierarchy` component commit path. Assert:

```python
with database.read_session() as session:
    evidence = session.scalars(
        select(Evidence)
        .where(Evidence.task_id == task_id, Evidence.code == "duplicate_semantic")
        .order_by(Evidence.sample_id)
    ).all()
    decisions = session.scalars(
        select(ReviewDecision).where(ReviewDecision.task_id == task_id)
    ).all()

assert len(evidence) == 4
assert len({row.metadata_json["group_key"] for row in evidence}) == 2
assert {row.metadata_json["leaf_cluster_key"] for row in evidence} == {
    "test:leaf-a",
    "test:leaf-b",
}
assert all(row.source == "semantic_duplicate_siglip2_v1" for row in evidence)
assert all(row.review_only is True for row in evidence)
assert all(row.threshold_number == pytest.approx(0.985) for row in evidence)
assert all(0.985 <= float(row.value_number or 0.0) <= 1.0 for row in evidence)
assert decisions == []
```

Also assert every metadata record contains `model_id`, `model_sha256`, `preprocessing_version`, `embedding_identity_hash`, `hierarchy_config_hash`, `scope_kind`, `scope_id`, and `provenance`.

- [ ] **Step 2: Run the test and verify no evidence exists**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_modular_clustering.py::test_hierarchy_persists_leaf_scoped_semantic_duplicate_evidence -q
```

Expected: FAIL because the production hierarchy path writes no `duplicate_semantic` rows.

- [ ] **Step 3: Add explicit semantic evidence constants and generic embedding identity metadata**

In `clustering/repository.py` define:

```python
SEMANTIC_DUPLICATE_EVIDENCE_CODE = "duplicate_semantic"
SEMANTIC_DUPLICATE_EVIDENCE_SOURCE = "semantic_duplicate_siglip2_v1"
```

Extract the model/preprocessing identity portion of `character_consistency_metadata()` into:

```python
@staticmethod
def embedding_identity_metadata(
    shards: tuple[EmbeddingShard, ...] | list[EmbeddingShard],
) -> dict[str, object]:
    if not shards:
        raise ValueError("Embedding identity requires at least one shard")
    embedding_versions = {
        (shard.model_sha256, shard.preprocessing_version) for shard in shards
    }
    if len(embedding_versions) != 1:
        raise RuntimeError("Embedding shard identity is inconsistent")
    model_sha256, preprocessing_version = next(iter(embedding_versions))
    embedding_identity_hash = hashlib.sha256(
        json.dumps(
            {
                "model_id": SIGLIP_MODEL_ID,
                "model_sha256": model_sha256,
                "preprocessing_version": preprocessing_version,
                "shards": [shard.sha256 for shard in shards],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "model_id": SIGLIP_MODEL_ID,
        "model_sha256": model_sha256,
        "preprocessing_version": preprocessing_version,
        "embedding_identity_hash": embedding_identity_hash,
    }
```

Make `character_consistency_metadata()` compose this result and append only its character-specific algorithm fields. Preserve its current output exactly.

- [ ] **Step 4: Persist each leaf group as review-only evidence**

Import `semantic_duplicate_groups`. Extend `persist_cluster_scope()` with required semantic inputs:

```python
semantic_duplicate_threshold: float,
embedding_identity: Mapping[str, object],
```

When `prepare=True`, delete the old semantic rows in the same transaction and beside the existing cluster/character cleanup. Do this only on the first committed scope:

```python
session.execute(
    delete(Evidence).where(
        Evidence.task_id == task_id,
        Evidence.source == SEMANTIC_DUPLICATE_EVIDENCE_SOURCE,
    )
)
```

Build both local identity arrays from `scope.sample_indices`, then call a private `_persist_semantic_duplicates()` after the cluster nodes are persisted:

```python
local_sample_ids = tuple(samples[index].sample_id for index in scope.sample_indices)
local_relative_paths = tuple(
    samples[index].relative_path for index in scope.sample_indices
)

for node in nodes:
    if not node.is_leaf or len(node.sample_indices) < 2:
        continue
    groups = semantic_duplicate_groups(
        node.sample_indices,
        scope_embeddings,
        threshold=semantic_duplicate_threshold,
        rank=lambda index: (local_relative_paths[index], local_sample_ids[index]),
        stable_keys=tuple(local_sample_ids),
    )
```

Use this complete row contract for every group member:

```python
representative_sample_id = local_sample_ids[group.representative_index]
for position, local_index in enumerate(group.member_indices):
    session.add(
        Evidence(
            task_id=task_id,
            sample_id=local_sample_ids[local_index],
            code=SEMANTIC_DUPLICATE_EVIDENCE_CODE,
            source=SEMANTIC_DUPLICATE_EVIDENCE_SOURCE,
            value_json=group.group_key,
            threshold_json=semantic_duplicate_threshold,
            value_number=group.member_scores[position],
            threshold_number=semantic_duplicate_threshold,
            metadata_json={
                **embedding_identity,
                "group_key": group.group_key,
                "group_size": len(group.member_indices),
                "representative_sample_id": representative_sample_id,
                "leaf_cluster_key": node.cluster_key,
                "scope_kind": node.scope_kind,
                "scope_id": scope.scope_id,
                "hierarchy_config_hash": hierarchy_config_hash,
                "threshold": semantic_duplicate_threshold,
                "provenance": {
                    "component_id": "cluster.hierarchy",
                    "algorithm_version": SEMANTIC_DUPLICATE_EVIDENCE_SOURCE,
                },
            },
            severity="medium",
            review_only=True,
            bbox_json=None,
            algorithm_version=SEMANTIC_DUPLICATE_EVIDENCE_SOURCE,
        )
    )
```

Add the same cleanup to `prepare_empty_clusters()`. Do not delete human decisions or any other evidence source.

- [ ] **Step 5: Re-run the production-path test**

Run the command from Step 2.

Expected: PASS with two leaf-local groups, four review-only evidence rows, full provenance, and zero automatic decisions.

- [ ] **Step 6: Commit persistence and its regression**

```powershell
git add -- backend/dataset_audit_studio/clustering/repository.py tests/test_modular_clustering.py
git commit -m "feat: persist leaf semantic duplicate evidence"
```

### Task 4: Wire Active and Compatibility Clustering Runners

**Files:**
- Modify: `backend/dataset_audit_studio/app/modular_clustering.py:505-705`
- Modify: `backend/dataset_audit_studio/clustering/service.py:88-113,294-302,514-532`
- Modify: `tests/test_modular_clustering.py`
- Modify: `tests/test_clustering_service.py`

- [ ] **Step 1: Write a failing threshold-change cleanup/reuse regression**

In `tests/test_modular_clustering.py`, run a task whose first threshold creates one semantic group. Then update only:

```python
components["cluster.hierarchy"]["config"]["semantic_duplicate_threshold"] = 1.0
```

Resume the task through the real modular component flow. Assert:

```python
assert second_embedding_runtime_calls == 0
with database.read_session() as session:
    assert session.scalar(
        select(func.count())
        .select_from(Evidence)
        .where(Evidence.task_id == task_id, Evidence.code == "duplicate_semantic")
    ) == 0
```

The fake vectors must have cosine similarity below `1.0` and above the first threshold. Also assert the registered embedding artifact SHA is unchanged.

- [ ] **Step 2: Run the regression and verify it fails**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_modular_clustering.py::test_semantic_threshold_change_reuses_embeddings_and_replaces_evidence -q
```

Expected: FAIL because the runner does not pass threshold/identity to persistence or because old evidence is not replaced.

- [ ] **Step 3: Wire the active modular service**

In `_run_hierarchy()`, keep the existing empty-scope branch before generic embedding identity construction. `prepare_empty_clusters()` must still remove old semantic rows even though an empty task has no shards. Immediately after that branch, require the non-empty shard identity:

```python
embedding_identity = self.repository.embedding_identity_metadata(shards)
```

Do not call this helper unconditionally before `if not scopes:`; an empty task legitimately has no embedding shard.

When constructing `HierarchyConfig`, forward the compatibility value rather than falling back to the component default:

```python
semantic_duplicate_threshold=config.semantic_duplicate_threshold,
```

Because `component_config.model_dump()` now contains `semantic_duplicate_threshold`, the existing `hierarchy_hash` and checkpoint identity must change with the threshold. Pass both new arguments to `persist_cluster_scope()`:

```python
semantic_duplicate_threshold=component_config.semantic_duplicate_threshold,
embedding_identity=embedding_identity,
```

Do not recompute embeddings and do not add a new model request.

- [ ] **Step 4: Keep the compatibility runner aligned**

In `ClusteringConfig.hierarchy_payload()`, include `semantic_duplicate_threshold` so compatibility checkpoint identity changes when the threshold changes. In `SemanticClusterer.run()`, derive generic embedding identity from `registered_shards` only on the non-empty scope path and pass:

```python
semantic_duplicate_threshold=config.semantic_duplicate_threshold,
embedding_identity=semantic_embedding_identity,
```

to the shared repository call. Preserve existing SAE and character-consistency behavior.

- [ ] **Step 5: Verify retry, pause/resume, and compatibility tests**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_modular_clustering.py tests/test_clustering_service.py tests/test_stage_f_processes.py -q
```

Expected: PASS. Existing pause/resume tests must show no duplicate semantic rows. If they do not assert row counts, add a focused assertion rather than rewriting their setup.

- [ ] **Step 6: Commit runner wiring**

```powershell
git add -- backend/dataset_audit_studio/app/modular_clustering.py backend/dataset_audit_studio/clustering/config.py backend/dataset_audit_studio/clustering/service.py tests/test_modular_clustering.py tests/test_clustering_service.py
git commit -m "feat: run semantic dedupe with cluster hierarchy"
```

Do not stage a test file if it required no change.

### Task 5: Prove Review-Only Export Behavior

**Files:**
- Modify: `tests/test_modular_clustering.py`
- Verify unchanged: `backend/dataset_audit_studio/reviews/service.py`
- Verify unchanged: `backend/dataset_audit_studio/export_runs/eligibility.py`
- Verify unchanged: `frontend/src/pages/DuplicatesPage.tsx`

- [ ] **Step 1: Extend the production integration test through review and export**

Using a generated semantic group from Task 3, query:

```python
reviews = ReviewService(database)
audit = reviews.list_duplicate_group_audit(
    task_id,
    evidence_type="semantic_duplicate",
)
assert audit.total == 2
assert audit.pending == 4
```

Choose one non-representative sample and record a human exclusion:

```python
reviews.decide_curated_candidates(
    task_id,
    selection=CuratedReviewSelection(
        evidence_type="semantic_duplicate",
        sample_ids=(candidate_id,),
    ),
    decision=ReviewState.APPROVED_EXCLUDE,
)
```

Resolve export eligibility with `exclude_exact_visual_duplicates=False` and assert the chosen sample has `manual_exclude`. Change the same sample to `APPROVED_KEEP` and assert its reason becomes `None`.

- [ ] **Step 2: Add an explicit automatic-filter guard**

Seed only `duplicate_semantic` evidence with no human decision, resolve eligibility once with `exclude_exact_visual_duplicates=False` and once with `True`, and assert the semantic candidate remains included both times. This proves the exact/visual export switch did not silently expand.

- [ ] **Step 3: Run review and export regressions**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest tests/test_modular_clustering.py tests/test_duplicate_group_audit.py tests/test_export_runs.py -q
```

Expected: PASS. No production change to review service, review API, frontend types, duplicate page, or automatic export code should be necessary.

- [ ] **Step 4: Run existing frontend contracts without changing product UI**

```powershell
& (Join-Path $paths.Node 'npm.cmd') --prefix frontend test
& (Join-Path $paths.Node 'npm.cmd') --prefix frontend run build
```

Expected: all frontend unit tests PASS and the production build succeeds. The existing E2E contract that clicks `语义重复` must remain discoverable:

```powershell
Push-Location frontend
try {
    & (Join-Path $paths.Node 'npx.cmd') playwright test e2e/task-workflows.spec.ts --grep "duplicate" --list
} finally {
    Pop-Location
}
```

Expected: Playwright lists the duplicate workflow test. Run the focused case if its exact title is available from the list and clean its generated test output afterward.

- [ ] **Step 5: Commit the behavioral regressions**

```powershell
git add -- tests/test_modular_clustering.py
git commit -m "test: protect semantic duplicate review boundaries"
```

### Task 6: Verification and Local Records

**Files:**
- Update locally only: `ROADMAP.md`
- Update locally only: `RULES.md`
- Update locally only: `MEMORY.md`

- [ ] **Step 1: Run affected Ruff checks**

```powershell
& (Join-Path $paths.Venv 'Scripts\python.exe') -m ruff check backend/dataset_audit_studio/clustering backend/dataset_audit_studio/components/cluster_hierarchy backend/dataset_audit_studio/app/modular_clustering.py tests/test_clustering_foundations.py tests/test_modular_clustering.py tests/test_clustering_service.py
```

Expected: `All checks passed!`

- [ ] **Step 2: Run the complete backend suite with an isolated project-local temp directory**

```powershell
$testTemp = Join-Path $paths.ProjectRoot '.test-tmp\semantic-duplicate-full'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
try {
    & (Join-Path $paths.Venv 'Scripts\python.exe') -m pytest --basetemp $testTemp -p no:cacheprovider -q
    $testCode = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $testTemp) {
        Remove-Item -LiteralPath $testTemp -Recurse -Force
    }
}
if ($testCode -ne 0) { exit $testCode }
```

Expected: full backend suite PASS with only the already documented Windows symlink skip, if still present.

- [ ] **Step 3: Inspect the final diff and provenance contract**

```powershell
git diff --check
git status --short
git diff -- backend/dataset_audit_studio/clustering backend/dataset_audit_studio/components/cluster_hierarchy backend/dataset_audit_studio/app tests
```

Confirm there is no change to `_DUPLICATE_CODES`, no model/dependency addition, no automatic decision writer, and no source-data path mutation.

- [ ] **Step 4: Update local internal records after verification**

Mark the semantic item `[x]` only after all required verification returns exit code 0. Record exact test counts and any unverified E2E limitation. Keep cluster-target/long-tail and SAE marked `[?] 无限期暂缓`.

Do not stage or commit `ROADMAP.md`, `RULES.md`, `MEMORY.md`, or `docs/superpowers/**`; the repository's public-release rules explicitly exclude internal plans and memory.

- [ ] **Step 5: Report completion in the Terra/max thread**

Report changed source/test files, exact verification commands/results, the uncalibrated-threshold limitation, and confirmation that long-tail/SAE were untouched. Do not push unless the user separately authorizes it.
