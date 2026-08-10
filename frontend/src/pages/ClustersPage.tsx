import { useVirtualizer } from '@tanstack/react-virtual'
import { Boxes, Eye } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getCoverageReport, listClusters, listClusterSamples, sampleThumbnailUrl } from '../clients/workspace'
import { coverageRequestState, coverageScopeIdentity, profileResolutionsForTask, workspaceDisplayName } from '../profileWorkspace'
import type { ClusterList, ClusterSampleList, CoverageReport, Task } from '../types'
import { EmptyState, ErrorBlock, LoadingBlock, Modal, Pagination, StatusPill, Thumbnail } from '../ui'
import { type AuditPageProps, ManualActionBar, SelectionCheckbox } from './auditPageSupport'

function CoverageReportPanel({ task }: { task: Task }) {
  const requestState = coverageRequestState(task)
  const resolutions = profileResolutionsForTask(task)
  const resolutionsKey = resolutions.join(',')
  const [resolution, setResolution] = useState(resolutions[0])
  const [report, setReport] = useState<CoverageReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setResolution((current) => resolutions.includes(current) ? current : resolutions[0])
  }, [resolutionsKey])
  useEffect(() => {
    if (requestState !== 'ready') {
      setReport(null)
      setError(null)
      setLoading(false)
      return
    }
    let canceled = false
    setLoading(true)
    setError(null)
    setReport(null)
    getCoverageReport(task.id, resolution)
      .then((value) => { if (!canceled) setReport(value) })
      .catch((reason) => { if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取覆盖报告') })
      .finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [requestState, resolution, task.id])

  if (requestState !== 'ready') {
    const detail = requestState === 'paused'
      ? `任务已暂停；恢复前保留当前 ${workspaceDisplayName('broad')} 成员与覆盖状态。`
      : requestState === 'exporting'
        ? '任务正在导出；完成后可查看覆盖报告。'
        : `等待 ${workspaceDisplayName('broad')} 成员和语义分析准备完成。`
    return <section className="panel coverage-report-panel"><header className="panel-header"><div><h2>覆盖报告</h2><span>coverage-report/v1</span></div></header><EmptyState title="覆盖报告尚未就绪" detail={detail} /></section>
  }
  return (
    <section className="panel coverage-report-panel" aria-live="polite">
      <header className="panel-header"><div><h2>覆盖报告</h2><span>coverage-report/v1</span></div><label className="coverage-resolution"><span>分辨率</span><select aria-label="覆盖报告分辨率" onChange={(event) => setResolution(Number(event.target.value))} value={resolution}>{resolutions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label></header>
      {loading ? <LoadingBlock label="正在读取覆盖报告" /> : null}
      {error ? <ErrorBlock message={error} /> : null}
      {!loading && !error && report?.status !== 'ready' ? <EmptyState title="覆盖报告暂无可用数据" /> : null}
      {!loading && !error && report?.status === 'ready' && report.scopes.length === 0 ? <EmptyState title={`当前分辨率没有 ${workspaceDisplayName('broad')} 成员`} /> : null}
      {!loading && !error && report?.status === 'ready' && report.scopes.length > 0 ? <div className="coverage-scope-list">{report.scopes.map((scope) => {
        const identity = coverageScopeIdentity(scope.scope_id)
        return <article className="coverage-scope-row" key={scope.scope_id}><div><span>范围</span><code data-coverage-scope data-scope-ascii-hex={identity.asciiHex ?? ''} data-scope-repr={identity.repr} title={identity.repr}>{identity.scopeId}</code></div><span>{workspaceDisplayName('broad')} {scope.broad_sample_count.toLocaleString()}</span><span>嵌入 {scope.embedding_count.toLocaleString()}</span><span>叶簇 {scope.leaf_count ?? '未就绪'}</span></article>
      })}</div> : null}
    </section>
  )
}

export function ClustersPage({ task, folder, notify, onChanged }: AuditPageProps) {
  const [data, setData] = useState<ClusterList | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailCluster, setDetailCluster] = useState<ClusterList['items'][number] | null>(null)
  const [detailData, setDetailData] = useState<ClusterSampleList | null>(null)
  const [detailOffset, setDetailOffset] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [individualSelected, setIndividualSelected] = useState<Set<string>>(new Set())
  const [clusterSelections, setClusterSelections] = useState<Record<string, string[]>>({})
  const [selectingCluster, setSelectingCluster] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const parent = useRef<HTMLDivElement>(null)
  const activeTaskId = useRef(task?.id)
  const limit = 100
  const detailLimit = 50
  useEffect(() => { activeTaskId.current = task?.id }, [task?.id])
  useEffect(() => { setOffset(0); setData(null); setError(null); setDetailCluster(null); setIndividualSelected(new Set()); setClusterSelections({}) }, [folder, task?.id])
  useEffect(() => {
    if (!task) return
    let canceled = false
    setLoading(true); setError(null)
    listClusters(task.id, { offset, limit, folder })
      .then((value) => { if (!canceled) setData(value) })
      .catch((reason) => { if (!canceled) setError(reason instanceof Error ? reason.message : '无法读取聚类') })
      .finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [folder, offset, task?.id, version])
  useEffect(() => {
    if (!task || !detailCluster) return
    let canceled = false
    setDetailLoading(true); setDetailError(null)
    listClusterSamples(task.id, detailCluster.cluster_id, { offset: detailOffset, limit: detailLimit, folder })
      .then((value) => { if (!canceled) setDetailData(value) })
      .catch((reason) => { if (!canceled) setDetailError(reason instanceof Error ? reason.message : '无法读取聚类成员') })
      .finally(() => { if (!canceled) setDetailLoading(false) })
    return () => { canceled = true }
  }, [detailCluster, detailOffset, folder, task?.id, version])
  const items = data?.items ?? []
  const virtualizer = useVirtualizer({ count: items.length, getScrollElement: () => parent.current, estimateSize: () => 116, overscan: 8 })
  const selectedIds = useMemo(() => [...new Set([...individualSelected, ...Object.values(clusterSelections).flat()])], [clusterSelections, individualSelected])
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds])
  const clearSelection = useCallback(() => { setIndividualSelected(new Set()); setClusterSelections({}); setVersion((current) => current + 1) }, [])
  const toggleCluster = async (cluster: ClusterList['items'][number]) => {
    const expected = folder ? cluster.folder_size : cluster.total_size
    const current = clusterSelections[cluster.cluster_id] ?? []
    if (current.length === expected && expected > 0) { setClusterSelections((values) => { const next = { ...values }; delete next[cluster.cluster_id]; return next }); return }
    setSelectingCluster(cluster.cluster_id)
    try {
      let nextOffset = 0; let total = 0; const ids: string[] = []
      do {
        const page = await listClusterSamples(task?.id as string, cluster.cluster_id, { offset: nextOffset, limit: 200, folder })
        total = page.total
        if (total > 5000) throw new Error('单个簇超过 5000 张，请在成员详情中分批选择')
        ids.push(...page.items.map((item) => item.sample_id)); nextOffset += page.items.length
        if (page.items.length === 0) break
      } while (nextOffset < total)
      if (task && activeTaskId.current === task.id) setClusterSelections((values) => ({ ...values, [cluster.cluster_id]: [...new Set(ids)] }))
    } catch (reason) { notify(reason instanceof Error ? reason.message : '无法选择聚类成员', 'error') } finally { setSelectingCluster(null) }
  }
  const removeFromClusterSelections = (sampleIds: Set<string>) => setClusterSelections((values) => Object.fromEntries(Object.entries(values).map(([clusterId, ids]) => [clusterId, ids.filter((sampleId) => !sampleIds.has(sampleId))]).filter(([, ids]) => ids.length > 0)))
  const toggleSample = (sampleId: string) => {
    if (selectedSet.has(sampleId)) { setIndividualSelected((values) => { const next = new Set(values); next.delete(sampleId); return next }); removeFromClusterSelections(new Set([sampleId])); return }
    setIndividualSelected((values) => new Set(values).add(sampleId))
  }
  const detailItems = detailData?.items ?? []
  const allDetailSelected = detailItems.length > 0 && detailItems.every((item) => selectedSet.has(item.sample_id))
  const toggleDetailPage = () => {
    const ids = new Set(detailItems.map((item) => item.sample_id))
    if (allDetailSelected) { setIndividualSelected((values) => { const next = new Set(values); ids.forEach((sampleId) => next.delete(sampleId)); return next }); removeFromClusterSelections(ids); return }
    setIndividualSelected((values) => { const next = new Set(values); ids.forEach((sampleId) => next.add(sampleId)); return next })
  }
  if (!task) return <EmptyState title="未选择任务" detail="先选择需要查看的任务" />
  return <div className="page-stack">
    <section className="review-toolbar"><div><span>当前查看任务</span><strong title={task.name}>{task.name}</strong><p>{workspaceDisplayName('broad')} 工作区的聚类与 coverage-report/v1。</p></div><div className="data-page-controls"><div className="data-page-count"><Boxes aria-hidden="true" size={20} /><strong>{data?.total.toLocaleString() ?? 0}</strong><span>叶簇</span></div></div></section>
    <CoverageReportPanel task={task} />
    <ManualActionBar folder={folder} notify={notify} onChanged={onChanged} onCompleted={clearSelection} page="clusters" sampleIds={selectedIds} task={task} />
    {error ? <ErrorBlock message={error} /> : null}
    {loading && items.length === 0 ? <LoadingBlock label="正在读取聚类" /> : null}
    {!loading && !error && items.length === 0 ? <EmptyState title="尚无聚类结果" /> : <section className="cluster-list" ref={parent}><div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>{virtualizer.getVirtualItems().map((row) => {
      const cluster = items[row.index]; const expected = folder ? cluster.folder_size : cluster.total_size; const selectedCount = clusterSelections[cluster.cluster_id]?.length ?? 0; const checked = expected > 0 && selectedCount === expected
      return <article className="cluster-row audit-row" key={cluster.cluster_id} onClick={() => { setDetailOffset(0); setDetailData(null); setDetailCluster(cluster) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setDetailOffset(0); setDetailData(null); setDetailCluster(cluster) } }} role="button" style={{ transform: `translateY(${row.start}px)` }} tabIndex={0}><span className="row-check" onClick={(event) => event.stopPropagation()}><SelectionCheckbox checked={checked} label={`选择簇 ${cluster.cluster_key}`} onChange={() => void toggleCluster(cluster)} partial={selectedCount > 0 && !checked} /></span>{cluster.representative_sample_id ? <Thumbnail alt={cluster.representative_path ?? cluster.cluster_key} src={sampleThumbnailUrl(task.id, cluster.representative_sample_id, 192)} /> : <div className="cluster-placeholder"><Boxes size={20} /></div>}<div><strong title={cluster.cluster_key}>{cluster.cluster_key}</strong><span title={cluster.representative_path ?? ''}>{cluster.representative_path ?? '无代表样本'}</span>{selectingCluster === cluster.cluster_id ? <small>正在解析成员…</small> : null}</div><span>{cluster.scope_kind === 'artist' || cluster.scope_kind === 'concept' ? cluster.scope_id : '全局'}</span><b>{folder ? `${cluster.folder_size.toLocaleString()} / ${cluster.total_size.toLocaleString()} 张` : `${cluster.total_size.toLocaleString()} 张`}</b><Eye aria-label="查看成员" size={17} /></article>
    })}</div></section>}
    <Pagination limit={limit} offset={offset} onChange={setOffset} total={data?.total ?? 0} />
    <Modal onClose={() => setDetailCluster(null)} open={detailCluster !== null} title={detailCluster ? `聚类成员 · ${detailCluster.cluster_key}` : '聚类成员'} width="large"><div className="detail-selection-head"><label><SelectionCheckbox checked={allDetailSelected} label="选择本页全部成员" onChange={toggleDetailPage} partial={!allDetailSelected && detailItems.some((item) => selectedSet.has(item.sample_id))} />本页全选</label><span>已选择 {selectedIds.length.toLocaleString()} 张</span></div>{detailError ? <ErrorBlock message={detailError} /> : null}{detailLoading && detailItems.length === 0 ? <LoadingBlock label="正在读取聚类成员" /> : null}{!detailLoading && !detailError && detailItems.length === 0 ? <EmptyState title="当前文件夹没有该簇成员" /> : <div className="audit-member-grid">{detailItems.map((sample) => <label className={selectedSet.has(sample.sample_id) ? 'audit-member selected' : 'audit-member'} key={sample.sample_id}><input checked={selectedSet.has(sample.sample_id)} onChange={() => toggleSample(sample.sample_id)} type="checkbox" /><Thumbnail alt={sample.relative_path} src={sampleThumbnailUrl(task.id, sample.sample_id, 256)} /><strong title={sample.relative_path}>{sample.relative_path}</strong><span>{sample.score === null ? '无相似度' : `相似度 ${sample.score.toFixed(4)}`}</span><div>{sample.is_representative ? <i>代表样本</i> : null}{sample.manually_excluded ? <StatusPill value="approved_exclude" /> : null}</div></label>)}</div>}<Pagination limit={detailLimit} offset={detailOffset} onChange={setDetailOffset} total={detailData?.total ?? 0} /></Modal>
  </div>
}
