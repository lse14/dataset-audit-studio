# Memory

## Confirmed Request

- Restore `start_webui.bat`.
- The user approved thorough removal of the obsolete database and its files because they are no longer needed.
- Review other scripts for the same class of stale-state or missing-build issue.

## Root Cause Evidence

- `data/app.db` reported Alembic revision `0006_export_runs`.
- The current source only contains `0001_clean_slate_schema` as the migration head.
- Application startup calls `upgrade_database()` before serving requests, so Alembic rejected the obsolete revision and Uvicorn exited.
- The old database business tables contained zero rows during the read-only probe.
- `frontend/dist/index.html` was absent; the backend intentionally does not mount frontend routes when the directory is absent.

## Scope Boundaries

- Cleanup targets are limited to obsolete state under `data/`: database files, task artifacts, and database backups.
- Do not remove `data/README.md`, source datasets, `models/`, project source, runtime locks, or project-local runtime executables.

## Completed Cleanup

- Removed `data/app.db`, `data/app.db-shm`, `data/app.db-wal`, `data/backups/`, and `data/tasks/` after verifying no listener on port 7865.
- Preserved `data/README.md`.

## Script Review

- No launch, setup, stop, test, or support script refers to the retired revision directly.
- `setup.ps1` and `test.ps1` build the frontend; `start.ps1` does not preflight `frontend/dist/index.html`.
- The missing-dist preflight is an identified hardening follow-up and is not changed in this recovery scope.

## Stop Script Follow-up

- A real stop attempt reached the verified listener (PID 28804) but `Stop-Process` raised a Windows `NullReferenceException` before termination.
- The nested helper-array issue is fixed and covered by `tests/test_stop_webui_script.py`.
- `taskkill /PID 28804 /T /F` successfully terminated that verified WebUI tree; the script now has a tested fallback for this case.

## Final Verification

- The fallback and worker-descendant stop paths pass in `tests/test_stop_webui_script.py`.
- `start_webui.bat` started successfully, the health endpoint and root page returned HTTP 200, and production Playwright smoke testing rendered the task page and new-task dialog with no console errors.
- A final `stop_webui.ps1` run returned exit code 0 and port 7865 had no listener afterward.
- Full backend Pytest, isolation checks, Ruff, component-boundary checks, dependency report validation, frontend unit tests, and production build passed.
- All 41 project E2E cases reported `ok`, but the Playwright runner did not exit before the external command limit. No Vite, Playwright, or WebUI process remained after cancellation.

## UI Explanation Diagnostic

- The task-configuration interface no longer renders per-option help badges, hover/click explanations, component-purpose text, profile descriptions, or the former guide route by design in the checked-in frontend.
- Evidence: `frontend/tests/ui1Presentation.test.mjs` asserts that `SchemaField.tsx` contains no `FieldHelp`, `.field-help`, `schema.description`, or `FIELD_HELP`; `frontend/e2e/task7-profile-workspace.spec.ts` asserts no visible configuration explanations and zero `.field-help` nodes.
- The API/frontend types still permit schema/profile `description` values, but `frontend/src/components/SchemaField.tsx` renders only labels and controls. `ComponentConfigEditor.tsx` likewise does not render a component description.
- A production Playwright check reproduced the current behavior with zero help nodes and zero Info/CircleHelp icons after opening a task configuration component; no browser console issues occurred.
- The repository history available locally begins at the current source state, so the exact historical removal revision is unavailable.

## Confirmed Help Restoration

- The user explicitly requested restoration of the prior task-configuration `i` badges and click-to-open explanatory popup behavior on 2026-08-08.
- Scope: task creation/configuration only. Do not restore the removed guide page or permanent explanatory paragraphs.
- Design: a small shared, keyboard-accessible help button; schema description first; curated or generated fallback text for every field; no backend API or dependency change.

## Completed Help Restoration

- Added `frontend/src/components/FieldHelp.tsx` for the `i` button and click-to-open tooltip interaction. It closes on outside pointer input, a second click, or Escape; Escape propagation is stopped so the parent task dialog remains open.
- Added `frontend/src/taskConfigHelp.ts` for component purposes, common schema-field explanations, fixed task-setting text, and a numeric/enum/default fallback for unlisted schema fields.
- Wired help controls into task presets, dataset profile, task name, directories, runtime settings, profile setting, each component summary, and all schema-rendered configuration fields.
- Playwright production validation passed at desktop and `390x844` mobile viewports. There were no relevant console messages, and all tested tooltips remained in the viewport through measured positioning.
- Frontend production build and all 125 unit tests passed. The E2E file parses with the updated click/open/Escape expectations; the existing test's fixed repository screenshot side effect was not run.

## Zero-weight Style Model Request

- The user requested that a style model with weight `0` be disabled.
- Intended scope: omit that model from preflight/download/runtime loading and preserve the existing feature contract with neutral values; do not change the required top-level weight sum.
- The dynamic resolver, conditional runtime loading, neutral feature columns, and zero-weight algorithm gates are implemented; full backend Ruff and Pytest passed with one existing skip. Generated test temporary directories were removed.

## Native Path Picker Request

- The user requested compact path buttons that open Windows' native selection windows instead of an in-page directory comparison/list.
- Task source/output fields and repeat-export output use the native folder picker; local replacement-model import uses the native `.safetensors` file picker.
- The backend exposes `/api/filesystem/select-directory` and `/api/filesystem/select-file`; both preserve cancellation and return validated absolute paths. The existing directory-listing endpoint remains available only for protocol compatibility and is no longer rendered by the UI.
- Picker calls are guarded against duplicate clicks; picker-busy states disable the related input and model-dialog cancel/submit controls, and errors use the existing notice/form-error paths.
- Verification on 2026-08-08: frontend unit tests `126/126`, production build, focused repeat-export E2E `8/8`, full backend Pytest with one expected skip, and a Playwright mock smoke flow for task source/output plus model file backfill. No unhandled API requests or browser console warnings/errors were observed.
- The dev server and project-local test processes were stopped after verification; temporary `native-picker-*` directories and generated `.last-run.json` were removed.
- Broader E2E note: Playwright still hangs during runner shutdown in this desktop environment. An 11-case task-workflow run reached its final case without reporting a failure and the focused help flow rendered expected screenshots, but the commands did not return a final exit code before their outer limits; do not count those runs as full passes.

## Windows 11 Common Item Dialog Confirmation

- The user clarified that the picker must be the Windows 11 Explorer-style window with Home/Desktop/pinned locations/This PC navigation, not `FolderBrowserDialog`.
- `scripts/select_directory.ps1` now uses the COM `FileOpenDialog` with an explicit `IFileDialog` vtable and `FOS_PICKFOLDERS` for directories; model files use the same dialog with `.safetensors` filters.
- The explicit `Show` declaration is intentional: inheriting `IModalWindow` caused `GetOptions` to map to the wrong vtable slot under Windows PowerShell and produced `E_INVALIDARG` in a real smoke probe.
- On Windows 11 Pro build 26200, directory and file scripts both opened and cancelled cleanly (exit code 0, `CANCELLED`, empty stderr). No old picker API remains in scripts or backend code.

## Risk Evidence Layout Fix

- The reported garbled-looking risk evidence was caused by all `.risk-row` elements being included in an absolute-positioned virtual-list selector, so multiple filenames were painted on top of one another. It was not a character-encoding or database-path issue.
- `frontend/src/styles.css` now leaves `.risk-row` in normal document flow while retaining the existing virtual-list positioning for `.event-row`, `.sae-row`, and `.cluster-row`.
- `frontend/tests/riskListLayout.test.mjs` records the regression. It failed before the CSS change and passed afterward; the complete frontend unit suite passed `127/127`, production build passed, and a two-row mocked Playwright run confirmed non-overlapping layout plus detail opening with no browser errors.

## Duplicate Audit Batch Exclusion Selection

- The user confirmed on 2026-08-09 that the duplicate-review page's current "select all" behavior is unsuitable because selecting every group member triggers the existing safeguard against excluding an entire group.
- Confirmed behavior: checking the page selection control selects every eligible member except one per displayed duplicate group, so the user can then use the existing batch-exclude action.
- Representative rule: retain the largest actual pixel area (`width * height`); on equal area, retain the lexicographically earliest relative path, then sample ID for a total stable order.
- Scope: extend the existing duplicate-audit read model with the pixel area required by the frontend selection helper. Do not add dependencies, bypass the confirmation dialog, alter historic decisions, or change the existing at-least-one-retained guard.

## Completed Duplicate Audit Batch Exclusion Selection

- The duplicate-audit API now derives and returns `pixel_area` from the same canonical width and height used for the existing resolution tiers.
- `frontend/src/duplicateSelection.ts` selects eligible non-representatives only. It keeps the largest area; equal areas resolve by relative path and then sample ID, while unknown dimensions rank below known areas.
- The checkbox now reads `本页自动选择可排除成员`; it only prepares the existing batch decision, which still uses the confirmation dialog and all-excluded protection.
- Verification completed on 2026-08-09: focused backend Pytest and Ruff passed; frontend unit tests passed `131/131`; the production build passed; and a mocked Playwright flow passed at desktop plus `390x844`, including the expected exclusion payload and a no-horizontal-overflow assertion.

## Confirmed Style Audit Default Tuning

- The user confirmed on 2026-08-09 that the tested `E:\Desktop\10_6suan` style-audit parameters should become defaults for new task configurations.
- Confirmed defaults: `max_iterations=2`, `outlier_sigma=1.3`, `minimum_style_score=94.5`, LSNet/Gram/DINO weights `0.9/0.1/0.0`, and Gram average/centroid weights `0.8/0.2`.
- Scope: defaults only; do not mutate persisted task configurations or source data.

## Completed Style Audit Default Tuning

- `StyleConfig` now defaults to the confirmed tuned values; new task profiles and component schemas derive their values from this model.
- Existing task configurations retain their explicit stored values because task parsing validates supplied values rather than rewriting them.
- Verification on 2026-08-09: the new defaults regression completed a red-green cycle; `tests/test_style_analysis.py` passed `15/15`, `tests/test_components_api.py` passed `5/5`, and focused Ruff passed.

## Multi-scope Style Outlier Evaluation

- The user supplied `E:\Desktop\画风离群测试` on 2026-08-10 for a broader read-only parameter evaluation.
- Its 12 immediate subdirectories are independent artist-style scopes. Each contains 11 filenames with `6suan`, used as injected different-style target samples; the remaining images are non-targets.
- The acceptance constraint is aggregate non-target false-positive rate no greater than 10%; all three model weights may be zero individually, provided the existing weight-sum validation remains valid.
- This is an offline tuning run only. Do not modify the current new-task defaults unless the user explicitly asks after seeing the results.

## Completed Multi-scope Style Outlier Evaluation

- On 2026-08-10, all 997 images in `E:\Desktop\画风离群测试` decoded successfully with the project-local Pillow runtime. The 12 immediate child directories were evaluated independently, producing 132 filename-labelled `6suan` targets and 865 non-targets.
- The existing defaults produced `88 TP / 129 FP / 44 FN` (`14.913%` aggregate non-target false-positive rate), which exceeds the requested 10% ceiling on this test set.
- About 191,328 parameter evaluations covered model-weight boundaries, broad random combinations, LSNet+DINO local sampling, Gram-related sampling, and two LSNet+DINO grids. The best aggregate-constrained stable configuration was `max_iterations=3`, `outlier_sigma=0.522`, `minimum_style_score=92.07`, LSNet/Gram/DINO weights `0.892/0.0/0.108`, and Gram internal weights `0.8/0.2`.
- A fresh raw-feature run with the official `analyze_artist_scope()` verified every scope against the pairwise search evaluator. Final aggregate result: `108 TP / 86 FP / 24 FN`, `86/865 = 9.942%` false-positive rate, and `108/132 = 81.818%` recall.
- The 10% result is aggregate only. Several individual artist scopes exceed 10% false positives under this same global configuration. Do not claim a per-scope 10% guarantee without a separately confirmed criterion and retuning.
- The user explicitly authorized applying the tested aggregate-constrained configuration as the new-task default on 2026-08-10. `StyleConfig` now defaults to `max_iterations=3`, `outlier_sigma=0.522`, `minimum_style_score=92.07`, LSNet/Gram/DINO `0.892/0.0/0.108`, and Gram internal `0.8/0.2`.
- Existing saved task configurations retain explicit values. Default runtime model resolution now requests only LSNet and DINO; VGG remains skipped while Gram has zero weight.
- Verification after the update: the targeted default regression first failed against the old values, then `tests/test_style_analysis.py`, `tests/test_style_service.py`, and `tests/test_components_api.py` passed `23` tests; focused Ruff also passed.

## WebP Original Media MIME Fallback

- The user confirmed the minimal fix on 2026-08-09: when `mimetypes.guess_type()` returns an empty value for a `.webp` media path, use `image/webp`; do not relax path containment, scanned-source identity checks, or the existing `image/*` rejection.
- The project-local Python runtime reproduced the missing mapping (`mimetypes.guess_type('example.webp')[0]` produced no value), and the new API regression test reproduced the prior HTTP 409.
- `WorkspaceFileAccess.media()` now applies the fallback only for an empty inferred MIME and a `.webp` suffix. The focused media test module and targeted Ruff check passed after the change.

## 角色预设子文件夹一致性请求

- 用户要求修复角色预设，使每个一级子文件夹都能检查是否混入无关角色，并可在审核后排除。
- 现状根因：`character_concept` 默认关闭 `embedding.semantic` 与 `cluster.hierarchy`；即使手动启用，兼容层还会把 `concept` 改成要求完整画风证据的 `artist`，导致无画风证据的角色任务在 SigLIP2 推理前失败。
- 安全边界采用“自动生成候选、人工确认后排除”：不自动改变导出资格或源文件；SigLIP2 视觉语义结果不得描述为严格角色身份保证。
- 第一阶段已完成：角色预设强制启用 SigLIP2 与聚类，运行时保留 `concept`，按扫描所得一级子文件夹 scope 分组；画师与通用预设的默认值和手动高级开启能力不变。
- 第一阶段验证：新增测试先在旧行为上失败，修复后连同预设、组件、模块化聚类和工作区契约共 27 项通过。

## Completed Character Folder Consistency Review

- `character_concept` now owns and forces `embedding.semantic`, `cluster.hierarchy`, and hierarchy `scope_mode=concept` in both the profile API contract and server-side task materialization. Artist/general profiles retain their prior optional advanced behavior.
- Each first-level folder is analyzed independently. Folders with fewer than four samples produce no automatic role guess; qualifying folders use an iterative SigLIP2 core and a core-centroid threshold to emit `character_role_outlier` review-only evidence.
- Evidence provenance includes the actual embedding model SHA, preprocessing version, embedding identity hash, explicit detector parameters and hash, hierarchy config hash, scope/core sizes, and measured similarities. Detector config changes invalidate the hierarchy checkpoint identity.
- Role candidates use the existing risk-review overlay. `approved_exclude` produces `manual_exclude` in export eligibility, while a later `approved_keep` restores eligibility. No automatic decision or source-file mutation is performed.
- Evidence cleanup is chunked to avoid SQLite variable limits. Multi-folder, tiny-folder, profile-lock, provenance, folder-filter, review/export, and non-character regressions are covered.
- Final verification on 2026-08-10: full backend Pytest reached 100% with one expected skip; full Ruff passed; frontend unit tests passed `131/131`; the production TypeScript/Vite build passed.

## Confirmed Technical Screening Default Optimization

- The user confirmed on 2026-08-10 that all three built-in profiles should use one shared, calibrated technical-quality baseline rather than unverified profile-specific sensitivity tiers.
- Confirmed defaults for new tasks: RGB entropy `2.5`, black ratio `0.90`, white ratio `0.90`, Laplacian variance `16.0`, high-frequency ratio `0.32`, border ratio `0.03`, blockiness `0.35`, and luminance standard deviation `10.0`.
- Border semantics are confirmed as continuous same-color opposing strips with at least `99.5%` scanline coverage, at least `0.5%` side depth, and at least `0.60` coverage drop toward the image interior. Single edges and natural dark/bright backgrounds must not trigger.
- The read-only calibration set `E:\Desktop\画风离群测试` contains 997 decodable images. The current outer-ring algorithm hit 57 black-border and 229 white-border cases; the candidate paired-strip algorithm hit 18 visually confirmed real margins, bars, or frames.
- Other candidate thresholds produced zero hits on the 997-image base set. The observed extrema were entropy `2.764`, black ratio `0.800`, white ratio `0.793`, Laplacian variance `24.925`, high-frequency ratio `0.311`, blockiness `0.302`, and luminance standard deviation `16.156`.
- Implementation must advance technical evidence to `technical_metrics_v2`, use that version consistently as source and metadata, and delete all prior `technical_metrics_v%` evidence for the rescanned sample to prevent duplicates.
- Existing saved task thresholds and calibration images remain unchanged. The external calibration directory must not be required by automated tests.

## Completed Technical Screening Default Optimization

- `MetricThresholds` now supplies the confirmed shared defaults to all new built-in profile configurations: `2.5/0.90/0.90/16.0/0.32/0.03/0.35/10.0`.
- `technical_metrics_v2` replaces outer-ring border coverage with same-color continuous opposing strips: at least `99.5%` scanline coverage, at least `0.5%` side depth, and at least `0.60` drop at the immediate interior boundary. The evidence source and metadata version now agree.
- Rescanning deletes `scanner` evidence and every `technical_metrics_v%` evidence row for the sample before v2 output is inserted; nontechnical evidence remains intact.
- TDD verification on 2026-08-10: the new profile-default, paired-border, and historical-evidence regressions all failed against the preceding implementation, then passed. Focused Pytest reported `25 passed, 1 skipped`; affected Ruff passed; final full backend Pytest reported `492 passed, 1 skipped in 175.33s`. The only skip is the existing Windows directory-symlink privilege limitation.
- Product-path calibration after implementation: v2 at the actual `metrics_max_side=1024` detected 10 of 997 read-only base images. Visual spot checks showed continuous black bars/frames or white canvas margins. A 4096-only exploration returned 8 hits, so the earlier 18-candidate exploration is not used as a verified result.
- On 36 stratified base samples, in-memory paired bars were detected at black 2% `30/36`, black 5% `32/36`, white 2% `29/36`, and white 5% `27/36`. No source image, database, or saved task configuration was changed.

## 角色一致性参数交叉验证（2026-08-10）

- 用户提供 `E:\Desktop\角色测试`，并确认 9 个一级子文件夹各自是单一角色数据集，可用于交叉验证。
- 只读提取了 859 张 `.webp` 的 SigLIP2 So400m NaFlex 图像向量。原始目录作为干净单角色范围；对每个目标角色/异角色目录对分别在内存注入 1 张或 2 张异角色图，共 72 个交叉验证对，未写入外部数据。
- 当前角色检测参数 `4/1.5/3`（最小范围/sigma/最大轮数）在干净目录误报 `249/859=28.987%`。
- 在合计误报率 `<=10%` 下，当前实现最多 3 轮的建议候选是 `minimum_scope_size=4`、`outlier_sigma=2.04`、`max_iterations=2`：干净误报 `74/859=8.615%`；单异角色注入召回 `86.111%`；双异角色注入召回 `84.722%`；平均召回 `85.417%`。
- 该候选的最坏单目录误报为 `9/69=13.043%`，因此只能宣称合计误报不超过 10%，不能宣称每个目录都不超过 10%。
- 探索性 4 轮候选 `sigma=2.115` 为 `81/859=9.430%` 误报、单/双注入召回均 `86.111%`，但需要突破当前算法的 3 轮上限，且收益有限，未应用。
- 同角色和异角色 SigLIP2 相似度分布重叠，推荐参数仍有 `10/72` 单注入对未检出；SigLIP2 只能提供审核候选，不能保证严格角色身份。
- 用户随后确认应用该候选。角色默认常量现为 `minimum_scope_size=4`、`outlier_sigma=2.04`、`max_iterations=2`；它用于新的角色层级运行，已有任务只有重新运行该阶段时才按新配置重新生成候选。源数据和既有审核决定未改写。
- 配置回归测试先在旧 `1.5/3` 值上失败；更新后角色一致性与模块化聚类聚焦测试 `12 passed`，后端全量 Pytest 通过（1 个预期跳过），全仓 Ruff 通过。

## Confirmed AI Detection Calibration

- The user confirmed on 2026-08-10 that `D:\flux-aki\ComfyUI-aki-v1.6\output` contains 705 AI-generated images and `E:\Desktop\画风离群测试` contains 997 non-AI images; both directories are read-only evaluation data.
- Existing UniversalFakeDetect was run with the pinned project-local CLIP ViT-L/14 and produced AUC `0.512304` under those labels. At the current `0.35` candidate threshold it achieved `229/705` true positives and `251/997` false positives; it cannot be repaired by a threshold-only default change.
- The user approved downloading and comparing the predeclared Community Forensics `commfor_model_384` candidate. Higher false-positive operating points are acceptable because the AI-positive set contains newer, visually difficult models; report the full tradeoff rather than assuming a fixed 10% ceiling.
- Do not replace a product default unless a stratified holdout/cross-validation comparison shows the candidate clearly outperforms UFD. Do not change saved tasks, source images, or human decisions.
- Community Forensics passed the five-fold held-out comparison on 2026-08-10: AUC `0.845551` versus UFD `0.512304`; at approximately 10%/20%/30% false-positive rate it recalled `61.418%`/`74.610%`/`82.411%` of the 705 AI-positive images. The file passed the pinned size/SHA-256 preflight and remains under the project-local `models/benchmarks` directory.
- Proposed operational points await user confirmation: balanced-review candidate threshold `0.175102` (held-out `71.206%` recall, `16.349%` false-positive rate) with strong-reference threshold `0.464626` (about 10% FPR), or a higher-recall approximately 20% FPR candidate threshold. Product integration has not started.

## Confirmed Community Forensics Integration

- 用户确认新任务采用 Community Forensics 的高召回审核档：`candidate_threshold=0.121558`、`reference_threshold=0.464626`。
- 新组件配置须显式保存 Community 模型；旧的直接 `scoring.ai` 配置若没有 `model_id`，必须继续使用 UniversalFakeDetect，避免已保存任务在重跑时静默切换模型。
- Community 命中仅进入既有人工审核队列；不得自动排除、修改源图、已保存任务或人工决定。

## Completed Community Forensics Integration

- 新组件任务使用 `community_forensics_model_384`；旧直接评分配置在缺少 `model_id` 时继续使用 `universal_fake_detector_head`。模型、预处理、缓存身份和证据来源按显式模型 ID 分离。
- 固定生产模型已通过 `ModelService` 安装在项目内注册表路径，安装清单记录 `community_forensics_vit_small_384_v1`、固定 revision、`87,262,324` 字节和 SHA-256 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387`；服务复核结果为 `ready`、`runtime_ready=true`。
- 对 6 张只读 AI 样本的真实 CPU 运行时冒烟产生有限概率 `0.059891-0.960428`，其中 5 张进入审核候选。持久化证据的来源为 `community_forensics`、预处理版本为 `commfor-resize440-center-crop384-imagenet-v1`、全部 `review_only=true`，没有自动审核决定或源图改动。
- 最终验证：Community 相关聚焦测试 `172 passed`，完整后端 Pytest `495 passed, 1 skipped`，全量 Ruff 通过。新增注册表资产还暴露了健康/API 测试中旧的 12 项硬编码；测试现从 `DEFAULT_REGISTRY` 派生数量和文件总数。
- 高召回阈值的误报成本保持为此前分层留出评估的已知限制；模型输出只能提供人工审核候选，不能作为自动排除或通用真伪结论。

## Public Release

- 用户确认将 `lse14/dataset-audit-studio` 作为公开仓库重新发布，并要求不上传本地数据、模型、运行时、测试产物、内部路线图、记忆、历史或 superpowers 文档。
- 2026-08-10 创建了单一根提交 `Initial public release`，并仅推送了 `main`；本地旧版本仍由 `backup/pre-public-release-20260810` 保留，未推送。
- 远端 API 与无认证 Git 读取均确认仓库为公开、默认分支为 `main`，且远端提交与本地树哈希完全一致。
- 公开树中仅保留 `data/README.md`、`models/README.md` 作为本地目录占位说明；`docs/` 仅包含第三方依赖和许可合规文件。临时 GitHub 认证未写入仓库、远端 URL 或 Git 配置。

## README Technical Report Positioning

- 用户要求公开 README 说明本项目参考 Krea 2 官方技术报告，并列出已实现和待完善事项。
- 已核验官方报告链接为 `https://www.krea.ai/blog/krea-2-technical-report`，页面标题为 `Krea 2 Technical Report - Krea`。报告的数据治理原则包括保持多样性、处理重复/过度代表样本、无法可靠描述的样本、偏差或伪影，以及低分辨率下难以稳定建模的复杂内容。
- README 将此定位为独立本地工具对公开原则的参考，不复刻 Krea 的训练系统、数据集或模型，也不暗示官方关联或背书。
- 待完善部分明确标为项目后续规划，不表示已交付能力或 Krea 官方路线图。
- 文档提交 `d536f36` 已推送；公开原始 README 返回 HTTP 200，内容与本地提交一致。认证头只用于单次推送，Git 配置中没有持久化 `http.*.extraHeader`。

## Confirmed LoRA and SAE Scope

- 用户确认本项目目标是筛选 LoRA 微调训练集，不是构建基础模型预训练语料；caption 由独立脚本处理，不应作为本项目 README 的能力或待办。
- 已有 SAE 组件在语义嵌入上训练稀疏自编码器，并输出激活、阈值、top indices 和代表样本；它不是已完成的伪影或长尾概念检测器。
- 公开 README 的仅有两项待完善功能是：可解释的基于 SAE 伪影检测，以及基于 SAE 激活和覆盖度的概念长尾检测。两者都只能生成带溯源的人工审核候选，不得自动排除或删除素材。
- README 修正已在提交 `2cc3a1c` 推送到公开 `main`；公开原始 README 的 HTTP 200 验证确认该范围和两项 SAE 规划均已发布。

## Confirmed Semantic Duplicate Review Integration

- 用户于 2026-08-10 明确要求接入语义去重，并要求把实施企划交给新的 Terra/max 对话执行。
- 确认方案是：仅在现有 `cluster.hierarchy` 的每个 leaf 内运行 SigLIP2 相似搜索，持久化 `duplicate_semantic` review-only evidence，并复用已存在的重复审核 API/UI。
- 当前 `0.985` 只能作为可配置、未校准的候选阈值；项目没有相关真值集，因此不得自动排除或宣称已确认重复。
- stable group key 必须基于 sample ID 而不是 scope 内整数索引；每个成员须记录最高直接相似度和完整 embedding、阈值、叶簇及 hierarchy provenance。
- 现有 `exclude_exact_visual_duplicates` 必须保持只处理 exact/visual。语义候选只有经人工 `approved_exclude` 后才通过通用人工覆盖层形成 `manual_exclude`；`approved_keep` 可恢复资格。
- 本事项不增加模型、依赖、caption、长尾配额、VLM 聚类命名或 SAE 修改，不改写源图片和既有人工决定。

## Indefinitely Deferred Cluster-target and SAE Plans

- 用户要求直接优化 2026-08-10 的两份新企划，不恢复已在公开上传前清理的旧 R10.2 文件；两项继续为 `[?]` 无限期暂缓，无日期、执行人或估时。
- 簇语义/目标选集企划固定为 `层次簇 -> VLM 批量解释代表样本 -> 人工簇复核 -> 用户目标覆盖 -> 确定性建议 -> 人工确认 -> broad/proposed/approved/export 覆盖复验 -> digest-bound 导出快照`。
- 目标按 `scope -> dimension -> label` 配置，artist/character 按一级目录、general 按全局，不统计分辨率或跨 scope 借样本；支持 `min/max count/share` 与 required/preferred，不可满足时报告 `unmet`。
- 平方根配额只在用户仅给总预算时分配显式目标后的余量；它不能代替或覆盖用户定义目标。不实现 caption/PageRank/Wikidata，不声称基础模型级世界知识覆盖。
- SAE 企划固定为 `跨任务稳定性验证 -> VLM 批量解释 feature top-k -> 人工 feature 决策 -> 确定性逐图 candidate -> 人工 sample 决策`；纯人工只作为 VLM 失败 fallback，不再是并列未决架构。
- SAE annotation、feature decision、sample candidate、sample decision 四层独立并绑定 SAE/top-k/VLM/prompt/input provenance；feature 批准不自动排除图片，只有 sample 级 active human overlay 可影响新导出。
- 两项只有用户分别明确恢复后，才能重新 brainstorming 并逐项授权；任一企划获授权不得自动启动另一企划。

## README Evidence-Loop Revision (2026-08-10)

- 用户要求根据 Krea 2 报告对照结论更新公开 README。
- README 现在明确：项目面向 LoRA 微调数据集，产品形态是“证据采集 + 人工复核 + 安全导出”，不是 Krea 式大规模预训练数据治理管线；caption 仍由独立脚本处理。
- README 将任务内语义重复审核、分辨率感知技术/OCR 策略列为近期待完善闭环；聚类语义审核、跨任务长尾和 SAE 伪影/概念长尾检测改为共享语料充足后再评估。
- 依据源码核验：生产重复证据仅接入 exact/visual；语义去重函数尚未接入；FAISS 层级聚类已有代表样本但簇标签为空；SAE 仅输出激活、阈值和 top indices。
- 本次仅修改 `README.md` 和本地未跟踪记录文件；未修改源码、依赖、数据、模型或公开发布排除规则。
- README 提交 `612d5ac` 已推送到公开 `main`；远端提交与本地 HEAD 一致，公开原始 README 返回 HTTP 200。推送使用一次性代理配置，未持久化 Git 代理或认证头。

## README Wording Simplification (2026-08-10)

- 用户认为上一版 README 像分析报告，不适合使用者阅读。
- README 改为直接说明工具用途、已实现功能、先做事项和数据积累后再做事项；删除“原语、闭环、门控、共享语料”等报告式表述。
- 事实边界保持不变：面向 LoRA、caption 不在范围内、人工决定才影响大多数筛选结果、SAE 尚不是伪影检测器。
- 文风修正作为 `6dde1d8` 推送到公开 `main`；公开原始 README 返回 HTTP 200，本地与远端 HEAD 一致。

## Confirmed EXIF and OCR Hotfix

- 用户于 2026-08-11 确认修复两个生产问题，并要求在验证后直接推送公开 `main`，再制作供最终用户覆盖安装目录的 ZIP 热补丁。
- EXIF 根因证据：`scanner/media.py` 将标签 274 直接 `int()` 转换；带 `Orientation=0` 的临时 JPEG 实际解码为 `0`，而 `samples` 约束只允许 `NULL` 或 `1..8`，SQLite 返回 `CHECK constraint failed: ck_samples_exif_orientation_valid`。
- OCR 根因证据：项目锁定的 Transformers `PPOCRV5ServerRecImageProcessor.post_process_text_recognition()` 删除 blank token 后对剩余 `preds_prob` 求均值；全部为 blank 时结果为 `NaN`。项目 OCR runtime 直接写入该 `score`，模块化评分有限值守卫随后以 `result[0].ocr.regions[20].recognition_score` 终止任务。
- 已确认行为：越界 EXIF 规范为 `None`；OCR 非有限识别置信度视为未识别，保留检测区域但写入空文本和 `0.0`；不移除全局有限值守卫或放宽数据库约束。
- EXIF 修复完成：范围外整数在 `scanner/media.py` 的读取边界改为 `None`；新增 `0`、`9` 回归后，扫描媒体测试为 `7 passed`。
- OCR 修复完成：识别运行时在写入区域前检查分数有限性；`NaN`/无穷分数会清空文本并写入 `0.0`。针对空文本 `NaN` 和含文本无穷分数的红绿回归完成；数值测试 `11 passed`、模块化评分测试 `9 passed`、受影响文件 Ruff 通过。
- 发布方案：版本升为 `0.1.1`，同步 `pyproject.toml`、`uv.lock` 和后端版本常量；推送 `main` 后生成仅含根相对生产覆盖文件、说明和 SHA-256 的 ZIP。数据库、模型、运行时、数据集、缓存、测试和内部文档不进入热补丁。
- 当前发布验证：`uv lock --locked`、全仓 Ruff、组件边界和第三方报告均通过；前端单测 `131 passed`、生产构建、E2E `43 passed` 均通过。完整后端 Pytest 是 `505 passed, 1 skipped, 1 failed`，唯一失败为未修改的 `test_r10_1_contract.py` 对 general profile `embedding.semantic.enabled=False` 的旧期望，而当前产品配置为 `True`。用户确认本热修复不处理该独立默认值。
- 发布完成：公开 `main` 的热修复提交为 `ea07a38336ba170e38ed0f9dd30569bfdf1599f8`，远端引用已核对一致。最终用户覆盖安装包为 `E:\Desktop\dataset-audit-studio-hotfix-0.1.1.zip`，校验文件为 `E:\Desktop\dataset-audit-studio-hotfix-0.1.1.zip.sha256`，SHA-256 为 `79d2e1467e1419239767ae6435811ca0fa783072348a67fb5cbc311a40402a86`。包内五个生产/版本文件均逐字节匹配已推送提交，另含根目录安装说明；不含数据、模型、运行时、数据库、缓存、测试或内部文档。
- 用户于 2026-08-11 确认统一图像格式导出：在现有复制导出页保留默认“保持原格式”，并增加 JPEG、PNG、WebP 三种转换选项；JPEG 的透明区域铺白，PNG/WebP 保留透明。固定质量为 JPEG/WebP `95`、PNG 无损，不新增数据库迁移、依赖或质量控制；旧导出运行缺少字段时继续原格式复制。实现位于隔离分支 `codex/unified-export-format`，不得直接修改 `main`。
- 隔离分支基线：项目内 `.venv` 已可导入 Pillow `12.3.0` 与 Torch `2.9.1+cu128`，Node 为 `v24.18.0`；`tests/test_export_runs.py` 通过。`tests/test_r10_1_contract.py` 继续失败于未修改的 general profile `embedding.semantic.enabled=False` 旧期望，作为既有基线保留。
- 统一格式导出于 2026-08-12 在隔离分支完成：前后端请求、不可变快照、发布器和历史展示都包含格式选择；转换输出在规划时记录哈希/大小，在发布时重新编码并校验。旧运行/快照缺少字段时仅在内存中补为 `original`，没有数据库迁移或历史写回。
- JPEG 对透明像素使用白底，PNG/WebP 保留 alpha；JPEG/WebP 固定质量 `95`，PNG 无损。转换只影响新导出文件，源图片、既有导出树和人工决定保持不变。
- 独立审阅发现同目录 `sample.jpg` 与 `sample.png` 会在转换后碰撞；现已按稳定输入排序分配目标，冲突项追加原扩展名，必要时追加稳定序号，两个样本及其配对标注均保留，禁止以相同哈希静默去重。
- 新增 API 回归表明 JSON 数组 `image_format` 曾导致未捕获 `TypeError`；输入规范化现先验证字符串类型并返回 `422/export_image_format_invalid`。2026-08-12 验证通过：后端导出聚焦 `79 passed`、Ruff、组件边界、第三方报告、前端 `131 passed`、生产构建、导出 E2E `4 passed`。全量后端仅保留既有 R10.1 语义默认值断言失败和一个预期跳过。

## Confirmed Native Picker Cold-start Optimization

- 用户于 2026-08-11 报告点击路径选择按钮后 Windows Explorer 窗口出现过慢，并确认实施常驻 STA PowerShell 选择器宿主。
- 已测得当前每次调用在 `windows_dialog.py` 中启动独立 `powershell.exe`，脚本再动态编译 `IFileDialog` C# 定义；空初始路径到 `Show()` 为 `340-631 ms`，原生窗口出现约 `1.05 s`。
- 保留 Windows 11 Explorer 风格的 Common Item Dialog、文件/目录模式、取消语义、绝对路径验证、重复点击保护和既有 API；不得回退到 `FolderBrowserDialog` 或网页目录列表。
- 宿主须为生产 WebUI 的应用作用域资源，正常关闭时清理；预热失败不能阻止 WebUI 启动，不增加依赖或修改用户数据。
- 追加回归先证明普通 `RuntimeError` 会从预热穿透并阻断 FastAPI 生命周期；`main.py` 现将该可选优化的异常隔离为 `Exception`（不捕获退出信号），测试确认健康检查仍为 `200` 且宿主会关闭。
- `tests/test_windows_dialog.py` 与 `tests/test_directory_selection_api.py` 聚焦回归为 `18 passed`，改动 Python 文件 Ruff 通过。真实无界面宿主首次预热为 `505.9 ms`，第二次 `start()` 为 `0.01 ms` 且复用同一 PID，`QUIT` 后退出码为 `0`。
- 为避免干扰用户现有“选择源数据目录”窗口，本轮未调用真实 `Show()`；连续目录/文件选择、取消及视觉窗口出现耗时仍待窗口关闭后人工确认。

## Confirmed Audit Selection Overlay Fix

- 用户于 2026-08-11 确认修复画风、美学和风险审计页无法单项选择的问题。
- 生产包探针证据：任务处于 `evidence_review` 时，`Modal` 生成的 `.modal-backdrop` 会截获单项复选框的指针事件；关闭提示后，三个页面的单项选择均显示 `已选 1`，无控制台错误。
- 确认方案：用户已直接位于任一审计路由时不显示该提示，并在进入审计路由时关闭它；非审计路由继续保留提示。不得修改后端、审核决定、缩略图预览或通用弹窗行为。
- 验证：项目内前端单测 `131/131`、两个聚焦 Playwright 用例 `2/2`、生产构建均通过；对 `7865` 已构建页面的 mock 探针确认四个审计路由可以单项选择，且 `#tasks` 仍显示人工复核提示。

## Confirmed Reliability Review Fixes (2026-08-13)

- 用户确认补齐任务 8/9、保留审计页抑制人工复核弹窗，并修复 Ruff 与 planner 私有调用问题。
- CF-only Community Forensics 的模块化执行计划不再包含自动依赖的 `feature.clip_l14`，不请求或初始化 `openai_clip_vit_l14`；用户显式启用 CLIP 或启用美学时仍保留 CLIP。旧缺 `model_id` → UFD 与新显式 CF 边界不变。
- CLIP、语义 embedding、scoring、聚类 embedding 上限为 `256`，style 为 `64`，scan 为 `4096`；所有默认 batch 保持原值。
- 聚类 hierarchy 在 SAE 关闭时按当前 scope 从重叠 embedding shards 加载；语义重复相似搜索只接收当前 persisted leaf 的矩阵，并显式查询 membership/sample 必要列。SAE 全任务训练仍按算法需要加载全矩阵。
- 导出 service 改用 planner 的公开 `plan_current`、`finalize_plan`、`plan_fingerprint`；写事务内第二次规划与 fingerprint 校验保留，十万级持锁优化需独立基准和测试。
- 可靠性计划已修正：任务 1 明确为从零新增 `NativePickerHost`；规格 3.3 新增独立“扫描/打分推理批与写库批分离”任务；任务 9 文件图补齐 shard store 与两个生产调用方。
- 新鲜验证：任务 8 `14 passed`、任务 9 `35 passed`、完整导出模块通过；Ruff、组件边界、第三方报告、前端 `137 passed` 和生产构建通过。后端全量仅保留既有 `tests/test_r10_1_contract.py:23` 的 semantic 默认值冲突。

## Planned Scan/Scoring Inference-Write Batch Separation (2026-08-13)

- 本轮先完成规格 3.3 企划，随后由后续实施会话完成生产代码与测试；已核对根文档、设计 3.3、Task 10 计划、扫描/评分配置与服务、`TaskService`/组件 checkpoint、暂停恢复及缓存测试。
- 当前扫描和模块化评分按内部派生目标聚合完整 decode/inference batch 后调用 `TaskService.commit_batch`；该方法在同一个 `BEGIN IMMEDIATE` 事务中完成业务写、phase checkpoint、任务进度、租约、组件 checkpoint 与暂停/终止转换，原子边界未拆散。
- 最小方案不增加公共 `write_batch_size`：扫描公开字段会进入 `ScanConfig.cache_payload()` 并改变 manifest/cache identity，评分字段还会扩大组件物化/Schema 契约。采用内部目标（扫描 256、评分 64）并向上对齐完整 inference batch；现有默认配置、上限和依赖不变。
- 缓冲区只允许 `ScannedMedia`、有限值校验后的 `SampleScore` 和 primitive CLIP shard descriptor；不得跨写批持有 PIL 图片、NumPy/Torch tensor、FeatureBatch、runtime/model 或 SQLAlchemy session。
- CLIP 已按计划修订：合并写批 checkpoint 保存 `feature_shards` 列表，单 shard 保留 `feature_shard` 兼容字段，消费者同时读取新旧形式。
- 合作式暂停/终止在完整 inference batch 后提交已验证前缀；现有 subprocess supervisor 强停时不承诺 flush，未提交内存丢弃并从最后 committed `next_index` 恢复。评分父进程现有 30 秒 heartbeat 保留；直接扫描/inline scoring 超过 30 秒未 flush 时只续租、不推进 checkpoint。
- Task 10 已补齐 scanner、CLIP、aesthetic、Community/UFD AI、OCR、watermark、混合 cache hit、finite guard、`results_prepared`、`component_complete`、原子 rollback、强停/恢复及 100,000 项确定性事务计数。性能基准只记录实施后的实际耗时/事务/行数，不预设或虚构 speedup。
- 状态：企划 `[x]`；生产实现与测试 `[x]`。core `55 passed, 1 skipped`，focused `89 passed, 1 skipped`，Ruff 通过；真实 10 万样本吞吐基准与跨进程强停时序仍待单独验证。

## Full-project Bug Scan and Stale Contract Fix (2026-08-15)

- 用户要求扫描整个项目，随后确认修复全部已证实缺陷并推送到 `main`。
- 正确初始化项目隔离变量后，完整后端测试只有 `tests/test_r10_1_contract.py:23` 失败；前端 `137` 条单测、生产构建和项目内 Chromium 的 `44` 条 E2E 基线通过。
- 提交 `3396197` 明确将三个 profile 的 semantic embedding 与 hierarchy 默认改为启用，并同步 `tests/test_profile_contracts.py`；初版 R10.1 测试未同步，是矛盾合同的根因。
- 生产 profile 约束保持不变；修复仅更新旧 R10.1 测试的名称、True 断言，并删除已经默认为 True 的无效显式赋值。
- 最终完整后端 Pytest 运行至 `100%` 且退出码为 `0`，仅有 1 个预期 skip；Ruff、组件边界、第三方报告、前端 `137` 条单测、生产构建和 `44` 条 E2E 均通过。

## Circular progress refinement (2026-08-17)

- The browser sample keeps the existing four-stage workflow and treats the first stage as `25%` for the empty task view.
- The former quarter-highlight conic gradient was removed. The progress ring is now a single muted ring with centered `25%` text, so progress is not communicated by a bright-versus-dim segment contrast.
- Desktop and mobile positioning both use `top: 50%` with `translateY(-50%)`; Chrome checks at `1440x900` and `390x844` found zero button overlap and a center offset below `0.01px`.
- The artificial-review boundary card is rectangular; only workflow stages retain the chamfer because it communicates sequence.
- The ring now uses the prior sample's three concentric, low-opacity expansion rings as a static gravity-wave effect. Mobile uses reduced radii so the wave stays inside the task panel.
- Final scanline treatment is one `1px` line every `6px`: `.28` on the base canvas and `.22` on the main content background so it reads clearly through the work area without entering cards or the sidebar.
- The global `design-dead-space-ui` Skill now records these final scanline values and the confirmed chamfer/progress-ring rules. Its UTF-8 validation returned `Skill is valid!`.
- The final balance reduces scanline opacity to `.16` on the base canvas and `.10` in the work area. A horizontal work-area material gradient now uses pale sides (`.12`) and a darker center (`.28`), so depth does not depend on overly crisp scanlines.
- The `.10` work-area scanline belongs on the full `.main` background, not the constrained content container, so it covers the workspace edge to edge. The inverse dark-side/light-center experiment was rejected; the global Skill retains pale sides and a dark center.
- The Skill now owns `assets/dataset-audit-terminal-reference-1440x900.png`, a verified current-page desktop reference. Future uses must inspect it after reading the target product, using it only for visual hierarchy and material decisions, never as a source of invented task data or business text.

## Confirmed Global Frontend Aesthetic Skill (2026-08-17)

- 全局 Skill 安装目录已由用户确认：`C:\Users\lse\.codex\skills\design-lone-trail-frontend`，显示名为“孤星风格前端设计”。
- 视觉合同：纸白是主色，`#FFFDAB` 仅用于选中、进度、计数和小几何点缀；黑色仅用于文字、标尺、状态与薄的右下硬阴影。前景面板不使用厚黑四边框。
- 侧栏为纸白标尺而非黑色色块，包含细外线、竖向基线、刻度、章节编号和浅黄色 active state；采用不对称网格、细结构线与少量方/圆/短斜线。
- 已通过无 Skill / 有 Skill 的 Terra xhigh 对照与 `quick_validate.py`。无 Skill 输出的黑底荧光方案被视为必须规避的反例；本项尚未开始前端源码实现。
- 用户反馈当前概念可能杂乱时，Skill 的结论是压缩品牌区，而非添加图形：按任务状态 -> 核心数据 -> 操作 -> 品牌排序；导航分组为 mission/analysis/system；黑色只表达锁定、高风险、错误或确认状态，不能是无意义的大容器。
- 一切信号、计数、图表和编号必须有标签、单位和既有数据来源；缺少产品字段时不得为了视觉效果伪造记录数、速度、ETA、告警或状态。Skill 复测已按真实 `App.tsx`/页面结构提出进度、审计和导出页的有序布局建议。
- 本轮 9 个 `.test-tmp` 目录已精确清理，`4174`/`7865` 无监听；提交与推送结果以 Git 历史记录核验，不在提交前预填哈希。
- 对齐补充合同：侧栏默认不显示刻度；只有用户明确要求并批准固定间距时才可加入刻度。不得有第二根平行竖线；每项菜单均有下方短横线，横线在基线前留空隙；侧栏品牌分隔线与页首分隔线必须以渲染测量证明同一 Y 坐标。`CURRENT TASK` 等摘要固定标签在上、值在下，间距 `4-8px`。
- 宇航服配色候选仅为视觉探索：`EVA / 01` 是当前纸白基调；Oxygen Blue、Rescue Red、Deep Space Black 不进入全局 Skill，也不构成产品实现需求。用户尚未选择最终产品配色。
- 用户要求深空生存恐怖方向必须服务于本项目的数据集审计工作流，而不是独立游戏 HUD。项目预览只能复用现有“任务、进度、风险、画风、重复、美学、导出、模型、系统”信息架构，以及新建任务、任务选择和人工复核边界；未经确认不得修改 React 源码。
- 2026-08-17 深空审计工作台探索：用户明确要求本轮不使用边缘强调线、标尺、细线和纯色低密度排布，也不套用“孤星风格前端设计”Skill 的既有黄白/侧栏约束。视觉重心改由有业务含义的分段模块、层级材质、状态色块、宽字标和流程节奏承担；仍禁止无关装饰与伪造任务数据。
- 深空工作台第一版配色被用户否定为混乱、缺乏死亡空间气质。当前草图改为暖灰铁材、低饱和琥珀投影、少量青色设备信号的三层色彩关系；风险红只能由真实风险状态触发，空任务页禁止出现。
- 用户继续否定暖灰铁材版“太土”。当前深空草图采用深石墨金属、低亮度蓝青投影，旧金色限于单个工作流阶段；背景扫描线仅可低对比地放在主工作区，不能成为组件内的噪声或主要装饰。
- 用户提供的第一版截图是当前正确基准：冷青黑色、单一屏幕色阶、扫描背景与任务控制区格网有一致介质感。暖红仅用于人工复核边界；不得再次引入大面积棕色、金色、青色状态块或多套竞争配色。
- 已创建全局 Skill `design-dead-space-ui`（显示名“死亡空间风格UI设计”），路径为 `C:\Users\lse\.codex\skills\design-dead-space-ui`。触发场景是冷青黑终端、克制扫描线和功能化世界内界面；任何项目实现前仍须读取真实 UI/API，不得伪造指标或使用游戏资产。
- 死亡空间风格 Skill 的补充合同：整体是苍白、褪色的冷青灰屏幕而不是深绿或荧光青；任务控制区、流程阶段、人工复核模块使用右上 `18px` 切角矩形，输入、小按钮、列表行和拥挤移动布局保持普通直角。
- `pale-chamfer-cyan-console.html` 是当前死亡空间风格 Skill 的浏览器样例，服务于 `http://localhost:50989/`；它只展示现有任务页的真实导航、任务选择、新建任务和人工复核边界。
- 该样例外层侧栏与任务控制区保持直角；当前选中的菜单项、流程阶段和人工复核卡使用右下缺角。任务控制区的圆形投影为桌面 `104px`、移动端 `72px`，仅作局部状态投影。
- 每个保留的缺角都由模块自身的 `1px` 外框线沿轮廓闭合包裹；斜边段必须贴在右下切口边界并连接底边、右边，不得穿过面板内容。
- 右下切口的斜边必须与缺口边界同向，从底边连接到右边；不得使用反向斜线。
- 任务控制框最小高度为桌面 `205px`、移动端 `250px`；背景横向扫描线保持每 `6px` 一条 `1px`，透明度为 `0.18`。

## 已确认：孤星风格前端重构（2026-08-17）

- 用户确认当前产品前端采用全局 `design-lone-trail-frontend` Skill 的纸白、局部 `#FFFDAB`、单基线标尺侧栏和克制右下硬阴影视觉合同。
- 本次范围仅限视觉层。`App.tsx` 的页面 ID/顺序、路由别名、任务选择器、页面布局、字段、接口、表格、虚拟列表和现有响应式断点均必须保持不变。
- 不使用默认侧栏刻度、黑色侧栏、厚黑四边框、HUD/霓虹效果、游戏资产或伪造的业务计数、速度、ETA、告警与任务状态。
- 验收需要源码契约、前端生产构建，以及桌面与 `390px` 窄视口的渲染复核。
- 已实施于隔离分支 `codex/lone-trail-frontend-20260817`：`styles.css` 只增加共享视觉覆盖层，未改动 `App.tsx`、页面、接口或数据模型；新增源码视觉契约。
- 红绿验证确认契约先因缺少 `--lone-paper` 失败，再在实现后通过。前端单测 `138 passed`、生产构建通过、完整 Playwright E2E `44 passed`。
- Browser 插件不可用时按授权安装项目 Playwright Chromium。任务页在 `1440x900` 和 `390x844` 中均通过标题、刷新、无控制台 warning/error 和无横向溢出检查；画风审计页的选择与批准排除交互也通过。
- 截图人工复核确认纸白单基线侧栏、品牌/页首同 Y 分隔线、浅黄当前项、局部主操作信号、移动端图标折叠和正常的表格/卡片折叠；本轮临时 QA 脚本与截图已清理。
- 该样例的侧栏外轮廓也使用与任务控制区一致的右上 `18px` 缺角；菜单项、按钮和内容区不使用切角。
- 用户确认“苍白”要求覆盖背景而非只覆盖文字，当前全局底色/侧栏/工作区为 `#1A2828/#203131/#304747`；禁止退回近黑主画布。

## Lone Trail 工作台实施验证（2026-08-17）

- 已将孤星风格重构合并到 `main`：纸白工作区、`#FFFDAB` 局部信号、192px 标尺侧栏、任务/审计/输出/系统分组和右侧上下文栏。
- 导航序号通过 `data-sequence` + `::before` 渲染，DOM 文本仍保持既有页面标签；上下文栏在 DOM 中先于主内容但通过 CSS grid 保持桌面右侧、移动端主内容优先。
- 验证证据：前端单测 `138 passed`，生产构建通过，Playwright `44 passed`，`git diff --check` 通过；`4174` 无监听。
- Browser 插件不可用，使用项目 Playwright Chromium；已检查参考图对齐关系、1440 级桌面截图和 390px 移动截图，无控制台错误或横向溢出。
- 后续视觉修正将桌面侧栏的唯一基线延伸进品牌区（距顶 `18px`），并将品牌横线的终点与菜单短横线对齐，保持其与基线的明确空隙；移动图标栏继续隐藏该基线，避免第二根平行竖线。回归契约按红绿验证，最终前端单测 `139 passed`、构建通过、Playwright `44 passed`，并在本机运行中的 `7865` 页面截图确认。
- 任务页“新建任务”的真实容器是 `.toolbar-band`，主操作阴影必须绑定该选择器而非未使用的 `.task-command-bar`；当前导航项也使用相同的右下黑色硬阴影。桌面内容最大宽度使用自动左右外边距，页首和摘要通过匹配的动态内距与内容边界对齐；`390px` 保持原有紧凑间距和无水平溢出。
- 选中导航项的下方必须预留与硬阴影偏移量相同的空间，并隐藏该项自身底部短横线，防止投影压住下一菜单项。侧栏保留唯一黑色内部标尺；当需要壳层分隔时，可在外侧使用一条浅色中性边界，不能再添加第二条黑色标尺。美学审计在保持分析组/路由不变的前提下取消 `nav-subitem` 缩进，作为该组内的左对齐入口。孤星 Skill 已同步这些规则，并通过本地前置结构与文本契约检查；官方 `quick_validate.py` 因运行时缺少 `PyYAML` 未执行，未安装依赖。

## 孤星工作台最新确认（2026-08-17）

- 用户要求任务上下文位于最左侧。桌面网格固定为上下文窄列在左、主内容弹性列在右；上下文的分隔线与内边距均朝向主内容。移动端不改变阅读优先级，仍先显示主内容。
- 用户要求大标题使用参考图中的淡投影。仅 `.page-title h1` 使用 `5px 8px 0 rgb(23 23 23 / 12%)` 的单层硬投影；不扩散到标签、菜单或按钮。
- 全局 `design-lone-trail-frontend` Skill 已同步上述两条规则，并通过项目 `.venv` 中的 `quick_validate.py` 校验。
- 用户最终否定侧边子菜单文字投影。当前 `.nav-item.nav-subitem` 不使用 `text-shadow`；仅保留页面大标题投影和当前菜单项的黑色右下硬阴影。
- 侧栏审计子菜单对齐：最终覆盖层将 `.nav-item.nav-subitem` 的左内边距固定为 `9px`，与普通菜单共用序号、图标和文字列；`loneTrailPresentation.test.mjs` 锁定最后一条覆盖规则。生产页面 `#risks` 在 `1440x900` 下量测五项菜单的 X 坐标均为序号/图标/文字 `21/49/77`，无控制台 warning/error 或横向溢出。孤星 Skill 已同步“以分组和紧凑行高表达层级，不得额外缩进”的规则。
- 最新确认覆盖此前“任务上下文在左侧”的方向：进度、风险、画风等页面的 `.workbench-context` 在桌面网格固定为最右受限列，主内容在左且分隔线朝左；`390px` 继续主内容在前、上下文在后。`#progress` 量测桌面主内容/上下文为 `x=248–1110` 与 `x=1134–1384`，移动端分别为 `top=303` 与 `top=443`，均无横向溢出或控制台 warning/error。孤星 Skill 已同步为右侧上下文规则。
- 用户进一步确认“最右侧”是工作区右内边距，不是仅位于受限内容网格的右列。超过 `1732px` 时，仅 `.workbench-grid` 向右延伸，保留主内容既有起始对齐；`#exports` 在 `1920x1080` 下主内容为 `x=342–1505`、上下文为 `x=1529–1864`，右边距 `56px`。窄屏规则不变；孤星 Skill 已同步，且无控制台 warning/error 或水平溢出。
- `start_webui.bat` 的实际入口 `launch_webui.ps1` 现先用项目内 `.runtime\\node\\npm.cmd` 执行 `--prefix frontend run build`，再检查或启动 `7865` 服务。因此前端修改会生成新哈希静态资源，现有服务也能在本次启动操作后提供新包；本机实际运行已产出 `index-aXhUIGp5.js` 和 `index-D61IFBO3.css`。全局 `design-lone-trail-frontend` Skill 的 Finish Check 已同步要求通过正常启动入口重建并确认服务的哈希入口。
