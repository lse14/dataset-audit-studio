import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const CANONICAL_TASK_EVENTS_URL = new URL('../src/transport/taskEvents.ts', import.meta.url)
const EVENT_REFRESH_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useTaskEventRefresh.ts', import.meta.url))
const eventTypes = [
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
]

async function canonicalTaskEvents() {
  return import(CANONICAL_TASK_EVENTS_URL.href)
}

class FakeEventSource {
  static instances = []

  constructor(url) {
    this.url = url
    this.added = []
    this.removed = []
    this.closeCalls = 0
    this.onerror = null
    this.onopen = null
    FakeEventSource.instances.push(this)
  }

  addEventListener(type, listener) {
    this.added.push([type, listener])
  }

  removeEventListener(type, listener) {
    this.removed.push([type, listener])
  }

  close() {
    this.closeCalls += 1
  }
}

function resetFakeEventSource() {
  FakeEventSource.instances = []
}

test('task event transport uses the exact stream URL and canonical subscriptions', async () => {
  const originalEventSource = globalThis.EventSource
  resetFakeEventSource()

  try {
    globalThis.EventSource = FakeEventSource
    const { openTaskEventStream } = await canonicalTaskEvents()
    const onEvent = () => {}

    openTaskEventStream('task-1', 17, {
      onError: () => {},
      onEvent,
      onOpen: () => {},
    })

    const stream = FakeEventSource.instances[0]
    assert.equal(stream.url, '/api/tasks/task-1/events/stream?after=17')
    assert.deepEqual(stream.added.map(([type]) => type), eventTypes)
    assert.deepEqual(stream.added.map(([, listener]) => listener), eventTypes.map(() => onEvent))
  } finally {
    globalThis.EventSource = originalEventSource
  }
})

test('task event transport forwards open error and named events to supplied callbacks', async () => {
  const originalEventSource = globalThis.EventSource
  resetFakeEventSource()

  try {
    globalThis.EventSource = FakeEventSource
    const { openTaskEventStream } = await canonicalTaskEvents()
    let openCalls = 0
    let errorCalls = 0
    let eventCalls = 0
    openTaskEventStream('task-1', 17, {
      onError: () => { errorCalls += 1 },
      onEvent: () => { eventCalls += 1 },
      onOpen: () => { openCalls += 1 },
    })

    const stream = FakeEventSource.instances[0]
    stream.onopen()
    stream.onerror()
    stream.added[0][1]()

    assert.equal(openCalls, 1)
    assert.equal(errorCalls, 1)
    assert.equal(eventCalls, 1)
  } finally {
    globalThis.EventSource = originalEventSource
  }
})

test('task event stream close removes canonical listeners before closing the native source', async () => {
  const originalEventSource = globalThis.EventSource
  resetFakeEventSource()

  try {
    globalThis.EventSource = FakeEventSource
    const { openTaskEventStream } = await canonicalTaskEvents()
    const onEvent = () => {}
    const handle = openTaskEventStream('task-1', 17, {
      onError: () => {},
      onEvent,
      onOpen: () => {},
    })

    handle.close()

    const stream = FakeEventSource.instances[0]
    assert.deepEqual(stream.removed.map(([type]) => type), eventTypes)
    assert.deepEqual(stream.removed.map(([, listener]) => listener), eventTypes.map(() => onEvent))
    assert.equal(stream.closeCalls, 1)
  } finally {
    globalThis.EventSource = originalEventSource
  }
})

test('event refresh hook orchestrates the canonical task-event transport for App', async () => {
  const [app, hook] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(EVENT_REFRESH_HOOK_PATH, 'utf8'),
  ])

  assert.doesNotMatch(hook, /\bEventSource\b/)
  assert.doesNotMatch(hook, /events\/stream/)
  assert.doesNotMatch(hook, /eventTypes/)
  assert.doesNotMatch(hook, /task_created|config_changed|task_queued|worker_claimed|batch_committed|phase_process_ready|phase_completed|watermark_review_threshold_changed|pause_requested|task_paused|task_resumed|terminate_requested|task_force_terminated|task_terminated|task_failed|review_gate_released|rewrite_preview_confirmed|legacy_task_rejected|stale_worker_recovered/)
  assert.match(hook, /from '\.\.\/transport\/taskEvents'/)
  assert.match(hook, /openStream: openTaskEventStream/)
  assert.match(hook, /from '\.\.\/taskRefreshPolicy'/)
  assert.match(hook, /createRefreshPolicy: createTaskRefreshPolicy/)
  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
  assert.match(app, /useTaskEventRefresh\(\{/)
  assert.doesNotMatch(app, /from '\.\/transport\/taskEvents'|from '\.\/taskRefreshPolicy'/)
})
