import { Ban, CheckSquare2, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { updateManualExclusions } from '../clients/reviews'
import type { FolderList, Task } from '../types'
import { ConfirmDialog } from '../ui'

export type Notify = (message: string, tone?: 'success' | 'error') => void

export type AuditPageProps = {
  task: Task | null
  folders: FolderList | null
  folder: string
  onFolderChange: (folder: string) => void
  notify: Notify
  onChanged: () => Promise<void>
}

export function canReclassifyWatermarkEvidence(task: Task) {
  return task.status === 'evidence_review'
    || (task.status === 'paused' && task.resume_state === 'evidence_review')
}

export function FolderScopeSelector({
  folders,
  folder,
  metric,
  onChange,
}: {
  folders: FolderList | null
  folder: string
  metric: 'leaf_cluster_count' | 'risk_sample_count'
  onChange: (folder: string) => void
}) {
  const total = folders?.items.reduce((sum, item) => sum + item.sample_count, 0) ?? 0
  return (
    <label className="folder-scope">
      <span>文件夹</span>
      <select aria-label="一级文件夹" onChange={(event) => onChange(event.target.value)} value={folder}>
        <option value="">全部文件夹 ({total.toLocaleString()} 张)</option>
        {folders?.items.map((item) => (
          <option key={item.folder_id} value={item.folder_id}>
            {item.display_name} ({item.sample_count.toLocaleString()} 张，{item[metric].toLocaleString()} 项)
          </option>
        ))}
      </select>
    </label>
  )
}

export function SelectionCheckbox({
  checked,
  partial = false,
  label,
  onChange,
}: {
  checked: boolean
  partial?: boolean
  label: string
  onChange: () => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = partial
  }, [partial])
  return <input aria-label={label} checked={checked} onChange={onChange} ref={ref} type="checkbox" />
}

export function ManualActionBar({
  task,
  sampleIds,
  page,
  folder,
  notify,
  onChanged,
  onCompleted,
}: {
  task: Task
  sampleIds: string[]
  page: 'clusters' | 'risks'
  folder: string
  notify: Notify
  onChanged: () => Promise<void>
  onCompleted: () => void
}) {
  const [intent, setIntent] = useState<'exclude' | 'restore' | null>(null)
  const [busy, setBusy] = useState(false)
  const editable = task.status === 'evidence_review'
    || (task.status === 'paused' && task.resume_state === 'evidence_review')
  const excluded = intent === 'exclude'

  const submit = async () => {
    if (intent === null || sampleIds.length === 0) return
    setBusy(true)
    try {
      const result = await updateManualExclusions(task.id, {
        sample_ids: sampleIds,
        excluded,
        context: { page, folder_id: folder || null },
      })
      notify(
        excluded
          ? `已将 ${result.changed.toLocaleString()} 张图片从导出中排除`
          : `已撤销 ${result.changed.toLocaleString()} 张图片的手动排除`,
      )
      setIntent(null)
      onCompleted()
      await onChanged()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法更新手动排除', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <section className="selection-bar audit-selection-bar">
        <CheckSquare2 aria-hidden="true" size={18} />
        <strong>已选择 {sampleIds.length.toLocaleString()} 张</strong>
        <div>
          <button className="button danger" disabled={!editable || sampleIds.length === 0 || busy} onClick={() => setIntent('exclude')} type="button">
            <Ban size={16} />
            从导出中排除
          </button>
          <button className="button secondary" disabled={!editable || sampleIds.length === 0 || busy} onClick={() => setIntent('restore')} type="button">
            <RotateCcw size={16} />
            撤销排除
          </button>
        </div>
        {!editable ? <small>当前任务为只读；只有等待精选复核或暂停在该阶段时可以更改。</small> : null}
      </section>
      <ConfirmDialog
        busy={busy}
        confirmLabel={excluded ? '确认排除' : '确认撤销'}
        danger={excluded}
        detail={excluded
          ? `这 ${sampleIds.length.toLocaleString()} 张图片将不进入后续导出，源文件和分析记录保持不变。`
          : `恢复这 ${sampleIds.length.toLocaleString()} 张图片的导出资格，历史决定仍会保留。`}
        onCancel={() => !busy && setIntent(null)}
        onConfirm={() => void submit()}
        open={intent !== null}
        title={excluded ? '从导出中排除' : '撤销手动排除'}
      />
    </>
  )
}
