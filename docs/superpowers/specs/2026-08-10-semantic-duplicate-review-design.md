# 语义近重复审核接入设计

## 状态

- 用户已于 2026-08-10 确认接入语义去重。
- 已确认采用“候选证据 + 人工审核”边界，不允许模型结果直接排除图片。
- 本设计不依赖外部重复数据集；默认阈值仅作为可配置候选阈值，不宣称已经校准。

## 目标

在已经启用 SigLIP2 语义嵌入与分层聚类的任务中，对每个叶子簇内部执行语义近重复搜索，生成可追溯的 `duplicate_semantic` 审核组，并复用现有重复审计页面完成保留或排除决定。

## 非目标

- 不自动创建 `ReviewDecision`，不自动排除任何图片。
- 不把语义候选加入“排除完全和视觉重复”的导出开关。
- 不跨任务、跨一级子文件夹范围或跨叶子簇比较。
- 不训练新的相似度模型，不下载新模型，不要求 caption。
- 不在本事项中实现长尾配额、VLM 聚类命名、SAE 特征标注或伪影检测。
- 不使用不存在的外部真值集声称准确率、召回率或最佳阈值。

## 当前基础

- `clustering/dedupe.py` 已有 `semantic_duplicate_groups()`，使用归一化向量、FAISS 内积范围搜索和连通分量分组。
- `clustering/config.py` 已有默认阈值 `0.985`，但模块化 `cluster.hierarchy` 配置尚未暴露该字段。
- 重复审计后端、API、前端类型和 `DuplicatesPage` 已支持 `semantic_duplicate` / `duplicate_semantic`，但生产流程没有写入该证据。
- 现有重复审计会保留至少一个成员，人工 `approved_exclude` 通过通用人工覆盖层影响导出；`approved_keep` 可恢复资格。
- exact 和 visual 证据由技术指标阶段生成；其现有自动导出开关必须继续只处理这两类证据。

## 方案选择

### 采用方案：在 `cluster.hierarchy` 完成每个 scope 时生成叶簇语义候选

分层聚类已经同时持有当前 scope 的 SigLIP2 向量、叶子节点成员和稳定样本身份。在同一事务中保存聚类树和语义候选，可以确保候选严格属于叶子簇，并沿用现有暂停、恢复、配置身份和分批提交行为。

不新增独立组件。新增组件会扩大注册表、预设、执行编排和 UI 契约，而当前候选的唯一有效范围正是 `cluster.hierarchy` 的叶子节点；在没有独立调度需求时没有必要增加该复杂度。

### 未采用方案：在重复审核 API 请求时即时计算

审核读取路径不持有已验证的 embedding shard，也不应在 GET 请求中执行 FAISS 计算或改变数据库，因此不采用。

### 未采用方案：全任务全局语义搜索

LoRA 数据中同角色、同画风和相近姿势本来就可能高度相似。全局搜索会把有效训练变化混入候选，且不符合 Krea 2 报告描述的叶簇内语义去重范围，因此不采用。

## 配置

在 `HierarchyConfig` 增加：

```python
semantic_duplicate_threshold: float = Field(default=0.985, ge=0.8, le=1.0)
```

该字段由现有组件 schema 自动暴露，前端已经有中文字段名与帮助文本。旧任务缺少字段时使用 Pydantic 默认值；显式保存的未来阈值必须进入组件配置哈希，使阈值变化触发层级阶段重新生成候选，但继续复用已有 embedding shard。

不增加自动排除开关。关闭 `embedding.semantic` 或 `cluster.hierarchy` 时，不运行语义候选生成，前端也继续隐藏语义重复标签。

## 算法与分组身份

对每个 `is_leaf=True` 的节点分别调用语义范围搜索：

1. 只把该叶节点的 `sample_indices` 传给 `semantic_duplicate_groups()`。
2. 以 SigLIP2 归一化内积 `>= semantic_duplicate_threshold` 建立无向边。
3. 延续现有并查集连通分量语义；A-B、B-C 达阈值时可形成同组，但因为结果只供人工复核，不据此自动删除。
4. 为每个成员记录其与组内直接相邻成员的最高达阈值相似度，供审核页显示和后续本地阈值校准。
5. 语义组 key 必须由排序后的稳定 sample ID 计算，不能继续仅使用 scope 内整数索引，否则不同 scope 的相同局部索引会发生 group-key 冲突。

`DuplicateGroup` 增加与 `member_indices` 对齐的只读 `member_scores`；exact/visual 组保持空元组，既有调用行为不变。

## 证据契约

每个语义组成员写入一条 `Evidence`：

| 字段 | 值 |
| --- | --- |
| `code` | `duplicate_semantic` |
| `source` | `semantic_duplicate_siglip2_v1` |
| `value_json` | 稳定 group key |
| `value_number` | 该成员最高直接相似度 |
| `threshold_json` / `threshold_number` | 当前配置阈值 |
| `severity` | `medium` |
| `review_only` | `true` |
| `algorithm_version` | `semantic_duplicate_siglip2_v1` |

`metadata_json` 至少包含：

- `group_key`、`group_size`、`representative_sample_id`；
- `leaf_cluster_key`、`scope_kind`、`scope_id`；
- `hierarchy_config_hash`、`threshold`；
- `model_id`、`model_sha256`、`preprocessing_version`、`embedding_identity_hash`；
- `provenance.component_id=cluster.hierarchy` 与算法版本。

证据不得创建自动审核决定，也不得伪装成已确认重复。

## 数据流

```text
已验证 SigLIP2 shards
  -> scope 内向量
  -> FAISS 分层聚类
  -> 每个 leaf 内阈值范围搜索
  -> 稳定语义重复组
  -> duplicate_semantic review-only evidence
  -> 现有重复审计页
  -> 人工 approved_keep / approved_exclude
  -> 通用人工覆盖层影响导出
```

exact/visual 自动导出过滤继续读取且只读取 `duplicate_exact` 和 `duplicate_visual`。语义候选只有经过人工决定后才通过现有 `manual_exclude` 路径影响导出。

## 重跑、暂停与清理

- 新层级运行第一次持久化 scope 前，删除该任务旧的 `semantic_duplicate_siglip2_v1` 证据，再保存新结果。
- 空任务或没有 scope 时，`prepare_empty_clusters()` 同样清理旧语义证据。
- 暂停后从后续 scope 恢复时不得再次清除已提交 scope 的候选，也不得重复写入同组成员。
- 配置阈值、embedding identity、样本 identity 或层级 identity 变化时必须重新生成候选。
- 人工审核决定属于用户覆盖层，重算证据不得删除或自动改写既有人工决定。

## 异常与兼容行为

- 叶簇不足两张时不产生语义候选。
- 零向量、非有限向量仍沿用 embedding/FAISS 现有验证；不得静默写入非有限分数。
- 没有达到阈值的组时，层级组件仍正常完成。
- 语义证据的 group key 在样本成员和算法输入不变时保持稳定。
- 已保存旧任务无需数据库迁移；新字段由配置模型默认值补齐。
- 不修改源图片、embedding 文件、模型资产或 caption。

## 验收与测试

- 单元测试证明 stable sample IDs 会生成跨 scope 不冲突且可复现的 group key，并记录成员最高直接相似度。
- 集成测试证明只在同一叶簇内成组，跨叶簇和跨 scope 的高相似图不会被合并。
- 证据测试验证完整模型、预处理、阈值、层级和算法溯源，且 `review_only=true`、不存在自动 `ReviewDecision`。
- 重跑测试证明阈值变化复用 embedding shard、删除旧语义证据并写入新结果。
- 暂停恢复测试证明没有重复证据或误清理。
- 审核测试证明现有 `semantic_duplicate` API 能读取生产证据，人工排除后导出理由为 `manual_exclude`，人工保留后恢复。
- 导出回归证明 `exclude_exact_visual_duplicates` 不读取 `duplicate_semantic`。
- 运行聚焦 Pytest、受影响 Ruff、完整后端 Pytest、前端单元测试和现有语义重复 E2E 契约。

## 已知限制

- `0.985` 未经本地重复真值集校准，只能称为默认候选阈值。
- SigLIP2 可能把同角色、同画风、同构图但并非重复的图片判为相近，因此必须保持人工审核。
- 连通分量允许传递连接，组内任意两张不保证都直接达到阈值；成员分数只表示最强直接邻接。
- 小数据集可能不拆分为多个叶簇，此时整个 scope 是一个叶簇，但仍不会跨 scope 搜索。
