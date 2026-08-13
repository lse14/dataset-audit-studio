# 簇语义、目标覆盖与长尾选集企划（无限期暂缓）

## 状态

**[?] 无限期暂缓。** 本文只冻结未来目标、边界和验收门，不代表实施授权；无目标日期、执行人或估时。用户必须再次明确说“恢复簇语义与目标选集企划”或同等含义，才能重新确认当时接口并逐项实施。

## 定位与依据

本企划面向约 100k 图片的 LoRA/FT 训练集，不复刻基础模型预训练语料治理。Krea 2 报告在 midtraining data 中描述了层次聚类、VLM 解释簇及保护长尾视觉概念，但没有公开本项目可直接采用的配额、覆盖目标或阈值：[Krea 2 Technical Report / Midtraining Data](https://www.krea.ai/blog/krea-2-technical-report#midtraining-data)。

本项目未来只实现以下人工策展闭环：

```text
当前 eligible cohort
  -> 层次簇与代表样本
  -> VLM 批量解释簇
  -> 人工复核簇语义
  -> 用户定义目标覆盖
  -> 确定性选集建议
  -> 人工确认
  -> 覆盖复验
  -> 新导出快照
```

caption 继续由项目外独立脚本处理；不引入 Wikipedia、Wikidata、PageRank 或训练器专用包装。

## 固定范围

- `artist_concept` 与 `character_concept`：每个一级目录是独立 scope。
- `general`：整个任务是一个全局 scope。
- 多个一级目录和少数超大一级目录使用同一套 scope-local 算法；跨 scope 只汇总，不借样本满足目标。
- 分辨率不是覆盖统计轴。本企划只消费当前导出上下文已经确定的单一 eligible cohort；最低分辨率资格仍由上游导出合同负责。
- VLM 只批量解释簇代表样本和不确定簇，不逐图调用。
- 用户定义训练目标；profile 最多提供可编辑模板，不替用户推断应学习的角色、画风、姿势或背景。
- 所有自动结果只是 evidence 或 recommendation；不得删除源图、改写任务样本、覆盖既有人工决定或自动排除图片。

## 已有基础与真实缺口

- `components/cluster_hierarchy/algorithm.py::leaf_coverage_order()` 已提供稳定叶簇覆盖顺序。
- `clustering/quota.py::allocate_sqrt_quota()` 已提供先覆盖叶簇、再按簇大小平方根分配预算的基础算法。
- `clustering/quota.py::select_diverse()` 已提供簇内确定性多样选择基础。
- 当前层次簇已有成员和代表样本，但 `clustering/repository.py` 仍以 `label=None` 保存簇；没有语义 annotation、用户目标、选集快照或覆盖复验合同。
- 现有 export run 已有 preview、input digest、manifest、人工 overlay 和源文件只读边界，可作为未来集成点。

这些基础不证明簇具有稳定语义，也不证明平方根配额适合任何 LoRA 目标。

## 未来数据合同

### ClusterAnnotation

每条簇标注至少记录：

- `task_id`、配置 revision/hash、`scope_id`、`cluster_key`、hierarchy hash；
- 稳定排序的代表 sample IDs 与代表输入 digest；
- `label`、`description`、结构化 `facets`、`problem_flags`、confidence；
- `vlm_model_id`、model revision、preprocess version、prompt SHA-256；
- `suggested/approved/rejected/needs_review/failed/stale` 状态。

混合语义、低置信度或代表图不足的簇进入 `needs_review`/`unclassified`，不得伪装成精确逐图标签。簇边界、代表图、VLM 或 prompt 任一身份变化时，旧 annotation 立即 stale。

### CoverageTarget

目标键固定为：

```text
scope -> dimension -> label
```

每个目标支持：

- `min_count`、可选 `max_count`；
- `min_share`、可选 `max_share`；
- `priority=required|preferred`；
- 可选 profile template 与 scope override。

同一 dimension 内标签默认互斥，不同 dimension 可多重计数。目标不可满足时必须输出 `unmet`，不能复制图片、跨 scope 借样本或静默降低要求。这些字段是项目配置，不得表述为 Krea 2 官方阈值。

### SelectionRecommendation

建议必须确定性、可预览、可撤销，并逐项记录：

- `sample_id`、scope、cluster 与 target group；
- `keep/exclude_candidate/downsample_candidate/needs_review`；
- 人工决定、重复组保护、头部过密、尾部保护或目标缺失等 reason codes；
- hierarchy、annotation、target、eligible cohort 和算法 digest。

处理顺序固定为：active human keep/exclude -> 已审核重复组及至少保留一张 -> required 目标保护 -> preferred 目标与头部降采样 -> 簇内多样选择。`allocate_sqrt_quota()` 只可作为用户仅给总预算时的未分配余量策略，不能覆盖显式目标。

### CoverageSnapshot

覆盖报告比较：

```text
broad -> proposed -> approved -> export
```

报告按 `scope -> dimension -> label` 输出数量、比例、目标上下限、delta、`unmet`、`overfull` 和完全消失项，不包含 resolution 字段。它是 digest-bound snapshot，必须绑定 task/config/hierarchy/input/annotation/target/selection digests；任一身份变化时拒绝复用。

## 异常和人工门

- VLM 批次失败、输出 schema 非法或 provenance 不匹配：不发布该批次为 approved annotation，并保留明确失败状态。
- 簇边界或代表样本变化：旧 annotation 与下游 recommendation 标记 stale。
- `required` 目标从非零降为零：阻止批准 snapshot；`preferred` 目标未满足只告警。
- active human exclude 始终优先；active human keep 不得静默丢失，但可以产生 `overfull` 警告。
- 目标冲突或源数据不足：保留 `unmet`，不得伪造样本或自动改写目标。
- export input digest 与 approved snapshot 不一致：fail closed，要求重新预览和人工确认。

## 重新激活前提

恢复前必须重新确认：

1. 当前 hierarchy、duplicate review、export run 和人工 overlay 接口；
2. VLM 模型、revision、资产 SHA-256、预处理、prompt 和本地运行/隐私边界；
3. 用户目标模板、required/preferred 语义及总导出预算；
4. 对簇语义和目标覆盖的人工验收样本；
5. 100k 验收只使用项目内合成 metadata/embedding/hierarchy/VLM fixture，真实用户数据另行授权。

## 未来实施顺序

恢复后一次只实施一项，并在每项验证后停止：

1. 锁定 annotation、target、recommendation、snapshot schema 与 digest 身份；
2. 使用 fake VLM 实现簇级批量解释和人工簇复核；
3. 实现 scope-local 覆盖报告和不可满足目标诊断；
4. 实现非破坏性建议、人工批准/撤销和 stale 检测；
5. 接入 export snapshot 与高级分页 UI；
6. 完成合成 100k 的分块、分页、内存、缓存和确定性验收。

不得因为其中一项获授权而自动启动下一项或 SAE 企划。

## 未来验收门槛

- 相同输入和 digests 产生字节级稳定的建议与 snapshot。
- 多 scope、单个超大 scope、目标冲突、重复组和 `unclassified` 场景均有测试。
- VLM 调用量随簇/不确定簇数量增长，不随图片数量逐图增长。
- 目标、API、覆盖报告和 UI 均不生成分辨率分组。
- 预算足够时 required 目标不被静默清零；不足时稳定报告 `unmet`。
- broad、源图、caption、embedding、人工决定、任务历史和旧导出保持不变。
- 合成 100k fixture 验证分页/分块处理、缓存、失败恢复、内存上界和 digest 不变性。

## 已知风险

- 小数据或不稳定层级可能让“长尾保护”退化为随机压缩。
- SigLIP2 视觉簇不等于角色身份、画风语义或训练价值。
- VLM 可能对代表样本过度解释；人工批准不能被 confidence 替代。
- count/share 目标可能互相冲突，且相同比例在不同 scope 规模下含义不同。
- 平方根配额只是 fallback 启发式，不能宣称优于用户目标或人工选择。

## 恢复规则

恢复后必须先重新进行 brainstorming 并核对届时接口，不得直接照本文编码。R10.2/长尾企划与 SAE 可解释审核必须分别授权。
