import { useEffect, useState } from 'react'

import { createTaskRefreshPolicy } from '../taskRefreshPolicy'
import { openTaskEventStream } from '../transport/taskEvents'

type TimerId = ReturnType<typeof window.setTimeout>

type TaskEventRefreshLifecyclePorts = {
  clearTimeout: (timer: TimerId) => void
  createRefreshPolicy: typeof createTaskRefreshPolicy
  loadTaskData: (taskId: string) => Promise<number | null>
  openStream: typeof openTaskEventStream
  scheduleTimeout: (callback: () => void, delay: number) => TimerId
  setConnected: (connected: boolean) => void
}

type UseTaskEventRefreshOptions = {
  enabled: boolean
  loadTaskData: (taskId: string) => Promise<number | null>
  taskId: string | null
}

export function startTaskEventRefreshLifecycle(
  taskId: string,
  {
    clearTimeout,
    createRefreshPolicy,
    loadTaskData,
    openStream,
    scheduleTimeout,
    setConnected,
  }: TaskEventRefreshLifecyclePorts,
) {
  let cancelled = false
  let stream: ReturnType<typeof openTaskEventStream> | null = null
  let refreshTimer: TimerId | null = null
  let refreshRunning = false
  let refreshQueued = false

  const connectStream = (after: number) => {
    if (cancelled || stream !== null) return
    stream = openStream(taskId, after, {
      onError: () => {
        if (cancelled) return
        refreshPolicy.onError()
        setConnected(false)
      },
      onEvent: scheduleRefresh,
      onOpen: () => {
        if (cancelled) return
        refreshPolicy.onOpen()
        setConnected(true)
      },
    })
  }
  const refresh = async () => {
    if (refreshRunning) {
      refreshQueued = true
      return
    }
    refreshRunning = true
    do {
      refreshQueued = false
      const after = await loadTaskData(taskId)
      if (!cancelled && after !== null && stream === null) connectStream(after)
    } while (!cancelled && refreshQueued)
    refreshRunning = false
  }
  const scheduleRefresh = () => {
    if (cancelled) return
    if (refreshTimer !== null) clearTimeout(refreshTimer)
    refreshTimer = scheduleTimeout(() => {
      refreshTimer = null
      void refresh()
    }, 120)
  }
  const refreshPolicy = createRefreshPolicy({
    clearTimeout,
    scheduleRefresh,
    scheduleTimeout,
  })
  const start = async () => {
    const after = await loadTaskData(taskId)
    if (cancelled || after === null) return
    connectStream(after)
  }

  void start()
  refreshPolicy.start()

  return {
    dispose() {
      cancelled = true
      if (refreshTimer !== null) clearTimeout(refreshTimer)
      stream?.close()
      refreshPolicy.dispose()
      setConnected(false)
    },
  }
}

export function useTaskEventRefresh({ enabled, loadTaskData, taskId }: UseTaskEventRefreshOptions) {
  const [sseConnected, setSseConnected] = useState(false)

  useEffect(() => {
    if (!enabled || !taskId) {
      setSseConnected(false)
      return
    }
    const lifecycle = startTaskEventRefreshLifecycle(taskId, {
      clearTimeout: window.clearTimeout,
      createRefreshPolicy: createTaskRefreshPolicy,
      loadTaskData,
      openStream: openTaskEventStream,
      scheduleTimeout: window.setTimeout,
      setConnected: setSseConnected,
    })
    return () => lifecycle.dispose()
  }, [enabled, loadTaskData, taskId])

  return { sseConnected }
}
