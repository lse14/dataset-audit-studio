# Kaloscope v3 阈值标定报告（已应用）

标定集：画风 746 张 / 13 个画师 + 60 张截图对照；角色 576 张 / 12 个角色。全部在 box 上用 v3 权重重算特征，未在你的机器上跑重负载。

## 一、最终采用的参数

| 项 | 旧值 | 新值 |
| --- | --- | --- |
| `StyleConfig.outlier_sigma` | 0.522 | **1.8** |
| `StyleConfig.minimum_style_score` | 92.07 | **30.0** |
| `_CHARACTER_OUTLIER_SIGMA` | 2.04 | **2.2** |
| `_CHARACTER_MINIMUM_CENTROID_SIMILARITY`（新增） | 无 | **0.72** |
| `_CHARACTER_MAX_ITERATIONS` | 2 | 2（不变） |
| `CHARACTER_CONSISTENCY_ALGORITHM_VERSION` | siglip2_character_consistency_v1 | **siglip2_character_consistency_v2** |

## 二、画风（artist_style）

| 指标 | 旧默认 0.522 / 92.07 | 新默认 1.8 / 30 |
| --- | --- | --- |
| 同画师误判（686 张） | 412（60.1%） | **3（0.44%）** |
| 小文件夹误判（n=8 抽样） | 185/312（59.3%） | **0/312** |
| 截图对照召回（60 张） | 100% | 97–100% |
| 异画师召回（每文件夹混 2/3/5 张） | 100% | 94.2% / 90.6% / 90.6% |
| 同图 11 种变形误判 | 5 张 | **0 张** |
| 3% / 5% / 10% / 20% 污染召回 | 100% / 100% / 100% / 100% | 100% / 97.1% / 93.7% / 84.3% |

`sigma` 在 1.4–2.1 之间结果基本不变，实际起作用的是 `minimum_style_score` 从 92 降到 30。

## 三、角色（cluster_hierarchy）

原来的算法只有「相对阈值」一道闸门：`centroid_similarity < mean - sigma*std`。问题是在紧致的小文件夹里，标准差极小，阈值会掉到 0.09 这种荒谬的水平，塞进去一张完全不同的角色也抓不到。

所以改成两道闸门同时满足才判离群：

1. 相对阈值：`centroid_similarity < mean - sigma*std`（sigma = 2.2）
2. 绝对下限：`centroid_similarity < 0.72`

| sigma / floor | 同角色误判 | 注入召回 k=2 / k=3 / k=5 | 紧致小文件夹能抓到异角色 |
| --- | --- | --- | --- |
| 2.04 / 无（旧） | 69/576（12.0%） | 81.9% / 82.9% / 75.3% | 否（阈值塌陷） |
| 2.6 / 0.76 | 8/576（1.4%） | 46.7% / 52.8% / 36.0% | 否（sigma 太大） |
| **2.2 / 0.72（采用）** | **7/576（1.22%）** | **33.3% / 42.8% / 37.0%** | **是** |

选 2.2 / 0.72 而不是 2.6 / 0.76 的原因：2.6 时相对闸门已经失效，实际只剩绝对下限一道，等于退回单阈值；2.2 保住了两道闸门，误报还更低。

## 四、为什么两套加权不一样（复核结论）

- 画风：Kaloscope v3（256 维）+ DINOv2，权重 0.892 / 0.108，判定是「平均相似度低于阈值 **且** 加权分低于 minimum_style_score」。
- 角色：只用 SigLIP2（1152 维），无加权、无打分，只有相似度阈值。
- 这就是角色必须在算法里补一道绝对下限、而画风不用改算法的根本原因。

## 五、验证

| 项目 | 结果 |
| --- | --- |
| box 后端全量 pytest | 585 passed / 16 skipped |
| box ruff lint | 全部通过 |
| box 前端单测 | 146/146 |
| box 前端 tsc | 通过 |
| box 前端 build | 通过，dist 20 文件 |
| 你机器 角色+画风测试 | 23 passed |
| 你机器 modular clustering | 9 passed |
| 你机器 文件哈希 | 5 个文件逐个与 box 一致 |

## 六、向后兼容

- 画风：旧任务的配置里带显式 `outlier_sigma` / `minimum_style_score`，不受新默认值影响；只有新建任务才用新默认。
- 角色：`algorithm_config_hash` 变了，旧任务的证据带的是旧 hash，但没有下游按旧 hash 校验的地方，旧任务照常可读。
- 备份：`C:\Users\lse\das-backup-before-calib\` 和 `frontend\dist-old-calib\`。