import type { PageId, Task } from './types'

export type CompletionPrompt = {
  key: string
  taskId: string
  taskName: string
  status: 'completed'
  rowVersion: number
  targetPage: Extract<PageId, 'exports'>
  actionLabel: string
  detail: string
}

export type CompletionTask = Pick<Task, 'id' | 'name' | 'status' | 'row_version'>

export function isCompletionTransition(
  task: CompletionTask | null,
  previousStatus: string | undefined,
): boolean {
  return task?.status === 'completed'
    && previousStatus !== undefined
    && previousStatus !== 'completed'
}

export function createCompletionPrompt(
  task: CompletionTask | null,
): CompletionPrompt | null {
  if (!task || task.status !== 'completed') return null
  return {
    key: `${task.id}:completed:${task.row_version}`,
    taskId: task.id,
    taskName: task.name,
    status: 'completed',
    rowVersion: task.row_version,
    targetPage: 'exports',
    actionLabel: '查看导出',
    detail: `“${task.name}”已完成分析，可以前往导出页配置并预览 copy 导出。`,
  }
}
