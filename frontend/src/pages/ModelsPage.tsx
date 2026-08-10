import {
  Ban,
  CheckCircle2,
  Download,
  FileInput,
  HardDriveDownload,
  LoaderCircle,
  RefreshCw,
} from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useState } from 'react'

import {
  downloadAllModels,
  listModels,
  registerLocalModel,
  runModelAction,
} from '../clients/models'
import { selectFile } from '../clients/filesystem'
import type {
  ComponentManifest,
  ComponentRun,
  ModelList,
  ModelStatus,
  Task,
} from '../types'
import { EmptyState, ErrorBlock, LoadingBlock, Modal, StatusPill } from '../ui'

type Notice = (message: string, tone?: 'success' | 'error') => void

export function ModelsPage({
  notify,
  components,
  componentRuns,
  task,
}: {
  notify: Notice
  components: ComponentManifest[]
  componentRuns: ComponentRun[]
  task: Task | null
}) {
  const [data, setData] = useState<ModelList | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [localOpen, setLocalOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await listModels())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法读取模型')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    const active = data?.items.some((item) =>
      ['queued', 'downloading', 'verifying'].includes(item.installation_status),
    )
    if (!active) return
    const timer = window.setInterval(() => void load(), 2000)
    return () => window.clearInterval(timer)
  }, [data, load])

  const action = async (model: ModelStatus, name: 'download' | 'verify' | 'cancel') => {
    setBusy(model.id)
    try {
      await runModelAction(model.id, name)
      notify(name === 'cancel' ? '已请求取消模型操作' : '模型操作已启动')
      await load()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '模型操作失败', 'error')
    } finally {
      setBusy(null)
    }
  }

  const downloadAll = async () => {
    setBusy('__all__')
    try {
      await downloadAllModels()
      notify('已加入全部模型下载任务')
      await load()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法启动全部下载', 'error')
    } finally {
      setBusy(null)
    }
  }

  const replacements = useMemo(
    () => data?.items.filter((item) => item.replaceable && !item.is_custom) ?? [],
    [data],
  )
  return (
    <div className="page-stack">
      <section className="toolbar-band">
        <div>
          <strong>{data?.total ?? 0} 个注册模型</strong>
          <span>{task ? `当前任务：${task.name}` : data ? `注册表 ${data.registry_version}` : '正在读取注册表'}</span>
        </div>
        <div className="toolbar-actions">
          <button className="button secondary" onClick={() => setLocalOpen(true)} type="button">
            <FileInput size={16} />
            导入本地模型
          </button>
          <button className="button primary" disabled={busy !== null} onClick={() => void downloadAll()} type="button">
            <HardDriveDownload size={16} />
            预下载全部
          </button>
          <button className="icon-button" disabled={loading} onClick={() => void load()} title="刷新模型" type="button">
            <RefreshCw className={loading ? 'spin' : ''} size={17} />
          </button>
        </div>
      </section>
      {error ? <ErrorBlock message={error} /> : null}
      {loading && !data ? <LoadingBlock label="正在读取模型注册表" /> : null}
      {!loading && data?.items.length === 0 ? <EmptyState title="模型注册表为空" /> : null}
      <section className="model-list">
        {data?.items.map((model) => {
          const progress = model.total_bytes
            ? Math.min(100, (model.bytes_downloaded / model.total_bytes) * 100)
            : 0
          const active = ['queued', 'downloading', 'verifying'].includes(model.installation_status)
          const consumers = modelConsumers(model, components, componentRuns)
          return (
            <article className="model-row" key={model.id}>
              <div className="model-title">
                <strong>{model.display_name}</strong>
                <span>{purposeLabel(model.purpose)}</span>
                {model.is_custom ? <i>本地</i> : null}
              </div>
              <div className="model-source">
                <code title={model.repository ?? model.local_root}>{model.repository ?? model.local_root}</code>
                <small>{formatBytes(model.total_bytes)} · {model.license}</small>
              </div>
              <div className="model-state">
                <StatusPill value={model.installation_status} />
                {active ? <div className="mini-progress"><i style={{ width: `${progress}%` }} /></div> : null}
                {model.error ? <small title={model.error}>{model.error}</small> : null}
              </div>
              <div className="model-consumers">
                <div title={consumers.map((item) => item.manifest.id).join(', ')}>
                  {consumers.length ? consumers.map((consumer) => (
                    <span
                      className={consumer.run ? `state-${consumer.run.status}` : 'state-declared'}
                      key={consumer.manifest.id}
                      title={consumerTitle(consumer, components)}
                    >
                      <i />
                      {consumer.manifest.display_name}
                    </span>
                  )) : <span className="no-consumer">未声明消费者</span>}
                </div>
                <small title={model.dependencies.join(', ')}>
                  {model.blocking_dependencies.length
                    ? `${model.blocking_dependencies.length} 个模型依赖未就绪`
                    : model.dependencies.length
                      ? `${model.dependencies.length} 个模型依赖已就绪`
                      : '无模型依赖'}
                </small>
              </div>
              <div className="row-actions">
                {active ? (
                  <button
                    className="icon-button danger-icon"
                    disabled={busy === model.id}
                    onClick={() => void action(model, 'cancel')}
                    title="取消操作"
                    type="button"
                  >
                    <Ban size={17} />
                  </button>
                ) : model.installation_status === 'ready' ? (
                  <button
                    className="icon-button"
                    disabled={busy === model.id}
                    onClick={() => void action(model, 'verify')}
                    title="重新校验"
                    type="button"
                  >
                    <CheckCircle2 size={17} />
                  </button>
                ) : (
                  <button
                    className="icon-button"
                    disabled={busy === model.id}
                    onClick={() => void action(model, 'download')}
                    title="下载模型与依赖"
                    type="button"
                  >
                    <Download size={17} />
                  </button>
                )}
              </div>
            </article>
          )
        })}
      </section>
      <LocalModelDialog
        bases={replacements}
        notify={notify}
        onClose={() => setLocalOpen(false)}
        onRegistered={async () => {
          setLocalOpen(false)
          await load()
        }}
        open={localOpen}
      />
    </div>
  )
}

type ModelConsumer = {
  manifest: ComponentManifest
  run: ComponentRun | null
}

function modelConsumers(
  model: ModelStatus,
  manifests: ComponentManifest[],
  runs: ComponentRun[],
): ModelConsumer[] {
  const identities = new Set([model.id, model.base_model_id].filter((item): item is string => Boolean(item)))
  const runById = new Map(runs.map((run) => [run.component_id, run]))
  return manifests
    .filter((manifest) => {
      const run = runById.get(manifest.id)
      return manifest.model_ids.some((id) => identities.has(id))
        || run?.model_ids.some((id) => identities.has(id)) === true
    })
    .map((manifest) => ({ manifest, run: runById.get(manifest.id) ?? null }))
}

function consumerTitle(consumer: ModelConsumer, manifests: ComponentManifest[]): string {
  const names = new Map(manifests.map((item) => [item.id, item.display_name]))
  const dependencies = consumer.run?.dependency_ids.map((id) => names.get(id) ?? id) ?? []
  const status = consumer.run?.status ?? '未加入当前任务'
  return dependencies.length
    ? `${status} · 依赖：${dependencies.join('、')}`
    : status
}

function LocalModelDialog({
  open,
  bases,
  onClose,
  onRegistered,
  notify,
}: {
  open: boolean
  bases: ModelStatus[]
  onClose: () => void
  onRegistered: () => Promise<void>
  notify: Notice
}) {
  const [base, setBase] = useState('aesthetic_lse14_5k')
  const [path, setPath] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [pickerBusy, setPickerBusy] = useState(false)
  const pathId = useId()
  const browse = async () => {
    if (busy || pickerBusy) return
    setPickerBusy(true)
    try {
      const selection = await selectFile('model', path)
      if (!selection.cancelled && selection.path) setPath(selection.path)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法打开 Windows 文件选择窗口', 'error')
    } finally {
      setPickerBusy(false)
    }
  }
  const submit = async () => {
    if (busy || pickerBusy) return
    setBusy(true)
    try {
      await registerLocalModel(base, path, displayName)
      notify('本地模型已复制并注册')
      await onRegistered()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '本地模型注册失败', 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      title="导入本地替换模型"
      onClose={busy || pickerBusy ? () => undefined : onClose}
      width="medium"
    >
      <div className="form-grid">
        <label className="field span-2">
          <span>替换基础模型</span>
          <select onChange={(event) => setBase(event.target.value)} value={base}>
            {bases.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}
          </select>
        </label>
        <div className="field span-2">
          <label htmlFor={pathId}>本地文件或模型目录绝对路径</label>
          <span className="input-with-button">
            <input
              disabled={busy || pickerBusy}
              id={pathId}
              onChange={(event) => setPath(event.target.value)}
              value={path}
            />
            <button
              aria-busy={pickerBusy}
              aria-label="选择本地模型文件"
              className="icon-button path-picker-button"
              disabled={busy || pickerBusy}
              onClick={() => void browse()}
              title="用 Windows 窗口选择本地模型文件"
              type="button"
            >
              {pickerBusy ? <LoaderCircle className="spin" size={17} /> : <FileInput size={17} />}
            </button>
          </span>
        </div>
        <label className="field span-2">
          <span>显示名称（可选）</span>
          <input onChange={(event) => setDisplayName(event.target.value)} value={displayName} />
        </label>
      </div>
      <div className="modal-actions">
        <button className="button secondary" disabled={busy || pickerBusy} onClick={onClose} type="button">取消</button>
        <button className="button primary" disabled={busy || !path.trim() || !base} onClick={() => void submit()} type="button">
          <FileInput size={16} />
          导入并校验
        </button>
      </div>
    </Modal>
  )
}

function purposeLabel(value: string) {
  const labels: Record<string, string> = {
    aesthetic_and_domain: '美学 / 目标域',
    ai_detection: 'AI 图检测',
    image_embeddings: '图像向量',
    style_analysis: '画风分析',
    watermark_detection: '水印证据',
    ocr_detection: 'OCR 检测',
    ocr_recognition: 'OCR 识别',
  }
  return labels[value] ?? value
}

function formatBytes(value: number) {
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(0)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}
