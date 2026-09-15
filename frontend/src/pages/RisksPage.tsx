import { useVirtualizer } from '@tanstack/react-virtual'
import { Eye, ShieldAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  listCuratedReviews,
  setWatermarkReviewThreshold,
  submitCuratedReviewDecisions,
} from '../clients/reviews'
import { getRiskSampleDetail, listRiskSampleIds, listRiskSamples, sampleThumbnailUrl } from '../clients/workspace'
import { getRiskEvidenceExplanation, getRiskEvidenceLabel } from '../riskEvidenceHelp'
import type { CuratedReviewItem, RiskEvidence, RiskSampleDetail, RiskSampleList, TaskOverview } from '../types'
import {
  AuditDecisionTabs,
  type AuditDecisionFilter,
  ConfirmDialog,
  EmptyState,
  ErrorBlock,
  LoadingBlock,
  Modal,
  Pagination,
  SampleMediaViewer,
  StatusPill,
  Thumbnail,
} from '../ui'
import { type AuditPageProps, canReclassifyWatermarkEvidence, FolderScopeSelector, SelectionCheckbox } from './auditPageSupport'

export function RisksPage({ task, overview, folders, folder, onFolderChange, notify, onChanged }: AuditPageProps & { overview: TaskOverview | null }) {
  const [data, setData] = useState<RiskSampleList | null>(null)
  const [decisions, setDecisions] = useState<Map<string, CuratedReviewItem>>(new Map())
  const [offset, setOffset] = useState(0)
  const [code, setCode] = useState('')
  const [severity, setSeverity] = useState('')
  const [decision, setDecision] = useState<AuditDecisionFilter>('all')
  const [watermarkThreshold, setWatermarkThreshold] = useState('0.995')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirm, setConfirm] = useState<{ decision: 'approved_keep' | 'approved_exclude'; label: string; danger?: boolean } | null>(null)
  const [detailSampleId, setDetailSampleId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RiskSampleDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [mediaSampleId, setMediaSampleId] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const listParent = useRef<HTMLDivElement>(null)
  const [limit, setLimit] = useState(200)
  const [allResultsSelected, setAllResultsSelected] = useState(false)
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null)
  const selectionRequest = useRef(0)
  const scopeLabel = `${code ? getRiskEvidenceLabel(code) : '全部风险类型'} · ${folder || '全部文件夹'} · ${severity || '全部级别'} · ${{ all: '全部状态', pending_review: '待复核', approved_keep: '已保留', approved_exclude: '已排除' }[decision]}`

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
    setAllResultsSelected(false)
    selectionRequest.current += 1
    setConfirm(null)
    setDetailSampleId(null)
    setMediaSampleId(null)
  }, [code, decision, folder, limit, severity, task?.id])

  useEffect(() => {
    if (!task) {
      setData(null)
      setDecisions(new Map())
      setLoading(false)
      return
    }
    let canceled = false
    setLoading(true)
    setError(null)
    setData(null)
    void listRiskSamples(task.id, { offset, limit, code, severity, folder, decision })
      .then(async (risks) => {
        const sampleIds = risks.items.map((item) => item.sample_id)
        const activePages = await Promise.all(Array.from({ length: Math.ceil(sampleIds.length / 100) }, (_, index) => {
          const pageIds = sampleIds.slice(index * 100, (index + 1) * 100)
          return listCuratedReviews(task.id, {
              evidenceType: 'risk',
              limit: pageIds.length,
              offset: 0,
              decision: decision === 'all' ? undefined : decision,
              folder: folder || undefined,
              reasonCode: code || undefined,
              severity: severity || undefined,
              candidateGroup: undefined,
              sampleIds: pageIds,
            })
        }))
        if (!canceled) {
          setData(risks)
          setDecisions(new Map(activePages.flatMap((page) => page.items.map((item) => [item.sample_id, item] as const))))
        }
      })
      .catch((reason: unknown) => { if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取风险图片') })
      .finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [code, decision, folder, limit, offset, revision, severity, task?.id])

  useEffect(() => {
    if (!task || !detailSampleId) return
    let canceled = false
    setDetailLoading(true)
    setDetailError(null)
    void getRiskSampleDetail(task.id, detailSampleId, { code, severity })
      .then((value) => { if (!canceled) setDetail(value) })
      .catch((reason: unknown) => { if (!canceled) setDetailError(reason instanceof Error ? reason.message : '无法读取风险详情') })
      .finally(() => { if (!canceled) setDetailLoading(false) })
    return () => { canceled = true }
  }, [code, data?.items, detailSampleId, severity, task?.id])

  const items = data?.items ?? []
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => listParent.current,
    estimateSize: () => 132,
    overscan: 8,
  })
  const selectedIds = useMemo(() => [...selected], [selected])
  const allPageSelected = items.length > 0 && items.every((item) => selected.has(item.sample_id))
  const toggleSample = (sampleId: string) => { setAllResultsSelected(false); setSelected((values) => {
    const next = new Set(values)
    if (next.has(sampleId)) next.delete(sampleId)
    else next.add(sampleId)
    return next
  }) }
  const togglePage = () => {
    setAllResultsSelected(false)
    setSelected((values) => {
      const next = new Set(values)
      for (const item of items) {
        if (allPageSelected) next.delete(item.sample_id)
        else next.add(item.sample_id)
      }
      return next
    })
  }
  const selectAllMatching = async () => {
    if (!task) return
    const request = ++selectionRequest.current
    setBusy(true)
    try {
      const ids = await listRiskSampleIds(task.id, { code, severity, folder, decision })
      if (request !== selectionRequest.current) return
      setSelected(new Set(ids))
      setAllResultsSelected(true)
      notify(`已选择当前筛选下的 ${ids.length.toLocaleString()} 张图片（包含其他页）`)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '全选失败', 'error')
    } finally { setBusy(false) }
  }
  const canReclassifyWatermark = task !== null && canReclassifyWatermarkEvidence(task)

  const reclassifyWatermarkEvidence = async () => {
    if (!task) return
    const threshold = Number(watermarkThreshold)
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
      notify('水印阈值必须在 0 到 1 之间', 'error')
      return
    }
    setBusy(true)
    try {
      const result = await setWatermarkReviewThreshold(task.id, threshold, task.row_version)
      notify(`已按 ${result.threshold.toFixed(3)} 重判 ${result.updated.toLocaleString()} 条水印证据，候选 ${result.candidates.toLocaleString()} 张`)
      setRevision((value) => value + 1)
      await onChanged()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法重判水印证据', 'error')
    } finally {
      setBusy(false)
    }
  }

  const submitDecision = async () => {
    if (!task || !confirm || busy || selectedIds.length === 0) return
    setBusy(true)
    setBatchProgress({ done: 0, total: selectedIds.length })
    try {
      let changed = 0
      for (let start = 0; start < selectedIds.length; start += 500) {
        const batch = selectedIds.slice(start, start + 500)
        const result = await submitCuratedReviewDecisions(task.id, {
          decision: confirm.decision,
          evidence_type: 'risk',
          sample_ids: batch,
        })
        changed += result.changed
        setSelected((values) => {
          const remaining = new Set(values)
          for (const id of batch) remaining.delete(id)
          return remaining
        })
        setBatchProgress({ done: start + batch.length, total: selectedIds.length })
      }
      notify(`已处理 ${selectedIds.length.toLocaleString()} 张图片，更新 ${changed.toLocaleString()} 项风险决定`)
      setSelected(new Set())
      setConfirm(null)
      setAllResultsSelected(false)
      setOffset(0)
      setRevision((value) => value + 1)
      await onChanged()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '风险决定提交失败', 'error')
      setConfirm(null)
      setAllResultsSelected(false)
      setRevision((value) => value + 1)
    } finally {
      setBusy(false)
      setBatchProgress(null)
    }
  }

  if (!task) return <EmptyState title="未选择任务" detail="先选择需要查看的任务" />

  return (
    <div className="page-stack">
      <section className="review-toolbar risk-toolbar" inert={busy}>
        <div><span>当前查看任务</span><strong title={task.name}>{task.name}</strong></div>
        <div className="data-page-controls">
          <div className="data-page-count"><ShieldAlert aria-hidden="true" size={20} /><strong>{data?.total.toLocaleString() ?? 0}</strong><span>风险图片</span></div>
          <AuditDecisionTabs onChange={setDecision} value={decision} />
          <FolderScopeSelector folder={folder} folders={folders} metric="risk_sample_count" onChange={(value) => onFolderChange(value)} />
          <label><span>证据类型</span><select aria-label="证据类型" onChange={(event) => setCode(event.target.value)} value={code}><option value="">全部证据</option>{overview?.evidence_codes.map((item) => <option key={item.name} value={item.name}>{item.name} ({item.count})</option>)}</select></label>
          <label><span>风险级别</span><select aria-label="风险级别" onChange={(event) => setSeverity(event.target.value)} value={severity}><option value="">全部级别</option><option value="fatal">致命</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label>
          <label><span>每页数量</span><select aria-label="每页数量" value={limit} onChange={(event) => setLimit(Number(event.target.value))}><option value={100}>100 张</option><option value={200}>200 张</option><option value={500}>500 张</option></select></label>
          {code === 'watermark_probability' ? <label><span>水印复核阈值</span><div className="inline-control"><input aria-label="水印复核阈值" max="1" min="0" onChange={(event) => setWatermarkThreshold(event.target.value)} step="0.001" type="number" value={watermarkThreshold} /><button className="button secondary" disabled={busy || !canReclassifyWatermark} onClick={() => void reclassifyWatermarkEvidence()} type="button">重判</button></div></label> : null}
        </div>
      </section>
      <section className="selection-bar risk-selection-bar" inert={busy || loading}><label><SelectionCheckbox checked={allPageSelected} label="选择本页全部风险图片" onChange={togglePage} partial={!allPageSelected && items.some((item) => selected.has(item.sample_id))} />本页全选</label><button className="button secondary" disabled={!data?.total} onClick={() => void selectAllMatching()} type="button">全选当前筛选结果（{data?.total.toLocaleString() ?? 0}）</button><strong>已选 {selected.size.toLocaleString()}{allResultsSelected ? '（跨页）' : ''}</strong><button className="button secondary" disabled={!selected.size} onClick={() => { setSelected(new Set()); setAllResultsSelected(false) }} type="button">清空选择</button><div><DecisionButton disabled={selected.size === 0} label={decision === 'approved_exclude' ? '撤销排除/保留' : '保留'} onClick={() => setConfirm({ decision: 'approved_keep', label: '批准保留' })} /><DecisionButton danger disabled={selected.size === 0} label="排除" onClick={() => setConfirm({ decision: 'approved_exclude', label: '批准排除', danger: true })} /></div></section>
      {batchProgress ? <p role="status">正在批量更新：{batchProgress.done.toLocaleString()} / {batchProgress.total.toLocaleString()}。已完成的批次会保留；失败后可重试剩余选择。</p> : busy ? <p role="status">正在读取当前筛选的完整选择范围…</p> : null}
      {error ? <ErrorBlock message={error} /> : null}
      {loading && items.length === 0 ? <LoadingBlock label="正在读取风险图片" /> : null}
      {!loading && !error && data && items.length === 0 ? <EmptyState title="当前筛选没有风险证据" /> : null}
      {items.length > 0 ? <section className="risk-list" inert={busy} ref={listParent}><div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>{virtualizer.getVirtualItems().map((row) => { const item = items[row.index]; const active = decisions.get(item.sample_id); return <article className="risk-row audit-row" key={item.sample_id} onClick={() => { setDetail(null); setDetailSampleId(item.sample_id) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setDetail(null); setDetailSampleId(item.sample_id) } }} role="button" style={{ transform: `translateY(${row.start}px)` }} tabIndex={0}><span className="row-check" onClick={(event) => event.stopPropagation()}><input aria-label={`选择 ${item.relative_path}`} checked={selected.has(item.sample_id)} onChange={() => toggleSample(item.sample_id)} type="checkbox" /></span><Thumbnail alt={item.relative_path} onClick={() => setMediaSampleId(item.sample_id)} src={sampleThumbnailUrl(task.id, item.sample_id, 224)} /><div><strong title={item.relative_path}>{item.relative_path}</strong><span title={item.evidence_codes.join('、')}>{item.evidence_codes.join('、')}</span><small>{item.evidence_count.toLocaleString()} 条证据</small></div><span className={`severity ${item.highest_severity}`}>{item.highest_severity}</span><StatusPill value={active?.decision ?? 'pending_review'} /><Eye aria-label="查看证据" size={17} /></article> })}</div></section> : null}
      <div inert={busy}><Pagination limit={limit} offset={offset} onChange={setOffset} total={data?.total ?? 0} /></div>
      <ConfirmDialog busy={busy} confirmLabel={confirm?.label ?? '确认'} danger={confirm?.danger} detail={`${scopeLabel}。本次${confirm?.label ?? '更新'}已选择的 ${(batchProgress?.total ?? selected.size).toLocaleString()} 张图片${allResultsSelected ? '，包含当前筛选下的其他页面' : ''}。操作可通过“已排除”筛选后批量保留来撤销。${batchProgress ? ` 已处理 ${batchProgress.done.toLocaleString()} / ${batchProgress.total.toLocaleString()}` : ''}`} onCancel={() => { if (!busy) setConfirm(null) }} onConfirm={() => void submitDecision()} open={confirm !== null} title="确认批量复核" />
      <ModalRiskDetail detail={detail} detailError={detailError} detailLoading={detailLoading} onClose={() => setDetailSampleId(null)} onOpenMedia={setMediaSampleId} open={detailSampleId !== null} taskId={task.id} />
      <SampleMediaViewer onClose={() => setMediaSampleId(null)} sampleId={mediaSampleId} taskId={task.id} />
    </div>
  )
}

function ModalRiskDetail({ detail, detailError, detailLoading, onClose, onOpenMedia, open, taskId }: { detail: RiskSampleDetail | null; detailError: string | null; detailLoading: boolean; onClose: () => void; onOpenMedia: (sampleId: string) => void; open: boolean; taskId: string }) {
  return <Modal onClose={onClose} open={open} title={detail ? `风险详情 · ${detail.relative_path}` : '风险详情'} width="large">{detailError ? <ErrorBlock message={detailError} /> : null}{detailLoading && !detail ? <LoadingBlock label="正在读取风险详情" /> : null}{detail ? <div className="risk-detail"><div className="risk-detail-preview"><Thumbnail alt={detail.relative_path} onClick={() => onOpenMedia(detail.sample_id)} src={sampleThumbnailUrl(taskId, detail.sample_id, 768)} /><strong title={detail.relative_path}>{detail.relative_path}</strong><span>{detail.artist_scope}</span></div><div className="evidence-detail-list">{detail.evidence.map((evidence) => <EvidenceDetail evidence={evidence} key={evidence.evidence_id} />)}</div></div> : null}</Modal>
}

function DecisionButton({ danger = false, disabled, label, onClick }: { danger?: boolean; disabled: boolean; label: string; onClick: () => void }) {
  return <button className={`button ${danger ? 'danger' : 'secondary'}`} disabled={disabled} onClick={onClick} type="button">{label}</button>
}

function EvidenceDetail({ evidence }: { evidence: RiskEvidence }) {
  return <article className="evidence-detail"><header><div><strong>{getRiskEvidenceLabel(evidence.code)}</strong><span>英文代码：{evidence.code}</span><span>{evidence.source}</span></div><span className={`severity ${evidence.severity}`}>{evidence.severity}</span></header><p className="evidence-detail-explanation">{getRiskEvidenceExplanation(evidence.code)}</p><dl><div><dt>检测值</dt><dd>{formatEvidence(evidence.value_number ?? evidence.value)}</dd></div><div><dt>阈值</dt><dd>{formatEvidence(evidence.threshold_number ?? evidence.threshold)}</dd></div></dl></article>
}

function formatEvidence(value: unknown) {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  if (typeof value === 'string') return value
  return value === null || value === undefined ? '无' : JSON.stringify(value)
}
