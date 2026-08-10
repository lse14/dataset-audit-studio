import { jsonBody, request } from '../transport/http'
import type {
  ComponentConfigValue,
  DatasetProfile,
  TaskPreset,
  TaskPresetDeleteResult,
  TaskPresetList,
} from '../types'

export function listTaskPresets(): Promise<TaskPresetList> {
  return request<TaskPresetList>('/api/task-presets')
}

export function createTaskPreset(
  name: string,
  components: Record<string, ComponentConfigValue>,
  profile: DatasetProfile,
): Promise<TaskPreset> {
  return request<TaskPreset>('/api/task-presets', {
    method: 'POST',
    ...jsonBody({ name, components, profile }),
  })
}

export function updateTaskPreset(
  presetId: string,
  name: string,
  components: Record<string, ComponentConfigValue>,
  expectedVersion: number,
  profile: DatasetProfile,
): Promise<TaskPreset> {
  return request<TaskPreset>(`/api/task-presets/${presetId}`, {
    method: 'PUT',
    ...jsonBody({ name, components, profile, expected_version: expectedVersion }),
  })
}

export function deleteTaskPreset(
  presetId: string,
  expectedVersion: number,
): Promise<TaskPresetDeleteResult> {
  return request<TaskPresetDeleteResult>(`/api/task-presets/${presetId}`, {
    method: 'DELETE',
    ...jsonBody({ expected_version: expectedVersion }),
  })
}

export function createTaskPresetFromTask(taskId: string, name: string): Promise<TaskPreset> {
  return request<TaskPreset>(`/api/task-presets/from-task/${taskId}`, {
    method: 'POST',
    ...jsonBody({ name }),
  })
}
