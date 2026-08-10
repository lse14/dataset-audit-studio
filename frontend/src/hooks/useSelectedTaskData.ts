import { useCallback, useEffect, useState } from 'react'

import { listComponentRuns } from '../clients/components'
import { getTask, listTaskEvents } from '../clients/tasks'
import { getTaskOverview, listTaskFolders } from '../clients/workspace'
import { isBuiltinProfileTask } from '../profileWorkspace'
import type { ComponentRun, FolderList, Task, TaskEvent, TaskOverview } from '../types'

type Notice = (message: string, tone?: 'success' | 'error') => void

type UseSelectedTaskDataOptions = {
  selectedTaskId: string | null
  notify: Notice
  upsertTask: (task: Task) => void
  clearSelectedTask: (taskId: string) => void
}

export function useSelectedTaskData({
  selectedTaskId,
  notify,
  upsertTask,
  clearSelectedTask,
}: UseSelectedTaskDataOptions) {
  const [overview, setOverview] = useState<TaskOverview | null>(null)
  const [folders, setFolders] = useState<FolderList | null>(null)
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [componentRuns, setComponentRuns] = useState<ComponentRun[]>([])
  const [taskDataTaskId, setTaskDataTaskId] = useState<string | null>(null)

  const loadTaskData = useCallback(async (taskId: string): Promise<number | null> => {
    try {
      const [eventList, task, taskOverview, runList, folderList] = await Promise.all([
        listTaskEvents(taskId),
        getTask(taskId),
        getTaskOverview(taskId),
        listComponentRuns(taskId),
        listTaskFolders(taskId),
      ])
      if (!isBuiltinProfileTask(task)) {
        clearSelectedTask(taskId)
        return null
      }
      upsertTask(task)
      setOverview(taskOverview)
      setFolders(folderList)
      setEvents(eventList.items)
      setComponentRuns(runList.items)
      setTaskDataTaskId(taskId)
      return eventList.latest_sequence
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '无法读取任务详情', 'error')
      return null
    }
  }, [clearSelectedTask, notify, upsertTask])

  useEffect(() => {
    setTaskDataTaskId(null)
    if (!selectedTaskId) {
      setOverview(null)
      setFolders(null)
      setEvents([])
      setComponentRuns([])
    }
  }, [selectedTaskId])

  const hasCurrentTaskData = taskDataTaskId === selectedTaskId

  return {
    componentRuns: hasCurrentTaskData ? componentRuns : [],
    events: hasCurrentTaskData ? events : [],
    folders: hasCurrentTaskData ? folders : null,
    loadTaskData,
    overview: hasCurrentTaskData ? overview : null,
  }
}
