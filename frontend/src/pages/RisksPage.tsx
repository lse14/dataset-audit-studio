import { Eye, ShieldAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  listCuratedReviews,
  setWatermarkReviewThreshold,
  submitCuratedReviewDecisions,
} from '../clients/reviews'
import { getRiskSampleDetail, listRiskSamples, sampleThumbnailUrl } from '../clients/workspace'
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
  const limit = 100

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
    setConfirm(null)
    setDetailSampleId(null)
    setMediaSampleId(null)
  }, [code, decision, folder, severity, task?.id])

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
    void Promise.all([
      listRiskSamples(task.id, { offset, limit, code, severity, folder, decision }),
      listCuratedReviews(task.id, {
        evidenceType: 'risk',
        limit,
        offset,
        decision: decision === 'all' ? undefined : decision,
        folder: folder || undefined,
        reasonCode: code || undefined,
        severity: severity || undefined,
        candidateGroup: undefined,
      }),
    ])
      .then(([risks, active]) => {
        if (!canceled) {
          setData(risks)
          setDecisions(new Map(active.items.map((item) => [item.sample_id, item])))
        }
      })
      .catch((reason: unknown) => { if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取风险图片') })
      .finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [code, decision, folder, offset, revision, severity, task?.id])

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
  const selectedIds = useMemo(() => [...selected], [selected])
  const allPageSelected = items.length > 0 && items.every((item) => selected.has(item.sample_id))
  const toggleSample = (sampleId: string) => setSelected((values) => {
    const next = new Set(values)
    if (next.has(sampleId)) next.delete(sampleId)
    else next.add(sampleId)
    return next
  })
  const togglePage = () => setSelected(allPageSelected ? new Set() : new Set(items.map((item) => item.sample_id)))
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
    if (!task || !confirm || selectedIds.length === 0) return
    setBusy(true)
    try {
      await submitCuratedReviewDecisions(task.id, {
        decision: confirm.decision,
        evidence_type: 'risk',
        sample_ids: selectedIds,
      })
      notify(`已更新 ${selectedIds.length} 项风险决定`)
      setSelected(new Set())
      setConfirm(null)
      setRevision((value) => value + 1)
      await onChanged()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '风险决定提交失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  if (!task) return <EmptyState title="未选择任务" detail="先选择需要查看的任务" />

  return (
    <div className="page-stack">
      <section className="review-toolbar">
        <div><span>当前查看任务</span><strong title={task.name}>{task.name}</strong></div>
        <div className="data-page-controls">
          <div className="data-page-count"><ShieldAlert aria-hidden="true" size={20} /><strong>{data?.total.toLocaleString() ?? 0}</strong><span>风险图片</span></div>
          <AuditDecisionTabs onChange={setDecision} value={decision} />
          <FolderScopeSelector folder={folder} folders={folders} metric="risk_sample_count" onChange={(value) => onFolderChange(value)} />
          <label><span>证据类型</span><select aria-label="证据类型" onChange={(event) => setCode(event.target.value)} value={code}><option value="">全部证据</option>{overview?.evidence_codes.map((item) => <option key={item.name} value={item.name}>{item.name} ({item.count})</option>)}</select></label>
          <label><span>风险级别</span><select aria-label="风险级别" onChange={(event) => setSeverity(event.target.value)} value={severity}><option value="">全部级别</option><option value="fatal">致命</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option><option value="info">提示</option></select></label>
          {code === 'watermark_probability' ? <label><span>水印复核阈值</span><div className="inline-control"><input aria-label="水印复核阈值" max="1" min="0" onChange={(event) => setWatermarkThreshold(event.target.value)} step="0.001" type="number" value={watermarkThreshold} /><button className="button secondary" disabled={busy || !canReclassifyWatermark} onClick={() => void reclassifyWatermarkEvidence()} type="button">重判</button></div></label> : null}
        </div>
      </section>
      <section className="selection-bar"><label><SelectionCheckbox checked={allPageSelected} label="选择本页全部风险图片" onChange={togglePage} partial={!allPageSelected && items.some((item) => selected.has(item.sample_id))} />本页全选</label><strong>已选 {selected.size}</strong><div><DecisionButton disabled={selected.size === 0} label={decision === 'approved_exclude' ? '撤销排除/保留' : '保留'} onClick={() => setConfirm({ decision: 'approved_keep', label: '批准保留' })} /><DecisionButton danger disabled={selected.size === 0} label="排除" onClick={() => setConfirm({ decision: 'approved_exclude', label: '批准排除', danger: true })} /></div></section>
      {error ? <ErrorBlock message={error} /> : null}
      {loading && items.length === 0 ? <LoadingBlock label="正在读取风险图片" /> : null}
      {!loading && !error && data && items.length === 0 ? <EmptyState title="当前筛选没有风险证据" /> : null}
      {items.length > 0 ? <section className="risk-list">{items.map((item) => { const active = decisions.get(item.sample_id); return <article className="risk-row audit-row" key={item.sample_id} onClick={() => { setDetail(null); setDetailSampleId(item.sample_id) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setDetail(null); setDetailSampleId(item.sample_id) } }} role="button" tabIndex={0}><span className="row-check" onClick={(event) => event.stopPropagation()}><input aria-label={`选择 ${item.relative_path}`} checked={selected.has(item.sample_id)} onChange={() => toggleSample(item.sample_id)} type="checkbox" /></span><Thumbnail alt={item.relative_path} onClick={() => setMediaSampleId(item.sample_id)} src={sampleThumbnailUrl(task.id, item.sample_id, 224)} /><div><strong title={item.relative_path}>{item.relative_path}</strong><span title={item.evidence_codes.join('、')}>{item.evidence_codes.join('、')}</span><small>{item.evidence_count.toLocaleString()} 条证据</small></div><span className={`severity ${item.highest_severity}`}>{item.highest_severity}</span>{active ? <StatusPill value={active.decision} /> : <span>状态不可用</span>}<Eye aria-label="查看证据" size={17} /></article> })}</section> : null}
      <Pagination limit={limit} offset={offset} onChange={(nextOffset) => { setSelected(new Set()); setOffset(nextOffset) }} total={data?.total ?? 0} />
      <ConfirmDialog busy={busy} confirmLabel={confirm?.label ?? '确认'} danger={confirm?.danger} detail={`本次只修改明确勾选的 ${selected.size} 项，可再次选择并改回其他状态。`} onCancel={() => setConfirm(null)} onConfirm={() => void submitDecision()} open={confirm !== null} title="确认批量复核" />
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
  return <article className="evidence-detail"><header><div><strong>{evidence.code}</strong><span>{evidence.source}</span></div><span className={`severity ${evidence.severity}`}>{evidence.severity}</span></header><dl><div><dt>检测值</dt><dd>{formatEvidence(evidence.value_number ?? evidence.value)}</dd></div><div><dt>阈值</dt><dd>{formatEvidence(evidence.threshold_number ?? evidence.threshold)}</dd></div></dl></article>
}

function formatEvidence(value: unknown) {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  if (typeof value === 'string') return value
  return value === null || value === undefined ? '无' : JSON.stringify(value)
}
