import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const HOOK_URL = new URL('../src/hooks/useTaskEventRefresh.ts', import.meta.url)
const TASK_EVENTS_URL = new URL('../src/transport/taskEvents.ts', import.meta.url)
const REFRESH_POLICY_URL = new URL('../src/taskRefreshPolicy.ts', import.meta.url)

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL === HOOK_URL.href && specifier === '../transport/taskEvents') {
      return { shortCircuit: true, url: TASK_EVENTS_URL.href }
    }
    if (context.parentURL === HOOK_URL.href && specifier === '../taskRefreshPolicy') {
      return { shortCircuit: true, url: REFRESH_POLICY_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function eventRefreshHook() {
  return import(HOOK_URL.href)
}

async function hookSource() {
  return readFile(fileURLToPath(HOOK_URL), 'utf8')
}

function deferred() {
  let resolve
  const promise = new Promise((next) => {
    resolve = next
  })
  return { promise, resolve }
}

async function settle() {
  await Promise.resolve()
  await Promise.resolve()
}

function createTimerHarness(order = []) {
  let nextId = 1
  const timers = new Map()
  const cleared = []
  return {
    clearTimeout(id) {
      order.push('clear')
      cleared.push(id)
      timers.delete(id)
    },
    cleared,
    run(id) {
      const timer = timers.get(id)
      assert.ok(timer, `timer ${id} must be pending`)
      timers.delete(id)
      timer.callback()
    },
    scheduleTimeout(callback, delay) {
      const id = nextId++
      timers.set(id, { callback, delay })
      return id
    },
    timers,
  }
}

function createLifecycleHarness(loadTaskData) {
  const order = []
  const timers = createTimerHarness(order)
  const connections = []
  const policy = {
    disposeCalls: 0,
    errorCalls: 0,
    openCalls: 0,
    startCalls: 0,
    dispose() {
      order.push('policy.dispose')
      this.disposeCalls += 1
    },
    onError() {
      this.errorCalls += 1
    },
    onOpen() {
      this.openCalls += 1
    },
    start() {
      this.startCalls += 1
    },
  }
  const stream = {
    closeCalls: 0,
    close() {
      order.push('stream.close')
      this.closeCalls += 1
    },
  }
  const opened = []
  let handlers = null

  return {
    connections,
    opened,
    order,
    policy,
    ports: {
      clearTimeout: timers.clearTimeout,
      createRefreshPolicy: () => policy,
      loadTaskData,
      openStream(taskId, after, nextHandlers) {
        opened.push({ after, taskId })
        handlers = nextHandlers
        return stream
      },
      scheduleTimeout: timers.scheduleTimeout,
      setConnected(value) {
        order.push(`connected:${value}`)
        connections.push(value)
      },
    },
    stream,
    timers,
    handlers() {
      assert.ok(handlers, 'stream handlers must be registered')
      return handlers
    },
  }
}

test('event refresh hook exports a bounded lifecycle helper and dependency surface', async () => {
  const hook = await eventRefreshHook()
  const source = await hookSource()

  assert.equal(typeof hook.startTaskEventRefreshLifecycle, 'function')
  assert.equal(typeof hook.useTaskEventRefresh, 'function')
  assert.match(source, /from 'react'/)
  assert.match(source, /from '\.\.\/transport\/taskEvents'/)
  assert.match(source, /from '\.\.\/taskRefreshPolicy'/)
  assert.doesNotMatch(source, /localStorage|\/api\/|from '\.\.\/pages\//)
})

test('lifecycle starts fallback and initial load before opening the exact task stream sequence', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  const initial = deferred()
  const harness = createLifecycleHarness(() => initial.promise)

  const lifecycle = startTaskEventRefreshLifecycle('task-1', harness.ports)
  assert.equal(harness.policy.startCalls, 1)
  assert.deepEqual(harness.opened, [])

  initial.resolve(17)
  await settle()
  assert.deepEqual(harness.opened, [{ after: 17, taskId: 'task-1' }])

  lifecycle.dispose()
})

test('lifecycle skips a null initial sequence and cannot open a stream after disposal', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  const nullHarness = createLifecycleHarness(async () => null)
  const nullLifecycle = startTaskEventRefreshLifecycle('task-1', nullHarness.ports)
  await settle()
  assert.deepEqual(nullHarness.opened, [])
  nullLifecycle.dispose()

  const late = deferred()
  const lateHarness = createLifecycleHarness(() => late.promise)
  const lateLifecycle = startTaskEventRefreshLifecycle('task-2', lateHarness.ports)
  lateLifecycle.dispose()
  late.resolve(23)
  await settle()
  assert.deepEqual(lateHarness.opened, [])
})

test('lifecycle reopens the stream after fallback obtains a sequence', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  const { createTaskRefreshPolicy } = await import(REFRESH_POLICY_URL.href)
  let loads = 0
  const harness = createLifecycleHarness(async () => {
    loads += 1
    if (loads === 1) return null
    return 71
  })
  harness.ports.createRefreshPolicy = createTaskRefreshPolicy
  const lifecycle = startTaskEventRefreshLifecycle('task-1', harness.ports)
  await settle()
  assert.deepEqual(harness.opened, [])

  const fallbackId = [...harness.timers.timers.entries()].find(([, timer]) => timer.delay === 5000)?.[0]
  assert.ok(fallbackId, 'fallback timer must be pending after a null initial sequence')
  harness.timers.run(fallbackId)

  const debounceId = [...harness.timers.timers.entries()].find(([, timer]) => timer.delay === 120)?.[0]
  assert.ok(debounceId, 'fallback must schedule a refresh debounce')
  harness.timers.run(debounceId)
  await settle()

  assert.deepEqual(harness.opened, [{ after: 71, taskId: 'task-1' }])
  lifecycle.dispose()
})

test('stream open and error update connection state and fallback policy', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  const harness = createLifecycleHarness(async () => 31)
  const lifecycle = startTaskEventRefreshLifecycle('task-1', harness.ports)
  await settle()

  harness.handlers().onOpen()
  assert.equal(harness.policy.openCalls, 1)
  assert.deepEqual(harness.connections, [true])

  harness.handlers().onError()
  assert.equal(harness.policy.errorCalls, 1)
  assert.deepEqual(harness.connections, [true, false])

  lifecycle.dispose()
})

test('multiple stream events retain one exact 120ms refresh timer', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  let loads = 0
  const harness = createLifecycleHarness(async () => {
    loads += 1
    return 41
  })
  const lifecycle = startTaskEventRefreshLifecycle('task-1', harness.ports)
  await settle()

  harness.handlers().onEvent()
  harness.handlers().onEvent()
  harness.handlers().onEvent()
  assert.equal(harness.timers.timers.size, 1)
  assert.deepEqual([...harness.timers.timers.values()].map((timer) => timer.delay), [120])
  assert.equal(harness.timers.cleared.length, 2)

  const [timerId] = harness.timers.timers.keys()
  harness.timers.run(timerId)
  await settle()
  assert.equal(loads, 2)

  lifecycle.dispose()
})

test('a running refresh queues exactly one follow-up load without concurrent fan-out', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  const firstRefresh = deferred()
  const secondRefresh = deferred()
  let loads = 0
  const harness = createLifecycleHarness(() => {
    loads += 1
    if (loads === 1) return Promise.resolve(53)
    if (loads === 2) return firstRefresh.promise
    return secondRefresh.promise
  })
  const lifecycle = startTaskEventRefreshLifecycle('task-1', harness.ports)
  await settle()

  harness.handlers().onEvent()
  harness.timers.run([...harness.timers.timers.keys()][0])
  await settle()
  assert.equal(loads, 2)

  harness.handlers().onEvent()
  harness.timers.run([...harness.timers.timers.keys()][0])
  await settle()
  assert.equal(loads, 2)

  firstRefresh.resolve(54)
  await settle()
  assert.equal(loads, 3)

  secondRefresh.resolve(55)
  await settle()
  lifecycle.dispose()
})

test('dispose clears debounce, closes stream, disposes policy, and ignores later events', async () => {
  const { startTaskEventRefreshLifecycle } = await eventRefreshHook()
  const harness = createLifecycleHarness(async () => 61)
  const lifecycle = startTaskEventRefreshLifecycle('task-1', harness.ports)
  await settle()
  harness.handlers().onEvent()
  assert.equal(harness.timers.timers.size, 1)

  harness.order.length = 0
  lifecycle.dispose()
  assert.deepEqual(harness.order, ['clear', 'stream.close', 'policy.dispose', 'connected:false'])
  assert.equal(harness.timers.timers.size, 0)
  assert.equal(harness.stream.closeCalls, 1)
  assert.equal(harness.policy.disposeCalls, 1)

  harness.handlers().onEvent()
  harness.handlers().onOpen()
  harness.handlers().onError()
  assert.equal(harness.timers.timers.size, 0)
  assert.equal(harness.policy.openCalls, 0)
  assert.equal(harness.policy.errorCalls, 0)
})

test('App delegates event refresh while retaining storage, folder selection, and task handlers', async () => {
  const app = await readFile(APP_PATH, 'utf8')

  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
  assert.match(app, /useTaskEventRefresh\(\{[\s\S]*enabled: taskListReady,[\s\S]*loadTaskData,[\s\S]*taskId: selectedTaskId/)
  assert.doesNotMatch(app, /from '\.\/transport\/taskEvents'|from '\.\/taskRefreshPolicy'/)
  assert.doesNotMatch(app, /\[sseConnected, setSseConnected\]|let refreshTimer|let refreshRunning|let refreshQueued|openTaskEventStream\(|createTaskRefreshPolicy\(/)
  assert.match(app, /window\.localStorage/)
  assert.match(app, /\[selectedFolder, setSelectedFolder\]/)
  assert.match(app, /const onTaskChanged/)
  assert.match(app, /const onTaskDeleted/)
  assert.match(app, /const onAuditChanged/)
})
