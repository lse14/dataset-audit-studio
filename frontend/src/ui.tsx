import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ImageOff,
  LoaderCircle,
  X,
} from 'lucide-react'
import {
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from 'react'

import { sampleMediaUrl } from './clients/workspace'

export type AuditDecisionFilter = 'all' | 'pending_review' | 'approved_keep' | 'approved_exclude'

const auditDecisionLabels: Record<AuditDecisionFilter, string> = {
  all: '全部',
  pending_review: '待复核',
  approved_keep: '已保留',
  approved_exclude: '已排除',
}

export function StatusPill({ value }: { value: string }) {
  const tone =
    value.includes('failed') || value.includes('corrupt') || value.includes('exclude')
      ? 'danger'
      : value.includes('ready') || value.includes('completed') || value.includes('keep')
        ? 'success'
        : value.includes('paused') || value.includes('review') || value.includes('verif')
          ? 'warning'
          : 'neutral'
  return <span className={`status-pill ${tone}`}>{statusLabel(value)}</span>
}

export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    draft: '草稿',
    queued: '排队中',
    scanning: '扫描',
    cpu_metrics: 'CPU 指标',
    model_scoring: '模型评分',
    style_analysis: '画风分析',
    semantic_clustering: '语义聚类',
    evidence_review: '等待证据复核',
    exporting: '缓存 / 导出',
    completed: '已完成',
    pending: '等待执行',
    running: '执行中',
    pausing: '正在暂停',
    paused: '已暂停',
    terminating: '正在终止',
    terminated: '已终止',
    failed: '失败',
    pending_review: '待复核',
    approved_keep: '保留',
    approved_exclude: '排除',
    ready: '已就绪',
    missing: '未下载',
    downloading: '下载中',
    verifying: '校验中',
    verification_required: '待校验',
    partial: '未完成',
    corrupt: '损坏',
    normal: '正常',
    risk: '风险',
    ignore: '忽略',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <ImageOff size={22} />
      <strong>{title}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  )
}

export function LoadingBlock({ label = '正在加载' }: { label?: string }) {
  return (
    <div className="loading-block" aria-live="polite">
      <LoaderCircle className="spin" size={20} />
      <span>{label}</span>
    </div>
  )
}

export function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="inline-error" role="alert">
      <AlertTriangle size={18} />
      <span>{message}</span>
    </div>
  )
}

export function Pagination({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number
  limit: number
  total: number
  onChange: (offset: number) => void
}) {
  const page = total === 0 ? 0 : Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  return (
    <div className="pagination" aria-label="分页">
      <button
        className="icon-button"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
        title="上一页"
        type="button"
      >
        <ChevronLeft size={17} />
      </button>
      <span>
        {page} / {pages || 0}，共 {total.toLocaleString()} 项
      </span>
      <button
        className="icon-button"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
        title="下一页"
        type="button"
      >
        <ChevronRight size={17} />
      </button>
    </div>
  )
}

export function AuditDecisionTabs({
  value,
  onChange,
}: {
  value: AuditDecisionFilter
  onChange: (value: AuditDecisionFilter) => void
}) {
  return (
    <div aria-label="审核状态" className="segmented-control" role="group">
      {(Object.keys(auditDecisionLabels) as AuditDecisionFilter[]).map((decision) => (
        <button
          aria-pressed={value === decision}
          className={value === decision ? 'active' : ''}
          key={decision}
          onClick={() => onChange(decision)}
          type="button"
        >
          {auditDecisionLabels[decision]}
        </button>
      ))}
    </div>
  )
}

export function Modal({
  open,
  title,
  children,
  onClose,
  width = 'medium',
}: {
  open: boolean
  title: string
  children: ReactNode
  onClose: () => void
  width?: 'small' | 'medium' | 'large'
}) {
  useEffect(() => {
    if (!open) return
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, open])
  if (!open) return null
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-label={title}
        aria-modal="true"
        className={`modal ${width}`}
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className="modal-header">
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} title="关闭" type="button">
            <X size={18} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  )
}

export function ConfirmDialog({
  open,
  title,
  detail,
  confirmLabel,
  danger = false,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean
  title: string
  detail: string
  confirmLabel: string
  danger?: boolean
  busy?: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Modal open={open} title={title} onClose={onCancel} width="small">
      <p className="confirm-detail">{detail}</p>
      <div className="modal-actions">
        <button className="button secondary" disabled={busy} onClick={onCancel} type="button">
          取消
        </button>
        <button
          className={`button ${danger ? 'danger' : 'primary'}`}
          disabled={busy}
          onClick={onConfirm}
          type="button"
        >
          {busy ? <LoaderCircle className="spin" size={16} /> : null}
          {confirmLabel}
        </button>
      </div>
    </Modal>
  )
}

export function Thumbnail({
  src,
  alt,
  bbox,
  onClick,
}: {
  src: string
  alt: string
  bbox?: number[] | null
  onClick?: () => void
}) {
  const [failed, setFailed] = useState(false)
  const normalized = bbox && bbox.length === 4 && bbox.every((value) => value >= 0 && value <= 1)
  const frame = (
    <div className="thumbnail-frame">
      {failed ? (
        <span className="thumbnail-fallback">
          <ImageOff size={22} />
        </span>
      ) : (
        <img alt={alt} loading="lazy" onError={() => setFailed(true)} src={src} />
      )}
      {normalized ? (
        <span
          className="evidence-box"
          style={{
            left: `${bbox[0] * 100}%`,
            top: `${bbox[1] * 100}%`,
            width: `${bbox[2] * 100}%`,
            height: `${bbox[3] * 100}%`,
          }}
        />
      ) : null}
    </div>
  )
  return onClick ? (
    <button
      aria-label={`查看原图 ${alt}`}
      className="thumbnail-button"
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      type="button"
    >
      {frame}
    </button>
  ) : frame
}

export function SampleMediaViewer({
  taskId,
  sampleId,
  onClose,
}: {
  taskId: string | null
  sampleId: string | null
  onClose: () => void
}) {
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const open = taskId !== null && sampleId !== null
  const url = open ? sampleMediaUrl(taskId, sampleId) : ''

  useEffect(() => {
    setState('loading')
  }, [taskId, sampleId])

  return (
    <Modal open={open} title="原图查看" onClose={onClose} width="large">
      <div className="sample-media-viewer" aria-busy={state === 'loading'}>
        {state === 'loading' ? <LoadingBlock label="正在加载原图" /> : null}
        {state === 'error' ? <ErrorBlock message="无法加载原图" /> : null}
        {open ? (
          <img
            alt="原图"
            className={state === 'error' ? 'sample-media-image failed' : 'sample-media-image'}
            onError={() => setState('error')}
            onLoad={() => setState('ready')}
            src={url}
          />
        ) : null}
      </div>
    </Modal>
  )
}

export function useElementWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0))
    observer.observe(element)
    setWidth(element.getBoundingClientRect().width)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}
