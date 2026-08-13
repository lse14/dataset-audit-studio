const taskEventTypes = [
  'task_created',
  'config_changed',
  'task_queued',
  'worker_claimed',
  'batch_committed',
  'phase_process_ready',
  'phase_completed',
  'watermark_review_threshold_changed',
  'pause_requested',
  'task_paused',
  'task_resumed',
  'terminate_requested',
  'task_force_terminated',
  'task_terminated',
  'task_failed',
  'review_gate_released',
  'rewrite_preview_confirmed',
  'legacy_task_rejected',
  'stale_worker_recovered',
] as const

export type TaskEventStreamHandlers = {
  onError: () => void
  onEvent: () => void
  onOpen: () => void
}

export type TaskEventStream = {
  close: () => void
}

export function openTaskEventStream(
  taskId: string,
  after: number,
  { onError, onEvent, onOpen }: TaskEventStreamHandlers,
): TaskEventStream {
  const stream = new EventSource(
    `/api/tasks/${taskId}/events/stream?after=${after}`,
  )
  stream.onopen = onOpen
  stream.onerror = onError
  taskEventTypes.forEach((type) => stream.addEventListener(type, onEvent))

  return {
    close() {
      taskEventTypes.forEach((type) => stream.removeEventListener(type, onEvent))
      stream.close()
    },
  }
}
