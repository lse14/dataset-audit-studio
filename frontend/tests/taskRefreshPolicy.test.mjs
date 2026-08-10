import assert from 'node:assert/strict'
import test from 'node:test'

import { createTaskRefreshPolicy } from '../src/taskRefreshPolicy.ts'

function createTimerHarness() {
  let nextId = 1
  const timers = new Map()
  const cleared = []
  return {
    clearTimeout(id) {
      cleared.push(id)
      timers.delete(id)
    },
    cleared,
    scheduleTimeout(callback, delay) {
      const id = nextId++
      timers.set(id, { callback, delay })
      return id
    },
    timers,
  }
}

test('stops fallback refreshes while the task event stream is open', () => {
  const harness = createTimerHarness()
  const policy = createTaskRefreshPolicy({
    clearTimeout: harness.clearTimeout,
    scheduleRefresh: () => {},
    scheduleTimeout: harness.scheduleTimeout,
  })

  policy.start()
  assert.deepEqual([...harness.timers.values()].map((timer) => timer.delay), [5000])

  policy.onOpen()
  assert.equal(harness.timers.size, 0)
  assert.deepEqual(harness.cleared, [1])
})

test('restarts one fallback timer after an event stream error and clears it on dispose', () => {
  const harness = createTimerHarness()
  let refreshes = 0
  const policy = createTaskRefreshPolicy({
    clearTimeout: harness.clearTimeout,
    scheduleRefresh: () => {
      refreshes += 1
    },
    scheduleTimeout: harness.scheduleTimeout,
  })

  policy.start()
  policy.onError()
  assert.equal(harness.timers.size, 1)

  const [[timerId, { callback, delay }]] = harness.timers.entries()
  assert.equal(delay, 5000)
  harness.timers.delete(timerId)
  callback()
  assert.equal(refreshes, 1)
  assert.equal(harness.timers.size, 1)

  policy.dispose()
  assert.equal(harness.timers.size, 0)
})
