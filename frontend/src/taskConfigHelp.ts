import type { JsonSchema } from './types'

const SCHEMA_FIELD_HELP: Record<string, string> = {
  recursive: '开启后会扫描源数据目录下的所有子文件夹。',
  resolutions: '选择要建立审计结果的分辨率档位；同一图片会按可用档位参与处理。',
  batch_size: '每批交给当前组件处理的图片数。增大可提高吞吐，但会增加内存或显存占用。',
  cpu_workers: '媒体扫描时并行使用的 CPU 进程数。不要超过机器可用核心数。',
  bucket_step: '分辨率分桶的边长步进，用于归类尺寸相近的图片。',
  maximum_aspect_ratio: '超过此长宽比的图片会被标记为技术风险。',
  crop_loss_warning: '为了适配目标分辨率而裁切的面积达到该比例时给出警告。',
  upscale_warning: '为了适配目标分辨率需要放大的倍率达到该值时给出警告。',
  metrics_max_side: '计算技术质量指标前，图片最长边会被限制到该尺寸以控制开销。',
  fft_max_side: '计算频域指标前，图片最长边会被限制到该尺寸。',
  max_decode_pixels: '允许解码的最大像素数，用于防止异常超大图片占用过多内存。',
  thresholds: '技术质量检测使用的各项阈值集合。',
  minimum_rgb_entropy: 'RGB 熵低于该值时，图片颜色信息不足，可能是纯色或低质量样本。',
  maximum_black_ratio: '黑色像素比例超过该值时，图片会被标记为过暗风险。',
  maximum_white_ratio: '白色像素比例超过该值时，图片会被标记为过亮风险。',
  minimum_laplacian_variance: '清晰度方差低于该值时，图片可能过于模糊。',
  maximum_high_frequency_ratio: '高频成分比例超过该值时，图片可能包含异常噪点或锐化伪影。',
  maximum_border_ratio: '边缘区域占比超过该值时，图片可能带有边框或留白。',
  maximum_blockiness: '块效应高于该值时，图片可能存在明显压缩伪影。',
  minimum_luminance_std: '亮度标准差低于该值时，图片的明暗变化不足。',
  device: '选择当前组件的推理设备。自动模式会按本机可用硬件选择。',
  precision: '选择推理精度。较低精度通常更快、更省显存，但可能影响兼容性。',
  in_domain_threshold: '目标域得分达到该值的图片会被视为符合目标域。',
  jtp_max_sequence: '目标域模型一次处理的最大序列长度，较大值会增加显存占用。',
  candidate_threshold: 'AI 图得分达到该值时会列为候选，供后续参考阈值或人工复核使用。',
  reference_threshold: 'AI 图得分达到该值时会被视为更强的风险参考。',
  bitmap_threshold: 'OCR 前景二值化阈值，用于区分文字与背景。',
  box_threshold: 'OCR 文本框置信度阈值，较高值会减少弱候选文本框。',
  unclip_ratio: 'OCR 文本框向外扩展比例，用于覆盖完整文字边界。',
  min_size: 'OCR 识别的最小文字尺寸，小于该值的候选会被忽略。',
  max_candidates: '单张图片最多保留的 OCR 文本候选数。',
  recognition_batch_size: 'OCR 文字识别的单批数量。增大可提高吞吐，但会增加显存占用。',
  review_threshold: '风险得分达到该值时会进入人工复核候选。',
  text_density_threshold: '文字覆盖面积达到该比例时，会作为 OCR 风险证据。',
  minimum_scope_size: '建立画师风格统计前，单个画师目录至少需要的样本数。',
  max_iterations: '风格离群检测的最大迭代次数。',
  outlier_sigma: '与风格中心偏离超过该标准差倍数的样本会被标记为离群。',
  minimum_style_score: '风格一致性得分低于该值的样本会被标记为风险。',
  lsnet_weight: 'LSNet 画师特征在综合风格分中的权重。',
  gram_weight: '纹理 Gram 特征在综合风格分中的权重。',
  dino_weight: 'DINO 语义特征在综合风格分中的权重。',
  gram_average_weight: '平均纹理距离在风格判定中的权重。',
  gram_centroid_weight: '纹理中心距离在风格判定中的权重。',
  shard_size: '特征缓存的单个分片大小。较大分片减少文件数，较小分片降低单次内存压力。',
  feature_count: 'SAE 稀疏自编码器保留的特征数量。',
  epochs: 'SAE 训练遍数。更多轮次会增加耗时。',
  learning_rate: 'SAE 训练学习率。',
  l1_coefficient: 'SAE 稀疏正则强度。值越大，激活通常越稀疏。',
  activation_percentile: '用于筛选 SAE 激活的百分位阈值。',
  top_k: '每张图片保留的最高激活 SAE 特征数量。',
  seed: '随机过程的种子。保持不变可提高结果可复现性。',
  scope_mode: '决定聚类是在全局范围还是按画师范围内执行。',
  minimum_split_size: '达到该样本数的簇才会继续拆分。',
  target_leaf_size: '分层聚类期望的叶子簇大小。',
  max_branching: '单个聚类节点允许拆分出的最大子簇数。',
  kmeans_iterations: '每次 K-means 聚类最多迭代次数。',
  phash_max_distance: '感知哈希距离不高于该值的图片会被视为近似重复候选。',
  colorhash_max_distance: '颜色哈希距离不高于该值的图片会被视为近似重复候选。',
  semantic_duplicate_threshold: '语义相似度达到该值的图片会被视为语义近重复候选。',
  aesthetic_minimum: '美学得分低于该值的图片不会进入导出结果。',
  maximum_ratio: '从同一候选集合中最多保留的比例。',
  technical_strictness: '技术风险筛选的严格程度。',
  keep_annotation_files: '导出时保留与图片同名的 TXT 和 JSON 标注文件。',
  keep_latent_files: '导出时保留可用的 Latent 缓存文件。',
  mikazuki_enabled: '允许复用 Mikazuki 生成的缓存。',
  mikazuki_namespaces: '允许读取的 Mikazuki 缓存命名空间列表。',
  single_file_rules: '单文件 Latent 缓存的匹配规则。',
  name: '规则名称，用于区分多条缓存匹配规则。',
  pattern: '用于匹配文件名或路径的规则表达式。',
  cache_kind: '匹配到的缓存类型。',
  refuse_nonempty_output: '开启后，输出目录已有文件时会拒绝写入，避免误覆盖。',
}

const COMPONENT_HELP: Record<string, string> = {
  'media.scan': '扫描可处理媒体并建立分辨率、路径和基础输入记录。',
  'metrics.technical': '计算图片尺寸、比例和技术质量指标，并生成对应风险证据。',
  'feature.clip_l14': '提取 CLIP 图像特征，供评分和后续分析组件使用。',
  'score.aesthetic_domain': '评估图片的美学质量和目标域匹配程度。',
  'detect.ai': '识别可能由 AI 生成的图片并产出风险候选。',
  'evidence.ocr': '检测图片中的文字并生成 OCR 风险证据。',
  'evidence.watermark': '检测可能存在的水印并生成风险证据。',
  'style.artist': '分析画师范围内的风格一致性并标记离群样本。',
  'embedding.semantic': '生成用于语义相似度和聚类的图像向量。',
  'analysis.sae': '训练并分析稀疏特征，辅助复核风格和语义模式。',
  'cluster.hierarchy': '按语义向量建立可浏览的分层聚类结果。',
  'review.decisions': '保存人工复核决定并将其纳入后续导出选择。',
  'latent.resolve': '查找并复用已有 Latent 缓存，减少重复计算。',
  'export.dataset': '按已确认的筛选规则复制入选数据集文件到输出目录。',
}

const TASK_CONFIG_HELP: Record<string, string> = {
  task_preset: '选择已保存的任务配置。应用预设会替换当前对话框中的配置值。',
  dataset_profile: '选择内置数据集配置。配置会确定适用的工作区与默认组件组合。',
  preset_name: '保存任务预设时显示的名称，不会修改已有任务名称。',
  task_name: '任务的显示名称，用于在任务列表、进度和导出记录中识别本次处理。',
  source_root: '待审计训练集所在的本地目录。创建任务前必须填写。',
  output_root: '任务结果和导出数据写入的目录。copy 模式可按配置留空。',
  runtime_device: '为评分组件统一选择推理设备。自动模式会依据当前硬件决定。',
  runtime_precision: '为评分组件统一选择推理精度。较低精度通常更省显存。',
  aesthetic_bins: '开启后，导出结果会按美学评分分档组织。',
}

export function schemaFieldHelp(name: string, label: string, schema: JsonSchema): string {
  const description = schema.description?.trim()
  if (description) return description
  const known = SCHEMA_FIELD_HELP[name]
  if (known) return known
  const range = numericRange(schema)
  const options = schema.enum?.map(String).join('、')
  const defaultValue = schema.default === undefined ? '' : `默认值为 ${String(schema.default)}。`
  if (range) return `${label}。允许范围 ${range}。${defaultValue}`
  if (options) return `${label}。可选值：${options}。${defaultValue}`
  return `${label}。请按当前任务需求设置。${defaultValue}`
}

export function componentHelpText(componentId: string, displayName: string): string {
  return COMPONENT_HELP[componentId] ?? `控制“${displayName}”组件是否参与本次任务处理。`
}

export function taskConfigHelp(name: string): string {
  return TASK_CONFIG_HELP[name] ?? '此项用于配置本次任务的处理行为。'
}

function numericRange(schema: JsonSchema): string | null {
  const minimum = schema.minimum ?? schema.exclusiveMinimum
  const maximum = schema.maximum ?? schema.exclusiveMaximum
  if (minimum === undefined && maximum === undefined) return null
  if (minimum === undefined) return `不高于 ${maximum}`
  if (maximum === undefined) return `不低于 ${minimum}`
  return `${minimum} 至 ${maximum}`
}
