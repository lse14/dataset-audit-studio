import { request } from '../transport/http'
import type {
  ComponentList,
  ComponentRunList,
  RuntimeTuningRecommendation,
} from '../types'

export function listComponents(): Promise<ComponentList> {
  return request<ComponentList>('/api/components')
}

export function listComponentRuns(taskId: string): Promise<ComponentRunList> {
  return request<ComponentRunList>(`/api/components/runs/${taskId}`)
}

export function getRuntimeTuningRecommendation(): Promise<RuntimeTuningRecommendation> {
  return request<RuntimeTuningRecommendation>('/api/components/runtime-tuning/recommendation')
}
