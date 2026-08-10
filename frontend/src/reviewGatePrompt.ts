import type { PageId, Task } from './types'

export type ReviewGateStatus = 'evidence_review'

export type ReviewGatePrompt = {
  key: string
  taskId: string
  taskName: string
  status: ReviewGateStatus
  rowVersion: number
  targetPage: Extract<PageId, 'risks'>
  actionLabel: string
  detail: string
}

type ReviewGateTask = Pick<Task, 'id' | 'name' | 'status' | 'row_version'>

const promptConfig: Record<
  ReviewGateStatus,
  Pick<ReviewGatePrompt, 'targetPage' | 'actionLabel'> & {
    detail: (taskName: string) => string
  }
> = {
  evidence_review: {
    targetPage: 'risks',
    actionLabel: '进入审计',
    detail: (taskName) =>
      `“${taskName}”已完成筛选，正在等待人工审计。请按风险、画风、重复和美学分类完成复核后，再继续任务。`,
  },
}

export function createReviewGatePrompt(
  task: ReviewGateTask | null,
): ReviewGatePrompt | null {
  if (!task || !(task.status in promptConfig)) return null
  const status = task.status as ReviewGateStatus
  const config = promptConfig[status]
  return {
    key: `${task.id}:${status}:${task.row_version}`,
    taskId: task.id,
    taskName: task.name,
    status,
    rowVersion: task.row_version,
    targetPage: config.targetPage,
    actionLabel: config.actionLabel,
    detail: config.detail(task.name),
  }
}
