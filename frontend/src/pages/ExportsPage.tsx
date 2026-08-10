import { FolderOpen, FolderOutput, LoaderCircle, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { createExportRun, listExportRuns, previewExportRun } from '../clients/exportRuns'
import { restoreRewriteBackup } from '../clients/exports'
import { selectDirectory } from '../clients/filesystem'
import { releaseCopyExport } from '../clients/tasks'
import {
  isCopyExportTask,
  profileResolutions,
  validateExportRunAestheticMinimum,
  validateExportRunDomainMinimum,
} from '../profileWorkspace'
import type { ExportRun, ExportRunPreview, ExportRunSettings, Task } from '../types'
import { EmptyState, ErrorBlock, LoadingBlock, StatusPill } from '../ui'

const activeRunStatuses = new Set(['queued', 'planning', 'copying', 'verifying', 'publishing'])
const exclusionKeys = [
  'manual_exclude',
  'domain_below_minimum',
  'duplicate_representative',
  'style_outlier',
  'aesthetic_below_minimum',
  'missing',
  'non_finite',
  'out_of_range',
  'provenance_mismatch',
  'ambiguous',
  'folder_below_minimum',
]

export function ExportsPage({ task }: { task: Task | null }) {
  const [restoreBusy, setRestoreBusy] = useState(false)
  const [runs, setRuns] = useState<ExportRun[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [outputRoot, setOutputRoot] = useState('')
  const [minimumResolution, setMinimumResolution] = useState<number>(profileResolutions[0])
  const [domainEnabled, setDomainEnabled] = useState(false)
  const [domainMinimum, setDomainMinimum] = useState('')
  const [excludeExactVisualDuplicates, setExcludeExactVisualDuplicates] = useState(false)
  const [styleOutlierMode, setStyleOutlierMode] = useState<ExportRunSettings['style_outlier_mode']>('off')
  const [aestheticEnabled, setAestheticEnabled] = useState(false)
  const [aestheticMinimum, setAestheticMinimum] = useState('')
  const [minimumFolderImages, setMinimumFolderImages] = useState('1')
  const [addRepeatPrefix, setAddRepeatPrefix] = useState(true)
  const [sampleSeenMode, setSampleSeenMode] = useState<ExportRunSettings['sample_seen_mode']>('off')
  const [sampleSeenTarget, setSampleSeenTarget] = useState('')
  const [preview, setPreview] = useState<ExportRunPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [pickerBusy, setPickerBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [firstRunCreated, setFirstRunCreated] = useState(false)
  const previewRequest = useRef(0)
  const isCopy = isCopyExportTask(task)
  const isFirstCopy = isCopy && task?.status === 'evidence_review'
  const isRepeatCopy = isCopy && task?.status === 'completed'
  const canConfigure = (isFirstCopy || isRepeatCopy) && !firstRunCreated
  const hasActiveRun = runs.some((run) => activeRunStatuses.has(run.status))
  const domainError = validateExportRunDomainMinimum(domainEnabled, domainMinimum)
  const aestheticError = validateExportRunAestheticMinimum(aestheticEnabled, aestheticMinimum)
  const folderImagesError = positiveIntegerError(minimumFolderImages, '最小文件夹图片数')
  const sampleTargetError = sampleSeenMode === 'manual'
    ? positiveIntegerError(sampleSeenTarget, '手动 sample seen 目标')
    : null

  useEffect(() => () => { previewRequest.current += 1 }, [])

  useEffect(() => {
    if (!task) {
      setRuns([])
      setRunsError(null)
      setRunsLoading(false)
      return
    }
    let alive = true
    setRunsLoading(true)
    setRunsError(null)
    listExportRuns(task.id, { offset: 0, limit: 50 })
      .then((result) => { if (alive) setRuns(result.items) })
      .catch((reason) => {
        if (alive) setRunsError(reason instanceof Error ? reason.message : '无法读取导出历史')
      })
      .finally(() => { if (alive) setRunsLoading(false) })
    return () => { alive = false }
  }, [refreshKey, task?.id])

  useEffect(() => {
    if (!task || !hasActiveRun) return
    const timer = window.setInterval(() => setRefreshKey((current) => current + 1), 2000)
    return () => window.clearInterval(timer)
  }, [hasActiveRun, task?.id])

  const invalidatePreview = () => {
    previewRequest.current += 1
    setPreview(null)
    setPreviewError(null)
    setFormError(null)
  }

  const settings = (): ExportRunSettings | null => {
    const root = outputRoot.trim()
    if (!root) {
      setFormError('请选择新的空导出目录')
      return null
    }
    if (domainError || aestheticError || folderImagesError || sampleTargetError) {
      setFormError(domainError ?? aestheticError ?? folderImagesError ?? sampleTargetError)
      return null
    }
    return {
      output_root: root,
      minimum_resolution: minimumResolution,
      domain_minimum: domainEnabled ? Number(domainMinimum) : null,
      exclude_exact_visual_duplicates: excludeExactVisualDuplicates,
      style_outlier_mode: styleOutlierMode,
      aesthetic_minimum: aestheticEnabled ? Number(aestheticMinimum) : null,
      minimum_folder_images: Number(minimumFolderImages),
      add_repeat_prefix: addRepeatPrefix,
      sample_seen_mode: sampleSeenMode,
      sample_seen_target: sampleSeenMode === 'manual' ? Number(sampleSeenTarget) : null,
    }
  }

  const browse = async () => {
    if (!task || pickerBusy || creating || previewLoading) return
    setPickerBusy(true)
    try {
      const selection = await selectDirectory('output', outputRoot)
      if (!selection.cancelled && selection.path) {
        setOutputRoot(selection.path)
        invalidatePreview()
      }
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : '无法选择导出目录')
    } finally {
      setPickerBusy(false)
    }
  }

  const requestPreview = async () => {
    if (!task) return
    const input = settings()
    if (!input) return
    const request = ++previewRequest.current
    setPreview(null)
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const result = await previewExportRun(task.id, input)
      if (request === previewRequest.current) setPreview(result)
    } catch (reason) {
      if (request === previewRequest.current) {
        setPreviewError(reason instanceof Error ? reason.message : '无法生成导出预览')
      }
    } finally {
      if (request === previewRequest.current) setPreviewLoading(false)
    }
  }

  const create = async () => {
    if (!task || !preview) return
    const input = settings()
    if (!input) return
    setCreating(true)
    setFormError(null)
    try {
      const run = isFirstCopy
        ? await releaseCopyExport(task.id, task.row_version, 'evidence_review', {
          ...input,
          preview_digest: preview.preview_digest,
        })
        : await createExportRun(task.id, { ...input, preview_digest: preview.preview_digest })
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)])
      setPreview(null)
      setRefreshKey((current) => current + 1)
      if (isFirstCopy) setFirstRunCreated(true)
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : '无法创建导出')
    } finally {
      setCreating(false)
    }
  }

  const restore = async () => {
    if (!task || !window.confirm('恢复最近一次复写备份。已有同名源文件不会被覆盖。')) return
    setRestoreBusy(true)
    try {
      const result = await restoreRewriteBackup(task.id, task.row_version)
      window.alert(`已恢复 ${result.restored_files.toLocaleString()} 个文件。`)
    } catch (reason) {
      window.alert(reason instanceof Error ? reason.message : '恢复复写备份失败')
    } finally {
      setRestoreBusy(false)
    }
  }

  if (!task) return <EmptyState title="未选择任务" detail="先选择需要查看的任务" />

  return (
    <div className="page-stack">
      {canConfigure ? (
        <section className="panel repeat-export-panel">
          <header className="panel-header"><div><h2>{isFirstCopy ? '配置首次导出' : '创建重复导出'}</h2><span>先生成预览，再创建后台导出。</span></div></header>
          <div className="repeat-export-form">
            <label className="field span-2"><span>输出目录</span><div className="path-input"><input aria-label="导出目录" disabled={creating || previewLoading || pickerBusy} onChange={(event) => { setOutputRoot(event.target.value); invalidatePreview() }} value={outputRoot} /><button aria-busy={pickerBusy} aria-label="选择导出目录" className="icon-button path-picker-button" disabled={creating || previewLoading || pickerBusy} onClick={() => void browse()} title="用 Windows 窗口选择导出目录" type="button">{pickerBusy ? <LoaderCircle className="spin" size={17} /> : <FolderOpen size={17} />}</button></div></label>
            <label className="field"><span>最低分辨率</span><select aria-label="最低分辨率" disabled={creating || previewLoading} onChange={(event) => { setMinimumResolution(Number(event.target.value)); invalidatePreview() }} value={minimumResolution}>{profileResolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}</select></label>
            <label className="field"><span>最小文件夹图片数</span><input aria-label="最小文件夹图片数" disabled={creating || previewLoading} min="1" onChange={(event) => { setMinimumFolderImages(event.target.value); invalidatePreview() }} step="1" type="number" value={minimumFolderImages} /></label>
            <label className="repeat-aesthetic-toggle"><input aria-label="启用目标域最低分" checked={domainEnabled} disabled={creating || previewLoading} onChange={(event) => { setDomainEnabled(event.target.checked); invalidatePreview() }} type="checkbox" /><span>按目标域最低分筛选</span></label>
            <label className="repeat-aesthetic-toggle"><input aria-label="排除完全和视觉重复" checked={excludeExactVisualDuplicates} disabled={creating || previewLoading} onChange={(event) => { setExcludeExactVisualDuplicates(event.target.checked); invalidatePreview() }} type="checkbox" /><span>排除完全和视觉重复</span></label>
            {domainEnabled ? <label className="field"><span>目标域最低分</span><input aria-label="目标域最低分" disabled={creating || previewLoading} max="1" min="0" onChange={(event) => { setDomainMinimum(event.target.value); invalidatePreview() }} step="0.01" type="number" value={domainMinimum} /></label> : null}
            <label className="field"><span>画风离群筛选</span><select aria-label="画风离群筛选" disabled={creating || previewLoading} onChange={(event) => { setStyleOutlierMode(event.target.value as ExportRunSettings['style_outlier_mode']); invalidatePreview() }} value={styleOutlierMode}><option value="off">关闭</option><option value="strong">仅强离群</option><option value="all">离群与强离群</option></select></label>
            <label className="repeat-aesthetic-toggle"><input aria-label="启用美学最低分" checked={aestheticEnabled} disabled={creating || previewLoading} onChange={(event) => { setAestheticEnabled(event.target.checked); invalidatePreview() }} type="checkbox" /><span>按美学最低分筛选</span></label>
            <label className="repeat-aesthetic-toggle"><input aria-label="增加 repeat 前缀" checked={addRepeatPrefix} disabled={creating || previewLoading} onChange={(event) => { setAddRepeatPrefix(event.target.checked); invalidatePreview() }} type="checkbox" /><span>增加 repeat 前缀</span></label>
            {aestheticEnabled ? <label className="field"><span>美学最低分</span><input aria-label="美学最低分" disabled={creating || previewLoading} max="5" min="1" onChange={(event) => { setAestheticMinimum(event.target.value); invalidatePreview() }} step="0.5" type="number" value={aestheticMinimum} /></label> : null}
            <label className="field"><span>sample seen 配平</span><select aria-label="sample seen 配平" disabled={creating || previewLoading} onChange={(event) => { setSampleSeenMode(event.target.value as ExportRunSettings['sample_seen_mode']); invalidatePreview() }} value={sampleSeenMode}><option value="off">关闭</option><option value="auto">自动</option><option value="manual">手动</option></select></label>
            {sampleSeenMode === 'manual' ? <label className="field"><span>手动 sample seen 目标</span><input aria-label="手动 sample seen 目标" disabled={creating || previewLoading} min="1" onChange={(event) => { setSampleSeenTarget(event.target.value); invalidatePreview() }} step="1" type="number" value={sampleSeenTarget} /></label> : null}
            {domainError ?? aestheticError ?? folderImagesError ?? sampleTargetError ? <ErrorBlock message={domainError ?? aestheticError ?? folderImagesError ?? sampleTargetError ?? ''} /> : null}
            {previewError ? <ErrorBlock message={previewError} /> : null}
            {formError ? <ErrorBlock message={formError} /> : null}
            <div className="repeat-export-actions span-2"><button className="button secondary" disabled={creating || previewLoading} onClick={() => void requestPreview()} type="button">{previewLoading ? '正在预览' : '预览导出'}</button><button className="button primary" disabled={creating || previewLoading || preview === null || domainError !== null || aestheticError !== null || folderImagesError !== null || sampleTargetError !== null} onClick={() => void create()} type="button"><FolderOutput size={16} />{isFirstCopy ? '完成复核并创建导出' : '创建重复导出'}</button></div>
          </div>
          {preview ? <ExportPreview preview={preview} /> : null}
        </section>
      ) : null}

      {firstRunCreated ? <section className="panel"><header className="panel-header"><div><h2>首次导出已创建</h2><span>任务已完成复核，导出在后台执行。</span></div></header></section> : null}

      {!isCopy ? <section className="panel table-panel"><header className="panel-header"><div><h2>复写备份</h2><span>复写路径保留独立预览、确认与备份恢复流程。</span></div><button className="button secondary" disabled={restoreBusy} onClick={() => void restore()} type="button"><RotateCcw size={16} />恢复最近复写备份</button></header></section> : null}

      <section className="panel repeat-export-history">
        <header className="panel-header"><div><h2>导出历史</h2><span>{runs.length.toLocaleString()} 条记录</span></div></header>
        {runsError ? <ErrorBlock message={runsError} /> : null}
        {runsLoading && runs.length === 0 ? <LoadingBlock label="正在读取导出历史" /> : null}
        {!runsLoading && !runsError && runs.length === 0 ? <EmptyState title="尚无导出记录" /> : null}
        <div className="repeat-export-run-list">{runs.map((run) => <ExportRunHistory key={run.id} run={run} />)}</div>
      </section>
    </div>
  )
}

function ExportPreview({ preview }: { preview: ExportRunPreview }) {
  const excluded = exclusionText(preview.exclusion_counts)
  return <section className="export-preview" aria-live="polite"><header><strong>预览</strong><span>纳入 {preview.included_count.toLocaleString()}，{excluded}</span></header><div className="export-preview-settings"><span>目标域 {preview.domain_minimum ?? '关闭'}</span><span>重复 {preview.exclude_exact_visual_duplicates ? '完全和视觉' : '关闭'}</span><span>画风 {styleOutlierLabel(preview.style_outlier_mode)}</span><span>美学 {preview.aesthetic_minimum ?? '关闭'}</span></div><div className="export-preview-folders">{preview.folders.map((folder, index) => { const item = record(folder); const name = text(item?.source_identifier) ?? `文件夹 ${index + 1}`; const imageCount = count(item?.image_count); const excludedFolder = item?.excluded === true; const warningCodes = Array.isArray(item?.warning_codes) ? item.warning_codes.filter((code): code is string => typeof code === 'string') : []; return <div className="export-preview-folder" key={`${name}:${index}`}><strong>{name}</strong><span>{excludedFolder ? '排除' : '纳入'} {imageCount.toLocaleString()} 张</span>{warningCodes.length > 0 ? <small>{warningCodes.join('，')}</small> : null}</div> })}</div>{preview.warnings.length > 0 ? <div className="export-preview-warnings">{preview.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div> : null}</section>
}

function ExportRunHistory({ run }: { run: ExportRun }) {
  const summary = record(run.summary)
  const total = summary ? { included: count(summary.included_count), exclusions: record(summary.exclusion_counts), warnings: Array.isArray(summary.warnings) ? summary.warnings.filter((item): item is string => typeof item === 'string') : [] } : null
  return <article className="repeat-export-run"><header><div><strong title={run.output_root}>{run.output_root}</strong><span>最低分辨率 {run.minimum_resolution}</span></div><StatusPill value={run.status} /></header><dl><div><dt>进度</dt><dd>{run.progress_current.toLocaleString()} / {run.progress_total?.toLocaleString() ?? '未知'} 个文件</dd></div><div><dt>大小</dt><dd>{formatBytes(run.bytes_current)} / {run.bytes_total === null ? '未知' : formatBytes(run.bytes_total)}</dd></div><div><dt>清单</dt><dd title={run.manifest_sha256 ?? ''}>{run.manifest_sha256 ?? '尚未生成'}</dd></div>{run.aesthetic_minimum !== null ? <div><dt>美学最低分</dt><dd>{run.aesthetic_minimum}</dd></div> : null}</dl><div className="repeat-export-summary"><strong>筛除设置</strong><span>目标域 {run.domain_minimum ?? '关闭'}，重复 {run.exclude_exact_visual_duplicates ? '完全和视觉' : '关闭'}，画风 {styleOutlierLabel(run.style_outlier_mode)}</span></div>{run.error_code || run.error_message ? <ErrorBlock message={[run.error_code, run.error_message].filter(Boolean).join('：')} /> : null}{total ? <div className="repeat-export-summary"><strong>汇总</strong><span>导出 {total.included.toLocaleString()}，{exclusionText(total.exclusions ?? {})}</span>{total.warnings.length > 0 ? <span>警告 {total.warnings.join('，')}</span> : null}</div> : null}</article>
}

function positiveIntegerError(value: string, label: string): string | null {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? null : `${label}必须为正整数`
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function exclusionText(summary: Record<string, unknown> | Record<string, number>): string {
  const values = exclusionKeys.map((key) => [key, count(summary[key])] as const).filter(([, value]) => value > 0)
  return values.length === 0 ? '无排除' : values.map(([key, value]) => `${key} ${value}`).join('，')
}

function styleOutlierLabel(mode: ExportRunSettings['style_outlier_mode']): string {
  return mode === 'all' ? '离群与强离群' : mode === 'strong' ? '仅强离群' : '关闭'
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}
