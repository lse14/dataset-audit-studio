import type { ComponentConfigValue, DatasetProfile, Task } from './types'

export const profileResolutions = [512, 768, 1024, 1216, 1536] as const

const DISPLAY_NAMES: Record<string, string> = {
  artist_concept: '画师概念',
  character_concept: '角色概念',
  general: '通用数据',
  broad: '全量工作区',
  score_x2_floor: '按美学评分分档',
}

export function profileDisplayName(profile: string): string {
  return DISPLAY_NAMES[profile] ?? profile
}

export function workspaceDisplayName(value: string): string {
  return DISPLAY_NAMES[value] ?? value
}

export type CoverageRequestState =
  | 'not_ready'
  | 'paused'
  | 'exporting'
  | 'ready'

type TaskLike = Pick<Task, 'resume_state' | 'status'>

export function currentTaskProfile(task: Pick<Task, 'config'> | null | undefined): DatasetProfile | null {
  const profile = isRecord(task?.config) ? task.config.profile : undefined
  return isDatasetProfile(profile) ? profile : null
}

export function isBuiltinProfileTask(task: Pick<Task, 'config'> | null | undefined): boolean {
  return currentTaskProfile(task) !== null
}

export function profileWorkspaceSummary(task: Pick<Task, 'config'> | null | undefined) {
  const profile = currentTaskProfile(task)
  return profile === null
    ? null
    : { profile, broadOnly: true, bypassesReviewGates: false }
}

export function isCopyExportTask(task: Pick<Task, 'config'> | null | undefined): boolean {
  const config = task?.config
  if (!isRecord(config) || !isRecord(config.components)) return true
  const exportComponent = config.components['export.dataset']
  if (!isRecord(exportComponent) || !isRecord(exportComponent.config)) return true
  return exportComponent.config.mode !== 'rewrite'
}

export function profileTaskSubmissionComponents(
  components: Record<string, ComponentConfigValue>,
  profile: DatasetProfile,
): Record<string, ComponentConfigValue> {
  const submitted = Object.fromEntries(Object.entries(components).map(([componentId, value]) => [
    componentId,
    { ...value, config: { ...value.config } },
  ]))
  const datasetExport = submitted['export.dataset']
  if (!datasetExport || profile === 'general') return submitted

  const { aesthetic_bins: _aestheticBins, ...config } = datasetExport.config
  submitted['export.dataset'] = { ...datasetExport, config }
  return submitted
}

export function profileResolutionsForTask(task: Pick<Task, 'config'> | null | undefined): number[] {
  return normalizeProfileResolutions(componentConfig(task, 'media.scan')?.resolutions)
}

export function profileResolutionsForComponents(
  components: Record<string, ComponentConfigValue>,
): number[] {
  return normalizeProfileResolutions(components['media.scan']?.config.resolutions)
}

export function validateExportRunAestheticMinimum(enabled: boolean, value: string): string | null {
  if (!enabled) return null
  const minimum = Number(value)
  if (!Number.isFinite(minimum) || minimum < 1 || minimum > 5) {
    return '美学最低分必须在 1.0 到 5.0 之间'
  }
  if (Math.abs(minimum * 2 - Math.round(minimum * 2)) > 1e-9) {
    return '美学最低分必须按 0.5 递进'
  }
  return null
}

export function validateExportRunDomainMinimum(enabled: boolean, value: string): string | null {
  if (!enabled) return null
  const minimum = Number(value)
  return Number.isFinite(minimum) && minimum >= 0 && minimum <= 1
    ? null
    : '目标域最低分必须在 0 到 1 之间'
}

export function validateStyleArtistWeights(
  components: Record<string, ComponentConfigValue>,
): string | null {
  const artist = components['style.artist']
  if (!artist || !artist.enabled) return null
  const values = [artist.config.lsnet_weight, artist.config.gram_weight, artist.config.dino_weight]
  if (!values.every((value) => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1)) {
    return '画风三项权重必须分别在 0 到 1 之间'
  }
  const sum = (values as number[]).reduce((total, value) => total + value, 0)
  return Math.abs(sum - 1) <= 1e-9 ? null : '画风三项权重总和必须等于 1'
}

export function toggleProfileResolution(current: readonly number[], resolution: number): number[] {
  if (!profileResolutions.includes(resolution as typeof profileResolutions[number])) {
    throw new Error('Unsupported profile resolution')
  }
  const selected = new Set(normalizeProfileResolutions(current))
  if (selected.has(resolution)) {
    if (selected.size === 1) throw new Error('At least one profile resolution is required')
    selected.delete(resolution)
  } else {
    selected.add(resolution)
  }
  return [...selected].sort((left, right) => left - right)
}

export function coverageRequestState(task: TaskLike): CoverageRequestState {
  if (task.status === 'paused') return 'paused'
  if (task.status === 'exporting') return 'exporting'
  return task.status === 'completed' ? 'ready' : 'not_ready'
}

export function coverageScopeIdentity(scopeId: string) {
  const isAscii = [...scopeId].every((character) => character.charCodeAt(0) <= 0x7f)
  const asciiHex = isAscii
    ? Array.from(new TextEncoder().encode(scopeId), (byte) => byte.toString(16).padStart(2, '0')).join('')
    : null
  return { scopeId, repr: JSON.stringify(scopeId), asciiHex }
}

function componentConfig(
  task: Pick<Task, 'config'> | null | undefined,
  componentId: string,
): Record<string, unknown> | null {
  const config = task?.config
  if (!isRecord(config) || !isRecord(config.components)) return null
  const component = config.components[componentId]
  return isRecord(component) && isRecord(component.config) ? component.config : null
}

function normalizeProfileResolutions(value: unknown): number[] {
  if (!Array.isArray(value)) return [...profileResolutions]
  const selected = value.filter((item): item is number => (
    typeof item === 'number'
    && profileResolutions.includes(item as typeof profileResolutions[number])
  ))
  return selected.length > 0
    ? [...new Set(selected)].sort((left, right) => left - right)
    : [...profileResolutions]
}

export function isDatasetProfile(value: unknown): value is DatasetProfile {
  return value === 'artist_concept' || value === 'character_concept' || value === 'general'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
