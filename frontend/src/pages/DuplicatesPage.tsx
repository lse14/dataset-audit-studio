import { Check, ShieldX } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { listDuplicateGroupAudit, submitCuratedReviewDecisions } from '../clients/reviews'
import { selectDuplicateMembersForExclusion } from '../duplicateSelection'
import { sampleThumbnailUrl } from '../clients/workspace'
import type {
  DuplicateAuditEvidenceType,
  DuplicateGroupAuditItem,
  DuplicateGroupAuditList,
  DuplicateGroupMemberAuditItem,
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

export type DuplicatesPageProps = {
  task: Task | null
  folders: FolderList | null
  folder: string
  onFolderChange: (folder: string) => void
  notify: Notice
}

const limit = 100

const evidenceLabels: Record<DuplicateAuditEvidenceType, string> = {
  exact_duplicate: '完全重复',
  visual_duplicate: '视觉重复',
  semantic_duplicate: '语义重复',
}

export function DuplicatesPage({ task, folders, folder, onFolderChange, notify }: DuplicatesPageProps) {
  const [evidenceType, setEvidenceType] = useState<DuplicateAuditEvidenceType>('exact_duplicate')
  const [decision, setDecision] = useState<AuditDecisionFilter>('all')
  const [offset, setOffset] = useState(0)
  const [audit, setAudit] = useState<DuplicateGroupAuditList | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [confirm, setConfirm] = useState<{ decision: 'approved_keep' | 'approved_exclude'; label: string; danger?: boolean } | null>(null)
  const [mediaSampleId, setMediaSampleId] = useState<string | null>(null)
  const semanticEnabled = isSemanticEnabled(task)

  useEffect(() => {
    if (!semanticEnabled && evidenceType === 'semantic_duplicate') {
      setEvidenceType('exact_duplicate')
      return
    }
    setOffset(0)
    setSelected(new Set())
    setError(null)
    setConfirm(null)
    setMediaSampleId(null)
  }, [decision, evidenceType, folder, semanticEnabled, task?.id])

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
    void listDuplicateGroupAudit(taskId, { evidenceType, offset, limit, folder, decision })
      .then((value) => {
        if (!canceled) setAudit(value)
      })
      .catch((reason: unknown) => {
        if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取重复组')
      })
      .finally(() => {
        if (!canceled) setLoading(false)
      })
    return () => {
      canceled = true
    }
  }, [decision, evidenceType, folder, offset, revision, task?.id])

  const automaticSelection = useMemo(
    () => selectDuplicateMembersForExclusion(audit?.items ?? []),
    [audit?.items],
  )
  const selectedAll = automaticSelection.size > 0
    && [...automaticSelection].every((sampleId) => selected.has(sampleId))

  const groupsFullyExcludedBySelection = () => (
    (audit?.items ?? []).filter((group) => group.members.every((member) => (
      selected.has(member.sample_id)
        ? true
        : member.decision === 'approved_exclude'
    )))
  )

  const requestDecision = (decision: 'approved_keep' | 'approved_exclude') => {
    if (decision === 'approved_exclude' && groupsFullyExcludedBySelection().length > 0) {
      setError('至少保留一张：本次排除会使重复组全部排除。')
      return
    }
    setError(null)
    setConfirm({
      decision,
      danger: decision === 'approved_exclude',
      label: decision === 'approved_exclude' ? '批准排除' : '批准保留',
    })
  }

  const decide = async () => {
    if (!task || !confirm || selected.size === 0) return
    if (confirm.decision === 'approved_exclude' && groupsFullyExcludedBySelection().length > 0) {
      setConfirm(null)
      setError('至少保留一张：本次排除会使重复组全部排除。')
      return
    }
    setLoading(true)
    try {
      await submitCuratedReviewDecisions(task.id, {
        decision: confirm.decision,
        evidence_type: evidenceType,
        sample_ids: [...selected],
      })
      notify(`已更新 ${selected.size} 个重复组成员决定`)
      setSelected(new Set())
      setConfirm(null)
      setRevision((value) => value + 1)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '重复组决定提交失败', 'error')
      setLoading(false)
    }
  }

  if (!task) return <EmptyState title="未选择任务" detail="先选择需要审计的任务" />

  return (
    <div className="page-stack">
      <section className="review-toolbar">
        <AuditDecisionTabs onChange={setDecision} value={decision} />
        <div className="segmented-control" aria-label="重复类型" role="group">
          {(Object.keys(evidenceLabels) as DuplicateAuditEvidenceType[])
            .filter((type) => type !== 'semantic_duplicate' || semanticEnabled)
            .map((type) => (
            <button
              className={evidenceType === type ? 'active' : ''}
              key={type}
              onClick={() => setEvidenceType(type)}
              type="button"
            >
              {evidenceLabels[type]}
            </button>
            ))}
        </div>
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
      </section>

      {audit ? (
        <section className="review-counts duplicate-audit-counts">
          <span>待复核 <strong>{audit.pending.toLocaleString()}</strong></span>
          <span>保留 <strong>{audit.approved_keep.toLocaleString()}</strong></span>
          <span>排除 <strong>{audit.approved_exclude.toLocaleString()}</strong></span>
          {audit.unresolved > 0 ? <span>无法分组 <strong>{audit.unresolved.toLocaleString()}</strong></span> : null}
        </section>
      ) : null}

      <section className="selection-bar">
        <label>
          <input
            checked={selectedAll}
            disabled={automaticSelection.size === 0}
            onChange={(event) => setSelected(event.target.checked
              ? new Set(automaticSelection)
              : new Set())}
            type="checkbox"
          />
          本页自动选择可排除成员
        </label>
        <strong>已选 {selected.size}</strong>
        <div>
          <DecisionButton
            disabled={selected.size === 0}
            icon={Check}
            label={decision === 'approved_exclude' ? '撤销排除/保留' : '保留'}
            onClick={() => requestDecision('approved_keep')}
          />
          <DecisionButton
            danger
            disabled={selected.size === 0}
            icon={ShieldX}
            label="排除"
            onClick={() => requestDecision('approved_exclude')}
          />
        </div>
      </section>

      {error ? <ErrorBlock message={error} /> : null}
      {loading && !audit ? <LoadingBlock label="正在读取重复组" /> : null}
      {!loading && !error && audit && audit.items.length === 0 ? (
        <EmptyState title={folder ? '当前子文件夹没有重复组' : '当前任务没有重复组'} />
      ) : null}
      {audit && audit.items.length > 0 ? (
        <DuplicateGroupList
          groups={audit.items}
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
        detail={`本次只修改明确勾选的 ${selected.size} 个成员，可再次选择并改回其他状态。`}
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

function isSemanticEnabled(task: Task | null): boolean {
  const components = task?.config && typeof task.config === 'object'
    ? (task.config as { components?: Record<string, { enabled?: boolean }> }).components
    : undefined
  return components?.['embedding.semantic']?.enabled === true
    && components?.['cluster.hierarchy']?.enabled === true
}

function DuplicateGroupList({
  groups,
  selected,
  setSelected,
  taskId,
  onOpenMedia,
}: {
  groups: DuplicateGroupAuditItem[]
  selected: Set<string>
  setSelected: (value: Set<string>) => void
  taskId: string
  onOpenMedia: (sampleId: string) => void
}) {
  return (
    <section className="duplicate-group-list">
      {groups.map((group) => (
        <article className="duplicate-group" key={group.group_key}>
          <header className="duplicate-group-head">
            <div>
              <strong>组 {group.group_key}</strong>
              <span>{evidenceLabels[group.evidence_type]} · 成员 {group.member_count}</span>
            </div>
            <div className="duplicate-group-summary">
              <span>待复核 {group.pending}</span>
              <span>保留 {group.approved_keep}</span>
              <span>排除 {group.approved_exclude}</span>
              <b>当前保留 {group.effective_retained_count}</b>
            </div>
          </header>
          {group.effective_retained_count === 0 ? (
            <p className="duplicate-group-warning">当前组已全部排除</p>
          ) : null}
          <div className="duplicate-member-list">
            {group.members.map((member) => (
              <DuplicateMember
                key={member.sample_id}
                member={member}
                selected={selected}
                setSelected={setSelected}
                taskId={taskId}
                onOpenMedia={onOpenMedia}
              />
            ))}
          </div>
        </article>
      ))}
    </section>
  )
}

function DuplicateMember({
  member,
  selected,
  setSelected,
  taskId,
  onOpenMedia,
}: {
  member: DuplicateGroupMemberAuditItem
  selected: Set<string>
  setSelected: (value: Set<string>) => void
  taskId: string
  onOpenMedia: (sampleId: string) => void
}) {
  return (
    <article className={`duplicate-member${selected.has(member.sample_id) ? ' selected' : ''}`}>
      {member.review_eligible || member.decision === 'approved_exclude' ? (
        <label className="card-check">
          <input
            aria-label={`选择 ${member.relative_path}`}
            checked={selected.has(member.sample_id)}
            onChange={() => {
              const next = new Set(selected)
              if (next.has(member.sample_id)) next.delete(member.sample_id)
              else next.add(member.sample_id)
              setSelected(next)
            }}
            type="checkbox"
          />
        </label>
      ) : null}
      <Thumbnail alt={member.relative_path} onClick={() => onOpenMedia(member.sample_id)} src={sampleThumbnailUrl(taskId, member.sample_id, 240)} />
      <div className="duplicate-member-body">
        <strong title={member.relative_path}>{member.relative_path}</strong>
        <span>{member.artist_scope}</span>
        <span>档位 {member.resolutions.length > 0 ? member.resolutions.join(' / ') : '无已纳入档位'}</span>
        <div>
          {member.decision ? <StatusPill value={member.decision} /> : <span className="duplicate-pending">待复核</span>}
          {member.score !== null ? <b>{member.score.toFixed(2)}</b> : null}
        </div>
      </div>
    </article>
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
