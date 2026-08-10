import { jsonBody, request } from '../transport/http'
import type {
  ComponentConfigValue,
  DatasetProfile,
  ExportRun,
  ExportRunSettings,
  Task,
  TaskDeleteResult,
  TaskEventList,
  TaskList,
} from '../types'

export type TaskControlAction =
  | 'queue'
  | 'pause'
  | 'resume'
  | 'terminate'
  | 'review-gate/release'

export type TaskCreateInput = {
  name: string
  source_root: string
  output_root?: string
  components: Record<string, ComponentConfigValue>
  profile: DatasetProfile
}

export function releaseCopyExport(
  taskId: string,
  expectedVersion: number,
  expectedGate: string,
  input: ExportRunSettings & { preview_digest: string },
): Promise<ExportRun> {
  return request<ExportRun>(`/api/tasks/${taskId}/review-gate/release`, {
    method: 'POST',
    ...jsonBody({ expected_version: expectedVersion, expected_gate: expectedGate, ...input }),
  })
}

export function listTasks(): Promise<TaskList> {
  return request<TaskList>('/api/tasks?limit=200')
}

export function getTask(taskId: string): Promise<Task> {
  return request<Task>(`/api/tasks/${taskId}`)
}

export function listTaskEvents(taskId: string): Promise<TaskEventList> {
  return request<TaskEventList>(`/api/tasks/${taskId}/events?limit=200`)
}

export function createTask(input: TaskCreateInput): Promise<Task> {
  return request<Task>('/api/tasks', {
    method: 'POST',
    ...jsonBody(input),
  })
}

export function controlTask(
  taskId: string,
  action: TaskControlAction,
  expectedVersion: number,
  extra: Record<string, unknown> = {},
): Promise<Task> {
  return request<Task>(`/api/tasks/${taskId}/${action}`, {
    method: 'POST',
    ...jsonBody({ expected_version: expectedVersion, ...extra }),
  })
}

export function deleteTask(taskId: string, expectedVersion: number): Promise<TaskDeleteResult> {
  return request<TaskDeleteResult>(`/api/tasks/${taskId}`, {
    method: 'DELETE',
    ...jsonBody({ expected_version: expectedVersion }),
  })
}
