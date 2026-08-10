import { Check, ShieldX } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { listStyleAudit, submitReviewDecisions } from '../clients/reviews'
import { sampleThumbnailUrl } from '../clients/workspace'
import type { FolderList, StyleAuditClassification, StyleAuditItem, StyleAuditList, Task } from '../types'
import {
  ConfirmDialog,
  EmptyState,
  ErrorBlock,
  LoadingBlock,
  Pagination,
  AuditDecisionTabs,
  type AuditDecisionFilter,
  SampleMediaViewer,
  StatusPill,
  Thumbnail,
} from '../ui'

type Notice = (message: string, tone?: 'success' | 'error') => void

export type StylePageProps = {
  task: Task | null
  folders: FolderList | null
  folder: string
  onFolderChange: (folder: string) => void
  notify: Notice
}

const limit = 100

const classificationLabel: Record<StyleAuditClassification, string> = {
  normal: '普通对照',
  outlier: '离群',
  strong_outlier: '强离群',
}

export function StylePage({ task, folders, folder, onFolderChange, notify }: StylePageProps) {
  const [offset, setOffset] = useState(0)
  const [decision, setDecision] = useState<AuditDecisionFilter>('all')
  const [audit, setAudit] = useState<StyleAuditList | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [confirm, setConfirm] = useState<{ decision: 'approved_keep' | 'approved_exclude'; label: string; danger?: boolean } | null>(null)
  const [mediaSampleId, setMediaSampleId] = useState<string | null>(null)

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
    setConfirm(null)
    setMediaSampleId(null)
  }, [decision, folder, task?.id])

  useEffect(() => {
    if (!task) {
      setAudit(null)
      setLoading(false)
      return
    }
    let canceled = false
    const taskId = task.id
    setLoading(true)
    setError(null)
    setAudit(null)
    setSelected(new Set())
    void listStyleAudit(taskId, { offset, limit, folder, decision })
      .then((value) => {
        if (!canceled) setAudit(value)
      })
      .catch((reason: unknown) => {
        if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取画风审计')
      })
      .finally(() => {
        if (!canceled) setLoading(false)
      })
    return () => {
      canceled = true
    }
  }, [decision, folder, offset, revision, task?.id])

  const selectableItems = useMemo(
    () => (audit?.items ?? []).filter((item) => item.review_eligible || item.decision === 'approved_exclude'),
    [audit?.items],
  )
  const selectedAll = selectableItems.length > 0 && selectableItems.every((item) => selected.has(item.sample_id))

  const toggleAll = (checked: boolean) => {
    setSelected(checked ? new Set(selectableItems.map((item) => item.sample_id)) : new Set())
  }

  const decide = async () => {
    if (!task || !confirm || selected.size === 0) return
    setLoading(true)
    try {
      await submitReviewDecisions(task.id, 'style', {
        sample_ids: [...selected],
        decision: confirm.decision,
      })
      notify(`已更新 ${selected.size} 项画风决定`)
      setSelected(new Set())
      setConfirm(null)
      setRevision((value) => value + 1)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '画风决定提交失败', 'error')
      setLoading(false)
    }
  }

  if (!task) return <EmptyState title="未选择任务" detail="先选择需要审计的任务" />

  return (
    <div className="page-stack">
      <section className="review-toolbar">
        <AuditDecisionTabs onChange={setDecision} value={decision} />
        <select
          aria-label="子文件夹筛选"
          onChange={(event) => {
            onFolderChange(event.target.value)
            setOffset(0)
            setSelected(new Set())
          }}
          value={folder}
        >
          <option value="">全部子文件夹</option>
          {(folders?.items ?? []).map((item) => (
            <option key={item.folder_id} value={item.folder_id}>{item.display_name}</option>
          ))}
        </select>
      </section>

      {audit ? (
        <section className="review-counts style-audit-counts">
          <span>普通 <strong>{audit.normal.toLocaleString()}</strong></span>
          <span>离群 <strong>{audit.outlier.toLocaleString()}</strong></span>
          <span>强离群 <strong>{audit.strong_outlier.toLocaleString()}</strong></span>
          <span>待复核 <strong>{audit.pending.toLocaleString()}</strong></span>
          <span>保留 <strong>{audit.approved_keep.toLocaleString()}</strong></span>
          <span>排除 <strong>{audit.approved_exclude.toLocaleString()}</strong></span>
        </section>
      ) : null}

      <section className="selection-bar">
        <label>
          <input
            checked={selectedAll}
            disabled={selectableItems.length === 0}
            onChange={(event) => toggleAll(event.target.checked)}
            type="checkbox"
          />
          本页全选可复核项
        </label>
        <strong>已选 {selected.size}</strong>
        <div>
          <DecisionButton
            disabled={selected.size === 0}
            icon={Check}
            label={decision === 'approved_exclude' ? '撤销排除/保留' : '保留'}
            onClick={() => setConfirm({ decision: 'approved_keep', label: '批准保留' })}
          />
          <DecisionButton
            danger
            disabled={selected.size === 0}
            icon={ShieldX}
            label="排除"
            onClick={() => setConfirm({ decision: 'approved_exclude', label: '批准排除', danger: true })}
          />
        </div>
      </section>

      {error ? <ErrorBlock message={error} /> : null}
      {loading && !audit ? <LoadingBlock label="正在读取画风审计" /> : null}
      {!loading && !error && audit && audit.items.length === 0 ? (
        <EmptyState title={folder ? '当前子文件夹没有画风证据' : '当前任务没有画风证据'} />
      ) : null}
      {audit && audit.items.length > 0 ? (
        <StyleAuditGrid
          items={audit.items}
          selected={selected}
          setSelected={setSelected}
          taskId={task.id}
          onOpenMedia={setMediaSampleId}
        />
      ) : null}

      <Pagination
        limit={limit}
        offset={offset}
        onChange={(nextOffset) => {
          setSelected(new Set())
          setOffset(nextOffset)
        }}
        total={audit?.total ?? 0}
      />
      <ConfirmDialog
        busy={loading}
        danger={confirm?.danger}
        detail={`本次只修改明确勾选的 ${selected.size} 项，可再次选择并改回其他状态。`}
        confirmLabel={confirm?.label ?? '确认'}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void decide()}
        open={confirm !== null}
        title="确认批量复核"
      />
      <SampleMediaViewer onClose={() => setMediaSampleId(null)} sampleId={mediaSampleId} taskId={task.id} />
    </div>
  )
}

function StyleAuditGrid({
  items,
  selected,
  setSelected,
  taskId,
  onOpenMedia,
}: {
  items: StyleAuditItem[]
  selected: Set<string>
  setSelected: (value: Set<string>) => void
  taskId: string
  onOpenMedia: (sampleId: string) => void
}) {
  return (
    <section className="style-audit-grid">
      {items.map((item) => (
        <article
          className={`style-audit-card ${item.classification}${selected.has(item.sample_id) ? ' selected' : ''}`}
          key={item.sample_id}
        >
          {item.review_eligible || item.decision === 'approved_exclude' ? (
            <label className="card-check">
              <input
                aria-label={`选择 ${item.relative_path}`}
                checked={selected.has(item.sample_id)}
                onChange={() => {
                  const next = new Set(selected)
                  if (next.has(item.sample_id)) next.delete(item.sample_id)
                  else next.add(item.sample_id)
                  setSelected(next)
                }}
                type="checkbox"
              />
            </label>
          ) : null}
          <Thumbnail alt={item.relative_path} onClick={() => onOpenMedia(item.sample_id)} src={sampleThumbnailUrl(taskId, item.sample_id, 320)} />
          <div className="review-card-body">
            <strong title={item.relative_path}>{item.relative_path}</strong>
            <span>{item.artist_scope}</span>
            {item.classification !== 'normal' ? (
              <span className="style-audit-label">{classificationLabel[item.classification]}</span>
            ) : null}
            <div>
              {item.decision ? <StatusPill value={item.decision} /> : <span />}
              <b>{item.style_score.toFixed(2)}</b>
            </div>
            {item.reason ? <small>{item.reason}</small> : null}
          </div>
        </article>
      ))}
    </section>
  )
}

function DecisionButton({
  icon: Icon,
  label,
  disabled,
  danger = false,
  onClick,
}: {
  icon: typeof Check
  label: string
  disabled: boolean
  danger?: boolean
  onClick: () => void
}) {
  return (
    <button
      className={`button ${danger ? 'danger' : 'secondary'}`}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      <Icon size={15} />
      {label}
    </button>
  )
}
