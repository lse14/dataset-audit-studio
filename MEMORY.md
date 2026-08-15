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
- 本轮 9 个 `.test-tmp` 目录已精确清理，`4174`/`7865` 无监听；提交与推送结果以 Git 历史记录核验，不在提交前预填哈希。
