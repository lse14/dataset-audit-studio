import { useCallback, useEffect, useState } from 'react'

import { listComponents } from '../clients/components'
import { getSystemHealth } from '../clients/system'
import { listTasks } from '../clients/tasks'
import { isBuiltinProfileTask } from '../profileWorkspace'
import type { ComponentManifest, Health, Task } from '../types'

type Notice = (message: string, tone?: 'success' | 'error') => void

type UseAppBootstrapOptions = {
  notify: Notice
  reconcileSelectedTask: (tasks: Task[]) => void
}

type TaskUpsertOptions = {
  insertIfMissing?: boolean
}

export function useAppBootstrap({ notify, reconcileSelectedTask }: UseAppBootstrapOptions) {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)
  const [tasks, setTasks] = useState<Task[]>([])
  const [taskListReady, setTaskListReady] = useState(false)
  const [components, setComponents] = useState<ComponentManifest[]>([])

  const reloadHealth = useCallback(async () => {
    setHealthLoading(true)
    setHealthError(null)
    try {
      setHealth(await getSystemHealth())
    } catch (reason) {
      setHealthError(reason instanceof Error ? reason.message : '无法连接后端')
    } finally {
      setHealthLoading(false)
    }
  }, [])

  const reloadTasks = useCallback(async () => {
    try {
      const data = await listTasks()
      const profileTasks = data.items.filter(isBuiltinProfileTask)
      setTasks(profileTasks)
      reconcileSelectedTask(profileTasks)
      setTaskListReady(true)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法读取任务', 'error')
    }
  }, [notify, reconcileSelectedTask])

  const reloadComponents = useCallback(async () => {
    try {
      const data = await listComponents()
      setComponents(data.items)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法读取组件清单', 'error')
    }
  }, [notify])

  const upsertTask = useCallback((task: Task, { insertIfMissing = false }: TaskUpsertOptions = {}) => {
    setTasks((current) => {
      const exists = current.some((item) => item.id === task.id)
      if (exists) return current.map((item) => (item.id === task.id ? task : item))
      return insertIfMissing ? [task, ...current] : current
    })
  }, [])

  useEffect(() => {
    void reloadHealth()
    void reloadTasks()
    void reloadComponents()
    const timer = window.setInterval(() => void reloadHealth(), 15000)
    return () => window.clearInterval(timer)
  }, [reloadComponents, reloadHealth, reloadTasks])

  return {
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
  }
}
