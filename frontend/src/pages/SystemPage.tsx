import { CheckCircle2, Database, HardDrive, Server, ShieldCheck } from 'lucide-react'

import type { Health } from '../types'
import { ErrorBlock, LoadingBlock } from '../ui'

export function SystemPage({
  health,
  loading,
  error,
}: {
  health: Health | null
  loading: boolean
  error: string | null
}) {
  if (loading && !health) return <LoadingBlock label="正在读取本地运行状态" />
  if (error && !health) return <ErrorBlock message={error} />
  if (!health) return null
  const statusItems = [
    {
      icon: Server,
      label: '后端服务',
      value: health.status === 'ok' ? '运行中' : health.status,
      ready: health.status === 'ok',
    },
    {
      icon: ShieldCheck,
      label: 'Python 隔离',
      value: health.runtime.isolated ? '已启用' : '未确认',
      ready: health.runtime.isolated,
    },
    {
      icon: Database,
      label: 'SQLite',
      value: `${health.database.journal_mode.toUpperCase()} / ${health.database.foreign_keys ? '外键' : '无外键'}`,
      ready: health.database.journal_mode === 'wal' && health.database.foreign_keys,
    },
    {
      icon: HardDrive,
      label: '模型',
      value: `${health.models.ready_models} / ${health.models.registered_models} 就绪`,
      ready: health.models.active_operations === 0,
    },
  ]
  return (
    <div className="page-stack">
      <section className="metric-strip">
        {statusItems.map(({ icon: Icon, label, value, ready }) => (
          <div className="metric-item" key={label}>
            <Icon size={19} />
            <span>{label}</span>
            <strong>{value}</strong>
            <i className={ready ? 'metric-state ready' : 'metric-state warning'} />
          </div>
        ))}
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>隔离运行时</h2>
            <span>Python {health.runtime.python_version}</span>
          </div>
          <CheckCircle2 className="success-icon" size={20} />
        </header>
        <dl className="detail-list">
          <PathRow label="项目" value={health.runtime.project_root} />
          <PathRow label="Python" value={health.runtime.python_executable} />
          <PathRow label="模型" value={health.runtime.models_root} />
          <PathRow label="任务数据" value={health.runtime.data_root} />
          <PathRow label="数据库" value={health.database.path} />
        </dl>
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>本地 Worker</h2>
            <span>{health.worker.owner ?? '未启用'}</span>
          </div>
          <span className={health.worker.running ? 'live-indicator online' : 'live-indicator'}>
            {health.worker.running ? '在线' : '停止'}
          </span>
        </header>
        <div className="worker-grid">
          <div>
            <span>当前任务</span>
            <strong>{health.worker.active_task_id ?? '空闲'}</strong>
          </div>
          <div>
            <span>活动模型操作</span>
            <strong>{health.models.active_operations}</strong>
          </div>
          <div>
            <span>运行时就绪模型</span>
            <strong>{health.models.runtime_ready_models}</strong>
          </div>
        </div>
      </section>
    </div>
  )
}

function PathRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
