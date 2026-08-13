# AI Detection Community Forensics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make new component-based tasks use Community Forensics with the approved high-recall thresholds while retaining UFD behavior for saved legacy scoring configs.

**Architecture:** `detect.ai` gains an explicit model ID and dynamically resolves its runtime asset. The Community branch scores decoded images with the pinned 440/384/ImageNet transform; the UFD branch retains the existing CLIP-feature path. Model, preprocessing, and evidence identities are selected from the explicit model ID so cached results cannot cross models.

**Tech Stack:** Python 3.11.15 project `.venv`, PyTorch, timm, safetensors, torchvision, Pydantic, Pytest.

---

### Task 1: Lock configuration and scheduling compatibility

**Files:**
- Modify: `backend/dataset_audit_studio/components/ai_detection/config.py`
- Modify: `backend/dataset_audit_studio/components/ai_detection/manifest.py`
- Modify: `backend/dataset_audit_studio/app/component_task_config.py`
- Modify: `backend/dataset_audit_studio/scoring/config.py`
- Test: `tests/test_components_api.py`
- Test: `tests/test_model_scoring.py`
- Test: `tests/test_modular_scoring.py`

- [x] Add failing assertions that a newly materialized `detect.ai` config contains `community_forensics_model_384`, `0.121558`, and `0.464626`, while `ScoringConfig.from_task_config({"scoring": {"ai": {"enabled": True}}})` retains `universal_fake_detector_head`.
- [x] Add a failing component-plan assertion that `detect.ai` requests only `community_forensics_model_384` for a new task.
- [x] Add `model_id` to the component config with the Community default, pass it through the materializer, add a dynamic manifest model resolver, and give the legacy scoring config a UFD default.
- [x] Run the three focused tests and confirm the assertions pass.

### Task 2: Add the pinned production model and runtime branches

**Files:**
- Modify: `backend/dataset_audit_studio/model_adapters/registry.json`
- Modify: `backend/dataset_audit_studio/model_adapters/types.py`
- Modify: `backend/dataset_audit_studio/components/ai_detection/runtime.py`
- Modify: `backend/dataset_audit_studio/scoring/assets.py`
- Modify: `backend/dataset_audit_studio/scoring/torch_runtime.py`
- Test: `tests/test_model_registry.py`
- Test: `tests/test_modular_scoring.py`
- Test: `tests/test_model_scoring.py`

- [x] Add failing assertions for the pinned `community_forensics_model_384` registry entry: OwensLab revision `6076002bf0d9dd37537f965ee2f06f826c333b61`, `model.safetensors`, 87,262,324 bytes, and SHA-256 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387`.
- [x] Add a failing modular-scoring test whose Community fake receives decoded `PIL.Image` objects rather than CLIP features.
- [x] Register the supported loader, implement the pinned ViT-Small 384 safetensors branch with resize-short-edge 440, center-crop 384, ImageNet normalization, and sigmoid single-logit scoring; preserve the UFD feature branch.
- [x] Select requested assets, cache identities, preprocessing versions, and evidence sources by model ID; only initialize CLIP in the legacy facade when UFD or aesthetics needs it.
- [x] Run focused tests and verify both model branches remain covered.

### Task 3: Install and verify the production asset

**Files:**
- Modify: `ROADMAP.md`
- Modify: `MEMORY.md`
- Modify: `RULES.md`

- [x] Use the registered `ModelService` download path to install the pinned file under the project model registry, not from `models/benchmarks` at runtime.
- [x] Verify the installed registry file's size, SHA-256, manifest, and runtime readiness.
- [x] Score a small read-only image batch with the production Community runtime and confirm finite probabilities and review-only evidence provenance.
- [x] Run affected Pytest modules, Ruff, and a registry/runtime smoke check; record exact results and any remaining limits in the root project documents.

### Execution evidence

- Production asset verification: `ready`, `runtime_ready=true`, size `87,262,324`, SHA-256 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387`.
- Real CPU smoke through `ModularScoringComponentService`: 6 read-only AI samples, finite probabilities `0.059891-0.960428`, 5 review candidates, all `community_forensics` and `review_only=true`, zero automatic review decisions.
- Verification: Community-focused Pytest `172 passed`; final backend Pytest `495 passed, 1 skipped in 177.11s`; `ruff check backend tests` passed.
