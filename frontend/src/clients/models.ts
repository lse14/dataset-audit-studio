import { jsonBody, request } from '../transport/http'
import type { ModelList, ModelStatus } from '../types'

export type ModelAction = 'download' | 'verify' | 'cancel'

export function listModels(): Promise<ModelList> {
  return request<ModelList>('/api/models?limit=200')
}

export function runModelAction(modelId: string, action: ModelAction): Promise<unknown> {
  return request(`/api/models/${modelId}/${action}`, action === 'download'
    ? { method: 'POST', ...jsonBody({ include_dependencies: true }) }
    : { method: 'POST' })
}

export function downloadAllModels(): Promise<unknown> {
  return request('/api/models/download-all', { method: 'POST' })
}

export function registerLocalModel(
  baseModelId: string,
  sourcePath: string,
  displayName: string,
): Promise<ModelStatus> {
  return request<ModelStatus>('/api/models/local', {
    method: 'POST',
    ...jsonBody({
      base_model_id: baseModelId,
      source_path: sourcePath.trim(),
      display_name: displayName.trim() || null,
    }),
  })
}
