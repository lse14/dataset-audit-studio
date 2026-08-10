type TimerId = ReturnType<typeof window.setTimeout>

type TaskRefreshPolicyOptions = {
  clearTimeout: (timer: TimerId) => void
  scheduleRefresh: () => void
  scheduleTimeout: (callback: () => void, delay: number) => TimerId
}

const fallbackDelayMs = 5000

export function createTaskRefreshPolicy({
  clearTimeout,
  scheduleRefresh,
  scheduleTimeout,
}: TaskRefreshPolicyOptions) {
  let fallbackTimer: TimerId | null = null
  let streamOpen = false

  const clearFallback = () => {
    if (fallbackTimer === null) return
    clearTimeout(fallbackTimer)
    fallbackTimer = null
  }

  const scheduleFallback = () => {
    if (streamOpen || fallbackTimer !== null) return
    fallbackTimer = scheduleTimeout(() => {
      fallbackTimer = null
      if (streamOpen) return
      scheduleRefresh()
      scheduleFallback()
    }, fallbackDelayMs)
  }

  return {
    dispose() {
      streamOpen = true
      clearFallback()
    },
    onError() {
      streamOpen = false
      scheduleFallback()
    },
    onOpen() {
      streamOpen = true
      clearFallback()
    },
    start() {
      scheduleFallback()
    },
  }
}
