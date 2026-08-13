# SAE 伪影解释与人工审核企划（无限期暂缓）

## 状态

**[?] 无限期暂缓。** 本文只冻结未来目标、边界和验收门，不代表实施授权；无目标日期、执行人或估时。用户必须再次明确说“恢复 SAE 企划”或同等含义，才能重新确认模型、数据和接口并逐项实施。

## 定位与依据

Krea 2 报告公开的链路是：在预训练语料的 SigLIP-2 embedding 样本上训练 SAE，VLM 根据每个 feature 的 top-k 激活样本标注 feature，再提取逐图主要 feature，用于过滤明显视觉伪影：[Krea 2 Technical Report / Pretraining Data](https://www.krea.ai/blog/krea-2-technical-report#pretraining-data)。报告没有公开 SAE 规模、feature 稳定性指标、主要 feature 规则、伪影分类表或过滤阈值。

本项目面向 LoRA/FT 数据集，因此未来只采用人工审核版本：

```text
跨任务只读 corpus
  -> SAE 稳定性验证
  -> 现有 SAE artifact
  -> VLM 批量解释 feature top-k
  -> 人工确认/驳回伪影 feature
  -> 确定性逐图伪影候选
  -> 人工 keep/exclude 样本
```

VLM 不逐图调用；feature 被批准为伪影也不得直接排除任何图片。

## 固定边界

- 不重做或替换现有 SAE 训练算法；先验证现有输出是否具备可用稳定性。
- 不删除源图，不改写 embedding、SAE artifact、caption 或既有人工决定。
- 不把 feature ID 直接称为已知概念，不把单任务 feature 推广为通用伪影检测器。
- feature annotation、feature decision、sample candidate 和 sample decision 是四层独立记录。
- 只有 sample 级 active human `approved_keep/approved_exclude` 才能沿用通用人工 overlay 影响导出。
- SAE 激活覆盖不等于命名实体或世界知识长尾；概念覆盖探索与簇目标选集企划保持独立。

## 已有基础与真实缺口

- `components/sae_analysis/runtime.py` 已能在 SigLIP2 embedding 上训练 SAE。
- `SAEAnalysis` 当前只有 state dict、activations、thresholds、top indices 和 losses。
- SAE artifact 已保存 sample IDs、feature count、thresholds、top indices 和 losses，并绑定缓存身份。
- `ReviewService.list_sae_features()` 与 `GET /reviews/sae/features` 只能分页读取匿名 feature 和代表样本。
- 当前没有 VLM annotation、feature 决策、predominant-feature policy、逐图 candidate 或样本写入链；既有只读行为不能称为伪影检测器。

## 阶段 A：跨任务 SAE 稳定性门

先定义只读 corpus manifest，绑定 task/sample identity、SigLIP2 模型 SHA-256、预处理、embedding digest、SAE config、seed 和代码版本。至少比较：

- 相同 corpus/config/seed 的 artifact 与 top-k 可复现性；
- 不同 seed 与训练切分下 top-k overlap、激活相关性和 feature matching；
- feature splitting/merging、dead feature 和只在单个 task 激活的 dataset-specific feature；
- 已知伪影、正常视觉因素、目标角色/画风在 feature 空间中的混淆。

如果跨运行无法建立可解释的 feature matching，停止在阶段 A；不得以 feature ID 相等假设语义稳定，也不得进入 VLM 标注。

## 阶段 B：VLM feature annotation

VLM 固定为批量解释每个 feature 的 top-k 代表样本；纯人工只作为 VLM 失败后的 fallback，不再作为与 VLM 并列的未决架构。

每条 annotation 至少绑定：

- task/corpus manifest、SAE cache key 与 artifact SHA-256；
- feature ID、稳定排序的 top-k sample IDs、激活值摘要和 input digest；
- VLM model ID、revision、资产 SHA-256、preprocess version、prompt SHA-256；
- `label`、`description`、`rationale`、confidence、problem flags；
- `artifact_candidate/normal_visual_factor/content_or_style/dataset_specific/mixed/unknown`；
- `suggested/approved/rejected/needs_review/failed/stale`。

批次内每个 feature 必须有明确状态；不得静默漏项或把解析失败当 `unknown`。相同 provenance 下只重试 `failed/needs_review`。SAE artifact、top-k、VLM 或 prompt 任一身份变化时旧 annotation 立即 stale，人工批准不得自动迁移。

## 阶段 C：人工 feature 复核

- 人工可以批准、驳回或标记需要更多代表样本。
- 只有人工批准且 category=`artifact_candidate` 的 feature 能传播到逐图候选。
- `mixed/unknown/dataset_specific/content_or_style` 不传播为伪影候选。
- feature 决策必须记录 annotation digest、审核人/来源、时间和 supersede 关系。
- feature 批准只改变 feature review state，不创建 sample decision，也不改变导出资格。

## 阶段 D：确定性逐图候选

逐图映射只读取现有 SAE activations、thresholds 和人工 approved artifact features。未来必须先版本化 predominant-feature policy；首版最小语义为：

- 对每张图按 activation 降序、feature ID 升序稳定排序；
- 只考虑 activation 有限且高于该 feature artifact threshold 的 approved features；
- 记录 feature ID、activation、threshold、图内 rank、annotation digest、feature-decision digest、SAE identity 和 policy version；
- 生成 `sae_artifact_candidate` evidence，不直接生成 `approved_exclude`。

top-N、activation margin 或多 feature 组合阈值必须在恢复设计时用独立真值确认；不得把当前 percentile threshold 或 Krea 未公开规则当成已校准判废阈值。

## 阶段 E：样本人工决定

- 候选进入现有风险审核语义，可按 feature/category/confidence/task scope 分页浏览。
- 人工逐图或批量确认后，才产生 sample 级 active keep/exclude，并沿用通用 supersede/撤销合同。
- `approved_keep` 可覆盖候选但不删除 evidence；`approved_exclude` 只影响新导出 overlay，不改 broad cohort 或源图。
- feature 决策撤销或 provenance stale 时，下游候选失效；既有 sample 人工决定保留审计记录，但不得被静默重新解释。

## 重新激活前提

恢复前必须重新确认：

1. 足够的跨任务本地只读语料，覆盖多个角色、画风、构图和已知技术伪影；
2. 固定 SigLIP2、SAE config/seed 和稳定性评价方法；
3. 一个项目内固定 revision、大小、SHA-256、许可与隐私边界的 VLM；
4. 固定 VLM prompt、结构化输出 schema、批大小和失败/重试策略；
5. feature 级与 sample 级独立真值，能报告误报、漏报和分类混淆；
6. 用户重新确认模型成本、交互、候选阈值和验收门。

## 未来实施顺序

恢复后一次只实施一项，并在每项验证后停止：

1. corpus manifest、稳定性指标与 feature matching；
2. annotation/decision/candidate contracts 和 provenance；
3. fake VLM 批量 feature annotation 与失败恢复；
4. feature 人工复核和 stale/supersede 行为；
5. 逐图候选映射与 sample 人工决定；
6. 高级分页 UI、独立真值评估与合成 100k 验收。

不得因为阶段 A 通过而自动授权真实 VLM，或因为 SAE 企划获授权而启动簇目标选集企划。

## 未来验收门槛

- 相同 corpus/config/seed 的 artifact、top-k 与 stability report 可复现。
- 不同 seed/切分下报告 feature matching 质量，不能只比较 feature ID。
- VLM 调用量随 feature 数量增长，不随图片数量逐图增长。
- annotation 具备完整 SAE/top-k/model/prompt/input provenance 和明确失败状态。
- 未经人工批准的 feature 不产生逐图伪影候选；feature 批准不产生样本排除。
- predominant-feature mapping 在相同输入下确定性一致，并覆盖非有限 activation、阈值边界、并列 rank 和 stale identity。
- 独立保留任务报告按伪影类别的 precision、recall、false-positive rate 和主要混淆；只展示 top-k 代表图不算验证。
- 源图、caption、embedding、SAE artifact、broad cohort、既有人工决定和旧导出均不被改写。
- 合成 100k activation/top-k/fake-VLM fixture 验证分块、分页、缓存、重试、内存上界和 digest 不变性；真实权重和用户数据需另行授权。

## 已知风险

- feature permutation、splitting/merging 和 dead features 会破坏跨训练身份。
- top-k 代表图会诱导 VLM 过度解释，生成流畅但错误的标签。
- 小语料容易把目标角色、画风或常见构图误判为伪影。
- VLM 许可、更新、成本和隐私会增加本地资产及复现负担。
- percentile threshold 是激活统计量，不是伪影概率或自动过滤阈值。

## 恢复规则

恢复后必须从阶段 A 重新核对语料和稳定性，并进行 brainstorming；不得直接照本文编码。SAE 企划与簇语义/目标覆盖企划必须分别授权。
