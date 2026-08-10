import { Check, ShieldX } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { listAestheticAudit, submitCuratedReviewDecisions } from '../clients/reviews'
import { sampleThumbnailUrl } from '../clients/workspace'
import type {
  AestheticAuditItem,
  AestheticAuditList,
  AestheticAuditReason,
  FolderList,
  Task,
} from '../types'
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

export type AestheticsPageProps = {
  task: Task | null
  folders: FolderList | null
  folder: string
  onFolderChange: (folder: string) => void
  notify: Notice
}

const limit = 100
const buckets = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]

const invalidReasonLabels: Record<AestheticAuditReason, string> = {
  missing: '缺失',
  non_finite: '非有限',
  out_of_range: '越界',
  provenance_mismatch: '来源不匹配',
  ambiguous: '歧义',
}

export function AestheticsPage({ task, folders, folder, onFolderChange, notify }: AestheticsPageProps) {
  const [offset, setOffset] = useState(0)
  const [decision, setDecision] = useState<AuditDecisionFilter>('all')
  const [bucket, setBucket] = useState<number | null>(null)
  const [reasonCode, setReasonCode] = useState<AestheticAuditReason | ''>('')
  const [audit, setAudit] = useState<AestheticAuditList | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [confirm, setConfirm] = useState<{
    decision: 'approved_keep' | 'approved_exclude'
    label: string
    danger?: boolean
  } | null>(null)
  const [mediaSampleId, setMediaSampleId] = useState<string | null>(null)

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
    setError(null)
    setConfirm(null)
    setMediaSampleId(null)
  }, [bucket, decision, folder, reasonCode, task?.id])

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
    void listAestheticAudit(taskId, {
      offset,
      limit,
      folder,
      bucket: bucket ?? undefined,
      reasonCode: reasonCode || undefined,
      decision,
    })
      .then((value) => {
        if (!canceled) setAudit(value)
      })
      .catch((reason: unknown) => {
        if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取美学审计')
      })
      .finally(() => {
        if (!canceled) setLoading(false)
      })
    return () => {
      canceled = true
    }
  }, [bucket, decision, folder, offset, reasonCode, revision, task?.id])

  const selectableItems = useMemo(
    () => (audit?.items ?? []).filter((item) => item.review_eligible || item.decision === 'approved_exclude'),
    [audit?.items],
  )
  const selectedAll = selectableItems.length > 0 && selectableItems.every((item) => selected.has(item.sample_id))

  const decide = async () => {
    if (!task || !confirm || selected.size === 0) return
    setLoading(true)
    try {
      await submitCuratedReviewDecisions(task.id, {
        decision: confirm.decision,
        evidence_type: 'aesthetic',
        sample_ids: [...selected],
      })
      notify(`已更新 ${selected.size} 项美学决定`)
      setSelected(new Set())
      setConfirm(null)
      setRevision((value) => value + 1)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '美学决定提交失败', 'error')
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
          onChange={(event) => onFolderChange(event.target.value)}
          value={folder}
        >
          <option value="">全部子文件夹</option>
          {(folders?.items ?? []).map((item) => (
            <option key={item.folder_id} value={item.folder_id}>{item.display_name}</option>
          ))}
        </select>
        <select
          aria-label="美学分档"
          onChange={(event) => {
            setBucket(event.target.value ? Number(event.target.value) : null)
            setReasonCode('')
          }}
          value={bucket ?? ''}
        >
          <option value="">全部分档</option>
          {buckets.map((value) => (
            <option key={value} value={value}>{value.toFixed(1)} 分</option>
          ))}
        </select>
        <select
          aria-label="无效分数筛选"
          onChange={(event) => {
            setReasonCode(event.target.value as AestheticAuditReason | '')
            setBucket(null)
          }}
          value={reasonCode}
        >
          <option value="">全部有效性</option>
          {(Object.keys(invalidReasonLabels) as AestheticAuditReason[]).map((reason) => (
            <option key={reason} value={reason}>{invalidReasonLabels[reason]}</option>
          ))}
        </select>
      </section>

      {audit ? <AestheticAuditCounts audit={audit} /> : null}

      <section className="selection-bar">
        <label>
          <input
            checked={selectedAll}
            disabled={selectableItems.length === 0}
            onChange={(event) => setSelected(event.target.checked
              ? new Set(selectableItems.map((item) => item.sample_id))
              : new Set())}
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
      {loading && !audit ? <LoadingBlock label="正在读取美学审计" /> : null}
      {!loading && !error && audit && audit.items.length === 0 ? (
        <EmptyState title={folder ? '当前子文件夹没有美学结果' : '当前任务没有美学结果'} />
      ) : null}
      {audit && audit.items.length > 0 ? (
        <AestheticAuditGrid
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

function AestheticAuditCounts({ audit }: { audit: AestheticAuditList }) {
  return (
    <section className="review-counts aesthetic-audit-counts">
      {buckets.map((value) => {
        const key = value.toFixed(1)
        return <span key={key}>{key} 分 <strong>{audit.bucket_counts[key]}</strong></span>
      })}
      {(Object.keys(invalidReasonLabels) as AestheticAuditReason[]).map((reason) => (
        <span key={reason}>{invalidReasonLabels[reason]} <strong>{audit.invalid_counts[reason]}</strong></span>
      ))}
      <span>待复核 <strong>{audit.pending.toLocaleString()}</strong></span>
      <span>保留 <strong>{audit.approved_keep.toLocaleString()}</strong></span>
      <span>排除 <strong>{audit.approved_exclude.toLocaleString()}</strong></span>
    </section>
  )
}

function AestheticAuditGrid({
  items,
  selected,
  setSelected,
  taskId,
  onOpenMedia,
}: {
  items: AestheticAuditItem[]
  selected: Set<string>
  setSelected: (value: Set<string>) => void
  taskId: string
  onOpenMedia: (sampleId: string) => void
}) {
  return (
    <section className="aesthetic-audit-grid">
      {items.map((item) => (
        <article
          className={`aesthetic-audit-card${selected.has(item.sample_id) ? ' selected' : ''}`}
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
            {item.reason_code ? (
              <span className="aesthetic-audit-invalid">{invalidReasonLabels[item.reason_code]}</span>
            ) : null}
            <div>
              {item.decision ? (
                <StatusPill value={item.decision} />
              ) : item.review_eligible ? (
                <span className="aesthetic-pending">待复核</span>
              ) : (
                <span className="aesthetic-read-only">只读浏览</span>
              )}
              <b>{item.bucket === null ? '无有效分数' : `${item.bucket.toFixed(1)} 分`}</b>
            </div>
            {item.score !== null ? <small>原始分 {item.score.toFixed(2)}</small> : null}
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
