import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useId, useState } from 'react'

import type { JsonSchema, JsonValue } from '../types'
import { workspaceDisplayName } from '../profileWorkspace'
import { schemaFieldHelp } from '../taskConfigHelp'
import { FieldHelp } from './FieldHelp'

type ObjectValue = Record<string, JsonValue>

export function SchemaObjectFields({
  root,
  schema,
  value,
  onChange,
  disabled = false,
}: {
  root: JsonSchema
  schema: JsonSchema
  value: ObjectValue
  onChange: (value: ObjectValue) => void
  disabled?: boolean
}) {
  const resolved = resolveSchema(root, schema)
  const properties = resolved.properties ?? {}
  return (
    <div className="schema-field-grid">
      {Object.entries(properties).filter(([key]) => key !== 'enabled').map(([key, property]) => (
        <SchemaField
          disabled={disabled || property.readOnly === true}
          key={key}
          name={key}
          onChange={(next) => onChange({ ...value, [key]: next })}
          root={root}
          schema={property}
          value={value[key] ?? defaultForSchema(root, property)}
        />
      ))}
    </div>
  )
}

function SchemaField({
  root,
  schema,
  name,
  value,
  onChange,
  disabled,
}: {
  root: JsonSchema
  schema: JsonSchema
  name: string
  value: JsonValue
  onChange: (value: JsonValue) => void
  disabled: boolean
}) {
  const resolved = resolveSchema(root, schema)
  const type = schemaType(resolved)
  const label = fieldLabel(name, resolved.title)
  const help = schemaFieldHelp(name, label, resolved)
  const fieldId = useId()

  if (type === 'boolean') {
    return (
      <div className="schema-field schema-toggle-field">
        <div className="schema-toggle-row">
          <label className={`toggle-control schema-toggle ${disabled ? 'disabled' : ''}`}>
            <input
              aria-label={label}
              checked={value === true}
              disabled={disabled}
              onChange={(event) => onChange(event.target.checked)}
              type="checkbox"
            />
            <span className="toggle-track"><i /></span>
            <strong>{label}</strong>
          </label>
          <FieldHelp label={label} text={help} />
        </div>
      </div>
    )
  }

  if (type === 'object') {
    const objectValue = isObject(value) ? value : {}
    if (!resolved.properties || Object.keys(resolved.properties).length === 0) {
      return (
        <JsonObjectField
          disabled={disabled}
          help={help}
          label={label}
          onChange={onChange}
          value={objectValue}
        />
      )
    }
    return (
      <fieldset className={`schema-fieldset ${disabled ? 'disabled' : ''}`}>
        <legend><span>{label}</span><FieldHelp label={label} text={help} /></legend>
        <SchemaObjectFields
          disabled={disabled}
          onChange={onChange}
          root={root}
          schema={resolved}
          value={objectValue}
        />
      </fieldset>
    )
  }

  if (type === 'array') {
    const values = Array.isArray(value) ? value : []
    const itemSchema = resolveSchema(root, resolved.items ?? {})
    if (schemaType(itemSchema) === 'object') {
      return (
        <ObjectArrayField
          disabled={disabled}
          help={help}
          label={label}
          onChange={onChange}
          root={root}
          schema={resolved}
          value={values}
        />
      )
    }
    return (
      <PrimitiveArrayField
        disabled={disabled}
        help={help}
        itemSchema={itemSchema}
        label={label}
        onChange={onChange}
        value={values}
      />
    )
  }

  if (resolved.enum) {
    return (
      <div className="field schema-field">
        <span className="field-label"><label htmlFor={fieldId}>{label}</label><FieldHelp label={label} text={help} /></span>
        <select
          id={fieldId}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          value={String(value ?? '')}
        >
          {resolved.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {enumLabel(String(option))}
            </option>
          ))}
        </select>
      </div>
    )
  }

  if (type === 'integer' || type === 'number') {
    const minimum = resolved.minimum ?? resolved.exclusiveMinimum
    const maximum = resolved.maximum ?? resolved.exclusiveMaximum
    return (
      <div className="field schema-field">
        <span className="field-label"><label htmlFor={fieldId}>{label}</label><FieldHelp label={label} text={help} /></span>
        <input
          id={fieldId}
          disabled={disabled}
          max={maximum}
          min={minimum}
          onChange={(event) => {
            if (event.target.value === '') return
            onChange(Number(event.target.value))
          }}
          step={resolved.multipleOf ?? (type === 'integer' ? 1 : 'any')}
          type="number"
          value={typeof value === 'number' ? value : ''}
        />
      </div>
    )
  }

  const multiline = name.includes('prompt') || name.includes('template')
  return (
    <div className={`field schema-field ${multiline ? 'span-2' : ''}`}>
      <span className="field-label"><label htmlFor={fieldId}>{label}</label><FieldHelp label={label} text={help} /></span>
      {multiline ? (
        <textarea
          id={fieldId}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
          value={typeof value === 'string' ? value : ''}
        />
      ) : (
        <input
          id={fieldId}
          disabled={disabled}
          maxLength={resolved.maxLength}
          minLength={resolved.minLength}
          onChange={(event) => onChange(event.target.value)}
          pattern={resolved.pattern}
          value={typeof value === 'string' ? value : ''}
        />
      )}
    </div>
  )
}

function PrimitiveArrayField({
  label,
  help,
  itemSchema,
  value,
  onChange,
  disabled,
}: {
  label: string
  help: string
  itemSchema: JsonSchema
  value: JsonValue[]
  onChange: (value: JsonValue) => void
  disabled: boolean
}) {
  const fieldId = useId()
  const serialize = (items: JsonValue[]) => items.map(String).join(', ')
  const [draft, setDraft] = useState(() => serialize(value))
  const [invalid, setInvalid] = useState(false)
  useEffect(() => setDraft(serialize(value)), [value])

  const commit = () => {
    const parts = draft.split(/[,\n]/).map((part) => part.trim()).filter(Boolean)
    if (schemaType(itemSchema) === 'integer' || schemaType(itemSchema) === 'number') {
      const numbers = parts.map(Number)
      if (numbers.some((item) => !Number.isFinite(item))) {
        setInvalid(true)
        return
      }
      setInvalid(false)
      onChange(numbers)
      return
    }
    setInvalid(false)
    onChange(parts)
  }

  return (
    <div className="field schema-field span-2">
      <span className="field-label"><label htmlFor={fieldId}>{label}</label><FieldHelp label={label} text={help} /></span>
      <input
        aria-invalid={invalid}
        disabled={disabled}
        id={fieldId}
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
        value={draft}
      />
      {invalid ? <small className="field-error">请输入用逗号分隔的有效数字</small> : null}
    </div>
  )
}

function ObjectArrayField({
  root,
  schema,
  label,
  help,
  value,
  onChange,
  disabled,
}: {
  root: JsonSchema
  schema: JsonSchema
  label: string
  help: string
  value: JsonValue[]
  onChange: (value: JsonValue) => void
  disabled: boolean
}) {
  const itemSchema = resolveSchema(root, schema.items ?? {})
  const minimum = schema.minItems ?? 0
  const maximum = schema.maxItems ?? Number.POSITIVE_INFINITY
  return (
    <fieldset className="schema-fieldset schema-array-fieldset">
      <legend><span>{label}</span><FieldHelp label={label} text={help} /></legend>
      <div className="schema-array-items">
        {value.map((item, index) => (
          <div className="schema-array-item" key={index}>
            <header>
              <strong>{label} {index + 1}</strong>
              <button
                className="icon-button danger-icon"
                disabled={disabled || value.length <= minimum}
                onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}
                title="删除项目"
                type="button"
              >
                <Trash2 size={15} />
              </button>
            </header>
            <SchemaObjectFields
              disabled={disabled}
              onChange={(next) => onChange(value.map((current, itemIndex) => (
                itemIndex === index ? next : current
              )))}
              root={root}
              schema={itemSchema}
              value={isObject(item) ? item : {}}
            />
          </div>
        ))}
      </div>
      {value.length < maximum ? (
        <button
          className="button secondary compact-button"
          disabled={disabled}
          onClick={() => onChange([...value, defaultForSchema(root, itemSchema)])}
          type="button"
        >
          <Plus size={15} />
          添加项目
        </button>
      ) : null}
    </fieldset>
  )
}

function JsonObjectField({
  label,
  help,
  value,
  onChange,
  disabled,
}: {
  label: string
  help: string
  value: ObjectValue
  onChange: (value: JsonValue) => void
  disabled: boolean
}) {
  const fieldId = useId()
  const [draft, setDraft] = useState(() => JSON.stringify(value, null, 2))
  const [invalid, setInvalid] = useState(false)
  useEffect(() => setDraft(JSON.stringify(value, null, 2)), [value])
  const commit = () => {
    try {
      const parsed: unknown = JSON.parse(draft)
      if (!isObject(parsed as JsonValue)) throw new Error('not an object')
      setInvalid(false)
      onChange(parsed as ObjectValue)
    } catch {
      setInvalid(true)
    }
  }
  return (
    <div className="field schema-field span-2">
      <span className="field-label"><label htmlFor={fieldId}>{label}</label><FieldHelp label={label} text={help} /></span>
      <textarea
        aria-invalid={invalid}
        disabled={disabled}
        id={fieldId}
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        rows={4}
        value={draft}
      />
      {invalid ? <small className="field-error">请输入有效的 JSON 对象</small> : null}
    </div>
  )
}

function resolveSchema(root: JsonSchema, schema: JsonSchema): JsonSchema {
  let current = schema
  const visited = new Set<string>()
  while (current.$ref) {
    if (visited.has(current.$ref)) break
    visited.add(current.$ref)
    const prefix = '#/$defs/'
    if (!current.$ref.startsWith(prefix)) break
    current = root.$defs?.[current.$ref.slice(prefix.length)] ?? current
  }
  if (current.anyOf) {
    return current.anyOf.find((item) => schemaType(item) !== 'null') ?? current
  }
  return current
}

function schemaType(schema: JsonSchema): string {
  if (Array.isArray(schema.type)) return schema.type.find((item) => item !== 'null') ?? 'string'
  return schema.type ?? (schema.properties ? 'object' : 'string')
}

function defaultForSchema(root: JsonSchema, schema: JsonSchema): JsonValue {
  const resolved = resolveSchema(root, schema)
  if (resolved.default !== undefined) return cloneJson(resolved.default)
  const type = schemaType(resolved)
  if (type === 'object') {
    return Object.fromEntries(
      Object.entries(resolved.properties ?? {}).map(([key, property]) => [
        key,
        defaultForSchema(root, property),
      ]),
    )
  }
  if (type === 'array') return []
  if (type === 'boolean') return false
  if (type === 'integer' || type === 'number') return resolved.minimum ?? 0
  return resolved.enum?.[0] ?? ''
}

function cloneJson<T extends JsonValue>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function isObject(value: JsonValue): value is ObjectValue {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const FIELD_LABELS: Record<string, string> = {
  recursive: '遍历子文件夹',
  resolutions: '分辨率档位',
  batch_size: '单批处理数量',
  cpu_workers: 'CPU 并发数',
  bucket_step: '分桶步进',
  maximum_aspect_ratio: '最大长宽比',
  crop_loss_warning: '裁切损失警告阈值',
  upscale_warning: '放大倍率警告阈值',
  metrics_max_side: '技术指标最长边',
  fft_max_side: '频域指标最长边',
  max_decode_pixels: '最大解码像素数',
  excluded_directory_names: '跳过的目录名',
  thresholds: '技术质量阈值',
  minimum_rgb_entropy: '最小 RGB 熵',
  maximum_black_ratio: '最大黑色比例',
  maximum_white_ratio: '最大白色比例',
  minimum_laplacian_variance: '最小清晰度方差',
  maximum_high_frequency_ratio: '最大高频比例',
  maximum_border_ratio: '最大边缘比例',
  maximum_blockiness: '最大块效应',
  minimum_luminance_std: '最小亮度标准差',
  device: '推理设备',
  precision: '推理精度',
  in_domain_threshold: '目标域阈值',
  jtp_max_sequence: '最大序列长度',
  candidate_threshold: 'AI 候选阈值',
  reference_threshold: 'AI 参考阈值',
  bitmap_threshold: 'OCR 二值化阈值',
  box_threshold: 'OCR 文本框阈值',
  unclip_ratio: 'OCR 文本框扩展比例',
  min_size: 'OCR 最小文字尺寸',
  max_candidates: 'OCR 最大候选数',
  recognition_batch_size: 'OCR 识别批量',
  review_threshold: '复核阈值',
  text_density_threshold: '文字密度阈值',
  minimum_scope_size: '最小画师样本数',
  max_iterations: '最大迭代次数',
  outlier_sigma: '离群标准差倍数',
  minimum_style_score: '最低风格分',
  lsnet_weight: 'LSNet 风格特征权重',
  gram_weight: '纹理特征权重',
  dino_weight: '语义特征权重',
  gram_average_weight: '平均纹理权重',
  gram_centroid_weight: '中心距离权重',
  shard_size: '缓存分片大小',
  feature_count: 'SAE 特征数量',
  epochs: 'SAE 训练轮数',
  learning_rate: '学习率',
  l1_coefficient: '稀疏正则系数',
  activation_percentile: '激活百分位',
  top_k: '每图保留特征数',
  seed: '随机种子',
  scope_mode: '聚类范围',
  minimum_split_size: '最小拆分簇大小',
  target_leaf_size: '目标叶子簇大小',
  max_branching: '最大分支数',
  kmeans_iterations: '聚类迭代次数',
  phash_max_distance: '感知哈希距离',
  colorhash_max_distance: '颜色哈希距离',
  semantic_duplicate_threshold: '语义近重复阈值',
  aesthetic_minimum: '美学最低分',
  maximum_ratio: '最多保留比例',
  technical_strictness: '技术严格度',
  keep_annotation_files: '保留同名标注文件',
  keep_latent_files: '保留 latent 缓存',
  mikazuki_enabled: '复用 Mikazuki 缓存',
  mikazuki_namespaces: 'Mikazuki 命名空间',
  single_file_rules: '单文件 Latent 规则',
  name: '规则名称',
  pattern: '文件名匹配模式',
  cache_kind: '缓存类型',
  refuse_nonempty_output: '拒绝写入非空目录',
}

function fieldLabel(name: string, fallback?: string): string {
  return FIELD_LABELS[name] ?? fallback ?? name.replaceAll('_', ' ')
}

function enumLabel(value: string): string {
  const labels: Record<string, string> = {
    auto: '自动',
    artist: '每个画师文件夹',
    global: '全局',
    fatal: '致命问题',
    high: '高风险',
    medium: '中风险',
    anima: 'Anima',
    krea2: 'Krea 2',
    single: '单模型',
    max: '最大值融合',
    vote: '投票',
    weighted_mean: '加权平均',
  }
  return labels[value] ?? workspaceDisplayName(value)
}
