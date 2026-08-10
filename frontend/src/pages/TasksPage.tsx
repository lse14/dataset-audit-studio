import {
  BookmarkPlus,
  Check,
  CirclePause,
  CirclePlay,
  FolderOpen,
  ListRestart,
  LoaderCircle,
  Plus,
  Save,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
} from 'lucide-react'
import { useEffect, useId, useMemo, useState } from 'react'

import {
  getRuntimeTuningRecommendation,
  listComponents,
} from '../clients/components'
import { selectDirectory } from '../clients/filesystem'
import {
  createTask,
  controlTask,
  deleteTask,
  type TaskControlAction,
} from '../clients/tasks'
import {
  createTaskPreset,
  createTaskPresetFromTask,
  deleteTaskPreset,
  listTaskPresets,
  updateTaskPreset,
} from '../clients/presets'
import { listBuiltinProfiles } from '../clients/profiles'
import {
  buildDefaultComponentConfig,
  ComponentConfigEditor,
} from '../components/ComponentConfigEditor'
import { FieldHelp } from '../components/FieldHelp'
import {
  isDatasetProfile,
  isCopyExportTask,
  profileDisplayName,
  profileTaskSubmissionComponents,
  validateStyleArtistWeights,
} from '../profileWorkspace'
import { taskConfigHelp } from '../taskConfigHelp'
import {
  mergeBuiltinProfileComponents,
  type BuiltinProfile,
  type ComponentConfigValue,
  type ComponentManifest,
  type DirectorySelection,
  type RuntimeTuningRecommendation,
  type Task,
  type TaskPreset,
} from '../types'
import {
  ConfirmDialog,
  EmptyState,
  ErrorBlock,
  LoadingBlock,
  Modal,
  StatusPill,
} from '../ui'

type Notice = (message: string, tone?: 'success' | 'error') => void

const TASK_CREATION_HIDDEN_CONFIG_FIELDS: Record<string, readonly string[]> = {
  'export.dataset': ['aesthetic_bins'],
}

export function TasksPage({
  tasks,
  selectedTaskId,
  onSelect,
  onChanged,
  onDeleted,
  notify,
}: {
  tasks: Task[]
  selectedTaskId: string | null
  onSelect: (taskId: string) => void
  onChanged: (task?: Task) => Promise<void>
  onDeleted: (taskId: string) => Promise<void>
  notify: Notice
}) {
  const [createOpen, setCreateOpen] = useState(false)
  const [presetSourceTask, setPresetSourceTask] = useState<Task | null>(null)
  const [terminate, setTerminate] = useState<Task | null>(null)
  const [deleting, setDeleting] = useState<Task | null>(null)
  const [busyTask, setBusyTask] = useState<string | null>(null)

  const control = async (
    task: Task,
    action: TaskControlAction,
    extra: Record<string, unknown> = {},
  ) => {
    setBusyTask(task.id)
    try {
      const updated = await controlTask(task.id, action, task.row_version, extra)
      await onChanged(updated)
      notify('任务状态已更新')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '任务操作失败', 'error')
    } finally {
      setBusyTask(null)
    }
  }

  const remove = async (task: Task) => {
    setBusyTask(task.id)
    try {
      const result = await deleteTask(task.id, task.row_version)
      setDeleting(null)
      await onDeleted(task.id)
      if (result.cache_cleared) {
        notify('任务记录与项目内缓存已删除')
      } else {
        notify(`任务记录已删除，但项目内缓存清理失败：${result.cache_cleanup_error ?? '未知错误'}`, 'error')
      }
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '删除任务失败', 'error')
    } finally {
      setBusyTask(null)
    }
  }

  return (
    <div className="page-stack">
      <section className="toolbar-band">
        <div>
          <strong>{tasks.length.toLocaleString()} 个任务</strong>
          <span>单 Worker 顺序执行</span>
        </div>
        <button className="button primary" onClick={() => setCreateOpen(true)} type="button">
          <Plus size={16} />
          新建任务
        </button>
      </section>

      <section className="panel table-panel">
        {tasks.length === 0 ? (
          <EmptyState title="还没有任务" detail="新建任务后可先检查配置，再加入队列" />
        ) : (
          <div className="data-table task-table">
            <div className="table-row table-head">
              <span>任务</span>
              <span>状态</span>
              <span>进度</span>
              <span>更新</span>
              <span>操作</span>
            </div>
            {tasks.map((task) => {
              const active = task.id === selectedTaskId
              const progress = task.progress_total
                ? Math.min(100, (task.progress_current / task.progress_total) * 100)
                : 0
              return (
                <div className={`table-row ${active ? 'selected' : ''}`} key={task.id}>
                  <button className="task-name-cell" onClick={() => onSelect(task.id)} type="button">
                    <strong>{task.name}</strong>
                    <span title={task.source_root}>{task.source_root}</span>
                  </button>
                  <span>
                    <StatusPill value={task.status} />
                  </span>
                  <span className="compact-progress">
                    <i style={{ width: `${progress}%` }} />
                    <small>
                      {task.progress_total
                        ? `${task.progress_current.toLocaleString()} / ${task.progress_total.toLocaleString()}`
                        : '等待阶段进度'}
                    </small>
                  </span>
                  <span className="date-cell">{formatDate(task.updated_at)}</span>
                  <span className="row-actions">
                    <button
                      className="icon-button"
                      disabled={busyTask === task.id}
                      onClick={() => setPresetSourceTask(task)}
                      title="保存为任务预设"
                      type="button"
                    >
                      <BookmarkPlus size={17} />
                    </button>
                    {task.status === 'draft' ? (
                      <button
                        className="icon-button"
                        disabled={busyTask === task.id}
                        onClick={() => void control(task, 'queue')}
                        title="加入队列"
                        type="button"
                      >
                        <CirclePlay size={17} />
                      </button>
                    ) : null}
                    {canPause(task.status) ? (
                      <button
                        className="icon-button"
                        disabled={busyTask === task.id}
                        onClick={() => void control(task, 'pause')}
                        title="暂停"
                        type="button"
                      >
                        <CirclePause size={17} />
                      </button>
                    ) : null}
                    {task.status === 'paused' ? (
                      <button
                        className="icon-button"
                        disabled={busyTask === task.id}
                        onClick={() => void control(task, 'resume')}
                        title="恢复"
                        type="button"
                      >
                        <ListRestart size={17} />
                      </button>
                    ) : null}
                    {task.status === 'evidence_review' ? (
                      <button
                        className="icon-button"
                        disabled={busyTask === task.id}
                        onClick={() => {
                          if (isCopyExportTask(task)) {
                            onSelect(task.id)
                            window.location.hash = 'exports'
                          } else {
                            void control(task, 'review-gate/release', { expected_gate: task.status })
                          }
                        }}
                        title="完成复核并继续"
                        type="button"
                      >
                        <ShieldAlert size={17} />
                      </button>
                    ) : null}
                    {!isTerminal(task.status) ? (
                      <button
                        className="icon-button danger-icon"
                        disabled={busyTask === task.id}
                        onClick={() => setTerminate(task)}
                        title="终止"
                        type="button"
                      >
                        <Square size={15} />
                      </button>
                    ) : null}
                    {isTerminal(task.status) ? (
                      <button
                        className="icon-button danger-icon"
                        disabled={busyTask === task.id}
                        onClick={() => setDeleting(task)}
                        title="删除任务记录"
                        type="button"
                      >
                        <Trash2 size={16} />
                      </button>
                    ) : null}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <TaskCreateDialog
        notify={notify}
        onClose={() => setCreateOpen(false)}
        onCreated={async (task) => {
          setCreateOpen(false)
          await onChanged(task)
        }}
        open={createOpen}
      />
      <TaskPresetFromTaskDialog
        notify={notify}
        onClose={() => setPresetSourceTask(null)}
        task={presetSourceTask}
      />
      <ConfirmDialog
        danger
        detail={terminate ? `终止“${terminate.name}”。已提交的阶段结果会保留。` : ''}
        confirmLabel="终止任务"
        onCancel={() => setTerminate(null)}
        onConfirm={() => {
          if (!terminate) return
          const current = terminate
          setTerminate(null)
          void control(current, 'terminate', { force: false, reason: 'WebUI request' })
        }}
        open={terminate !== null}
        title="确认终止"
      />
      <ConfirmDialog
        busy={deleting !== null && busyTask === deleting.id}
        danger
        detail={deleting
          ? `删除“${deleting.name}”的任务记录及项目内缓存。不会删除源目录或输出目录；此操作无法撤销。`
          : ''}
        confirmLabel="删除任务"
        onCancel={() => {
          if (busyTask === deleting?.id) return
          setDeleting(null)
        }}
        onConfirm={() => {
          if (deleting) void remove(deleting)
        }}
        open={deleting !== null}
        title="确认删除任务"
      />
    </div>
  )
}

function TaskCreateDialog({
  open,
  onClose,
  onCreated,
  notify,
}: {
  open: boolean
  onClose: () => void
  onCreated: (task: Task) => Promise<void>
  notify: Notice
}) {
  const [name, setName] = useState('')
  const [sourceRoot, setSourceRoot] = useState('')
  const [outputRoot, setOutputRoot] = useState('')
  const [manifests, setManifests] = useState<ComponentManifest[]>([])
  const [components, setComponents] = useState<Record<string, ComponentConfigValue>>({})
  const [builtinProfiles, setBuiltinProfiles] = useState<BuiltinProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<BuiltinProfile['id'] | ''>('')
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [catalogRetry, setCatalogRetry] = useState(0)
  const [picker, setPicker] = useState<'source' | 'output' | null>(null)
  const [busy, setBusy] = useState(false)
  const [runtimeDevice, setRuntimeDevice] = useState<'auto' | 'cpu' | 'cuda'>('auto')
  const [runtimePrecision, setRuntimePrecision] = useState<'float32' | 'float16' | 'bfloat16'>('float32')
  const [tuningBusy, setTuningBusy] = useState(false)
  const [tuningSummary, setTuningSummary] = useState<string | null>(null)
  const [presets, setPresets] = useState<TaskPreset[]>([])
  const [selectedPresetId, setSelectedPresetId] = useState('')
  const [presetLoading, setPresetLoading] = useState(false)
  const [presetError, setPresetError] = useState<string | null>(null)
  const [presetRetry, setPresetRetry] = useState(0)
  const [presetBusy, setPresetBusy] = useState(false)
  const [presetEditor, setPresetEditor] = useState<'new' | 'update' | null>(null)
  const [presetName, setPresetName] = useState('')
  const [deletingPreset, setDeletingPreset] = useState<TaskPreset | null>(null)

  const selectedBuiltinProfile = useMemo(
    () => builtinProfiles.find((profile) => profile.id === selectedProfileId),
    [builtinProfiles, selectedProfileId],
  )
  const hasValidProfileSelection = selectedBuiltinProfile !== undefined

  useEffect(() => {
    if (!open || manifests.length > 0) return
    let cancelled = false
    setCatalogLoading(true)
    setCatalogError(null)
    Promise.all([listComponents(), listBuiltinProfiles()])
      .then(([catalog, profiles]) => {
        if (cancelled) return
        const builtinProfiles = profiles.items.filter((profile) => isDatasetProfile(profile.id))
        setManifests(catalog.items)
        setComponents(buildDefaultComponentConfig(catalog.items))
        setBuiltinProfiles(builtinProfiles)
        setSelectedProfileId((current) => (
          builtinProfiles.some((profile) => profile.id === current) ? current : ''
        ))
      })
      .catch((reason) => {
        if (cancelled) return
        setCatalogError(reason instanceof Error ? reason.message : '无法读取组件清单')
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [catalogRetry, manifests.length, open])

  useEffect(() => {
    if (!open || builtinProfiles.length === 0) return
    let cancelled = false
    setPresetLoading(true)
    setPresetError(null)
    listTaskPresets()
      .then((result) => {
        if (cancelled) return
        const profilePresets = result.items.filter((item) => (
          item.profile !== null && builtinProfiles.some((profile) => profile.id === item.profile)
        ))
        setPresets(profilePresets)
        setSelectedPresetId((current) =>
          current && profilePresets.some((item) => item.id === current) ? current : '',
        )
      })
      .catch((reason) => {
        if (cancelled) return
        setPresetError(reason instanceof Error ? reason.message : '无法读取任务预设')
      })
      .finally(() => {
        if (!cancelled) setPresetLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [builtinProfiles, open, presetRetry])

  const scoringRuntimeMismatch = useMemo(
    () => hasScoringRuntimeMismatch(manifests, components),
    [components, manifests],
  )
  const styleWeightsError = useMemo(
    () => validateStyleArtistWeights(components),
    [components],
  )
  const exportMode = (components['export.dataset']?.config.mode ?? 'copy')
  const rewriteOutputRequired = exportMode === 'rewrite'

  const valid = useMemo(
    () =>
      name.trim() &&
      sourceRoot.trim() &&
      (!rewriteOutputRequired || outputRoot.trim()) &&
      manifests.length > 0 &&
      Object.keys(components).length === manifests.length &&
      hasValidProfileSelection &&
      !scoringRuntimeMismatch &&
      styleWeightsError === null,
    [
      components,
      manifests.length,
      name,
      outputRoot,
      rewriteOutputRequired,
      hasValidProfileSelection,
      scoringRuntimeMismatch,
      styleWeightsError,
      sourceRoot,
    ],
  )

  const applyScoringRuntime = () => {
    setComponents((previous) => mergeRuntimeUpdates(
      previous,
      scoringRuntimeUpdates(manifests, previous, runtimeDevice, runtimePrecision),
    ))
    setTuningSummary(null)
  }

  const applyHardwareRecommendation = async () => {
    setTuningBusy(true)
    try {
      const recommendation = await getRuntimeTuningRecommendation()
      setRuntimeDevice(recommendation.device)
      setRuntimePrecision(recommendation.precision)
      setComponents((previous) => mergeRuntimeUpdates(previous, recommendation.updates))
      setTuningSummary(formatHardwareSummary(recommendation))
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法读取硬件推荐', 'error')
    } finally {
      setTuningBusy(false)
    }
  }

  const browse = async (target: 'source' | 'output') => {
    if (picker !== null || busy || presetBusy) return
    setPicker(target)
    const initialPath = target === 'source' ? sourceRoot : outputRoot
    try {
      const selection = await selectDirectory(target, initialPath)
      if (!selection.cancelled && selection.path) {
        if (target === 'source') setSourceRoot(selection.path)
        else setOutputRoot(selection.path)
      }
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法打开 Windows 文件夹选择窗口', 'error')
    } finally {
      setPicker(null)
    }
  }

  const applyBuiltinProfile = (profile: BuiltinProfile) => {
    const next = mergeBuiltinProfileComponents(
      components,
      profile,
      selectedProfileId !== '',
    )
    setComponents(next)
    setSelectedProfileId(profile.id)
    const runtime = runtimeSettingsForComponents(manifests, next)
    setRuntimeDevice(runtime.device)
    setRuntimePrecision(runtime.precision)
    setTuningSummary(null)
  }

  const applyPreset = () => {
    if (manifests.length === 0 || presetBusy) return
    const selected = presets.find((item) => item.id === selectedPresetId)
    if (!selected) return
    const profileId = selected.profile ?? ''
    if (!profileId) return
    const next = cloneComponents(selected.components)
    setComponents(next)
    setSelectedProfileId(profileId)
    const runtime = runtimeSettingsForComponents(manifests, next)
    setRuntimeDevice(runtime.device)
    setRuntimePrecision(runtime.precision)
    setTuningSummary(null)
    notify(`已应用预设“${selected.name}”`)
  }

  const beginPresetSave = (mode: 'new' | 'update') => {
    const selected = presets.find((item) => item.id === selectedPresetId)
    if (mode === 'update' && !selected) return
    setPresetName(mode === 'update' && selected ? selected.name : '')
    setPresetEditor(mode)
  }

  const savePreset = async () => {
    const cleaned = presetName.trim()
    if (!cleaned || !presetEditor || presetBusy || !hasValidProfileSelection) return
    const selected = presets.find((item) => item.id === selectedPresetId)
    if (presetEditor === 'update' && !selected) return
    setPresetBusy(true)
    try {
      let saved: TaskPreset
      if (presetEditor === 'new') {
        saved = await createTaskPreset(cleaned, components, selectedBuiltinProfile!.id)
      } else {
        if (!selected) return
        saved = await updateTaskPreset(selected.id, cleaned, components, selected.row_version, selectedBuiltinProfile!.id)
      }
      if (saved.profile === null || !builtinProfiles.some((profile) => profile.id === saved.profile)) {
        notify('任务预设必须包含内置数据集配置', 'error')
        return
      }
      setPresets((current) => {
        const withoutSaved = current.filter((item) => item.id !== saved.id)
        return [...withoutSaved, saved].sort((left, right) => left.name.localeCompare(right.name))
      })
      setSelectedPresetId(saved.id)
      setComponents(cloneComponents(saved.components))
      setPresetEditor(null)
      setPresetName('')
      notify(presetEditor === 'new' ? '任务预设已保存' : '任务预设已覆盖')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '保存任务预设失败', 'error')
      if (presetEditor === 'update') setPresetRetry((value) => value + 1)
    } finally {
      setPresetBusy(false)
    }
  }

  const deletePreset = async () => {
    if (!deletingPreset || presetBusy) return
    setPresetBusy(true)
    try {
      await deleteTaskPreset(deletingPreset.id, deletingPreset.row_version)
      setPresets((current) => current.filter((item) => item.id !== deletingPreset.id))
      setSelectedPresetId((current) => current === deletingPreset.id ? '' : current)
      setDeletingPreset(null)
      notify('任务预设已删除')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '删除任务预设失败', 'error')
      setPresetRetry((value) => value + 1)
    } finally {
      setPresetBusy(false)
    }
  }

  const create = async () => {
    if (!valid || !selectedBuiltinProfile) return
    setBusy(true)
    try {
      const task = await createTask({
        name: name.trim(),
        source_root: sourceRoot.trim(),
        output_root: outputRoot.trim() || undefined,
        profile: selectedBuiltinProfile.id,
        components: profileTaskSubmissionComponents(
          components,
          selectedBuiltinProfile.id,
        ),
      })
      await onCreated(task)
      notify('任务已创建')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '创建任务失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const selectedPreset = presets.find((item) => item.id === selectedPresetId)

  return (
    <>
      <Modal
        open={open && deletingPreset === null}
        title="新建训练集处理任务"
        onClose={busy || picker !== null || presetBusy ? () => undefined : onClose}
        width="large"
      >
        <section className="preset-toolbar" aria-busy={presetLoading || presetBusy}>
          <div className="field preset-select-field">
            <span className="field-label"><label htmlFor="task-preset">任务预设</label><FieldHelp label="任务预设" text={taskConfigHelp('task_preset')} /></span>
            <select
              disabled={presetLoading || presetBusy}
              id="task-preset"
              onChange={(event) => setSelectedPresetId(event.target.value)}
              value={selectedPresetId}
            >
              <option value="">选择预设</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {presetLabel(preset)}
                </option>
              ))}
            </select>
          </div>
          <div className="preset-actions">
            <button
              className="button secondary"
              disabled={presetLoading || presetBusy || manifests.length === 0 || !selectedPreset}
              onClick={applyPreset}
              title="应用所选预设"
              type="button"
            >
              <Check size={15} />
              应用
            </button>
            <button
              className="button secondary"
              disabled={presetLoading || presetBusy || manifests.length === 0 || !hasValidProfileSelection}
              onClick={() => beginPresetSave('new')}
              title="将当前配置另存为新预设"
              type="button"
            >
              <BookmarkPlus size={15} />
              另存为
            </button>
            <button
              className="icon-button"
              disabled={!selectedPreset || presetBusy || manifests.length === 0}
              onClick={() => beginPresetSave('update')}
              title="覆盖并改名所选预设"
              type="button"
            >
              <Save size={17} />
            </button>
            <button
              className="icon-button danger-icon"
              disabled={!selectedPreset || presetBusy}
              onClick={() => setDeletingPreset(selectedPreset ?? null)}
              title="删除所选预设"
              type="button"
            >
              <Trash2 size={17} />
            </button>
            {presetLoading ? <LoaderCircle className="spin" size={17} /> : null}
          </div>
          {presetError ? (
            <div className="preset-error">
              <ErrorBlock message={presetError} />
              <button
                className="button secondary"
                disabled={presetLoading}
                onClick={() => setPresetRetry((value) => value + 1)}
                type="button"
              >
                重试
              </button>
            </div>
          ) : null}
        </section>
        <section className="preset-toolbar">
          <div className="field preset-select-field">
            <span className="field-label"><label htmlFor="dataset-profile">数据集配置</label><FieldHelp label="数据集配置" text={taskConfigHelp('dataset_profile')} /></span>
            <select
              disabled={catalogLoading || presetBusy || builtinProfiles.length === 0}
              id="dataset-profile"
              onChange={(event) => {
                const profile = builtinProfiles.find((item) => item.id === event.target.value)
                if (profile) applyBuiltinProfile(profile)
              }}
              value={selectedProfileId}
            >
              <option disabled value="">选择配置</option>
              {builtinProfiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profileDisplayName(profile.id)}
                </option>
              ))}
            </select>
          </div>
        </section>
        {selectedBuiltinProfile ? (
          <ProfileTaskSettings
            components={components}
            disabled={busy || catalogLoading || presetBusy}
            onChange={setComponents}
            profile={selectedBuiltinProfile}
          />
        ) : null}
        {presetEditor ? (
          <form
            className="preset-editor"
            onSubmit={(event) => {
              event.preventDefault()
              void savePreset()
            }}
          >
            <div className="field">
              <span className="field-label"><label htmlFor="task-preset-name">{presetEditor === 'new' ? '新预设名称' : '预设名称'}</label><FieldHelp label={presetEditor === 'new' ? '新预设名称' : '预设名称'} text={taskConfigHelp('preset_name')} /></span>
              <input
                autoFocus
                id="task-preset-name"
                maxLength={200}
                onChange={(event) => setPresetName(event.target.value)}
                value={presetName}
              />
            </div>
            <div className="preset-editor-actions">
              <button
                className="button secondary"
                disabled={presetBusy}
                onClick={() => setPresetEditor(null)}
                type="button"
              >
                取消
              </button>
              <button className="button primary" disabled={presetBusy || !presetName.trim()} type="submit">
                {presetBusy ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
                保存
              </button>
            </div>
          </form>
        ) : null}
        <div className="form-grid">
          <div className="field span-2">
            <span className="field-label"><label htmlFor="task-name">任务名称</label><FieldHelp label="任务名称" text={taskConfigHelp('task_name')} /></span>
            <input id="task-name" onChange={(event) => setName(event.target.value)} value={name} />
          </div>
          <PathField
            busy={picker === 'source'}
            disabled={busy || picker !== null || presetBusy}
            help={taskConfigHelp('source_root')}
            label="源数据目录"
            onBrowse={() => void browse('source')}
            onChange={setSourceRoot}
            value={sourceRoot}
          />
          <PathField
            busy={picker === 'output'}
            disabled={busy || picker !== null || presetBusy}
            help={taskConfigHelp('output_root')}
            label={rewriteOutputRequired ? '复写输出目录' : '任务级输出目录（copy 可留空）'}
            onBrowse={() => void browse('output')}
            onChange={setOutputRoot}
            value={outputRoot}
          />
        </div>

        <section className="form-section component-form-section">
          <h3>处理组件</h3>
          {catalogLoading ? <LoadingBlock label="正在读取组件清单" /> : null}
          {catalogError ? (
            <div className="catalog-error">
              <ErrorBlock message={catalogError} />
              <button className="button secondary" onClick={() => setCatalogRetry((value) => value + 1)} type="button">
                重试
              </button>
            </div>
          ) : null}
          {manifests.length > 0 ? (
            <>
              <div className="runtime-tuning-controls">
                <div className="field compact">
                  <span className="field-label"><label htmlFor="runtime-device">统一推理设备</label><FieldHelp label="统一推理设备" text={taskConfigHelp('runtime_device')} /></span>
                  <select
                    id="runtime-device"
                    onChange={(event) => setRuntimeDevice(event.target.value as 'auto' | 'cpu' | 'cuda')}
                    value={runtimeDevice}
                  >
                    <option value="auto">自动</option>
                    <option value="cuda">CUDA</option>
                    <option value="cpu">CPU</option>
                  </select>
                </div>
                <div className="field compact">
                  <span className="field-label"><label htmlFor="runtime-precision">统一推理精度</label><FieldHelp label="统一推理精度" text={taskConfigHelp('runtime_precision')} /></span>
                  <select
                    id="runtime-precision"
                    onChange={(event) => setRuntimePrecision(event.target.value as 'float32' | 'float16' | 'bfloat16')}
                    value={runtimePrecision}
                  >
                    <option value="float32">float32</option>
                    <option value="float16">float16</option>
                    <option value="bfloat16">bfloat16</option>
                  </select>
                </div>
                <div className="runtime-tuning-actions">
                  <button className="button secondary" onClick={applyScoringRuntime} type="button">
                    <SlidersHorizontal size={15} />
                    同步评分组件
                  </button>
                  <button
                    className="button secondary"
                    disabled={tuningBusy}
                    onClick={() => void applyHardwareRecommendation()}
                    type="button"
                  >
                    <Sparkles size={15} />
                    {tuningBusy ? '正在推荐' : '按当前硬件推荐'}
                  </button>
                </div>
                {tuningSummary ? <span className="runtime-tuning-summary">{tuningSummary}</span> : null}
              </div>
              {scoringRuntimeMismatch ? (
                <ErrorBlock message="评分组件的推理设备或精度不一致，请使用统一设置同步后再创建任务。" />
              ) : null}
              {styleWeightsError ? <ErrorBlock message={styleWeightsError} /> : null}
              <ComponentConfigEditor
                hiddenConfigFields={TASK_CREATION_HIDDEN_CONFIG_FIELDS}
                manifests={manifests}
                onChange={setComponents}
                profileOwnedComponentIds={
                  selectedBuiltinProfile?.profile_owned_component_ids ?? []
                }
                value={components}
              />
            </>
          ) : null}
        </section>

        <div className="modal-actions sticky-actions">
          <button
            className="button secondary"
            disabled={busy || picker !== null || presetBusy}
            onClick={onClose}
            type="button"
          >
            取消
          </button>
          <button
            className="button primary"
            disabled={!valid || busy || picker !== null || presetBusy}
            onClick={() => void create()}
            type="button"
          >
            <Plus size={16} />
            创建任务
          </button>
        </div>
      </Modal>
      <ConfirmDialog
        busy={presetBusy}
        danger
        detail={deletingPreset ? `删除预设“${deletingPreset.name}”。不会影响已有任务。` : ''}
        confirmLabel="删除预设"
        onCancel={() => {
          if (!presetBusy) setDeletingPreset(null)
        }}
        onConfirm={() => void deletePreset()}
        open={deletingPreset !== null}
        title="确认删除预设"
      />
    </>
  )
}

function ProfileTaskSettings({
  components,
  disabled,
  onChange,
  profile,
}: {
  components: Record<string, ComponentConfigValue>
  disabled: boolean
  onChange: (components: Record<string, ComponentConfigValue>) => void
  profile: BuiltinProfile
}) {
  const datasetExport = components['export.dataset']
  const hasAestheticBins = profile.scope_mode === 'global' && datasetExport !== undefined
  const aestheticBinsEnabled = datasetExport?.config.aesthetic_bins === 'score_x2_floor'

  const setAestheticBins = (enabled: boolean) => {
    if (!datasetExport) return
    onChange({
      ...components,
      'export.dataset': {
        ...datasetExport,
        config: {
          ...datasetExport.config,
          aesthetic_bins: enabled ? 'score_x2_floor' : 'disabled',
        },
      },
    })
  }

  return (
    <section className="profile-task-settings" aria-label="内置配置工作区">
      <div>
        <strong>全量工作区</strong>
        <span>{profileDisplayName(profile.id)}</span>
      </div>
      {hasAestheticBins ? (
        <div className="profile-aesthetic-option">
          <label className="profile-aesthetic-toggle">
            <input
              aria-label="按美学评分分档"
              checked={aestheticBinsEnabled}
              disabled={disabled}
              onChange={(event) => setAestheticBins(event.target.checked)}
              type="checkbox"
            />
            <span>按美学评分分档</span>
          </label>
          <FieldHelp label="按美学评分分档" text={taskConfigHelp('aesthetic_bins')} />
        </div>
      ) : null}
    </section>
  )
}

function mergeRuntimeUpdates(
  components: Record<string, ComponentConfigValue>,
  updates: RuntimeTuningRecommendation['updates'],
): Record<string, ComponentConfigValue> {
  return Object.fromEntries(Object.entries(components).map(([componentId, value]) => [
    componentId,
    updates[componentId]
      ? { ...value, config: { ...value.config, ...updates[componentId] } }
      : value,
  ]))
}

function scoringRuntimeUpdates(
  manifests: ComponentManifest[],
  components: Record<string, ComponentConfigValue>,
  device: 'auto' | 'cpu' | 'cuda',
  precision: 'float32' | 'float16' | 'bfloat16',
): RuntimeTuningRecommendation['updates'] {
  return Object.fromEntries(manifests
    .filter((manifest) => manifest.ui_group === 'screening' && hasRuntimeFields(components[manifest.id]))
    .map((manifest) => [manifest.id, { device, precision }]))
}

function hasScoringRuntimeMismatch(
  manifests: ComponentManifest[],
  components: Record<string, ComponentConfigValue>,
) {
  const runtimeComponents = manifests.filter(
    (manifest) => manifest.ui_group === 'screening' && hasRuntimeFields(components[manifest.id]),
  )
  const enabledScoring = runtimeComponents.filter(
    (manifest) => manifest.activation !== 'auto' && components[manifest.id]?.enabled,
  )
  if (enabledScoring.length === 0) return false
  const runtimeValues = runtimeComponents
    .filter((manifest) => manifest.activation !== 'auto' || enabledScoring.length > 0)
    .map((manifest) => components[manifest.id]?.config)
    .filter((config): config is ComponentConfigValue['config'] => config !== undefined)
    .map((config) => `${config.device ?? 'auto'}:${config.precision ?? 'float32'}`)
  return new Set(runtimeValues).size > 1
}

function hasRuntimeFields(value: ComponentConfigValue | undefined) {
  return value !== undefined && 'device' in value.config && 'precision' in value.config
}

function cloneComponents(
  components: Record<string, ComponentConfigValue>,
): Record<string, ComponentConfigValue> {
  return JSON.parse(JSON.stringify(components)) as Record<string, ComponentConfigValue>
}

function presetLabel(preset: TaskPreset) {
  return `自定义：${preset.name}`
}

function runtimeSettingsForComponents(
  manifests: ComponentManifest[],
  components: Record<string, ComponentConfigValue>,
): {
  device: 'auto' | 'cpu' | 'cuda'
  precision: 'float32' | 'float16' | 'bfloat16'
} {
  const active = manifests
    .filter((manifest) => manifest.ui_group === 'screening' && hasRuntimeFields(components[manifest.id]))
    .filter((manifest) => manifest.activation === 'auto' || components[manifest.id]?.enabled)
    .map((manifest) => components[manifest.id]?.config)
    .find((config): config is ComponentConfigValue['config'] => {
      const device = config?.device
      const precision = config?.precision
      return (
        device === 'auto' || device === 'cpu' || device === 'cuda'
      ) && (
        precision === 'float32' || precision === 'float16' || precision === 'bfloat16'
      )
    })
  return {
    device: active?.device as 'auto' | 'cpu' | 'cuda' ?? 'auto',
    precision: active?.precision as 'float32' | 'float16' | 'bfloat16' ?? 'float32',
  }
}

function formatHardwareSummary(recommendation: RuntimeTuningRecommendation) {
  const vram = formatBytes(recommendation.hardware.free_vram_bytes)
  const memory = formatBytes(recommendation.hardware.available_memory_bytes)
  const processor = recommendation.hardware.cuda_available ? 'CUDA' : 'CPU'
  return `${processor} | 空闲显存 ${vram} | 可用内存 ${memory}`
}

function formatBytes(value: number | null) {
  if (value === null) return '未提供'
  return `${(value / 1024 ** 3).toFixed(1)} GiB`
}

function TaskPresetFromTaskDialog({
  task,
  onClose,
  notify,
}: {
  task: Task | null
  onClose: () => void
  notify: Notice
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setName(task ? `${task.name} 预设` : '')
    setError(null)
  }, [task])

  const save = async () => {
    if (!task || !name.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await createTaskPresetFromTask(task.id, name.trim())
      notify('任务预设已保存')
      onClose()
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : '保存任务预设失败'
      setError(detail)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={task !== null}
      title="保存为任务预设"
      onClose={busy ? () => undefined : onClose}
      width="small"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <label className="field">
          <span>预设名称</span>
          <input
            autoFocus
            maxLength={200}
            onChange={(event) => setName(event.target.value)}
            value={name}
          />
        </label>
        {error ? <ErrorBlock message={error} /> : null}
        <div className="modal-actions">
          <button className="button secondary" disabled={busy} onClick={onClose} type="button">
            取消
          </button>
          <button className="button primary" disabled={busy || !name.trim()} type="submit">
            {busy ? <LoaderCircle className="spin" size={16} /> : <BookmarkPlus size={16} />}
            保存
          </button>
        </div>
      </form>
    </Modal>
  )
}

function PathField({
  label,
  help,
  value,
  onChange,
  onBrowse,
  busy = false,
  disabled = false,
}: {
  label: string
  help: string
  value: string
  onChange: (value: string) => void
  onBrowse: () => void
  busy?: boolean
  disabled?: boolean
}) {
  const inputId = useId()
  return (
    <div className="field span-2">
      <span className="field-label"><label htmlFor={inputId}>{label}</label><FieldHelp label={label} text={help} /></span>
      <span className="input-with-button">
        <input
          disabled={disabled}
          id={inputId}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        />
        <button
          aria-label={`选择${label}`}
          aria-busy={busy}
          className="icon-button path-picker-button"
          disabled={disabled}
          onClick={onBrowse}
          title={`用 Windows 窗口选择${label}`}
          type="button"
        >
          {busy ? <LoaderCircle className="spin" size={17} /> : <FolderOpen size={17} />}
        </button>
      </span>
    </div>
  )
}

function canPause(status: string) {
  return [
    'queued',
    'scanning',
    'cpu_metrics',
    'model_scoring',
    'style_analysis',
    'semantic_clustering',
    'exporting',
  ].includes(status)
}

function isTerminal(status: string) {
  return ['completed', 'terminated', 'failed'].includes(status)
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}
