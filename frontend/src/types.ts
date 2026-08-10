export type PageId =
  | 'tasks'
  | 'progress'
  | 'risks'
  | 'style'
  | 'duplicates'
  | 'aesthetics'
  | 'exports'
  | 'models'
  | 'system'

export type Health = {
  status: string
  app_version: string
  runtime: {
    isolated: boolean
    python_version: string
    python_executable: string
    project_root: string
    models_root: string
    data_root: string
    user_site_enabled: boolean
  }
  database: {
    path: string
    journal_mode: string
    foreign_keys: boolean
    busy_timeout_ms: number
  }
  worker: {
    enabled: boolean
    running: boolean
    owner: string | null
    active_task_id: string | null
    supported_phases: string[]
  }
  models: {
    registered_models: number
    custom_models: number
    ready_models: number
    runtime_ready_models: number
    active_operations: number
    models_root: string
  }
}

export type Task = {
  id: string
  name: string
  source_root: string
  output_root: string | null
  status: string
  resume_state: string | null
  current_config_revision: number
  config_hash: string
  config: Record<string, unknown>
  progress_current: number
  progress_total: number | null
  row_version: number
  execution_epoch: number
  lease_owner: string | null
  lease_expires_at: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export type TaskList = {
  items: Task[]
  total: number
  offset: number
  limit: number
}

export type ExportRunSettings = {
  output_root: string
  minimum_resolution: number
  domain_minimum: number | null
  exclude_exact_visual_duplicates: boolean
  style_outlier_mode: 'off' | 'strong' | 'all'
  aesthetic_minimum: number | null
  minimum_folder_images: number
  add_repeat_prefix: boolean
  sample_seen_mode: 'off' | 'auto' | 'manual'
  sample_seen_target: number | null
}

export type ExportRunCreateInput = ExportRunSettings & {
  preview_digest: string
}

export type ExportRun = {
  id: string
  task_id: string
  task_config_revision: number
  config_hash: string
  selection_version: number
  output_root: string
  output_key: string
  minimum_resolution: number
  resolutions: number[]
  domain_minimum: number | null
  exclude_exact_visual_duplicates: boolean
  style_outlier_mode: 'off' | 'strong' | 'all'
  aesthetic_minimum: number | null
  minimum_folder_images: number
  add_repeat_prefix: boolean
  sample_seen_mode: 'off' | 'auto' | 'manual'
  sample_seen_target: number | null
  preview_digest: string | null
  settings: Record<string, JsonValue>
  aesthetic_identity: Record<string, JsonValue> | null
  status: string
  checkpoint: Record<string, JsonValue>
  input_digest: string | null
  execution_epoch: number
  progress_current: number
  progress_total: number | null
  bytes_current: number
  bytes_total: number | null
  file_count: number
  manifest_path: string | null
  manifest_sha256: string | null
  summary: Record<string, JsonValue> | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export type ExportRunList = {
  items: ExportRun[]
  total: number
  offset: number
  limit: number
}

export type ExportRunPreview = {
  task_id: string
  minimum_resolution: number
  domain_minimum: number | null
  exclude_exact_visual_duplicates: boolean
  style_outlier_mode: 'off' | 'strong' | 'all'
  aesthetic_minimum: number | null
  minimum_folder_images: number
  add_repeat_prefix: boolean
  sample_seen_mode: 'off' | 'auto' | 'manual'
  sample_seen_target: number | null
  preview_digest: string
  input_digest: string
  included_count: number
  exclusion_counts: Record<string, number>
  folder_below_minimum: Record<string, number>
  folders: Array<Record<string, unknown>>
  warnings: string[]
}

export type TaskDeleteResult = {
  task_id: string
  cache_cleared: boolean
  cache_cleanup_error: string | null
}

export type TaskPreset = {
  id: string
  name: string
  components: Record<string, ComponentConfigValue>
  profile: DatasetProfile | null
  row_version: number
  created_at: string
  updated_at: string
}

export type TaskPresetList = {
  items: TaskPreset[]
  total: number
}

export type TaskPresetDeleteResult = {
  preset_id: string
}

export type TaskEvent = {
  sequence: number
  event_type: string
  from_status: string | null
  to_status: string | null
  payload: Record<string, unknown>
  created_at: string
}

export type TaskEventList = {
  items: TaskEvent[]
  next_after: number
  latest_sequence: number
}

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type JsonSchema = {
  $ref?: string
  $defs?: Record<string, JsonSchema>
  anyOf?: JsonSchema[]
  type?: string | string[]
  title?: string
  description?: string
  default?: JsonValue
  enum?: JsonValue[]
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  additionalProperties?: boolean | JsonSchema
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
  multipleOf?: number
  pattern?: string
  readOnly?: boolean
}

export type ComponentManifest = {
  id: string
  version: string
  phase_order: number
  config_schema: string
  consumes: Array<{ capability: string; optional: boolean }>
  produces: string[]
  model_ids: string[]
  execution: 'cpu_inline' | 'cpu_process' | 'gpu_process'
  failure_policy: 'stop'
  default_enabled: boolean
  display_name: string
  ui_group: string
  activation: 'required' | 'auto' | 'optional'
  recommended_enabled: boolean
  json_schema: JsonSchema
  default_config: Record<string, JsonValue>
}

export type ComponentList = {
  items: ComponentManifest[]
  total: number
}

export type DatasetProfile = 'artist_concept' | 'character_concept' | 'general'

export type BuiltinProfile = {
  id: DatasetProfile
  display_name: string
  description: string
  scope_mode: 'concept' | 'global'
  profile_owned_component_ids: string[]
  profile_owned_config_fields: Record<string, string[]>
  components: Record<string, ComponentConfigValue>
}

export type BuiltinProfileList = {
  items: BuiltinProfile[]
  total: number
}

export type RuntimeTuningRecommendation = {
  hardware: {
    cuda_available: boolean
    free_vram_bytes: number | null
    total_vram_bytes: number | null
    available_memory_bytes: number | null
  }
  device: 'cpu' | 'cuda'
  precision: 'float32' | 'float16'
  updates: Record<string, Record<string, JsonValue>>
}

export type ComponentConfigValue = {
  enabled: boolean
  config: Record<string, JsonValue>
}

export function mergeBuiltinProfileComponents(
  current: Record<string, ComponentConfigValue>,
  profile: BuiltinProfile,
  preserveUserManagedSettings: boolean,
): Record<string, ComponentConfigValue> {
  const materialized = cloneComponentConfig(profile.components)
  if (!preserveUserManagedSettings) return materialized

  const profileOwned = new Set(profile.profile_owned_component_ids)
  for (const [componentId, currentValue] of Object.entries(current)) {
    const materializedValue = materialized[componentId]
    if (!materializedValue) continue
    if (!profileOwned.has(componentId)) {
      materialized[componentId] = cloneComponentConfig(currentValue)
      continue
    }

    const profileConfigFields = profile.profile_owned_config_fields[componentId] ?? []
    materialized[componentId] = {
      enabled: materializedValue.enabled,
      config: {
        ...cloneComponentConfig(currentValue.config),
        ...Object.fromEntries(profileConfigFields
          .filter((field) => field in materializedValue.config)
          .map((field) => [field, materializedValue.config[field]])),
      },
    }
  }
  return materialized
}

function cloneComponentConfig<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export type ComponentRun = {
  component_id: string
  component_version: string
  phase: string
  phase_order: number
  execution: 'cpu_inline' | 'cpu_process' | 'gpu_process'
  status: 'pending' | 'running' | 'paused' | 'completed' | 'terminated' | 'failed'
  config_hash: string
  config_digest: string
  input_digest: string | null
  model_digest: string | null
  normalized_config: Record<string, JsonValue>
  dependency_ids: string[]
  model_ids: string[]
  checkpoint: Record<string, JsonValue>
  completed_items: number
  total_items: number | null
  auto_enabled: boolean
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export type ComponentRunList = {
  task_id: string
  config_hash: string
  items: ComponentRun[]
  total: number
}

export type TaskOverview = {
  samples_total: number
  samples_valid: number
  cluster_nodes: number
  leaf_clusters: number
  ready_artifacts: number
  evidence_codes: Array<{ name: string; count: number }>
  review_counts: Array<{ category: string; decision: string; count: number }>
}

export type CoverageScope = {
  scope_id: string
  broad_sample_count: number
  embedding_count: number
  missing_embedding_count: number
  embedding_status: string
  hierarchy_status: string
  leaf_coverage_status: string
  leaf_count: number | null
  single_leaf: boolean | null
  leaf_assigned_count: number | null
  unassigned_count: number | null
  leaf_size_histogram: number[] | null
  singleton_leaf_count: number | null
  singleton_sample_share: number | null
  largest_leaf_sample_share: number | null
  top_five_leaf_sample_share: number | null
  bottom_half_leaf_sample_share: number | null
  style_summary: {
    evidence_count: number
    missing_count: number
    finite_score_count: number
    score_status: string
    score_min: number | null
    score_median: number | null
    score_max: number | null
    review_only_count: number
    algorithm_version: string | null
    algorithm_versions: string[]
    algorithm_version_status: string
  } | null
}

export type CoverageReport = {
  schema_version: 'coverage-report/v1'
  status: string
  resolution: number
  profile: DatasetProfile
  coverage_type: string | null
  identity_assessment: string | null
  scope_count: number
  scope_size_histogram: number[]
  scope_size_distribution_status: string
  single_leaf_scope_count: number | null
  single_leaf_scope_share: number | null
  single_leaf_scope_status: string
  scopes: CoverageScope[]
}

export type ReviewDecision = 'pending_review' | 'approved_keep' | 'approved_exclude'

export type AIReviewItem = {
  sample_id: string
  relative_path: string
  artist_scope: string
  probability: number
  threshold: number
  reference_threshold: number
  decision: ReviewDecision
  decision_source: string
}

export type StyleReviewItem = {
  sample_id: string
  relative_path: string
  artist_scope: string
  style_score: number
  threshold: number
  strong_outlier: boolean
  reason: string | null
  decision: ReviewDecision
  decision_source: string
}

export type StyleAuditClassification = 'normal' | 'outlier' | 'strong_outlier'

export type StyleAuditItem = {
  sample_id: string
  relative_path: string
  artist_scope: string
  style_score: number
  threshold: number
  classification: StyleAuditClassification
  reason: string | null
  review_eligible: boolean
  decision: ReviewDecision | null
  decision_source: string
}

export type StyleAuditList = {
  items: StyleAuditItem[]
  total: number
  normal: number
  outlier: number
  strong_outlier: number
  pending: number
  approved_keep: number
  approved_exclude: number
  offset: number
  limit: number
}

export type AestheticAuditReason =
  | 'missing'
  | 'non_finite'
  | 'out_of_range'
  | 'provenance_mismatch'
  | 'ambiguous'

export type AestheticAuditItem = {
  sample_id: string
  relative_path: string
  artist_scope: string
  score: number | null
  bucket: number | null
  reason_code: AestheticAuditReason | null
  review_eligible: boolean
  decision: ReviewDecision | null
  decision_source: string
}

export type AestheticAuditList = {
  items: AestheticAuditItem[]
  total: number
  bucket_counts: Record<string, number>
  invalid_counts: Record<AestheticAuditReason, number>
  pending: number
  approved_keep: number
  approved_exclude: number
  offset: number
  limit: number
}

export type DuplicateAuditEvidenceType =
  | 'exact_duplicate'
  | 'visual_duplicate'
  | 'semantic_duplicate'

export type DuplicateGroupMemberAuditItem = {
  sample_id: string
  relative_path: string
  artist_scope: string
  score: number | null
  decision: ReviewDecision | null
  decision_source: string
  review_eligible: boolean
  pixel_area: number | null
  resolutions: number[]
}

export type DuplicateGroupAuditItem = {
  group_key: string
  evidence_type: DuplicateAuditEvidenceType
  member_count: number
  pending: number
  approved_keep: number
  approved_exclude: number
  effective_retained_count: number
  members: DuplicateGroupMemberAuditItem[]
}

export type DuplicateGroupAuditList = {
  items: DuplicateGroupAuditItem[]
  total: number
  pending: number
  approved_keep: number
  approved_exclude: number
  unresolved: number
  offset: number
  limit: number
}

export type CuratedReviewEvidenceType =
  | 'aesthetic'
  | 'risk'
  | 'style_outlier'
  | 'duplicate'
  | 'exact_duplicate'
  | 'visual_duplicate'
  | 'semantic_duplicate'

export type CuratedReviewItem = {
  sample_id: string
  relative_path: string
  artist_scope: string
  evidence_type: CuratedReviewEvidenceType
  reason_code: string
  score: number | null
  severity: string | null
  candidate_group: string | null
  decision: ReviewDecision
  decision_source: string
}

export type ReviewList<T> = {
  items: T[]
  total: number
  pending: number
  approved_keep: number
  approved_exclude: number
  offset: number
  limit: number
}

export type SAEFeature = {
  feature_id: number
  threshold: number
  top_sample_ids: string[]
  representative_samples: Array<{
    sample_id: string
    relative_path: string
  }>
}

export type SAEFeatureList = {
  cache_key: string
  items: SAEFeature[]
  total: number
  offset: number
  limit: number
}

export type ClusterList = {
  items: Array<{
    cluster_id: string
    cluster_key: string
    scope_kind: string
    scope_id: string
    level: number
    size: number
    total_size: number
    folder_size: number
    representative_sample_id: string | null
    representative_path: string | null
  }>
  total: number
  offset: number
  limit: number
}

export type FolderList = {
  items: Array<{
    folder_id: string
    display_name: string
    sample_count: number
    leaf_cluster_count: number
    risk_sample_count: number
    risk_evidence_count: number
  }>
}

export type ClusterSampleList = {
  items: Array<{
    sample_id: string
    relative_path: string
    artist_scope: string
    score: number | null
    is_representative: boolean
    manually_excluded: boolean
  }>
  total: number
  offset: number
  limit: number
}

export type RiskEvidence = {
    evidence_id: string
    sample_id: string
    relative_path: string
    artist_scope: string
    code: string
    source: string
    value: unknown
    threshold: unknown
    value_number: number | null
    threshold_number: number | null
    severity: string
    review_only: boolean
    bbox: number[] | null
    metadata: Record<string, unknown>
}

export type RiskSampleList = {
  items: Array<{
    sample_id: string
    relative_path: string
    artist_scope: string
    highest_severity: string
    evidence_count: number
    evidence_codes: string[]
    manually_excluded: boolean
  }>
  total: number
  offset: number
  limit: number
}

export type RiskSampleDetail = {
  sample_id: string
  relative_path: string
  artist_scope: string
  manually_excluded: boolean
  evidence: RiskEvidence[]
}

export type ManualExclusionResult = {
  selected: number
  changed: number
  excluded: boolean
}

export type WatermarkReviewThresholdResult = {
  threshold: number
  updated: number
  candidates: number
}

export type ModelStatus = {
  id: string
  display_name: string
  purpose: string
  source_kind: string
  repository: string | null
  revision: string | null
  homepage: string
  license: string
  loader: string
  dependencies: string[]
  replaceable: boolean
  replacement_schema: string | null
  total_bytes: number
  local_root: string
  installation_status: string
  runtime_ready: boolean
  blocking_dependencies: string[]
  bytes_downloaded: number
  bytes_verified: number
  current_file: string | null
  error: string | null
  verified_at: string | null
  is_custom: boolean
  base_model_id: string | null
}

export type ModelList = {
  items: ModelStatus[]
  total: number
  registry_version: string
  registry_digest: string
}

export type DirectoryListing = {
  current: string | null
  parent: string | null
  entries: Array<{ name: string; path: string }>
}

export type DirectorySelection = {
  path: string | null
  cancelled: boolean
}
