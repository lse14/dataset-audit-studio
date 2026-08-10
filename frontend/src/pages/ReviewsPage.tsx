import { useVirtualizer } from '@tanstack/react-virtual'
import { Check, ShieldX, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  listCuratedReviews,
  listReviewItems,
  listSaeFeatures,
  submitCuratedReviewDecisions,
  submitReviewDecisions,
} from '../clients/reviews'
import { sampleThumbnailUrl } from '../clients/workspace'
import type {
  AIReviewItem,
  CuratedReviewEvidenceType,
  CuratedReviewItem,
  FolderList,
  ReviewList,
  SAEFeature,
  SAEFeatureList,
  StyleReviewItem,
  Task,
} from '../types'
import {
  ConfirmDialog,
  EmptyState,
  ErrorBlock,
  LoadingBlock,
  Pagination,
  StatusPill,
  Thumbnail,
  useElementWidth,
} from '../ui'

type ReviewMode = 'ai' | 'style' | 'curated' | 'sae'
type ReviewItem = AIReviewItem | StyleReviewItem | CuratedReviewItem
type Notice = (message: string, tone?: 'success' | 'error') => void
export type ReviewSurface = 'legacy' | 'style' | 'aesthetics'

export type ReviewsPageProps = {
  task: Task | null
  folders: FolderList | null
  folder: string
  onFolderChange: (folder: string) => void
  notify: Notice
  surface?: ReviewSurface
}

function isCuratedConfirmationWindow(task: Task | null) {
  return task?.status === 'evidence_review'
    || (task?.status === 'paused' && task.resume_state === 'evidence_review')
}

function modeForSurface(surface: ReviewSurface, task: Task | null): ReviewMode {
  if (surface === 'style') return 'style'
  if (surface === 'aesthetics') return 'curated'
  return isCuratedConfirmationWindow(task) ? 'curated' : 'ai'
}

function evidenceForSurface(_surface: ReviewSurface): CuratedReviewEvidenceType {
  return 'aesthetic'
}

export function ReviewsPage({
  task,
  folders,
  folder,
  onFolderChange,
  notify,
  surface = 'legacy',
}: ReviewsPageProps) {
  const [mode, setMode] = useState<ReviewMode>(() => modeForSurface(surface, task))
  const [offset, setOffset] = useState(0)
  const [decision, setDecision] = useState<string>('pending_review')
  const [curatedEvidenceType, setCuratedEvidenceType] = useState<CuratedReviewEvidenceType>(
    () => evidenceForSurface(surface),
  )
  const [curatedSeverity, setCuratedSeverity] = useState('')
  const [candidateGroup, setCandidateGroup] = useState('')
  const [reviewList, setReviewList] = useState<ReviewList<ReviewItem> | null>(null)
  const [saeList, setSaeList] = useState<SAEFeatureList | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [confirm, setConfirm] = useState<{
    decision: string
    label: string
    danger?: boolean
  } | null>(null)
  const limit = 100

  const changeMode = (next: ReviewMode) => {
    setReviewList(null)
    setSaeList(null)
    setSelected(new Set())
    setOffset(0)
    setMode(next)
  }

  const changeCuratedEvidence = (next: CuratedReviewEvidenceType) => {
    setCuratedEvidenceType(next)
    setCuratedSeverity('')
    setCandidateGroup('')
    setSelected(new Set())
    setOffset(0)
  }

  useEffect(() => {
    setMode(modeForSurface(surface, task))
    setCuratedEvidenceType(evidenceForSurface(surface))
    setCuratedSeverity('')
    setCandidateGroup('')
  }, [surface, task?.id, task?.resume_state, task?.status])

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
    setError(null)
  }, [candidateGroup, curatedEvidenceType, curatedSeverity, folder, mode, task?.id])

  useEffect(() => {
    if (!task) {
      setLoading(false)
      return
    }
    let canceled = false
    setLoading(true)
    setError(null)
    const load = async () => {
      try {
        if (mode === 'sae') {
          const value = await listSaeFeatures(task.id, { offset, limit, folder })
          if (!canceled) setSaeList(value)
        } else if (mode === 'curated') {
          const value = await listCuratedReviews(task.id, {
            evidenceType: curatedEvidenceType,
            limit,
            offset,
            decision,
            folder,
            severity: curatedSeverity,
            candidateGroup,
          })
          if (!canceled) setReviewList(value)
        } else {
          const value = await listReviewItems(task.id, mode, {
            offset,
            limit,
            decision,
            folder,
          })
          if (!canceled) setReviewList(value)
        }
      } catch (reason) {
        if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取复核项')
      } finally {
        if (!canceled) setLoading(false)
      }
    }
    void load()
    return () => {
      canceled = true
    }
  }, [
    candidateGroup,
    curatedEvidenceType,
    curatedSeverity,
    decision,
    folder,
    mode,
    offset,
    revision,
    task,
  ])

  const decide = async () => {
    if (!task || !confirm || selected.size === 0 || mode === 'sae') return
    setLoading(true)
    try {
      if (mode === 'curated') {
        await submitCuratedReviewDecisions(task.id, {
          decision: confirm.decision,
          evidence_type: curatedEvidenceType,
          sample_ids: [...selected],
        })
      } else {
        await submitReviewDecisions(task.id, mode, {
          sample_ids: [...selected],
          decision: confirm.decision,
        })
      }
      notify(`已更新 ${selected.size} 项复核决定`)
      setSelected(new Set())
      setConfirm(null)
      setRevision((value) => value + 1)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '复核操作失败', 'error')
      setLoading(false)
    }
  }

  if (!task) return <EmptyState title="未选择任务" detail="先选择需要复核的任务" />
  const pageItems = mode === 'sae' ? saeList?.items ?? [] : reviewList?.items ?? []
  const total = mode === 'sae' ? saeList?.total ?? 0 : reviewList?.total ?? 0
  const isLegacySurface = surface === 'legacy'

  return (
    <div className="page-stack">
      <section className="review-toolbar">
        {isLegacySurface ? (
          <div className="segmented-control" aria-label="复核类型" role="group">
            <button className={mode === 'ai' ? 'active' : ''} onClick={() => changeMode('ai')} type="button">
              AI 候选
            </button>
            <button className={mode === 'style' ? 'active' : ''} onClick={() => changeMode('style')} type="button">
              画风离群
            </button>
            <button className={mode === 'curated' ? 'active' : ''} onClick={() => changeMode('curated')} type="button">
              精选候选
            </button>
            <button className={mode === 'sae' ? 'active' : ''} onClick={() => changeMode('sae')} type="button">
              SAE 特征
            </button>
          </div>
        ) : null}
        {isLegacySurface && mode === 'curated' ? (
          <select
            aria-label="精选候选类型"
            onChange={(event) => changeCuratedEvidence(event.target.value as CuratedReviewEvidenceType)}
            value={curatedEvidenceType}
          >
            <option value="aesthetic">美学淘汰</option>
            <option value="risk">通用风险</option>
            <option value="style_outlier">画风离群</option>
            <option value="exact_duplicate">完全重复</option>
            <option value="visual_duplicate">视觉重复</option>
            <option value="semantic_duplicate">语义重复</option>
          </select>
        ) : null}
        {isLegacySurface && mode === 'curated' && curatedEvidenceType !== 'aesthetic' ? (
          <select
            aria-label="严重度筛选"
            onChange={(event) => {
              setCuratedSeverity(event.target.value)
              setOffset(0)
            }}
            value={curatedSeverity}
          >
            <option value="">全部严重度</option>
            <option value="info">信息</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
            <option value="fatal">严重</option>
          </select>
        ) : null}
        {mode === 'curated' && curatedEvidenceType !== 'aesthetic' ? (
          <input
            aria-label="候选组筛选"
            maxLength={200}
            onChange={(event) => {
              setCandidateGroup(event.target.value)
              setOffset(0)
            }}
            placeholder="候选组"
            value={candidateGroup}
          />
        ) : null}
        {mode !== 'sae' ? (
          <select
            aria-label="复核状态筛选"
            onChange={(event) => {
              setDecision(event.target.value)
              setOffset(0)
              setSelected(new Set())
            }}
            value={decision}
          >
            <option value="pending_review">待复核</option>
            <option value="approved_keep">已保留</option>
            <option value="approved_exclude">已排除</option>
            <option value="">全部</option>
          </select>
        ) : null}
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

      {mode !== 'sae' && reviewList ? (
        <section className="review-counts">
          <span>待复核 <strong>{reviewList.pending.toLocaleString()}</strong></span>
          <span>保留 <strong>{reviewList.approved_keep.toLocaleString()}</strong></span>
          <span>排除 <strong>{reviewList.approved_exclude.toLocaleString()}</strong></span>
        </section>
      ) : null}

      {mode !== 'sae' ? (
        <section className="selection-bar">
          <label>
            <input
              checked={pageItems.length > 0 && selected.size === pageItems.length}
              onChange={(event) =>
                setSelected(
                  event.target.checked
                    ? new Set(pageItems.map((item) => itemIdentity(item)))
                    : new Set(),
                )
              }
              type="checkbox"
            />
            本页全选
          </label>
          <strong>已选 {selected.size}</strong>
          <div>
            {mode === 'curated' ? (
            <>
              <DecisionButton
                disabled={selected.size === 0}
                icon={Check}
                label="保留"
                onClick={() => setConfirm({ decision: 'approved_keep', label: '批准保留' })}
              />
              <DecisionButton
                danger
                disabled={selected.size === 0}
                icon={ShieldX}
                label="排除"
                onClick={() =>
                  setConfirm({ decision: 'approved_exclude', label: '批准排除', danger: true })
                }
              />
            </>
            ) : (
              <>
                <DecisionButton
                  disabled={selected.size === 0}
                  icon={Check}
                  label="保留"
                  onClick={() => setConfirm({ decision: 'approved_keep', label: '批准保留' })}
                />
                <DecisionButton
                  danger
                  disabled={selected.size === 0}
                  icon={ShieldX}
                  label="排除"
                  onClick={() =>
                    setConfirm({ decision: 'approved_exclude', label: '批准排除', danger: true })
                  }
                />
              </>
            )}
          </div>
        </section>
      ) : null}

      {error ? <ErrorBlock message={error} /> : null}
      {loading && pageItems.length === 0 ? <LoadingBlock label="正在读取复核项" /> : null}
      {!loading && !error && pageItems.length === 0 ? (
        <EmptyState title="当前筛选没有复核项" />
      ) : mode === 'sae' ? (
        <SAEList
          items={saeList?.items ?? []}
          taskId={task.id}
        />
      ) : (
        <ReviewGrid
          items={(reviewList?.items ?? []) as ReviewItem[]}
          mode={mode}
          selected={selected}
          setSelected={setSelected}
          taskId={task.id}
        />
      )}

      <Pagination limit={limit} offset={offset} onChange={setOffset} total={total} />
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
    </div>
  )
}

function ReviewGrid({
  taskId,
  mode,
  items,
  selected,
  setSelected,
}: {
  taskId: string
  mode: 'ai' | 'style' | 'curated'
  items: ReviewItem[]
  selected: Set<string>
  setSelected: (value: Set<string>) => void
}) {
  const [parentRef, width] = useElementWidth<HTMLDivElement>()
  const columns = width < 620 ? 1 : width < 980 ? 2 : width < 1320 ? 3 : 4
  const rowCount = Math.ceil(items.length / columns)
  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 238,
    overscan: 3,
  })
  const toggle = useCallback(
    (id: string) => {
      const next = new Set(selected)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      setSelected(next)
    },
    [selected, setSelected],
  )
  return (
    <div className="review-grid-scroll" ref={parentRef}>
      <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const rowItems = items.slice(
            virtualRow.index * columns,
            virtualRow.index * columns + columns,
          )
          return (
            <div
              className="review-grid-row"
              key={virtualRow.key}
              style={{
                gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              {rowItems.map((item) => {
                const styleItem = item as StyleReviewItem
                const aiItem = item as AIReviewItem
                const curatedItem = item as CuratedReviewItem
                return (
                  <article className={selected.has(item.sample_id) ? 'review-card selected' : 'review-card'} key={item.sample_id}>
                    <label className="card-check">
                      <input
                        aria-label={`选择 ${item.relative_path}`}
                        checked={selected.has(item.sample_id)}
                        onChange={() => toggle(item.sample_id)}
                        type="checkbox"
                      />
                    </label>
                    <Thumbnail
                      alt={item.relative_path}
                      src={sampleThumbnailUrl(taskId, item.sample_id, 320)}
                    />
                    <div className="review-card-body">
                      <strong title={item.relative_path}>{item.relative_path}</strong>
                      <span>{item.artist_scope}</span>
                      <div>
                        <StatusPill value={item.decision} />
                        <b>
                          {mode === 'ai' && 'probability' in aiItem
                            ? `${(aiItem.probability * 100).toFixed(1)}%`
                            : 'style_score' in styleItem
                              ? styleItem.style_score.toFixed(1)
                              : curatedItem.score === null
                                ? curatedItem.reason_code
                                : `${curatedItem.reason_code} ${curatedItem.score.toFixed(2)}`}
                        </b>
                      </div>
                      {mode === 'curated' && (curatedItem.severity || curatedItem.candidate_group) ? (
                        <small>{[curatedItem.severity, curatedItem.candidate_group].filter(Boolean).join(' · ')}</small>
                      ) : null}
                    </div>
                  </article>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SAEList({
  items,
  taskId,
}: {
  items: SAEFeature[]
  taskId: string
}) {
  const [parentRef] = useElementWidth<HTMLDivElement>()
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 152,
    overscan: 8,
  })
  return (
    <div className="sae-list" ref={parentRef}>
      <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
        {virtualizer.getVirtualItems().map((row) => {
          const feature = items[row.index]
          const id = String(feature.feature_id)
          return (
            <article className="sae-row" key={id} style={{ transform: `translateY(${row.start}px)` }}>
              <Sparkles size={18} />
              <strong>特征 {feature.feature_id}</strong>
              <span>阈值 {feature.threshold.toFixed(4)}</span>
              <div className="sae-representatives">
                {feature.representative_samples.length > 0 ? feature.representative_samples.map((sample) => (
                  <figure key={sample.sample_id} title={sample.relative_path}>
                    <Thumbnail
                      alt={sample.relative_path}
                      src={sampleThumbnailUrl(taskId, sample.sample_id, 160)}
                    />
                    <figcaption>{sample.relative_path}</figcaption>
                  </figure>
                )) : <span>当前文件夹没有代表图片</span>}
              </div>
            </article>
          )
        })}
      </div>
    </div>
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

function itemIdentity(item: ReviewItem | SAEFeature) {
  return 'sample_id' in item ? item.sample_id : String(item.feature_id)
}
