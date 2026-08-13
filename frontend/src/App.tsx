import {
  ClipboardCheck,
  Copy,
  Database,
  FolderOutput,
  FolderSearch,
  Gauge,
  Palette,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useAppBootstrap } from './hooks/useAppBootstrap'
import { useSelectedTaskData } from './hooks/useSelectedTaskData'
import { useTaskEventRefresh } from './hooks/useTaskEventRefresh'
import { isBuiltinProfileTask } from './profileWorkspace'
import {
  createReviewGatePrompt,
  type ReviewGatePrompt,
} from './reviewGatePrompt'
import { playTaskCompletionSound, unlockNotificationSound } from './notificationSound'
import {
  createCompletionPrompt,
  isCompletionTransition,
  type CompletionPrompt,
} from './taskStatusPrompt'
import type { PageId, Task } from './types'
import { LoadingBlock, Modal } from './ui'

const SystemPage = lazy(async () => ({
  default: (await import('./pages/SystemPage')).SystemPage,
}))
const TasksPage = lazy(async () => ({
  default: (await import('./pages/TasksPage')).TasksPage,
}))
const ProgressPage = lazy(async () => ({
  default: (await import('./pages/ProgressPage')).ProgressPage,
}))
const RisksPage = lazy(async () => ({
  default: (await import('./pages/RisksPage')).RisksPage,
}))
const StylePage = lazy(async () => ({
  default: (await import('./pages/StylePage')).StylePage,
}))
const DuplicatesPage = lazy(async () => ({
  default: (await import('./pages/DuplicatesPage')).DuplicatesPage,
}))
const AestheticsPage = lazy(async () => ({
  default: (await import('./pages/AestheticsPage')).AestheticsPage,
}))
const ExportsPage = lazy(async () => ({
  default: (await import('./pages/ExportsPage')).ExportsPage,
}))
const ModelsPage = lazy(async () => ({
  default: (await import('./pages/ModelsPage')).ModelsPage,
}))

type PageDefinition = {
  id: PageId
  label: string
  title: string
  subtitle: string
  icon: typeof Gauge
}

const primaryPages: PageDefinition[] = [
  { id: 'tasks', label: '任务', title: '任务', subtitle: '数据路径与处理配置', icon: FolderSearch },
  { id: 'progress', label: '进度', title: '进度', subtitle: '阶段、控制与事件', icon: ClipboardCheck },
]

const auditPages: PageDefinition[] = [
  { id: 'risks', label: '风险', title: '风险证据', subtitle: '检测证据与筛选影响', icon: ShieldAlert },
  { id: 'style', label: '画风', title: '画风审计', subtitle: '按子文件夹复核画风离群候选', icon: Palette },
  { id: 'duplicates', label: '重复', title: '重复审计', subtitle: '按组复核', icon: Copy },
  { id: 'aesthetics', label: '美学', title: '美学审计', subtitle: '按子文件夹复核美学候选', icon: Sparkles },
]

const exportPage: PageDefinition = {
  id: 'exports', label: '导出', title: '导出', subtitle: '单数据集 copy 导出与输出状态', icon: FolderOutput,
}

const utilityPages: PageDefinition[] = [
  { id: 'models', label: '模型', title: '模型', subtitle: '下载、校验与本地替换', icon: Database },
  { id: 'system', label: '系统', title: '系统状态', subtitle: '本地运行时与 Worker', icon: Gauge },
]

const pages = [...primaryPages, ...auditPages, exportPage, ...utilityPages]
const auditPageIds = new Set<PageId>(auditPages.map((item) => item.id))
const legacyPageAliases: Record<string, PageId> = {
  guide: 'tasks',
  reviews: 'risks',
  clusters: 'duplicates',
}

const taskSelectionStorageKey = 'dataset-audit-selected-task-v2'

function initialPage(): PageId {
  const requested = window.location.hash.slice(1)
  const page = legacyPageAliases[requested]
    ?? (pages.some((item) => item.id === requested) ? requested as PageId : 'tasks')
  if (requested !== page) window.history.replaceState(null, '', `#${page}`)
  return page
}

export default function App() {
  const [page, setPage] = useState<PageId>(initialPage)
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(
    () => window.localStorage.getItem(taskSelectionStorageKey),
  )
  const [selectedFolder, setSelectedFolder] = useState('')
  const [notice, setNotice] = useState<{ message: string; tone: 'success' | 'error' } | null>(null)
  const [statusPrompt, setStatusPrompt] = useState<ReviewGatePrompt | CompletionPrompt | null>(null)
  const seenStatusPrompts = useRef(new Set<string>())
  const observedTaskStatuses = useRef(new Map<string, string>())
  const notify = useCallback((message: string, tone: 'success' | 'error' = 'success') => {
    setNotice({ message, tone })
  }, [])

  const reconcileSelectedTask = useCallback((nextTasks: Task[]) => {
    setSelectedTaskId((current) => (
      current && nextTasks.some((task) => task.id === current) ? current : null
    ))
  }, [])

  const clearSelectedTask = useCallback((taskId: string) => {
    setSelectedTaskId((current) => current === taskId ? null : current)
  }, [])

  const {
    components,
    health,
    healthError,
    healthLoading,
    reloadComponents,
    reloadHealth,
    reloadTasks,
    taskListReady,
    tasks,
    upsertTask,
  } = useAppBootstrap({
    notify,
    reconcileSelectedTask,
  })

  const {
    componentRuns,
    events,
    folders,
    loadTaskData,
    overview,
  } = useSelectedTaskData({
    clearSelectedTask,
    notify,
    selectedTaskId,
    upsertTask,
  })

  const { sseConnected } = useTaskEventRefresh({
    enabled: taskListReady,
    loadTaskData,
    taskId: selectedTaskId,
  })

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  )

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 4200)
    return () => window.clearTimeout(timer)
  }, [notice])

  useEffect(() => {
    if (!selectedTask) {
      setStatusPrompt(null)
      return
    }
    const previousStatus = observedTaskStatuses.current.get(selectedTask.id)
    observedTaskStatuses.current.set(selectedTask.id, selectedTask.status)

    const reviewPrompt = createReviewGatePrompt(selectedTask)
    if (reviewPrompt) {
      if (auditPageIds.has(page)) {
        setStatusPrompt(null)
        return
      }
      if (seenStatusPrompts.current.has(reviewPrompt.key)) {
        setStatusPrompt((current) =>
          current && current.taskId === reviewPrompt.taskId && current.key === reviewPrompt.key
            ? current
            : null,
        )
        return
      }
      seenStatusPrompts.current.add(reviewPrompt.key)
      setStatusPrompt(reviewPrompt)
      return
    }

    const completionPrompt = createCompletionPrompt(selectedTask)
    const enteredCompleted = isCompletionTransition(selectedTask, previousStatus)
    if (completionPrompt !== null && enteredCompleted) {
      if (seenStatusPrompts.current.has(completionPrompt.key)) {
        setStatusPrompt((current) =>
          current && current.taskId === completionPrompt.taskId && current.key === completionPrompt.key
            ? current
            : null,
        )
        return
      }
      seenStatusPrompts.current.add(completionPrompt.key)
      setStatusPrompt(completionPrompt)
      playTaskCompletionSound()
      return
    }

    if (completionPrompt !== null) {
      setStatusPrompt((current) =>
        current && current.taskId === completionPrompt.taskId && current.key === completionPrompt.key
          ? current
          : null,
      )
      return
    }

    setStatusPrompt(null)
  }, [page, selectedTask])

  useEffect(() => {
    const unlock = () => unlockNotificationSound()
    window.addEventListener('pointerdown', unlock)
    window.addEventListener('keydown', unlock)
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [])

  const refreshAll = useCallback(async () => {
    await Promise.all([
      reloadHealth(),
      reloadTasks(),
      reloadComponents(),
      selectedTaskId ? loadTaskData(selectedTaskId) : Promise.resolve(),
    ])
  }, [loadTaskData, reloadComponents, reloadHealth, reloadTasks, selectedTaskId])

  useEffect(() => {
    if (!taskListReady) return
    setSelectedFolder('')
    if (!selectedTaskId) {
      window.localStorage.removeItem(taskSelectionStorageKey)
      return
    }
    window.localStorage.setItem(taskSelectionStorageKey, selectedTaskId)
  }, [selectedTaskId, taskListReady])

  useEffect(() => {
    const handler = () => setPage(initialPage())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  const navigate = (next: PageId) => {
    setPage(next)
    window.history.replaceState(null, '', `#${next}`)
  }
  const openStatusPromptTarget = () => {
    if (!statusPrompt) return
    const target = statusPrompt.targetPage
    setStatusPrompt(null)
    navigate(target)
  }
  const active = pages.find((item) => item.id === page) ?? primaryPages[0]
  const onTaskChanged = async (updated?: Task) => {
    if (updated && !isBuiltinProfileTask(updated)) {
      await reloadTasks()
      return
    }
    if (updated) {
      upsertTask(updated, { insertIfMissing: true })
      setSelectedTaskId(updated.id)
    }
    await reloadTasks()
    const taskId = updated?.id ?? selectedTaskId
    if (taskId) await loadTaskData(taskId)
  }
  const onTaskDeleted = async (taskId: string) => {
    if (selectedTaskId === taskId) {
      clearSelectedTask(taskId)
      setSelectedFolder('')
    }
    await reloadTasks()
  }
  const onAuditChanged = async () => {
    if (selectedTaskId) await loadTaskData(selectedTaskId)
  }
  const renderNavItem = (item: PageDefinition, nested = false) => {
    const Icon = item.icon
    return (
      <button
        aria-current={page === item.id ? 'page' : undefined}
        className={`${page === item.id ? 'nav-item active' : 'nav-item'}${nested ? ' nav-subitem' : ''}`}
        key={item.id}
        onClick={() => navigate(item.id)}
        title={item.label}
        type="button"
      >
        <Icon size={18} strokeWidth={1.8} />
        <span>{item.label}</span>
      </button>
    )
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand-mark" onClick={() => navigate('tasks')} title="Dataset Audit Studio" type="button">
          <ShieldCheck size={22} strokeWidth={1.8} />
          <span>Dataset Audit Studio</span>
        </button>
        <nav aria-label="主导航">
          {primaryPages.map((item) => renderNavItem(item))}
          <div aria-label="审计" className="nav-section" role="group">
            <span className="nav-section-label">审计</span>
            {auditPages.map((item) => renderNavItem(item, true))}
          </div>
          {renderNavItem(exportPage)}
          <div aria-hidden="true" className="nav-divider" />
          {utilityPages.map((item) => renderNavItem(item))}
        </nav>
        <div className="sidebar-status">
          <i className={health?.worker.running ? 'online' : ''} />
          <span>{health?.worker.running ? 'Worker 在线' : 'Worker 停止'}</span>
        </div>
        <div className="version-label">v{health?.app_version ?? '0.1.0'}</div>
      </aside>

      <main>
        <header className="topbar">
          <div className="page-title">
            <span>{active.subtitle}</span>
            <h1>{active.title}</h1>
          </div>
          <div className="topbar-actions">
            {page !== 'system' ? (
              <label className="task-selector">
                <span>任务</span>
                <select
                  onChange={(event) => setSelectedTaskId(event.target.value || null)}
                  value={selectedTaskId ?? ''}
                >
                  <option value="">未选择</option>
                  {tasks.map((task) => (
                    <option key={task.id} value={task.id}>{task.name}</option>
                  ))}
                </select>
              </label>
            ) : null}
            <button
              aria-label="刷新当前页面"
              className="icon-button"
              onClick={() => void refreshAll()}
              title="刷新"
              type="button"
            >
              <RefreshCw size={18} />
            </button>
          </div>
        </header>

        <div className="content">
          <Suspense fallback={<LoadingBlock label="正在加载页面" />}>
            {page === 'system' ? (
              <SystemPage error={healthError} health={health} loading={healthLoading} />
            ) : null}
            {page === 'tasks' ? (
              <TasksPage
                notify={notify}
                onChanged={onTaskChanged}
                onDeleted={onTaskDeleted}
                onSelect={setSelectedTaskId}
                selectedTaskId={selectedTaskId}
                tasks={tasks}
              />
            ) : null}
            {page === 'progress' ? (
              <ProgressPage
                components={components}
                componentRuns={componentRuns}
                events={events}
                health={health}
                notify={notify}
                onChanged={onTaskChanged}
                overview={overview}
                sseConnected={sseConnected}
                task={selectedTask}
              />
            ) : null}
            {page === 'style' ? (
              <StylePage
                key={selectedTaskId ?? 'none'}
                folder={selectedFolder}
                folders={folders}
                notify={notify}
                onFolderChange={setSelectedFolder}
                task={selectedTask}
              />
            ) : null}
            {page === 'duplicates' ? (
              <DuplicatesPage
                key={selectedTaskId ?? 'none'}
                folder={selectedFolder}
                folders={folders}
                notify={notify}
                onFolderChange={setSelectedFolder}
                task={selectedTask}
              />
            ) : null}
            {page === 'aesthetics' ? (
              <AestheticsPage
                key={selectedTaskId ?? 'none'}
                folder={selectedFolder}
                folders={folders}
                notify={notify}
                onFolderChange={setSelectedFolder}
                task={selectedTask}
              />
            ) : null}
            {page === 'risks' ? (
              <RisksPage
                key={selectedTaskId ?? 'none'}
                folder={selectedFolder}
                folders={folders}
                notify={notify}
                onChanged={onAuditChanged}
                onFolderChange={setSelectedFolder}
                overview={overview}
                task={selectedTask}
              />
            ) : null}
            {page === 'exports' ? <ExportsPage key={selectedTaskId ?? 'none'} task={selectedTask} /> : null}
            {page === 'models' ? (
              <ModelsPage
                components={components}
                componentRuns={componentRuns}
                notify={notify}
                task={selectedTask}
              />
            ) : null}
          </Suspense>
        </div>
      </main>

      <Modal
        open={statusPrompt !== null}
        title={statusPrompt?.status === 'completed' ? '任务处理完成' : '任务等待人工复核'}
        onClose={() => setStatusPrompt(null)}
        width="small"
      >
        <p className="confirm-detail">{statusPrompt?.detail ?? ''}</p>
        <div className="modal-actions">
          <button
            className="button secondary"
            onClick={() => setStatusPrompt(null)}
            type="button"
          >
            稍后处理
          </button>
          <button className="button primary" onClick={openStatusPromptTarget} type="button">
            {statusPrompt?.actionLabel ?? '查看'}
          </button>
        </div>
      </Modal>
      {notice ? <div className={`toast ${notice.tone}`} role="status">{notice.message}</div> : null}
    </div>
  )
}
