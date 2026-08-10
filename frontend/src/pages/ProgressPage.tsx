import { useVirtualizer } from '@tanstack/react-virtual'
import {
  CirclePause,
  CirclePlay,
  Clock3,
  Database,
  FileImage,
  ListRestart,
  Radio,
  ShieldCheck,
  Square,
} from 'lucide-react'
import { useRef, useState } from 'react'

import { controlTask, type TaskControlAction } from '../clients/tasks'
import { ComponentRunPanel } from '../components/ComponentRunPanel'
import { isCopyExportTask, profileDisplayName, profileWorkspaceSummary, workspaceDisplayName } from '../profileWorkspace'
import type {
  ComponentManifest,
  ComponentRun,
  Health,
  Task,
  TaskEvent,
  TaskOverview,
} from '../types'
import { ConfirmDialog, EmptyState, StatusPill, statusLabel } from '../ui'

type Notice = (message: string, tone?: 'success' | 'error') => void

export function ProgressPage({
  task,
  components,
  componentRuns,
  overview,
  events,
  health,
  sseConnected,
  onChanged,
  notify,
}: {
  task: Task | null
  components: ComponentManifest[]
  componentRuns: ComponentRun[]
  overview: TaskOverview | null
  events: TaskEvent[]
  health: Health | null
  sseConnected: boolean
  onChanged: (task?: Task) => Promise<void>
  notify: Notice
}) {
  const [busy, setBusy] = useState(false)
  const [terminateOpen, setTerminateOpen] = useState(false)
  const eventParent = useRef<HTMLDivElement>(null)
  const ordered = [...events].reverse()
  const virtualizer = useVirtualizer({
    count: ordered.length,
    getScrollElement: () => eventParent.current,
    estimateSize: () => 58,
    overscan: 8,
  })

  if (!task) {
    return <EmptyState title="未选择任务" detail="先在任务页选择一个任务" />
  }

  const control = async (action: TaskControlAction, extra: Record<string, unknown> = {}) => {
    setBusy(true)
    try {
      const updated = await controlTask(task.id, action, task.row_version, extra)
      await onChanged(updated)
      notify('任务状态已更新')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '任务操作失败', 'error')
    } finally {
      setBusy(false)
    }
  }
  const progress = task.progress_total
    ? Math.min(100, (task.progress_current / task.progress_total) * 100)
    : 0
  return (
    <div className="page-stack">
      <section className="task-command-bar">
        <div>
          <span>当前任务</span>
          <strong>{task.name}</strong>
          <StatusPill value={task.status} />
        </div>
        <div className="command-actions">
          {task.status === 'draft' ? (
            <button className="button primary" disabled={busy} onClick={() => void control('queue')} type="button">
              <CirclePlay size={16} />
              加入队列
            </button>
          ) : null}
          {canPause(task.status) ? (
            <button className="button secondary" disabled={busy} onClick={() => void control('pause')} type="button">
              <CirclePause size={16} />
              暂停
            </button>
          ) : null}
          {task.status === 'paused' ? (
            <button className="button primary" disabled={busy} onClick={() => void control('resume')} type="button">
              <ListRestart size={16} />
              恢复
            </button>
          ) : null}
          {task.status === 'evidence_review' ? (
            <button
              className="button primary"
              disabled={busy}
              onClick={() => {
                if (isCopyExportTask(task)) window.location.hash = 'exports'
                else void control('review-gate/release', { expected_gate: task.status })
              }}
              type="button"
            >
              <ShieldCheck size={16} />
              完成复核
            </button>
          ) : null}
          {!isTerminal(task.status) ? (
            <button className="button danger" disabled={busy} onClick={() => setTerminateOpen(true)} type="button">
              <Square size={14} />
              终止
            </button>
          ) : null}
        </div>
      </section>

      <ProfileWorkspaceBanner task={task} />

      <section className="progress-layout">
        <div className="panel progress-panel">
          <header className="panel-header">
            <div>
              <h2>{statusLabel(task.status)}</h2>
              <span>{task.resume_state ? `恢复阶段：${statusLabel(task.resume_state)}` : '当前执行阶段'}</span>
            </div>
            <span className={sseConnected ? 'live-indicator online' : 'live-indicator'}>
              <Radio size={13} />
              {sseConnected ? '实时' : '轮询'}
            </span>
          </header>
          <div className="large-progress">
            <div>
              <span>
                阶段进度
              </span>
              <strong>{task.progress_total ? `${progress.toFixed(1)}%` : '等待数据'}</strong>
            </div>
            <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
            <small>
              {task.progress_total
                ? `${task.progress_current.toLocaleString()} / ${task.progress_total.toLocaleString()}`
                : '阶段开始后显示已提交 batch 数量'}
            </small>
          </div>
          {task.error_message ? (
            <div className="task-error">
              <strong>{task.error_code ?? '任务错误'}</strong>
              <span>{task.error_message}</span>
            </div>
          ) : null}
        </div>

        <div className="summary-grid">
          <SummaryCell icon={FileImage} label="扫描样本" value={overview?.samples_total ?? 0} />
          <SummaryCell icon={ShieldCheck} label="有效样本" value={overview?.samples_valid ?? 0} />
          <SummaryCell icon={Database} label="缓存资产" value={overview?.ready_artifacts ?? 0} />
          <SummaryCell icon={Clock3} label="Worker" value={health?.worker.active_task_id === task.id ? '执行中' : '空闲'} />
        </div>
      </section>

      <ComponentRunPanel manifests={components} runs={componentRuns} />

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>事件流</h2>
            <span>最新事件在前</span>
          </div>
          <span className="count-label">{events.length}</span>
        </header>
        {events.length === 0 ? (
          <EmptyState title="暂无事件" />
        ) : (
          <div className="virtual-event-list" ref={eventParent}>
            <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
              {virtualizer.getVirtualItems().map((row) => {
                const event = ordered[row.index]
                return (
                  <div
                    className="event-row"
                    key={event.sequence}
                    style={{ transform: `translateY(${row.start}px)` }}
                  >
                    <time>{formatTime(event.created_at)}</time>
                    <span>{eventLabel(event.event_type)}</span>
                    <code>#{event.sequence}</code>
                    <small>{event.to_status ? statusLabel(event.to_status) : ''}</small>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </section>

      <ConfirmDialog
        danger
        detail={`终止“${task.name}”。已提交的 batch 和缓存会保留。`}
        confirmLabel="终止任务"
        onCancel={() => setTerminateOpen(false)}
        onConfirm={() => {
          setTerminateOpen(false)
          void control('terminate', { force: false, reason: 'WebUI request' })
        }}
        open={terminateOpen}
        title="确认终止"
      />
    </div>
  )
}

function ProfileWorkspaceBanner({ task }: { task: Task }) {
  const summary = profileWorkspaceSummary(task)
  if (!summary) return null
  return (
    <section className="profile-workspace-banner">
      <span>{profileDisplayName(summary.profile)}</span>
      <strong>{workspaceDisplayName('broad')}</strong>
    </section>
  )
}

function SummaryCell({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof FileImage
  label: string
  value: string | number
}) {
  return (
    <div>
      <Icon size={18} />
      <span>{label}</span>
      <strong>{typeof value === 'number' ? value.toLocaleString() : value}</strong>
    </div>
  )
}

function eventLabel(value: string) {
  const labels: Record<string, string> = {
    task_created: '任务已创建',
    task_queued: '任务已加入队列',
    task_started: '阶段开始',
    batch_committed: '批次已提交',
    phase_completed: '阶段已完成',
    task_paused: '任务已暂停',
    task_resumed: '任务已恢复',
    task_terminate_requested: '收到终止请求',
    task_terminated: '任务已终止',
    task_failed: '任务失败',
    review_gate_released: '人工复核已完成',
    stale_worker_recovered: '已恢复中断任务',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function canPause(status: string) {
  return [
    'queued',
    'scanning',
    'cpu_metrics',
    'model_scoring',
    'style_analysis',
    'semantic_clustering',
    'exporting',
  ].includes(status)
}

function isTerminal(status: string) {
  return ['completed', 'terminated', 'failed'].includes(status)
}
