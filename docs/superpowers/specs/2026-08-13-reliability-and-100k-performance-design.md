# 可靠性修复与十万级性能优化设计

## 状态

- 用户于 2026-08-13 确认采用方案 2（分层「先正确再吞吐」）。
- 硬件约束：设计默认按消费级 8–12GB 显存安全档；用户本机为 24GB 显存 / 128GB 内存，仅通过**可配置上限**放宽，不抬高全局默认。
- 范围档位：管道级加速（B）+ 全链路均衡（扫描/打分/聚类/审核 UI/导出）。
- 四段设计均已口头确认；实现须按本文执行，可并行多子代理，但不得突破下文契约与范围。

## 目标

1. 修掉已定位的正确性/卡死缺陷（选择器预热、AI `model_id`、前端刷新竞态、SSE、审计页串数据等）。
2. 使约 10 万张本地数据集在预览/创建导出、任务进度刷新、审核翻页时不因写锁或 UI 竞态假死。
3. 在不加新依赖、不改产品契约的前提下，提升扫描/打分/聚类与导出路径吞吐。

## 非目标

- 不引入多进程 worker、外部队列、新 Python/Node 依赖。
- 不改写源图片、已保存任务配置（除显式契约修复）、人工审核决定、已有导出树。
- 不恢复无限期暂缓的簇语义/目标选集或 SAE 伪影企划。
- 不把语义近重复或 Community Forensics 命中改为自动排除。
- 不把本机 24GB 探测结果写成所有用户的默认 batch。

## 方案选择

采用分层方案 2，而不是「只打已知热点」或「大改架构」：

| 层 | 名称 | 目的 |
| --- | --- | --- |
| 1 | 可靠性 | 正确性与启动/关停不卡死 |
| 2 | 锁与 I/O | 十万级导出与 DB 写锁外移 |
| 3 | 管道吞吐 | 扫描/打分/聚类 batch 与 CF-only |
| 4 | 审核 UI | 切任务不串数据、列表虚拟化缺口 |

实施顺序按层 1→4；同层内可多子代理并行，跨层依赖（例如 UI remount 不依赖导出缓存）允许提前开工，但层 1 契约测试必须先于发布合并。

## 全局约束（每项任务隐式继承）

- 仅使用项目内 Python / Node / 模型 / 缓存。
- 不加依赖；不改公开 README 范围声明。
- Community AI、语义近重复、角色一致性均保持 `review_only` + 人工确认边界。
- 旧直接 `scoring.ai` 缺 `model_id` → 必须保留 UFD；新组件默认仍可为 Community Forensics。
- Windows 选择器保持 Common Item Dialog；预热失败只告警，不阻断 WebUI 启动；正常关停清理宿主，不留孤儿进程。
- 统一格式导出语义不变：`original|jpeg|png|webp`、JPEG 白底、stem 碰撞保留、旧运行缺字段按 `original`。
- 风险列表虚拟化：普通映射行保持文档流；仅带 virtual offset 的行可用绝对/transform 定位。
- 内部 `docs/superpowers/`、MEMORY、ROADMAP 不进入公开发布树。

---

## 第 1 段：可靠性修复

### 1.1 Windows 原生选择器宿主

文件：`backend/dataset_audit_studio/workspace/windows_dialog.py`、`main.py`、相关测试。

- `stderr` 改为 `DEVNULL`，或独立线程排空，避免 `PIPE` 写满死锁。
- 等待 `READY` 必须带超时；超时终止子进程并抛错。
- `main` lifespan 预热路径继续只捕获非退出信号的 `Exception` 并告警，不阻断启动。
- `Popen` 成功但尚未赋给 host 字段前失败：必须终止该 PID，禁止孤儿 `powershell.exe`。
- `close()`：若对话框占用导致拿不到同一把锁，对当前 PID 强杀/超时强杀，保证 WebUI 关停可返回。

验收：聚焦 `tests/test_windows_dialog.py`、`tests/test_directory_selection_api.py`；预热超时/失败后健康检查仍 200；关停后无选择器子进程。

### 1.2 AI `model_id` 物化契约

文件：`components/ai_detection/config.py`、任务/组件物化路径（如 `app/component_task_config.py`）、契约测试。

- 物化时：输入缺 `model_id` → **显式写入 UFD**，不得因 Pydantic 默认变成 Community Forensics。
- 新任务/新组件默认仍可为 CF（产品当前默认）。
- 回归：缺字段经物化后 `scoring.ai.model_id` / 组件配置均为 UFD。

### 1.3 前端任务刷新与 SSE

文件：`frontend/src/hooks/useSelectedTaskData.ts`、`useTaskEventRefresh.ts`、`transport/taskEvents.ts`、`pages/ModelsPage.tsx`。

- `loadTaskData`：request 世代（递增 id 或 AbortController）；过期响应不得 `setState`。
- 首次 `loadTaskData` 返回 `null` 后：fallback 轮询一旦拿到有效 `after`，必须重开 SSE，不得永久停在仅轮询。
- SSE 订阅与后端实际发出的事件类型对齐，或增加不依赖枚举白名单的等价刷新触发，避免漏刷新却关掉 fallback。
- Models 本地导入：`pickerBusy` 时禁用主提交按钮（逻辑早退与 UI 一致）。
- 审计页 `key={selectedTaskId}` remount 见第 4 段，不在本段重复改布局。

### 1.4 语义相似度默认阈值

- `ClusteringConfig` 缺字段默认与 profile / `HierarchyConfig` 已锁定值对齐（以现有契约测试为准，目标一致为 `0.92`，消除与 `0.985` 漂移）。
- 不宣称该阈值已经本地真值校准；语义证据仍为未校准候选。

---

## 第 2 段：锁与 I/O

### 2.1 导出 create / preview 写锁外移

文件：`export_runs/service.py`、`planner.py`、相关测试。

- 短读/短写事务：状态校验 + 读出规划所需样本与设置。
- **转码与输出哈希在写锁外**完成。
- 再用短写事务比对 `preview_digest` 并插入 `ExportRun`。
- 禁止在 `IMMEDIATE` 写事务内对全量样本调用 `encode_export_image`。

### 2.2 避免双次 encode

- 规划期结果：进程内缓存（键：`source_path + mtime/size + format`）或 staging 旁路字节/哈希，供执行期 `tree_publisher` 复用。
- 缓存可丢：丢失仅导致再 encode，不得丢样本或改变 digest 语义。
- 默认缓存上限偏保守；可配置抬高（适配 128GB 内存），默认不对所有用户开数 GB。

### 2.3 执行期心跳批量

文件：`export_runs/executor.py`。

- 按 N 个文件或 T 秒批量更新 `next_file` / progress / heartbeat（默认 N 约 32–64，可配）。
- 崩溃恢复仍靠现有 checkpoint；staging 覆盖写保持幂等。

### 2.4 风格 eligibility

- `_style_scope_identities`（或等价路径）改为 SQL/`IN` 集合过滤 AI 排除与 domain 证据，避免 Python 全表加载再过滤。
- 身份 digest 的确定性 payload 不变。

### 2.5 清理与索引

- 删除 `planner.py` 中 `if False` 死分支；保留真实 `preview:` digest 计算。
- 索引仅在查询计划证明需要时添加；优先复用已有 `ix_evidence_*` / `ix_samples_*`；新增须走 Alembic 且兼容 clean-slate。

验收：导出契约测试全绿；可测断言「转码不在 write_session 内」；stem 碰撞 / JPEG 白底不变。

---

## 第 3 段：管道吞吐

### 3.1 默认 batch vs 可配上限

- **新任务默认**保持或仅小幅校准到 8–12GB 显存安全档（scoring / CLIP / 风格 / 语义 embedding / OCR recognition 等）。
- **Field 上限**放宽到适配 24GB 档（例如 embedding/CLIP/风格 `le` 上调）；UI/API 允许用户手动调大。
- `device: auto` 继续自适应；**禁止**按本机探测自动改写默认 batch。

### 3.2 CF-only 不白算 CLIP

- 仅 Community Forensics 且未启用依赖 CLIP 的组件（如美学）时：不加载、不跑 CLIP ViT-L/14。
- 审计 `TorchScoringRuntime`、组件物化、`resolve_models`、模块化路径；堵住仍拉 CLIP 的缺口。
- 回归：CF-only 资产/runtime 断言不含 CLIP（或 CLIP runtime 未初始化）。
- 与 1.2 并存：旧缺 `model_id`→UFD；新默认 CF。

### 3.3 扫描 / 打分写库

- 推理批与写库批分离；写库批默认可略大于推理批以减少事务次数。
- 不改样本身份、证据版本、模块化有限值守卫。
- 进度/心跳更新可按 N 或 T 聚合（与导出心跳同思路）。

### 3.4 聚类 / 语义候选

- leaf 内语义相似：按 leaf 流式取 embedding，避免全任务一次物化。
- 查询只取所需列；禁止为候选生成 `SELECT *` 全表。
- `duplicate_semantic` 仍为 `review_only`；阈值语义不变。

### 3.5 风格特征

- 与 2.4 eligibility 衔接，避免重复全表扫。
- Gram 权重为 0 时继续跳过 VGG；补回归防回退。

验收：相关评分/聚类/角色一致性聚焦测试全绿；默认配置不被过度抬高。

---

## 第 4 段：审核 UI

### 4.1 切任务 remount

- `StylePage` 已有 `key={selectedTaskId}`；为 `RisksPage` / `DuplicatesPage` / `AestheticsPage` 同样添加。
- remount 清除页内 offset、勾选、详情态。

### 4.2 大列表虚拟化缺口

- 四页审计保持服务端 `limit=100` 分页；不一次拉全库。
- 单页内含缩略图且仍 `items.map` 的列表：优先 Risks，其次 Duplicates/Aesthetics/Style，补齐 `@tanstack/react-virtual`（已有依赖）。
- 风险行：未提供 virtual offset 的内容保持文档流；禁止回归叠字。
- 不扩大默认 `limit`。

### 4.3 Models 忙态

- 见 1.3：`pickerBusy` 禁用提交。

### 4.4 明确不做

- 不重做审计页视觉布局；不改批量排除确认与自动选择语义；不改缩略图 API。

验收：连切任务不串数据；审计四页勾选从空开始；虚拟化后无叠字；桌面 + 窄屏无横向溢出；相关前端单测 / 聚焦 E2E 或 mock 探针通过。

---

## 测试与验证策略

1. **TDD**：每个缺陷/热点先写失败回归，再最小实现，再确认转绿。
2. **聚焦优先**：按层跑受影响 Pytest / 前端单测 / 相关 Playwright；全量套件在合并前或发布前跑。
3. **已知基线**：`tests/test_r10_1_contract.py` 对 general `embedding.semantic.enabled=False` 的旧期望若仍失败，且未在本事项改该默认，则记录为既有基线，不作为本事项回归失败（除非本事项改动触及该断言）。
4. **验证记录**：每层完成后更新本地 MEMORY/ROADMAP（不推入公开树）。

## 风险

- 导出缓存键错误可能导致错误复用转码结果 → 键必须含 path/mtime/size/format，并有错配测试。
- 批量心跳增大崩溃窗口 → staging 必须幂等覆盖；N/T 可配且默认保守。
- 抬高 Field 上限后用户手填过大 batch 可能 OOM → 文档/校验保留上限，默认不变。
- 选择器强杀可能打断用户未关对话框 → 仅在 WebUI 关停或超时路径使用。

## 实施与协作

- 实现计划写入 `docs/superpowers/plans/`（另文），推荐 `subagent-driven-development`，同层任务可多开子代理。
- 代码改动可提交到隔离分支或按用户指示提交；内部 spec/plan 默认不进入公开发布。
