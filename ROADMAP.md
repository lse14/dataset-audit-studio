# Roadmap

## 启动入口前端构建（2026-08-17）

- [x] 让 `start_webui.bat` 在复用现有服务或启动新服务之前，使用项目内 Node 重新构建前端静态资源。
- [x] 构建失败时阻止打开旧页面，且不安装依赖、不使用系统 Node 或修改任务数据。

### 验收与验证

- `launch_webui.ps1` 在首次 `Get-WebUIState` 前调用 `.runtime\\node\\npm.cmd --prefix frontend run build`。
- 回归测试先因缺少构建调用失败，修复后通过；实际运行 `start_webui.bat` 输出 Vite 生产构建成功。
- 运行中 `http://127.0.0.1:7865/` 返回新的带哈希入口资源：`index-aXhUIGp5.js` 与 `index-D61IFBO3.css`。

## Lone Trail 上下文最右侧锚定（2026-08-17）

- [x] 解除超宽桌面中工作台网格受主内容最大宽度限制，使当前任务上下文抵达工作区最右内边距。
- [x] 保持主内容原有左侧对齐和移动端主内容优先，不改变页面数据、路由或控件。

### 验收与验证

- 当视口宽度超过 `1732px` 时，工作台网格仅向右扩展；右栏不再停在 `1540px` 内容上限。
- 新契约先因缺少宽屏网格宽度规则失败，修复后通过；完整前端单测 `139/139` 和生产构建通过。
- `#exports` 在 `1920x1080` 下量测主内容为 `x=342–1505`、上下文为 `x=1529–1864`，右边距为 `56px`；`390x844` 下仍为主内容 `top=303`、上下文 `top=443`。两个视口无横向溢出及控制台 warning/error。

## Lone Trail 任务上下文右侧布局（2026-08-17）

- [x] 将进度、风险、画风等页面的当前任务上下文栏固定为桌面最右列。
- [x] 保持窄屏主内容优先，且不改动路由、任务选择器、接口或页面数据。

### 验收与验证

- 桌面网格为弹性主内容左列加受限上下文右列，上下文分隔线面向左侧主内容。
- 新布局契约先因旧的 `"context primary"` 顺序失败，修复后通过；完整前端单测 `139/139` 和生产构建通过。
- `#progress` 在 `1440x900` 下量测主内容为 `x=248–1110`、上下文为 `x=1134–1384`；在 `390x844` 下主内容位于 `top=303`、上下文位于 `top=443`。两个视口均无横向溢出及控制台 warning/error。

## Lone Trail 侧栏审计子菜单对齐（2026-08-17）

- [x] 将审计组子菜单恢复到与普通菜单一致的序号、图标和文字起始列。
- [x] 用源码契约锁定最终覆盖规则，避免后续覆盖层重新引入额外左缩进。

### 验收与验证

- 最终 `.nav-item.nav-subitem` 的 `padding-left` 为 `9px`，保留紧凑行高而不改变路由、任务逻辑或信息架构。
- 新契约先因旧值 `22px` 失败，修复后通过；完整前端单测 `139/139` 和生产构建通过。
- 在 `http://127.0.0.1:7865/#risks` 的 `1440x900` 量测中，任务、风险、画风、重复、美学的序号/图标/文字 X 坐标均为 `21/49/77`，无横向溢出及控制台 warning/error。

## Goal

Restore the local WebUI startup after removing obsolete local runtime data and rebuilding the missing frontend bundle.

## Work Items

- [x] Record the confirmed scope, constraints, evidence, and acceptance criteria.
- [x] Review launch, setup, stop, and test scripts for the same stale-state or missing-artifact assumptions.
- [x] Remove the confirmed obsolete database, SQLite sidecars, task artifacts, and database backups without touching source code, models, or user-selected source datasets.
- [x] Start from the current Alembic baseline and rebuild the frontend bundle using project-local runtimes only.
- [x] Add and verify a Windows-native fallback when the verified stop process cannot be terminated by `Stop-Process`.
- [x] Start the WebUI, verify its health endpoint and rendered root page, then stop the process cleanly.
- [x] Record verification results and remaining limitations.
- [x] A later full `scripts/test.ps1` run completed with exit code 0 and 43 E2E cases passed. The earlier Playwright timeout is retained only as historical diagnostic context.

## Acceptance Criteria

- `start_webui.bat` starts a service on `127.0.0.1:7865` without the obsolete Alembic revision error.
- `GET /api/health` returns HTTP 200 and reports the project-local runtime.
- `frontend/dist/index.html` exists and the root URL returns the WebUI page instead of a missing route.
- No global Python, Node, or model cache is used.
- The script review is recorded, with any needed minimal fixes covered by tests and verification.
- `stop_webui.bat` stops the verified WebUI process tree without leaving a listener or worker behind.

## Risks

- The approved cleanup is irreversible for local task history and old database backups.
- Model files and source datasets are out of scope and must not be removed.
- A missing frontend build can allow the API to start while leaving no usable WebUI; verification must check both endpoints.

## Script Review

- No script references the retired Alembic revision directly; the obsolete state was entirely inside the removed local database.
- `setup.ps1` and `test.ps1` both run the frontend build using project-local Node.
- `start.ps1` checks the Python runtime but not `frontend/dist/index.html`; if a user intentionally skips or later removes the build output, it can start an API without a usable WebUI. This is documented as a scoped follow-up rather than an unrelated script refactor.
- The first real stop test exposed nested arrays from helper-return values. Those call sites now have a regression test and use the returned arrays directly.
- In this desktop execution environment, `Stop-Process` throws `NullReferenceException` even after a successful listener lookup. `taskkill /PID 28804 /T /F` terminated that exact verified process tree, so the stop script needs a tested fallback.

## Verification Results

- `start_webui.bat` returned exit code 0 with desktop permission.
- `GET /api/health` returned `status: ok`, an isolated project-local Python runtime, the recreated `data/app.db`, and a running local worker.
- `GET /` returned HTTP 200 with the React mount point and production assets.
- Playwright opened the production page without console errors, rendered the empty task state, and opened the new-task configuration panel without submitting data.
- `stop_webui.ps1` returned exit code 0 after a final start, released port 7865, and left no WebUI process or listener.
- Full backend Pytest completed at 100% with one expected skip; isolation, Ruff, component-boundary, dependency-report, and 125 frontend unit tests passed; a fresh production build passed.

## Post-Recovery Diagnostic

- [x] Confirmed that task-configuration help badges and explanatory text are intentionally absent in the current frontend. `ui1Presentation.test.mjs` and the task-profile E2E test explicitly require no `FieldHelp`, `.field-help`, `schema.description`, component-purpose text, profile description, or former guide route. `SchemaField.tsx` only renders labels and inputs from a schema; it does not consume `description`.
- [x] Production Playwright reproduction on 2026-08-08: opened `/#tasks`, selected `新建任务`, expanded a component, and observed zero `.field-help` elements, zero Info/CircleHelp icons, and zero matching explanatory-text nodes, with no console warnings or errors.
- [!] The available Git history starts at the current initial private release, which already contains the removal tests. It cannot establish the exact earlier commit or date when the help UI was removed.

## Task Configuration Help Restoration

- [x] Restored the user-requested per-option `i` help control in the task-configuration dialog only. The control opens its explanation on click, closes on a second click, outside click, or Escape, and remains keyboard-accessible.
- [x] Uses schema descriptions when supplied; otherwise shows a concise maintained explanation or a generated constraint/default-value fallback so every rendered configuration option has help text.
- [x] Covered static task settings, runtime settings, profile workspace settings, component summaries, and dynamic schema fields without restoring the retired guide page or always-visible descriptive paragraphs.
- [x] Verified a failing-then-passing automated contract, production build, and rendered desktop/mobile interaction.

## Help Restoration Acceptance Criteria

- Every task-creation configuration field has an adjacent, labelled `i` button.
- Clicking the button shows only that field's concise explanation and sets the expected accessible state; Escape and an outside click dismiss it.
- Help content does not alter submitted configuration values or validation behavior.
- The task configuration dialog remains usable at desktop and mobile widths with no console errors.

## Help Restoration Verification

- The initial restoration contract failed because `frontend/src/components/FieldHelp.tsx` did not exist. After implementation, the focused test passed; a second red-green cycle covered Escape propagation and viewport positioning.
- `frontend` production build passed with TypeScript compilation.
- All 125 frontend unit tests passed.
- Playwright loaded the production WebUI at `http://127.0.0.1:7865/#tasks`; component and field help text opened correctly, outside click and Escape closed only the tooltip, the task dialog remained open after Escape, and no console warnings/errors occurred.
- At `390x844`, 18 visible help controls rendered; the tested component, field, and output-directory tooltips stayed inside the viewport.
- The updated task-profile E2E test was parsed successfully with Playwright test discovery. It now checks that Escape leaves the task dialog open.

## Windows 11 Native Path Pickers

- [x] Replace the remaining web-only directory picker surface with compact buttons that open Windows native pickers.
- [x] Use the Windows Common Item Dialog for both folders and local model files so the picker has the Windows 11 Explorer navigation pane.
- [x] Add a native file picker for local model files and wire every current local path field to the appropriate picker.
- [x] Verify cancellation, path backfill, duplicate-click protection, frontend contracts, backend dialog contracts, and production build.

### Native Path Picker Acceptance Criteria

- Task source/output and repeat-export output fields open a Windows native folder picker from a small icon button.
- Local model import opens a Windows native file picker and fills the selected `.safetensors` path.
- Both modes use the Windows Common Item Dialog (`IFileDialog`); the old `FolderBrowserDialog` and WinForms `OpenFileDialog` are not used.
- Cancellation leaves the existing value unchanged; picker errors are surfaced through the existing notice/error path.
- No task configuration path opens the old in-page directory listing modal.

### Native Path Picker Verification

- Frontend unit tests passed: `126/126`.
- Frontend production build passed with the project-local Node runtime.
- The focused repeat-export E2E suite passed: `8/8`, including native output-path backfill and 390px layout checks.
- A Playwright mock smoke flow passed for task source/output and local model file pickers; selected values were `E:/native-source`, `E:/native-output`, and `E:/models/replacement.safetensors`, with no unhandled API requests or console warnings/errors.
- Full backend Pytest passed with one expected skip and no failures; the project-local `.venv` and an isolated `--basetemp` were used.
- The old in-page `DirectoryPicker` surface is removed; generated native picker temp directories and test metadata were cleaned.
- `scripts/select_directory.ps1` now creates the COM `FileOpenDialog` and enables `FOS_PICKFOLDERS` for directories; the embedded C# compiles under Windows PowerShell 5.1.
- On Windows 11 Pro build 26200, real directory and file picker smoke runs opened the dialog, accepted an automated Escape, returned `CANCELLED` with exit code 0, and emitted no stderr.
- A repository-wide search found no remaining `FolderBrowserDialog`, WinForms `OpenFileDialog`, or `ShowDialog` picker call outside the regression assertion.
- The broader Playwright runner repeats a pre-existing shutdown hang in this desktop environment: the 11 task-workflow cases reached their final case without reporting a test failure, and the focused help flow rendered its desktop/mobile screenshots, but neither command returned a final runner exit code before the outer limit. These runs are not counted as passing verification.

## Zero-weight Style Model Gating

- [x] Make each zero-weight style feature model absent from runtime model requests and model loading.
- [x] Preserve the existing `StyleFeatureBatch` shape with neutral features for skipped models.
- [x] Verify config resolution, runtime loading, score gating, and focused backend regressions.

### Acceptance Criteria

- A zero `lsnet_weight`, `gram_weight`, or `dino_weight` omits only its corresponding model from the component runtime model list.
- The skipped model is not loaded or inferred, while downstream style evidence fields remain valid.
- Positive-weight behavior and the existing weight-sum validation remain unchanged.

### Verification Notes

- `tests/test_style_analysis.py`, `tests/test_style_service.py`, and `tests/test_component_architecture.py`: `33 passed`.
- Ruff passed for the changed artist-style modules and focused test.
- With project isolation initialized, stage subprocess tests passed (`7 passed`) and modular/component API tests passed (`12 passed`).
- Full backend Ruff and Pytest passed; Pytest reported one existing skip and no failures.
- Frontend unit tests passed (`125/125`) and the production TypeScript/Vite build passed.
- Generated `zero-weight-*` test temporary directories were removed after verification.

## Risk Evidence Layout Fix

- [x] Confirmed the reported risk-evidence "garbling" is visual overlap, not text decoding corruption.
- [x] Removed `.risk-row` from the absolute-positioned virtual-list selector used only by event, SAE, and cluster rows.
- [x] Added a frontend regression contract and rendered two risk rows through the real page with mocked API data.

### Acceptance Criteria

- Each risk sample occupies its own normal-flow row.
- File paths and evidence codes remain readable when multiple risks are present.
- Existing virtual-list rows retain their absolute positioning.

### Verification Notes

- The new focused test first failed against the original selector, then passed after the one-line CSS fix.
- Full frontend unit suite passed: `127/127`.
- Production TypeScript/Vite build passed.
- Playwright mock verification at `1280x900` found rows at `y=322–454` and `y=454–586`, with no overlap, console warnings/errors, or unhandled API requests; clicking the second row opened its matching detail modal.

## Duplicate Audit Auto-selection

- [x] Change duplicate-review page selection from selecting every member to selecting the members suitable for batch exclusion.
- [x] Supply each duplicate-audit member's actual pixel area through the existing read-only API response.
- [x] Preserve one representative per group: largest pixel area, then lexicographically smallest relative path, then sample ID.
- [x] Keep the existing explicit confirmation and "at least one retained" protection unchanged.
- [x] Add focused backend/frontend regression coverage and run the affected test suites.

### Acceptance Criteria

- Checking the page selection control selects every eligible member except one stable representative in each displayed duplicate group.
- The representative is the member with the greatest `width * height`; ties use relative path and then sample ID.
- The control's label states that it automatically selects members for exclusion, and clearing it clears the page selection.
- The selection is still only a draft: exclusion requires the existing confirmation flow and cannot exclude an entire group.

### Risks

- Existing historical exclusions can still cause the unchanged all-excluded guard to block a batch action; this change does not alter prior decisions automatically.
- A missing image dimension is ranked below a known pixel area; when all dimensions are missing, the stable path rule determines the representative.

### Verification Results

- The new frontend selection-rule test completed a red-green cycle: it first failed because `duplicateSelection.ts` was absent, then passed 4 checks for cross-group selection, tie resolution, missing dimensions, and page wiring.
- `tests/test_duplicate_group_audit.py` first failed with `KeyError: 'pixel_area'`, then passed after the existing read-only duplicate-audit response supplied the value.
- Focused backend Pytest and Ruff passed using the project-local `.venv`; the full frontend unit suite passed `131/131`, and the production TypeScript/Vite build passed using project-local Node.
- Focused Playwright passed `1/1` with the project-local Chromium at desktop and `390x844`. It verified two lower-resolution members were selected, the largest was not, the approval payload contained only the two selected IDs, and the mobile document had no horizontal overflow. Port `4174` had no listener after the run.

## WebP Original Media MIME Fallback

- [x] Restore original-image delivery for `.webp` files when the Windows MIME registry leaves `mimetypes.guess_type()` empty.
- [x] Add a focused regression test that simulates the missing MIME mapping through the original-media API.
- [x] Preserve source containment, source identity verification, and rejection of all non-image media.

### Acceptance Criteria

- When MIME inference returns `None` for a `.webp` original, the media endpoint returns HTTP 200 with `Content-Type: image/webp` and the original bytes.
- The fallback applies only to an otherwise unknown `.webp` suffix; known MIME types and unsupported files retain their current behavior.
- The focused regression test and the affected backend test module pass using the project-local virtual environment.

### Evidence

- `WorkspaceFileAccess.media()` currently rejects an empty inferred MIME type before returning the `FileResponse` metadata.
- `scanner/discovery.py` includes `.webp` in `STATIC_IMAGE_EXTENSIONS`.
- The project-local Python command `mimetypes.guess_type('example.webp')[0]` produced no output, reproducing the absent MIME mapping condition.

### Verification Results

- The regression test first returned HTTP 409 before the fallback was added, then passed with HTTP 200, `image/webp`, and unchanged source bytes.
- `tests/test_r12_2_review_media.py`: `4 passed` using the project-local `.venv`.
- Ruff passed for `backend/dataset_audit_studio/workspace/file_access.py` and `tests/test_r12_2_review_media.py`.

## 画风审计默认参数

- [x] 将已在 `E:\Desktop\10_6suan` 测试集上验证的画风审计参数设为新任务默认值。

### 验收标准

- 空画风配置解析为：`max_iterations=2`、`outlier_sigma=1.3`、`minimum_style_score=94.5`、LSNet/Gram/DINO 权重 `0.9/0.1/0.0`、Gram 内部权重 `0.8/0.2`。
- 已保存任务中的显式画风配置不被迁移或修改。
- 聚焦画风配置测试和静态检查通过。

### 验证结果

- 新增的默认值断言先在旧配置上失败（`max_iterations=3`），更新后通过。
- `tests/test_style_analysis.py`: `15 passed`。
- `tests/test_components_api.py`: `5 passed`。
- Ruff 通过 `artist_style/config.py` 和 `test_style_analysis.py`。

## 多画师范围画风离群参数评估

- [x] 将 `E:\Desktop\画风离群测试` 的每个一级子目录作为独立画师范围，提取特征并评估参数组合。
- [x] 在总误报率不超过 10% 的前提下，找出检出 `6suan` 样本最多且稳定的合法权重与阈值。
- [x] 用正式 `analyze_artist_scope()` 对最优候选复算，并记录各画师范围的 TP、FP、FN。

### 验收标准

- 12 个一级目录各自独立计算，不能混合成全局参考风格。
- 每目录 11 张文件名含 `6suan` 的图像作为目标真值；所有其他图像作为非目标。
- 报告聚合指标和各目录指标，聚合非目标误报率不超过 `10%`。
- 本轮只输出建议参数，不自动改写画风默认配置或测试集数据。

### 风险

- 文件名真值只能证明它们是注入的异风格样本，不能代表真实生产数据中的所有离群形态。
- 单一测试集调优的参数必须单独标记，未经额外数据集验证不应直接替换全局默认值。

### 验证结果

- 数据集含 12 个独立 scope、997 张可解码图像：132 张 `6suan` 目标与 865 张非目标。
- 当前默认值在此数据集为 `88 TP / 129 FP / 44 FN`，误报率 `14.913%`，不满足总计 10% 限制。
- 在约 191,328 次参数评估（含边界、随机与局部网格复核）中，最佳且稳定的总计约束候选为：`max_iterations=3`、`outlier_sigma=0.522`、`minimum_style_score=92.07`、LSNet/Gram/DINO=`0.892/0.0/0.108`、Gram 平均/中心=`0.8/0.2`。
- 候选正式复算结果为 `108 TP / 86 FP / 24 FN`；非目标误报率为 `86/865 = 9.942%`，目标召回率为 `108/132 = 81.818%`。
- 复算逐 scope 使用真实原始特征和正式 `analyze_artist_scope()`；12 个 scope 均与搜索加速器的离群标记和分数一致。
- 该约束按全部目录合计计算。`berubel`、`currycat24`、`kouta_nagamori`、`luodejun`、`winters29208178` 单独的误报率仍高于 10%，因此不能将此结果解释为“每个 scope 各自不超过 10%”。
- 本轮未修改 `StyleConfig` 默认值；应在独立数据集交叉验证或用户明确确认后再考虑替换。

## 多画师范围默认配置应用

- [x] 将用户确认的多画师范围最优参数设为仅影响新任务的 `StyleConfig` 默认值。
- [x] 更新默认值回归测试，并验证默认 Gram 权重为零时不会请求 VGG 模型。

### 验收标准

- 空画风配置解析为 `max_iterations=3`、`outlier_sigma=0.522`、`minimum_style_score=92.07`、LSNet/Gram/DINO=`0.892/0.0/0.108`、Gram 内部=`0.8/0.2`。
- 既有任务中保存的显式画风配置不被改写。
- 默认模型请求仅包含 LSNet 和 DINO；聚焦测试与静态检查通过。

### 验证结果

- 默认值断言先按预期失败：旧空配置仍为 `max_iterations=2`，而目标为 `3`。
- 更新后，`tests/test_style_analysis.py`、`tests/test_style_service.py`、`tests/test_components_api.py` 共 `23 passed`；聚焦 Ruff 检查通过。
- 默认模型请求断言为 `lsnet_kaloscope_v2` 与 `dinov2_large`，证实 `gram_weight=0` 不会请求或加载 VGG。
- Gram 专项算法测试改为显式传入 Gram 正权重，避免它隐式依赖默认值；其原有“LSNet 与 Gram 同时低于阈值”的行为仍由测试覆盖。

## 角色预设子文件夹一致性审核

- [x] 角色预设默认启用 SigLIP2 语义向量与分层聚类，并保留 `concept` 作为运行时一级子文件夹范围。
- [x] 为角色预设生成可解释的无关角色候选证据，不把视觉语义代理结果表述为身份识别保证。
- [x] 将候选接入现有按文件夹筛选、人工保留/排除和导出资格链路。
- [x] 完成聚焦后端、前端契约和完整受影响测试复核。

### 验收标准

- `character_concept` 新任务必须启用 `embedding.semantic` 和 `cluster.hierarchy`，且每个一级子文件夹独立计算。
- 角色聚类不得依赖 `artist_style_score`；画师预设的画风核心样本过滤保持不变。
- 无关角色只能先作为候选证据进入人工审核，不自动排除、不修改源文件。
- 人工批准排除后，现有导出资格解析必须排除对应图片；批准保留可覆盖自动候选。

### 验证结果

- 回归测试先分别复现角色预设组件未锁定、证据缺少模型身份、超大 `IN` 清理和单文件夹集成覆盖缺口，修复后聚焦测试通过。
- 角色预设 API 将 SigLIP2、分层聚类和 `scope_mode` 声明为角色专属控制；即使客户端提交关闭组件或 `global`，任务物化仍强制恢复为启用和 `concept`。
- 模块化服务测试使用两个 4 图角色文件夹和一个 3 图小文件夹，分别生成两个独立候选且小文件夹不猜测；按 `folder=alpha` 只返回 alpha 候选。
- 候选证据保存实际模型 SHA、预处理版本、embedding 身份哈希、角色算法参数/哈希、scope 大小、核心大小、相似度阈值和层级配置哈希；角色参数哈希同时进入 hierarchy 检查点身份。
- 人工 `approved_exclude` 后导出资格原因为 `manual_exclude`；改为 `approved_keep` 后恢复纳入。自动候选本身不排除图片，也不修改源文件。
- 每 scope 的旧角色证据按 500 个 sample ID 分块清理；测试将 SQLite 参数上限降至 999 后，1,201 个 ID 的清理通过。
- 完整后端 Pytest 运行至 100%，仅 1 个预期跳过；全仓 Ruff 通过。前端单测 `131/131` 和 TypeScript/Vite 生产构建通过。
- 限制：少于 4 张图片的文件夹不自动判断；SigLIP2 结果是视觉语义候选，不能作为严格身份识别保证。

## 技术筛选默认参数优化

- [x] 复核现有八项技术阈值、三个内置预设的物化路径和边框误报根因。
- [x] 只读分析 `E:\Desktop\画风离群测试` 的 997 张基础样本，并以内存合成缺陷校准候选值。
- [x] 确认三个预设采用共享技术基线和连续对边边框算法。
- [x] 用户复核并确认已写入的设计文档。
- [x] 编写并复核测试先行实施计划。
- [x] 以测试先行方式更新默认值、边框算法和技术指标版本清理。
- [x] 运行聚焦测试、Ruff 和完整后端验证，并记录结果。

### 验收标准

- `artist_concept`、`character_concept`、`general` 的新任务默认技术阈值均为 `2.5/0.90/0.90/16.0/0.32/0.03/0.35/10.0`。
- 黑白边框只在同色连续条带成对出现、与内部存在明显分界且合计厚度超过 3% 时告警。
- 自然深浅背景、单边色块和整幅纯黑纯白图不被边框指标误报。
- 重扫后不存在同一样本的 v1/v2 技术指标重复证据。
- 已保存任务的显式阈值和外部校准图像不被改写。

### 风险

- `E:\Desktop\画风离群测试` 是基础样本而非完整缺陷真值集，不能据此声称所有真实场景的召回率。
- 高频比例现有算法对部分盐胡椒噪声不敏感；本事项仅安全下调默认阈值，不扩大为算法重写。
- 算法版本升级若只删除当前来源会遗留 v1 证据，实施必须按来源模式清理全部历史技术指标版本。

### 验证结果

- 三项新增回归测试均完成红绿循环：旧默认值、旧外圈边框计算和旧版本清理分别按预期失败，最小实现后通过。
- 聚焦 Pytest：`25 passed, 1 skipped`；受影响扫描代码与测试的 Ruff 检查通过。
- 最终完整后端 Pytest：`492 passed, 1 skipped in 175.33s`；唯一跳过是 Windows 无创建目录符号链接权限的既有环境限制。
- 使用实装 v2 在 `E:\Desktop\画风离群测试` 的 997 张只读图像上复核：`metrics_max_side=1024` 下命中 10 张连续黑条、黑框或白色画布边距；1024 和 4096 的探索结果分别为 10 和 8，因此不再使用早期 18 张候选统计作为验收依据。
- 36 张分层样本的内存合成对边检出为：黑 2% `30/36`、黑 5% `32/36`、白 2% `29/36`、白 5% `27/36`。校准图像、数据库和已保存任务均未被修改。

## 角色预设一致性参数评估

- [x] 使用 `E:\Desktop\角色测试` 的 9 个单角色一级子文件夹提取 SigLIP2 特征并进行只读交叉验证。
- [x] 在合计误报率不超过 10% 的约束下搜索角色一致性 `sigma` 和迭代轮数，并限制最小范围值不能跳过本数据集的任何真实角色目录。
- [x] 对当前实现最多 3 轮的候选与 4 轮探索候选分别复核；用户确认后将当前实现兼容候选写为角色默认参数，不修改源数据或既有审核决定。

### 验收标准

- 9 个一级目录各自作为独立角色范围，不能混合为全局参考。
- 原始目录视为干净单角色数据，用于估计误报；每个目标/异角色目录对在内存中注入 1 张或 2 张异角色图，作为已知异角色交叉验证样本。
- 推荐参数的合计干净数据误报率不超过 `10%`，并报告异角色检出率、各目录误报和限制。
- `analyze_character_scope()` 的角色默认参数为 `minimum_scope_size=4`、`outlier_sigma=2.04`、`max_iterations=2`；既有任务仅在重新运行角色层级阶段时按新配置重新生成候选。

### 验证结果

- 数据集包含 9 个一级目录、859 张可解码 `.webp`：数量依次为 `73/40/69/35/156/272/73/39/102`。本地模型为 `siglip2_so400m_naflex`，使用项目注册版本和 `siglip2-naflex-image-processor-v1`。
- 当前参数 `minimum_scope_size=4`、`outlier_sigma=1.5`、`max_iterations=3` 在干净数据上标记 `249/859`，误报率 `28.987%`；因此不能作为此数据集的合适基线。
- 当前实现上限 3 轮的合计约束候选为 `minimum_scope_size=4`、`outlier_sigma=2.04`、`max_iterations=2`：干净误报 `74/859=8.615%`，逐对单异角色注入检出率 `86.111%`，逐对双异角色注入检出率 `84.722%`，平均 `85.417%`。最坏单目录误报为 `9/69=13.043%`，所以 `10%` 仅是合计约束。
- 探索性允许 4 轮时，`sigma=2.115` 达到 `81/859=9.430%` 干净误报和 `86.111%`/`86.111%` 的单/双注入检出率；相对 2 轮只提升约 `0.694` 个百分点，且需要扩展当前实现上限，未作为本轮推荐。
- 同角色相似度中位数为 `0.806936`，异角色跨目录相似度中位数为 `0.681905`，但两者分布仍明显重叠；有 `10/72` 单注入交叉验证对在推荐参数下未检出。因此该结果是校准建议，不是严格身份识别保证。
- 用户确认后已将候选写入角色默认常量；新增配置回归测试先在旧 `1.5/3` 值上失败，随后通过。角色一致性与模块化聚类聚焦测试 `12 passed`，后端全量 Pytest 通过（1 个预期跳过），全仓 Ruff 通过。

## AI 图识别校准

- [x] 对现有 UniversalFakeDetect 在已确认标签的两组外部数据上完成只读基线评估。
- [x] 确认候选 Community Forensics 模型的固定来源、版本、文件哈希和项目内预处理契约。
- [x] 下载候选模型至项目内部模型目录，并以分层留出交叉验证比较两个模型。
- [x] 仅在候选模型的留出结果显著优于现有模型后，更新默认 AI 审核模型/阈值并完成测试复核。

### 验收标准

- `D:\flux-aki\ComfyUI-aki-v1.6\output` 的 705 张图固定作为 AI 正样本；`E:\Desktop\画风离群测试` 的 997 张图固定作为非 AI 负样本，均只读。
- 评估必须报告完整 ROC 操作点，包括 10%、20%、30% 对照误报率下的召回率；用户已明确允许高于此前 10% 的误报率，以换取对较新 AI 模型更高的检出率。
- 训练或阈值选择不得在同一留出折上评估；候选模型只有在分层交叉验证的留出结果优于 UniversalFakeDetect 时才可替换新任务默认值。
- 已保存任务配置、外部测试图、审核决定和源图片均不得被改写。

### 已知基线

- 现有 `universal_fake_detector_head`（OpenAI CLIP ViT-L/14 特征）在 1,702 张样本上的 AUC 为 `0.512304`；默认候选阈值 `0.35` 的召回率为 `32.482%`、负样本误报率为 `25.176%`。
- 若强制负样本误报率不超过 10%，现有模型的最佳阈值为 `0.794937`，但召回率仅为 `17.589%`；单纯调节现有阈值无法达到可靠识别。
- 已选候选为 Community Forensics `commfor_model_384`，固定版本 `6076002bf0d9dd37537f965ee2f06f826c333b61`，模型文件为 87,262,324 字节，SHA-256 为 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387`。

### 候选验证结果

- 候选文件已下载到项目内部 `models/benchmarks/commfor_model_384/6076002bf0d9dd37537f965ee2f06f826c333b61/model.safetensors`，长度和 SHA-256 均与固定契约一致；预检状态为 `ready`，项目 `.venv` 已含 `torch`、`torchvision`、`timm`、`safetensors`。
- 双模型均在相同的 5 折分层划分上运行：每折 141 个 AI 正样本与 199 或 200 个非 AI 负样本；方向、阈值均在其余 4 折选择，只在留出折统计。
- Community Forensics 留出 AUC 为 `0.845551`，显著优于 UFD 的 `0.512304`。其约 10%/20%/30%/40% 误报操作点分别为：召回 `61.418%`/`74.610%`/`82.411%`/`87.660%`，实际误报 `10.231%`/`19.659%`/`29.890%`/`38.616%`。
- Community Forensics 的平衡操作点为召回 `71.206%`、误报 `16.349%`、精确率 `75.489%`、平衡准确率 `77.428%`，候选阈值中位数为 `0.175102`；10% 误报强参考阈值中位数为 `0.464626`。
- 用户已确认采用约 20% 误报的高召回档：候选阈值 `0.121558`，强参考阈值 `0.464626`；任何命中仍只生成审核候选，不自动排除图片。

### 生产接入验证

- 新组件任务的 `detect.ai` 明确保存 `community_forensics_model_384` 与已确认的 `0.121558/0.464626` 阈值；缺少 `model_id` 的旧直接 `scoring.ai` 配置仍解析为 UniversalFakeDetect，避免旧任务静默换模型。
- 生产资产已由 `ModelService` 下载到 `models/registry/community_forensics_model_384/6076002bf0d9dd37537f965ee2f06f826c333b61`。安装清单、文件长度 `87,262,324` 和 SHA-256 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387` 均与固定注册表一致，最终状态为 `ready`、`runtime_ready=true`。
- 真实 CPU 运行时经 `ModularScoringComponentService` 对 `D:\flux-aki\ComfyUI-aki-v1.6\output` 的 6 张只读样本完成冒烟：概率范围为 `0.059891-0.960428`，5 张进入人工审核候选。所有记录均为 `community_forensics`、`review_only=true`，包含 Community 模型 ID、模型身份哈希和 `commfor-resize440-center-crop384-imagenet-v1`；未产生自动审核决定。
- 聚焦 Community、模型下载和基准适配测试共 `172 passed`；完整后端 Pytest 为 `495 passed, 1 skipped in 177.11s`；`ruff check backend tests` 为 `All checks passed!`。新增注册表项目使总数从 12 变为 13，健康检查和模型 API 测试现在从 `DEFAULT_REGISTRY` 计算总数及文件数，避免该非功能性断言再次过期。
- 高召回档的误报权衡仍以已记录的分层留出结果为准；本次 6 张运行时冒烟只证明生产加载、溯源和人工审核路径，不构成新的准确率评估。

## 公开发布（2026-08-10）

- [x] 创建不含历史的公开发布根提交，并将发布分支设为 `main`。
- [x] 创建 GitHub 公开仓库并只推送 `main`。
- [x] 验证远端可见性、提交树和本地数据/内部文档排除规则。

### 验证结果

- `scripts/test.ps1` 以退出码 0 完成：隔离检查、Ruff、第三方报告、后端 `495 passed, 1 skipped`、前端 `131` 项、生产构建和 `43` 个 E2E 均通过。
- GitHub 返回 `lse14/dataset-audit-studio` 为 `public`、`private=false`，默认分支为 `main`。
- 远端 `main` 与本地提交和树哈希一致，根提交无父提交；递归树未截断，且不含内部记录、本地数据、模型、输出或项目运行时。
- 公开树内 `data/` 和 `models/` 仅包含各自 README；第三方合规文件保留在 `docs/`。本地内部记录保持未跟踪，未纳入公开发布。

## README 技术报告定位（2026-08-10）

- [x] 核验 Krea 2 官方技术报告，并在公开 README 中区分参考原则、已实现功能和后续规划。

### 验证结果

- 官方链接 `https://www.krea.ai/blog/krea-2-technical-report` 返回 HTTP 200，页面标题为 `Krea 2 Technical Report - Krea`。
- README 只新增说明文字与外部链接；`git diff --check -- README.md` 通过。README 明确项目独立性，不宣称复刻、隶属或获 Krea 官方背书。
- README 更新已作为 `d536f36` 推送到公开 `main`；公开原始 README 返回 HTTP 200，且包含技术报告链接、已实现功能、待完善事项和独立性声明。

## README LoRA 与 SAE 范围修正（2026-08-10）

- [x] 将公开 README 的处理目标限定为 LoRA 微调训练集，明确 caption 由独立脚本处理。
- [x] 将未完成项限定为基于 SAE 的伪影检测和概念长尾检测，并区分它们与现有 SAE 特征提取/通用语义报告。

### 验证结果

- `analysis.sae` 已实现语义嵌入上的 SAE 训练、激活阈值和代表样本输出；相关聚类测试以退出码 0 完成。
- 当前接口返回的是原始特征信息，尚未向伪影或长尾概念归因，因此 README 将这两项列为未交付功能。
- README 修正已作为 `2cc3a1c` 推送到公开 `main`；公开原始 README 返回 HTTP 200，且包含 LoRA 范围、外部 caption 边界和两项 SAE 规划。

## 叶簇语义近重复审核接入（2026-08-10）

- [x] 核对现有 SigLIP2 embedding、FAISS 语义分组、聚类叶节点、重复审核 API/UI 和导出人工覆盖路径。
- [x] 用户确认采用“候选证据 + 人工审核”，不使用未校准阈值自动排除图片。
- [x] 编写设计与测试先行实施企划，并限定只复用现有 `cluster.hierarchy` 和重复审计能力。
- [-] 已交由独立 Terra/max 对话逐项实现和验证；本对话不并行修改业务源码。
- [ ] 完成 leaf 内 `duplicate_semantic` 生产证据、稳定 group key、相似度与完整 embedding/层级溯源。
- [ ] 完成阈值重跑、暂停恢复、人工保留/排除和 exact/visual 自动导出边界回归。
- [ ] 运行聚焦测试、Ruff、完整后端、前端单元与生产构建并记录结果。

### 验收标准

- 仅在同一个 persisted leaf cluster 内比较语义向量，不跨 scope 或叶簇合并。
- `0.985` 是可配置且未校准的候选阈值；证据必须为 `review_only`，不得自动创建审核决定。
- group key 由稳定 sample ID 生成，不受 scope 内局部索引冲突影响。
- 每个成员保存最高直接邻接相似度、阈值、叶簇、层级配置和实际 SigLIP2 identity。
- 语义候选复用现有重复审核页；只有人工 `approved_exclude` 才通过 `manual_exclude` 影响导出。
- `exclude_exact_visual_duplicates` 继续且只处理 exact/visual 证据。
- 不增加依赖、模型或 caption 处理，不修改源图片与既有人工决定。

### 风险

- 同角色、同画风和相近构图可能达到高相似度，但仍是有效 LoRA 训练变化。
- 连通分量允许 A-B、B-C 传递成组，组内任意两张不保证直接达到阈值。
- 没有本地重复真值集，不能宣称默认阈值的准确率或最佳性。

## 簇语义、目标覆盖与长尾选集（无限期暂缓）

- [?] 无限期暂缓；无目标日期、无执行人、不得估时或随语义去重顺带实施。
- [x] 企划已优化为 `层次簇 -> VLM 批量解释代表样本 -> 人工簇复核 -> 用户目标覆盖 -> 确定性选集建议 -> 人工确认 -> 覆盖复验 -> 导出快照`。
- [x] 已记录现有 `leaf_coverage_order`、平方根配额和簇内多样选择基础；平方根配额只作为用户仅给总预算时的余量 fallback，不能覆盖显式目标。
- [?] 只有用户明确恢复，并确认固定 VLM、用户目标模板、导出预算和独立验收集后，才能逐项重新设计和实施。

### 暂缓边界

- 不实现 caption/PageRank/Wikidata 覆盖，不宣称基础模型级世界知识长尾。
- 不把 LoRA 目标角色或画风的高占比自动解释为需要压低的头部概念。
- scope 固定为 artist/character 一级目录或 general 全局；不得跨 scope 借样本满足目标。覆盖键为 `scope -> dimension -> label`，不统计分辨率。
- 目标支持 `min/max count/share` 与 required/preferred；无法满足时报告 `unmet`，不得复制图片、静默降低目标或冒充 Krea 官方阈值。
- 未来若恢复，只能生成可预览、可撤销、digest-bound 且需人工确认的新导出快照，不删除或改写原集合、人工决定或旧导出。

## SAE 伪影解释与人工审核（无限期暂缓）

- [?] 无限期暂缓；无目标日期、无执行人、不得估时或修改现有 SAE 组件。
- [x] 企划已固定为 `跨任务稳定性验证 -> VLM 批量解释 feature top-k -> 人工确认/驳回 feature -> 确定性逐图候选 -> 人工样本决定`。
- [x] 已记录 corpus/SAE/VLM/prompt provenance、feature stale/supersede、predominant-feature policy、独立真值和合成 100k 验收门。
- [?] 只有用户明确恢复，并具备足够跨任务语料、固定 VLM 与 prompt、feature/sample 伪影真值后，才能从稳定性阶段重新设计。

### 暂缓边界

- 当前 SAE 激活、阈值和 top-k 只能称为原始无监督特征，不是已解释伪影或长尾标签。
- VLM 不逐图调用；纯人工只作为 VLM 失败 fallback。feature 批准不产生样本排除，只有 sample 级 active human keep/exclude 可影响新导出 overlay。
- annotation、feature decision、sample candidate 和 sample decision 四层独立；任一 SAE/top-k/VLM/prompt identity 变化使下游 stale。
- SAE 激活覆盖不等于命名实体或世界知识长尾，不得与聚类配额企划混为同一交付。

## README 证据闭环定位修正（2026-08-10）

- [x] 根据源码与 Krea 2 报告对照，修正公开 README 对当前能力和待完善事项的描述。
- [x] 将项目定位为“证据采集 + 人工复核 + 安全导出”，明确技术/OCR 等多数结果尚未统一接入导出门控。
- [x] 将任务内语义重复审核和分辨率感知技术/OCR 策略列为近期闭环；将聚类语义审核、跨任务长尾和 SAE 伪影/长尾能力标为需要共享语料后再评估的规划。
- [x] 保留 LoRA 范围、caption 独立处理、人工决定和不自动删除源素材的边界。

### 验收与证据

- `backend/dataset_audit_studio/app/duplicate_evidence.py` 当前生产路径仅生成 exact/visual duplicate evidence；`semantic_duplicate_groups()` 仍是未接入生产重复证据的基础算法。
- `backend/dataset_audit_studio/components/cluster_hierarchy/algorithm.py` 生成层级节点和代表样本；`clustering/repository.py` 当前将簇 `label` 保存为 `None`。
- `backend/dataset_audit_studio/components/sae_analysis/runtime.py` 输出 SAE 激活、百分位阈值和 top indices，没有伪影/概念归因检测器。
- README 文档差异检查：`git diff --check -- README.md` 通过；未修改源码、依赖、数据、模型或公开发布排除规则。
- README 已作为 `612d5ac` 推送到公开 `main`；本地和远端提交哈希一致，公开原始 README 返回 HTTP 200 并包含任务内语义重复、共享语料限制和 caption 边界说明。

## README 文风修正（2026-08-10）

- [x] 将公开 README 的技术报告式表述改为面向使用者的短句说明。
- [x] 保留 LoRA 范围、人工复核、caption 独立处理、语义重复、分辨率策略、聚类和 SAE 的事实边界。

### 验证结果

- `git diff --check` 通过；仅 README 被提交。
- 提交 `6dde1d8` 已推送到公开 `main`；远端提交与本地 HEAD 一致，公开原始 README 返回 HTTP 200 并包含“### 先做”和“### 数据多了再做”。

## EXIF 与 OCR 热修复（2026-08-11）

- [x] 确认 EXIF Orientation 越界值会绕过扫描器范围校验，并在 `samples` 约束处导致扫描任务失败。
- [x] 确认 PP-OCRv5 在识别结果只含 blank token 时会对空置信度集合求平均，产生 `NaN`，随后由模块化评分守卫中止任务。
- [x] 将 EXIF Orientation 的范围外整数规范为未知值，并补充 `0` 与 `9` 的回归测试。
- [x] 将 OCR 非有限识别置信度降级为未识别区域，并补充空识别及外部非有限分数的回归测试。
- [x] 将热修复发布为 `0.1.1`，完成项目锁文件同步、受影响测试与静态检查。
- [x] 仅提交公开允许的源码、测试和版本文件，推送至 `main`。
- [x] 从已推送提交制作最终用户可直接覆盖安装目录的 ZIP 与 SHA-256 文件。

### 验收标准

- EXIF `0`、`9` 及其他不在 `1..8` 的整数以 `None` 持久化；合法方向值保持不变，且不需要数据库迁移。
- OCR 识别为空或分数为 `NaN`/无穷时，不中断整个任务；该区域保留检测边界，但文本为空、`recognition_score` 为 `0.0`。
- 模块化评分的通用有限值守卫保持不变，其他组件的非有限输出仍会失败，不能被本热修复静默吞掉。
- 热补丁 ZIP 保留项目根目录相对路径，用户关闭 WebUI 后解压覆盖安装目录并重启即可；不包含数据库、模型、运行时、数据集、测试或内部文档。

### 风险与验证

- 非有限 OCR 置信度仅表示该区域的文字识别不可用，不能伪装为已识别文本或高置信度结果。
- ZIP 必须从已经推送的 `main` 提交生成，并核对其中每个生产文件与该提交一致；压缩包本身提供 SHA-256 供交付校验。
- EXIF 回归先按预期失败：标签 `0` 与 `9` 分别原样返回。范围归一化后，`tests/test_scanner_media.py` 通过 `7 passed`。
- OCR 回归先按预期失败：`NaN` 与无穷分数原样进入区域结果。源端降级后，`tests/test_scoring_numerics.py` 通过 `11 passed`，`tests/test_modular_scoring.py` 在项目隔离环境下通过 `9 passed`，受影响文件 Ruff 通过。
- `uv lock --locked` 通过；`git diff --check` 无输出。前端单测 `131 passed`、生产构建和 Playwright E2E `43 passed` 均以退出码 0 通过。
- [!] `scripts/test.ps1` 的完整后端阶段当前为 `505 passed, 1 skipped, 1 failed`。唯一失败是未修改的 `tests/test_r10_1_contract.py` 仍期望 general profile 的 `embedding.semantic.enabled` 为 `False`，而现有 `profile_contracts.py` 物化为 `True`；此独立语义默认值不在本热修复范围内，已按用户确认保留。
- 发布提交 `ea07a38336ba170e38ed0f9dd30569bfdf1599f8` 已推送到 `origin/main`，远端引用与本地 HEAD 一致。
- 覆盖安装包为 `E:\Desktop\dataset-audit-studio-hotfix-0.1.1.zip`，SHA-256 为 `79d2e1467e1419239767ae6435811ca0fa783072348a67fb5cbc311a40402a86`，旁路校验文件为同目录 `.zip.sha256`。归档逐字节核验了 5 个生产/版本文件均匹配已推送提交；其余内容仅为根目录 `HOTFIX-0.1.1.txt` 与 ZIP 目录条目。

## 统一图像格式导出（已确认）

- [x] 确认在现有复制导出中提供 `保持原格式`、`JPEG`、`PNG`、`WebP`；默认保持原格式。
- [x] 确认 JPEG 遇透明像素时铺白底；PNG 和 WebP 保留透明通道。
- [x] 为预览、创建请求、不可变运行设置和结果摘要增加格式选择，缺省旧记录按保持原格式处理。
- [x] 在导出计划和发布器中实现可验证的逐图转换，保留源身份校验、暂存发布和恢复验证。
- [x] 补充后端与前端回归：默认兼容、三种格式、JPEG 白底、标注同名、清单恢复、失败不发布及界面请求。
- [x] 运行聚焦测试、静态检查、完整受影响后端与前端验证，并记录结果。

### 验收标准

- 用户可在首次和重复复制导出前选择统一格式；未选择时保持当前复制行为和输出扩展名。
- 选择 JPEG、PNG 或 WebP 后，导出图片均使用对应扩展名；配套 `.txt` 标注保留同一基名。
- JPEG 输出不含透明通道，透明区域为白色；PNG/WebP 不丢失透明通道。
- 转换后的输出哈希、尺寸和清单可被暂停恢复及发布后校验；解码或编码错误不发布半成品，源图片不被修改。
- JPEG/WebP 固定质量 `95`，PNG 无损；不增加新的运行时依赖或数据库迁移。

### 风险与边界

- 统一格式是新的导出文件，不保留源图的逐字节身份；但源文件仍在转换前按既有 SHA-256 身份检查。
- 旧运行记录没有格式字段时必须继续恢复为原格式复制，不能因本功能失效。
- 转换只适用于图片；现有动画首帧渲染及配套标注规则保持其原有路径。
- 开发位于隔离分支 `codex/unified-export-format`，工作区为 `C:\Users\lse\.config\superpowers\worktrees\dataset-audit-studio\codex-unified-export-format`；不得直接修改 `main`。
- 隔离工作区的项目内 Python、Node、Pillow 与 Torch 已就绪；`tests/test_export_runs.py` 基线通过。`tests/test_r10_1_contract.py` 仍复现既有 `embedding.semantic.enabled` 默认值断言失败，与本功能无关，不能作为本轮新增回归。

### 完成记录（2026-08-12）

- [x] 新建复制导出可选择 `original`、`jpeg`、`png`、`webp`。转换计划持久化输出哈希、大小和 `transcode_format`，发布时重新编码并沿用现有 SHA-256/大小校验；旧快照缺失字段时在内存中规范为 `original`，不迁移数据库。
- [x] JPEG 以白色背景合成透明像素；PNG/WebP 保留 alpha；JPEG/WebP 质量固定为 `95`，PNG 使用 Pillow 默认无损编码。源图和已有导出树均未改写。
- [x] 同目录同 stem 的异扩展名样本不会在统一格式后合并：首个路径保留常规目标名，冲突项追加源扩展名，仍冲突时追加稳定序号；配对 `.txt`/`.json` 跟随最终图像路径，避免静默丢样本。
- [x] 复核修复：非法 JSON 类型的 `image_format` 原先抛出未处理 `TypeError`；回归后统一返回 `422/export_image_format_invalid`。
- [x] 2026-08-12 新鲜验证：导出相关后端测试组 `79 passed`；Ruff、组件边界、第三方报告、前端单测 `131 passed`、生产构建和导出 Playwright `4 passed` 均通过。独立审阅完成两轮，先发现并复核关闭同 stem 碰撞问题。
- [!] 正确加载项目隔离环境后的完整后端 Pytest 只有既有 `tests/test_r10_1_contract.py::test_r10_1_catalog_and_profiles_remove_three_stage_and_disable_semantic_by_default` 失败（当前配置为 `embedding.semantic.enabled=True`，测试仍期望 `False`）；该文件和默认配置不在本项改动中，按既有用户确认保留。

## Windows 原生路径选择器冷启动优化（2026-08-11）

- [x] 复现并测量：空初始路径下，点击到 `IFileDialog.Show()` 为 `340-631 ms`，原生窗口出现约 `1.05 s`；每次调用都启动新的 Windows PowerShell 并动态编译 C#。
- [x] 用户确认使用常驻 STA 选择器宿主；保留 Windows 11 Explorer 风格、目录/文件模式、取消返回和现有 HTTP 接口。
- [x] 为常驻宿主的复用和关闭清理完成失败后转绿的回归测试；聚焦测试通过。
- [x] 实现并验证 PowerShell 常驻协议：真实脚本在不打开窗口的情况下输出 `READY` 并响应 `QUIT`。
- [x] 将宿主接入 WebUI 的可控预热和关闭生命周期；预热异常不会中止启动，生命周期回归通过。
- [x] 将生产目录选择请求路由到已预热宿主；目录 API 回归通过，未预热时保留一次性进程回退。
- [x] 将本地模型文件选择请求路由到已预热宿主；文件 API 回归通过。
- [x] 运行完整选择器聚焦回归，并验证真实宿主的预热、退出和连续启动耗时：`18 passed`，Ruff 通过；无界面真实宿主首次预热 `505.9 ms`、同一进程第二次 `start()` 为 `0.01 ms`，关闭后退出码为 `0`。
- [?] 为避免中断当前用户已打开的选择窗口，真实目录/文件对话框的连续打开、取消和视觉耗时仍待该窗口关闭后人工确认；目录/文件 API、取消、进程异常和重复点击的自动化回归已通过。

### 验收标准

- 生产 WebUI 启动后复用同一 STA PowerShell 子进程；选择器 C# 类型只编译一次。
- 每次点击仍显示 Windows Common Item Dialog，不回退到 `FolderBrowserDialog` 或网页目录列表。
- 目录和本地 `.safetensors` 文件选择继续返回已验证的绝对路径；取消不改变输入值。
- 选择器宿主在 WebUI 正常关闭时退出；异常退出、无效协议或已停止宿主返回现有错误语义，不留下孤立进程。
- 不增加 Python/Node 依赖，不修改数据、模型、任务记录或其他功能。

### 风险与范围

- 常驻宿主只能消除测得的项目侧冷启动；Windows Shell 本身的约 `0.45 s` 对话框加载不在本次可消除范围内。
- 预热失败不得阻止 WebUI 启动；用户点击时应收到已有的选择器错误响应。
- 当前已有的用户选择窗口不由本次实现关闭或中断。

## 审计页单项选择覆盖层修复（2026-08-11）

- [x] 已确认：任务已处于 `evidence_review` 且用户直接进入风险、画风、重复或美学审计页时，复核提示不得覆盖单项选择控件。
- [x] 新增前端回归，覆盖四个审计页的人工复核提示和单项选择计数。
- [x] 在 `App.tsx` 按当前路由关闭该提示，保持非审计页现有提示。
- [x] 已验证四个审计路由、现有提示行为、生产构建和渲染交互。

### 验收标准

- 直接打开 `#risks`、`#style`、`#duplicates` 或 `#aesthetics` 时，任务的人工复核提示不覆盖页面。
- 在这四页点击一个可复核样本后，选择计数立即显示 `已选 1`。
- 非审计页仍可显示同一任务的人工复核提示；不改变选择 API、后端、审核决定或缩略图预览。

### 风险与边界

- 仅依据当前路由处理提示可见性，不持久化“已读”状态，也不改变任务状态。
- 现有 `modal-backdrop` 的通用行为不修改，避免影响确认对话框和详情弹窗。

### 验证结果

- 项目内 Node 的 `npm test` 通过：`131 passed, 0 failed`。
- 聚焦 Playwright 回归通过：四个审计页均无人工复核对话框，单项勾选后显示 `已选 1`；遗留 `#reviews -> #risks` 路由也通过，合计 `2 passed`。
- 对 `http://127.0.0.1:7865` 的已构建生产包进行 API mock 探针：`#risks`、`#style`、`#duplicates`、`#aesthetics` 均完成单项勾选；桌面和 `390x844` 移动端无控制台警告或错误，移动端无横向溢出；回到 `#tasks` 后人工复核提示仍可见。
- `npm run build` 与本项三个源文件的 `git diff --check` 均以退出码 `0` 完成。

## 可靠性与十万级性能审查修复（2026-08-13）

- [x] 任务 8：按现有失败测试补齐 CF-only 不请求/不初始化 CLIP，并只放宽批量配置上限、保持默认值不变；隔离环境聚焦回归 `14 passed`。
- [x] 任务 9：增加按 sample ID 从已验证 shard 读取 embedding 的公开仓储接口，层次聚类按 scope 加载、语义候选按 leaf 切片；聚焦回归 `35 passed`。
- [x] 修复当前 Ruff 阻断项；全量 `ruff check backend tests scripts` 已通过。
- [x] 将导出服务对 planner 下划线方法的调用收敛为公开契约；保留第二次计划与 fingerprint 一致性校验，完整导出测试模块通过。
- [x] 审计页抑制人工复核弹窗是已确认行为，保留现有 `App.tsx` 与 E2E 约束，不回退。
- [x] 逐项完成聚焦测试后，运行 Ruff、相关后端回归、前端单测和生产构建，并记录实际结果。

### 验收标准

- 仅启用 Community Forensics 且没有其他 CLIP 消费者时，组件计划与 scoring runtime 均不请求 `openai_clip_vit_l14`；启用美学等 CLIP 消费者时仍正常请求。
- 批量字段的新上限满足 `tests/test_cf_only_clip_and_batch_ceilings.py`，现有默认值不变。
- `load_embeddings_for_sample_ids` 只加载包含请求 ID 的重叠 shard，并仅按请求顺序返回对应行；缺失或重复 ID 失败，生产调用继续经过既有 artifact 身份校验。
- `_persist_semantic_duplicates` 对每个 leaf 单独加载向量并生成既有 `review_only` 证据，不改变阈值、分组和人工审核语义。
- Ruff 无错误；聚焦失败测试全部转绿；不覆盖工作区中既有的选择器、导出、前端和测试改动。

### 计划复核与边界

- `docs/superpowers/plans/2026-08-13-reliability-and-100k-performance.md` 的任务 1 基线描述失真：`NativePickerHost` 是本轮从零引入，不是复用 HEAD 既有实现；后续计划应写成“新增宿主并保持既有 HTTP 选择器契约”。
- 规格 3.3“扫描/打分写库批分离”原计划没有对应任务或验收项；现已补为独立任务 10。本轮不借审查修复顺带实现，后续须按新增失败测试逐项落地。
- 任务 9 的文件图应明确包含 embedding shard 存储与注册 artifact 身份校验；逐 leaf 加载不能绕过现有 SHA-256/大小检查。
- 写事务内第二次完整规划仍可能在十万级数据上延长持锁时间；本轮只修复私有 API 耦合，不在缺少性能基准和新失败测试时扩展事务架构。

### 验证结果

- 任务 8：隔离环境下 `tests/test_cf_only_clip_and_batch_ceilings.py` + `tests/test_modular_scoring.py` 为 `14 passed`；覆盖 CF-only 不加载 CLIP、显式 CLIP 仍保留、六个批量上限及默认值不变。
- 任务 9：leaf/shard 新测试、clustering foundations、模块化/兼容聚类与角色一致性合计 `35 passed`；证据仍为 `review_only`。
- `tests/test_export_runs.py` 完整模块通过；service 已只调用 `plan_current`、`finalize_plan`、`plan_fingerprint` 公开契约。
- Ruff 全量、组件边界、第三方依赖报告、`git diff --check` 均通过；前端单测 `137 passed`，生产构建通过。
- 后端全量运行到 `100%`，仅有既有 `tests/test_r10_1_contract.py:23` 失败：测试要求 general profile 默认关闭 `embedding.semantic`，当前确认配置为启用；本轮未改该默认值。
- 未启动 WebUI 或前端开发服务器；常用端口 `7865/4173/4174/5173` 未发现监听。系统级进程枚举因权限拒绝未完成。
- 本轮 pytest 临时目录清理已精确限定到 `.test-tmp` 下的本轮前缀，但审批服务返回 503 并拒绝执行；目录仍保留，可安全重建，未触碰既有 `.tmp-pytest-*` 或代理报告。

## 规格 3.3 扫描/打分推理批与写库批分离企划（2026-08-13）

- [x] 完整核对规格 3.3、现有 Task 10、扫描/模块化评分实现、`TaskService.commit_batch` 原子事务、组件运行 checkpoint 与相关暂停恢复/缓存/有限值测试。
- [x] 将 Task 10 修订为可按 TDD 实施的精确计划，补齐扫描、CLIP、美学、AI、OCR、水印、缓存命中、有限值守卫、`next_index`、`results_prepared`、`component_complete` 和 10 万项事务计数覆盖。
- [x] 确认最小方案采用内部派生写批目标，不增加公共 `write_batch_size` 字段、依赖、迁移或全局环境改动；现有 `batch_size` 默认值及上限保持不变。
- [x] 已由后续实施会话按修订后的 Task 10 红绿步骤完成生产代码与测试，并通过 core/focused pytest 与 Ruff 验证。

### 企划验收与证据

- `scanner/service.py` 与 `app/modular_scoring.py` 已按内部派生目标聚合完整 decode/inference batch；业务行、checkpoint、进度、租约和控制切换仍由单个 `commit_batch` 原子提交。
- CLIP checkpoint 已支持 `feature_shards` 多 shard 登记，并保留旧 `feature_shard` 读取兼容；非 CLIP 结果继续在 `_require_finite` 后进入持久化。
- 扫描 `ScanConfig.cache_payload()` 会包含所有公开字段；公共写批字段会无意义改变 manifest/cache identity。评分公共字段还需穿过组件物化契约，现无用户配置必要性证据，因此选择内部常量与派生值。
- 生产评分子进程由父进程每 30 秒续租，并可能在暂停/终止时强停；企划明确区分合作式控制的已验证前缀 flush 与强停丢弃未提交内存、从最后 `next_index` 恢复。
- 10 万级验收只预先锁定可计算的事务/checkpoint 数量及实际行数一致性；耗时和提升比例必须由实施后的临时 SQLite 基准实测，不预填性能数字。
- 验证结果：Task 10 core `55 passed, 1 skipped`，focused `89 passed, 1 skipped`，Ruff 通过，`git diff --check` 通过。
- 残余风险：本轮未运行真实模型/10 万样本吞吐基准、超过 30 秒的时钟 heartbeat 情形及跨进程强停的真实 OS 时序；不得据此宣称性能提升。

## 全项目缺陷扫描与过时合同修复（2026-08-15）

- [x] 使用项目内 Python、Node 和 Playwright 浏览器运行后端、前端与 E2E 基线，排除未初始化隔离变量造成的环境误报。
- [x] 定位唯一稳定失败为 `tests/test_r10_1_contract.py` 与当前语义重复默认配置互相矛盾。
- [x] 以提交 `3396197`、`test_profile_contracts.py` 和现行 profile 约束为证据，确认生产默认启用是已确认行为；仅修正遗漏的旧 R10.1 测试名称、断言和无效显式赋值。
- [x] 运行完整后端、Ruff、组件边界、第三方报告、前端单测、生产构建和 Playwright E2E，并检查最终差异与临时产物。

### 验收标准

- general profile 的 `embedding.semantic` 与 `cluster.hierarchy` 继续默认启用，artist/character profile 和显式配置行为不变。
- 全量后端不再保留已知 R10.1 失败；前端单测、生产构建和 44 条 E2E 均通过。
- 不增加依赖、不修改数据库/模型/用户数据；最终提交只包含合同测试和项目记录。

### 验证结果

- 最终完整后端 Pytest 运行至 `100%`，退出码 `0`，仅有 1 个预期 skip；R10.1 与 profile 合同聚焦回归为 `12 passed`。
- Ruff、组件导入边界和第三方依赖报告均通过；前端单测 `137 passed`，生产 TypeScript/Vite 构建通过，Playwright E2E `44 passed`。
- 本轮 9 个 `.test-tmp` 临时目录已精确删除；`4174` 与 `7865` 均无监听，未遗留测试或 WebUI 服务。

## 全局前端审美 Skill（2026-08-17）

- [x] 根据已确认的纸白、`#FFFDAB`、标尺侧栏与向右下硬阴影方向，创建全局 `design-lone-trail-frontend` Skill（显示名：孤星风格前端设计）；本项未修改产品前端。
- [x] 无 Skill 的 Terra xhigh 对照错误使用深黑画布、荧光青与安全橙，且未提供标尺侧栏；这些偏差已写入明确的防错约束。
- [x] 使用 Skill 的 Terra xhigh 复测输出纸白主色、局部 `#FFFDAB`、纸质标尺侧栏、桌面 `4-7px` / 移动端 `2-5px` 右下硬阴影，并避开 HUD、黑色侧栏和厚黑框。
- [x] `quick_validate.py` 返回 `Skill is valid!`；安装位置为 `C:\Users\lse\.codex\skills\design-lone-trail-frontend`。
- [x] 根据“视觉语言已成形、信息系统未成形”的评价补充操作秩序：首屏按任务状态、核心数据、操作、品牌排序；导航按 mission/analysis/system 分组；装饰必须有真实数据语义，且不得虚构计数、速度、ETA、告警或任务状态。
- [x] Terra xhigh 使用更新后的 Skill 对现有页面复测，产出与 `App.tsx` 的任务/审计/导出结构一致；进度、审计和导出页均得到紧凑且单一主操作的布局建议。
- [x] 将最终的对齐合同写入全局 Skill：默认无刻度、单一侧栏基线、每个菜单项下方留空隙的短横线、页首与侧栏分隔线同一 Y 坐标、摘要标签/值上下分行；`quick_validate.py` 与独立 Terra xhigh 前向测试均通过。
- [x] 仅以视觉草图探索 Oxygen Blue、Rescue Red 与 Deep Space Black；用户明确这些不是产品实现需求，也不得写入全局 Skill。产品前端仍未改动。

## 深空终端项目重绘探索（2026-08-17）

- [-] 基于现有 `App.tsx` 的真实导航与任务/审计/导出工作流制作项目专用视觉预览；不改动前端源码、接口、数据或交互契约。
- [x] 阅读 Dino Ignacio 的 Dead Space UI 访谈，确认可迁移原则是“功能层优先、装饰必须绑定实际操作、定位信息清晰而不侵入”；项目预览据此将任务到导出的既有流程改为可定位工作流轨道，而非背景特效或虚构仪表。来源：`http://www.inventinginteractive.com/2013/07/10/interview-dino-ignacio-dead-space/`。
- [x] 新建浏览器草图 `layered-audit-station.html`；它不使用边缘强调线、标尺、细线或低密度纯色面板，视觉重量由任务阶段、工作进程与人工复核的厚实分段模块、状态色块和层级投影承担。
- [x] 用户指出第一版深空配色混杂，新增 `warm-holographic-audit-station.html`：主体统一为暖灰铁材与低饱和琥珀投影；青色只表达真实设备/服务信号，空任务页不出现风险红。
- [x] 用户否定暖灰版本的褐色倾向，新增 `scanline-audit-console.html`：深石墨金属为底、低亮度蓝青承担信息投影，旧金色仅保留于导出阶段；工作区底层使用低对比扫描线，不扩散到组件内容。
- [x] 用户提供第一版截图并确认其气质与颜色更好；新增 `first-pass-cyan-console-restored.html` 复原冷青黑色单一色阶、扫描背景、任务控制区格网和仅用于人工复核边界的暖红卡片。
- [x] 创建并全局安装 `C:\Users\lse\.codex\skills\design-dead-space-ui`（显示名：死亡空间风格UI设计）。Skill 固化冷青单色阶、扫描线范围、真实数据映射、人工复核暖红与非侵权边界；结构验证通过，独立 Terra xhigh 前向测试未生成虚构指标或多色 HUD。
- [x] 用户补充 Skill 缺少苍白色调与缺角面板语法；现已改为苍白冷青灰 `#0B1010/#101919/#183030/#DCE9E3/#A6D8D1`，并规定任务控制、流程和复核模块使用右上 `18px` 切角。全新 Terra xhigh 前向测试同时命中两项，且未将切角扩散到小控件。
- [x] 同步新 Skill 的浏览器样例 `pale-chamfer-cyan-console.html`，并恢复原 `http://localhost:50989/` 视觉伴侣服务；该样例沿用真实任务页导航与操作文案，使用苍白青灰和右上切角，不新增数据或控件。
- [x] 用户指出样例背景仍偏深，已将 Skill 与样例底色/侧栏/工作区同步提亮为 `#1A2828/#203131/#304747` 的苍白青灰；服务 HTTP 200，Skill 结构验证通过。
- [x] 最终确定缺角位置与范围：外层 `.sidebar`、任务控制区均为直角；只有当前 `.nav-item.active`、四个流程阶段和人工复核卡使用右下缺角。圆形投影缩至桌面 `104px`、移动端 `72px`。`http://localhost:50989/` 在 `1440x900` 与 `390x844` 均返回 HTTP 200、无横向溢出。
- [x] 所有剩余缺角使用模块自身的 `1px` 外框线闭合包裹；斜边段贴在右下切口边界并与底边、右边相接，不穿过面板内容。桌面与移动端均验证直角任务控制区、右下切角和外框样式。
- [x] 任务控制框最小高度压缩至桌面 `205px`、移动端 `250px`（移动端内容自然高度为 `257px`）；主背景 `6px` 扫描线透明度由 `0.09` 提至最终的 `0.18`。两个视口均确认内容和圆形投影未裁切、无横向溢出。
- [x] 修正右下切口斜边的方向，使其沿缺口从底边连接到右边；此前反向的斜线已移除。桌面渲染确认外框连续且无内容遮挡。
- [?] 用户明确本轮不沿用“孤星风格前端设计”Skill 的既有视觉合同；待确认此分层深空方向后，才创建最小前端实现计划。
- [ ] 用户确认项目专用预览的密度、配色与信息优先级后，再以最小 CSS/壳层改动实现并进行前端构建与交互回归。

## Circular progress refinement (2026-08-17)

- [x] Replaced the quarter-highlight conic ring with one uniform, low-contrast circular progress ring.
- [x] Added the current workflow percentage (`25%`) as centered text inside the ring; no fabricated metric or extra control was added.
- [x] Kept the ring vertically centered in the task-control panel at desktop and mobile widths.

### Verification

- Local preview `http://localhost:50989/` returned HTTP `200`.
- Chrome/Playwright checks at `1440x900` and `390x844` reported a centered ring, `25%` label, and no overlap with `.console-actions`.

## Workflow chamfer and gravity-wave refinement (2026-08-17)

- [x] Removed the chamfer from the artificial-review boundary card; it remains a complete rectangular panel.
- [x] Kept chamfers on workflow stages as the ordering cue.
- [x] Restored the original low-contrast concentric gravity-wave rings around the centered `25%` progress ring, with smaller radii on mobile to avoid clipping.

### Verification

- Chrome/Playwright at `1440x900` and `390x844` reported `reviewClip: none`, a centered ring, and the expected multi-layer `box-shadow` wave rings.

## Scanline visibility and Skill sync (2026-08-17)

- [x] Increased the base scanline to `.28` opacity and added a `.22` scanline layer to the main content background, keeping one `1px` line every `6px`.
- [x] Updated the global `design-dead-space-ui` Skill with the confirmed scanline, workflow-chamfer, rectangular review-card, centered percentage ring, and static gravity-wave rules.

### Verification

- Skill validator returned `Skill is valid!` with UTF-8 mode enabled.
- Chrome/Playwright at `1440x900` and `390x844` confirmed the scanline layers, centered `25%` ring, rectangular review card, and no horizontal overflow.
- Preview returned HTTP `200`.

## Scanline and depth-balance refinement (2026-08-17)

- [x] Reduced scanline strength to base `.16` and work-area `.10` so the texture no longer dominates the composition.
- [x] Added a restrained horizontal material gradient: pale cyan-grey edges at `.12`, a cold-dark center at `.28`.
- [x] Updated the global `design-dead-space-ui` Skill to make depth come from the side-light/center-dark relationship rather than dense scanlines.

### Verification

- Chrome/Playwright at `1440x900` and `390x844` found the expected gradient and scanline layers, clear heading color, and no horizontal overflow.
- Root preview `http://localhost:50989/` returned HTTP `200` from the restored local preview service.
- Skill validator returned `Skill is valid!`.

## 孤星风格前端重构（2026-08-17）

- [x] 仅重构前端视觉层：保留现有路由、导航顺序、字段、接口、控件位置、表格/虚拟列表数据密度与响应式布局。
- [x] 用户确认使用全局 `design-lone-trail-frontend` Skill，并确认仅替换视觉语言，不进行页面或功能重构。
- [x] 以纸白、局部 `#FFFDAB`、黑色结构线和克制右下硬阴影重设应用外壳、导航、页头、共享面板与控件。
- [x] 使用源码契约、生产构建及桌面/移动端浏览器复核，确认无横向溢出、控件遮挡或布局漂移。

### 验收标准

- 现有 `App.tsx` 页面 ID、页面顺序、任务选择器、路由别名、表单字段与数据请求不变。
- 侧栏为纸白单基线导航，默认无刻度；当前项为浅黄色，品牌分隔线与页首分隔线处于同一 Y 坐标。
- 纸白为主色，`#FFFDAB` 仅表达选中、主操作、进度或计数；不出现黑色侧栏、厚黑四边框、霓虹/HUD 效果或虚构业务数据。
- 桌面及窄移动端保持现有断点下的数据可读性、焦点可见性和无水平溢出。

### 验证结果

- 先新增 `loneTrailPresentation.test.mjs` 并观察到缺少 `--lone-paper` 的预期失败；完成 CSS 主题层后该契约通过。
- 隔离工作树前端单测 `138 passed`，`npm run build` 通过；前端完整 Playwright E2E `44 passed`。
- Browser 插件在当前会话不可用，使用经用户授权安装的项目 Playwright Chromium 复核。`1440x900` 与 `390x844` 的任务页均加载 `Dataset Audit Studio`、可触发刷新、没有浏览器 warning/error 且无水平溢出。
- 同一 Playwright 复核的画风审计页完成选择和批准排除交互；桌面截图确认侧栏品牌分隔线与页首线对齐、单基线和浅黄当前项，移动截图确认图标导航与表格卡片未被裁切。
- 临时 QA 脚本与测试截图已删除；`4174` 与 `4175` 仅剩 `TIME_WAIT`，无本地监听服务。

## Full-width scanline and reversed-depth experiment (2026-08-17)

- [x] Moved the scanline layer from the constrained content container to the full main-workspace background so it reaches both edges.
- [x] User rejected dark sides and a pale center. The confirmed material gradient restores pale sides (`.12`) and a cold-dark center (`.28`), and is now written into the global Skill.

### Verification

- Chrome/Playwright at `1440x900` and `390x844` confirmed the full-width main background layer, the restored pale-side/dark-center gradient, and no horizontal overflow.
- Skill validator returned `Skill is valid!`.

## Skill visual reference asset (2026-08-17)

- [x] Saved the current `1440x900` root-preview screenshot as `C:\Users\lse\.codex\skills\design-dead-space-ui\assets\dataset-audit-terminal-reference-1440x900.png`.
- [x] Added a mandatory visual-reference step to `design-dead-space-ui/SKILL.md`, while explicitly forbidding the use of screenshot text or empty-state values as fabricated product data.

### Verification

- The asset exists, is `349,062` bytes, and was visually opened after capture.
- `quick_validate.py` returned `Skill is valid!` with project-local Python in UTF-8 mode.

## 孤星风格前端工作台实施（2026-08-17）

- [x] 合并隔离分支 `codex/lone-trail-workbench-20260817` 的工作台壳层、样式层与视觉契约测试；保留现有路由、接口、页面数据和响应式行为。
- [x] 修正导航序号为 CSS 视觉伪元素，避免改变按钮文本语义；上下文栏先出现在 DOM，再由 grid areas 保持桌面右栏、移动端内容优先，兼容任务选择器和旧 E2E 定位。
- [x] 前端单测 `138/138`、生产构建、`git diff --check` 通过。
- [x] Playwright 全量 E2E `44/44` 通过（单 worker，避免本地 Vite 端口并发竞争）；覆盖桌面/390px 移动视口、任务选择、审计批准、导出切换和旧路由。
- [x] 浏览器插件不可用，使用项目 Playwright Chromium 完成渲染复核；验证纸白单基线侧栏、浅黄当前项、上下文栏和移动端无水平溢出。
- [x] 验证结束后无 `4174` 监听服务；未新增依赖、后端接口或产品数据。
- [x] 修正桌面侧栏基线：竖线从品牌区 `18px` 起连续显示，品牌横线与菜单短横线使用同一右侧留白，避免与基线黏连。静态视觉契约已先失败再通过；前端单测 `139/139`、生产构建与 Playwright `44/44` 均通过，已在运行中的 `http://127.0.0.1:7865/#tasks` 和 `390px` 窄视口截图复核。
- [x] 修正工作台视觉锚点与平衡：任务页的实际 `.toolbar-band` 主操作和当前菜单项均使用黑色右下硬阴影；桌面内容在工作区内居中，`1440px` 保持两侧 `56px` 内距，`1920px` 的最大内容宽度左右外边距均为 `94px`。红绿视觉契约、前端单测 `139/139`、构建、Playwright `44/44` 和桌面/`390px` 截图复核均通过。
- [x] 修正侧栏投影与边界：当前菜单项为硬阴影预留与投影等宽的下方空间，并移除重叠的激活底部短横线；在唯一黑色标尺外恢复浅色壳层分隔线。美学审计保留在审计组和原有路由中，但取消缩进并左对齐。全量前端单测 `139/139`、构建、Playwright `44/44`、桌面和 `390px` 截图复核通过。

## 孤星工作台布局微调（2026-08-17）

- [x] 桌面端将当前任务上下文栏置于工作区最左列，使用右侧内向分隔线；窄屏仍保持主内容在前、上下文在后。
- [x] 页面大标题增加单层低对比右下投影，不影响紧凑标签或操作控件。
- [x] 已将左侧任务栏和标题投影的可复用规则同步至全局 `design-lone-trail-frontend` Skill。
- [x] 按用户复核结果移除所有侧边子菜单文字投影；保留页面大标题投影与当前菜单项的黑色选中硬阴影，并同步更新 Skill。

### 验证结果

- 源码视觉契约先失败后通过；前端单测 `139/139`、生产构建、Playwright `44/44` 和 `git diff --check` 均通过。
- `http://127.0.0.1:7865/#tasks` 的 `1440x900` 与 `390x844` 截图确认：桌面任务栏位于主内容左侧，窄屏无横向溢出且主内容保持优先。
