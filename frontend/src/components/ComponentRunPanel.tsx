import { Boxes, Clock3, Database, GitBranch } from 'lucide-react'

import type { ComponentManifest, ComponentRun, JsonValue } from '../types'
import { EmptyState, StatusPill } from '../ui'

export function ComponentRunPanel({
  manifests,
  runs,
}: {
  manifests: ComponentManifest[]
  runs: ComponentRun[]
}) {
  const names = new Map(manifests.map((item) => [item.id, item.display_name]))
  const ordered = [...runs].sort((left, right) => (
    left.phase_order - right.phase_order || left.component_id.localeCompare(right.component_id)
  ))
  const complete = ordered.filter((item) => item.status === 'completed').length
  const active = ordered.find((item) => item.status === 'running' || item.status === 'paused')
  return (
    <section className="panel component-run-panel">
      <header className="panel-header">
        <div>
          <h2>组件执行</h2>
          <span>{active ? `当前：${names.get(active.component_id) ?? active.component_id}` : `${complete} / ${ordered.length} 已完成`}</span>
        </div>
        <span className="count-label">{ordered.length}</span>
      </header>
      {ordered.length === 0 ? (
        <EmptyState title="暂无组件运行计划" />
      ) : (
        <div className="component-run-table">
          <div className="component-run-row component-run-head">
            <span>组件与依赖</span>
            <span>状态</span>
            <span>处理量</span>
            <span>缓存</span>
            <span>模型</span>
            <span>耗时</span>
          </div>
          {ordered.map((run, index) => {
            const completedItems = run.completed_items
            const totalItems = run.total_items
            const progress = totalItems
              ? Math.min(100, (completedItems / totalItems) * 100)
              : 0
            const dependencies = run.dependency_ids.map((id) => names.get(id) ?? id)
            return (
              <div className={`component-run-row state-${run.status}`} key={run.component_id}>
                <div className="component-run-identity">
                  <span className="run-sequence">{index + 1}</span>
                  <span>
                    <strong>{names.get(run.component_id) ?? run.component_id}</strong>
                    <small title={dependencies.join('、')}>
                      <GitBranch size={11} />
                      {dependencies.length ? dependencies.join('、') : '起始组件'}
                      {run.auto_enabled ? ' · 自动启用' : ''}
                    </small>
                  </span>
                </div>
                <span><StatusPill value={run.status} /></span>
                <span className="run-metric">
                  <Boxes size={13} />
                  <span>
                    {completedItems.toLocaleString()}
                    {totalItems ? ` / ${totalItems.toLocaleString()}` : ''}
                  </span>
                  {totalItems ? <i><b style={{ width: `${progress}%` }} /></i> : null}
                </span>
                <span className="run-metric">
                  <Database size={13} />
                  {cacheLabel(run.checkpoint, run.completed_items)}
                </span>
                <span className="run-models" title={run.model_ids.join(', ')}>
                  {run.model_ids.length ? `${run.model_ids.length} 个` : '无'}
                </span>
                <span className="run-metric">
                  <Clock3 size={13} />
                  {durationLabel(run)}
                </span>
                {run.error_message ? (
                  <div className="component-run-error">
                    <strong>{run.error_code ?? '组件失败'}</strong>
                    <span>{run.error_message}</span>
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function cacheLabel(checkpoint: Record<string, JsonValue>, completed: number): string {
  const cached = checkpoint.cached_samples
  if (typeof cached === 'number') return cached.toLocaleString()
  if (checkpoint.cache_hit === true) return completed ? completed.toLocaleString() : '命中'
  return '-'
}

function durationLabel(run: ComponentRun): string {
  if (!run.started_at) return '-'
  const start = new Date(run.started_at).getTime()
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now()
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
