import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const HOOK_URL = new URL('../src/hooks/useSelectedTaskData.ts', import.meta.url)
const HOOK_PATH = fileURLToPath(HOOK_URL)
const MODELS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ModelsPage.tsx', import.meta.url))
const PROFILE_WORKSPACE_URL = new URL('../src/profileWorkspace.ts', import.meta.url)
const RACE_HARNESS_KEY = '__selectedTaskDataRaceHarness'

function dataModule(source) {
  return new URL(`data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`)
}

const fakeReactUrl = dataModule(`
export function useRef(initial) {
  return { current: initial }
}
export function useState(initial) {
  const slot = { value: initial }
  globalThis.${RACE_HARNESS_KEY}.states.push(slot)
  return [slot.value, (next) => {
    slot.value = typeof next === 'function' ? next(slot.value) : next
  }]
}
export function useCallback(fn) {
  return fn
}
export function useEffect(fn) {
  fn()
}
`)
const fakeTasksUrl = dataModule(`
const harness = () => globalThis.${RACE_HARNESS_KEY}
export function listTaskEvents(taskId) { return harness().listTaskEvents(taskId) }
export function getTask(taskId) { return harness().getTask(taskId) }
`)
const fakeComponentsUrl = dataModule(`
const harness = () => globalThis.${RACE_HARNESS_KEY}
export function listComponentRuns(taskId) { return harness().listComponentRuns(taskId) }
`)
const fakeWorkspaceUrl = dataModule(`
const harness = () => globalThis.${RACE_HARNESS_KEY}
export function getTaskOverview(taskId) { return harness().getTaskOverview(taskId) }
export function listTaskFolders(taskId) { return harness().listTaskFolders(taskId) }
`)

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL !== HOOK_URL.href) return nextResolve(specifier, context)
    if (specifier === 'react') return { shortCircuit: true, url: fakeReactUrl.href }
    if (specifier === '../clients/tasks') return { shortCircuit: true, url: fakeTasksUrl.href }
    if (specifier === '../clients/components') return { shortCircuit: true, url: fakeComponentsUrl.href }
    if (specifier === '../clients/workspace') return { shortCircuit: true, url: fakeWorkspaceUrl.href }
    if (specifier === '../profileWorkspace') return { shortCircuit: true, url: PROFILE_WORKSPACE_URL.href }
    return nextResolve(specifier, context)
  },
})

function deferred() {
  let resolve
  const promise = new Promise((next) => {
    resolve = next
  })
  return { promise, resolve }
}

async function hookSource() {
  return readFile(HOOK_PATH, 'utf8')
}

test('selected-task data hook exports the bounded React and client dependency surface', async () => {
  const source = await hookSource()

  assert.match(source, /export function useSelectedTaskData/)
  assert.match(source, /from 'react'/)
  assert.match(source, /from '\.\.\/clients\/tasks'/)
  assert.match(source, /from '\.\.\/clients\/components'/)
  assert.match(source, /from '\.\.\/clients\/workspace'/)
  assert.match(source, /from '\.\.\/profileWorkspace'/)
  assert.match(source, /from '\.\.\/types'/)
  assert.doesNotMatch(source, /transport\/taskEvents|taskRefreshPolicy|localStorage|\/api\/|from '\.\.\/pages\//)
})

test('selected-task data hook owns detail state and masks non-current task data', async () => {
  const source = await hookSource()

  assert.match(source, /\[overview, setOverview\]/)
  assert.match(source, /\[folders, setFolders\]/)
  assert.match(source, /\[events, setEvents\]/)
  assert.match(source, /\[componentRuns, setComponentRuns\]/)
  assert.match(source, /\[taskDataTaskId, setTaskDataTaskId\]/)
  assert.match(source, /taskDataTaskId === selectedTaskId/)
  assert.match(source, /overview: hasCurrentTaskData \? overview : null/)
  assert.match(source, /folders: hasCurrentTaskData \? folders : null/)
  assert.match(source, /events: hasCurrentTaskData \? events : \[\]/)
  assert.match(source, /componentRuns: hasCurrentTaskData \? componentRuns : \[\]/)
})

test('selected-task data hook keeps five independent reads in one Promise.all', async () => {
  const source = await hookSource()

  assert.match(source, /const \[eventList, task, taskOverview, runList, folderList\] = await Promise\.all\(\[/)
  assert.match(source, /listTaskEvents\(taskId\)/)
  assert.match(source, /getTask\(taskId\)/)
  assert.match(source, /getTaskOverview\(taskId\)/)
  assert.match(source, /listComponentRuns\(taskId\)/)
  assert.match(source, /listTaskFolders\(taskId\)/)
})

test('selected-task data hook preserves the profile success path and latest sequence', async () => {
  const source = await hookSource()

  assert.match(source, /isBuiltinProfileTask\(task\)/)
  assert.match(source, /upsertTask\(task\)/)
  assert.match(source, /setOverview\(taskOverview\)/)
  assert.match(source, /setFolders\(folderList\)/)
  assert.match(source, /setEvents\(eventList\.items\)/)
  assert.match(source, /setComponentRuns\(runList\.items\)/)
  assert.match(source, /setTaskDataTaskId\(taskId\)/)
  assert.match(source, /return eventList\.latest_sequence/)
})

test('selected-task data hook clears an invalid selected task and preserves error notification', async () => {
  const source = await hookSource()

  assert.match(source, /clearSelectedTask\(taskId\)/)
  assert.match(source, /无法读取任务详情/)
  assert.match(source, /return null/)
})

test('selected-task data hook invalidates and clears stale detail on selection changes', async () => {
  const source = await hookSource()

  assert.match(source, /useEffect\(\(\) => \{\s*setTaskDataTaskId\(null\)/)
  assert.match(source, /if \(!selectedTaskId\) \{[\s\S]*setOverview\(null\)/)
  assert.match(source, /setFolders\(null\)/)
  assert.match(source, /setEvents\(\[\]\)/)
  assert.match(source, /setComponentRuns\(\[\]\)/)
})

test('App delegates selected-task data while retaining storage and task-event orchestration', async () => {
  const app = await readFile(APP_PATH, 'utf8')

  assert.match(app, /from '\.\/hooks\/useSelectedTaskData'/)
  assert.match(app, /useSelectedTaskData\(\{/)
  assert.doesNotMatch(app, /const loadTaskData = useCallback/)
  assert.doesNotMatch(app, /listTaskEvents\(taskId\)|getTask\(taskId\)|getTaskOverview\(taskId\)|listComponentRuns\(taskId\)|listTaskFolders\(taskId\)/)
  assert.match(app, /window\.localStorage/)
  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
  assert.match(app, /useTaskEventRefresh\(\{/)
  assert.match(app, /\[selectedFolder, setSelectedFolder\]/)
})

test('selected-task data hook guards loadTaskData with a request generation and selected-task ref', async () => {
  const source = await hookSource()
  assert.match(source, /requestIdRef/)
  assert.match(source, /selectedTaskIdRef/)
  assert.match(source, /const requestId = \+\+requestIdRef\.current/)
  assert.match(
    source,
    /if \(requestId !== requestIdRef\.current \|\| taskId !== selectedTaskIdRef\.current\) return null/,
  )
})

test('selected-task data hook ignores a stale overlapping load that resolves last', async () => {
  const eventLists = []
  const upserts = []
  globalThis[RACE_HARNESS_KEY] = {
    states: [],
    getTask(taskId) {
      return Promise.resolve({ id: taskId, config: { profile: 'general' } })
    },
    getTaskOverview() {
      return Promise.resolve({ label: 'overview' })
    },
    listComponentRuns() {
      return Promise.resolve({ items: [] })
    },
    listTaskEvents() {
      const pending = deferred()
      eventLists.push(pending)
      return pending.promise
    },
    listTaskFolders() {
      return Promise.resolve({ items: [] })
    },
  }

  const { useSelectedTaskData } = await import(HOOK_URL.href)
  const result = useSelectedTaskData({
    clearSelectedTask() {},
    notify() {},
    selectedTaskId: 'task-1',
    upsertTask(task) {
      upserts.push(task.id)
    },
  })

  const stale = result.loadTaskData('task-1')
  const fresh = result.loadTaskData('task-1')
  assert.equal(eventLists.length, 2)

  eventLists[1].resolve({ items: [{ id: 'fresh' }], latest_sequence: 20 })
  assert.equal(await fresh, 20)

  eventLists[0].resolve({ items: [{ id: 'stale' }], latest_sequence: 10 })
  assert.equal(await stale, null)

  assert.deepEqual(globalThis[RACE_HARNESS_KEY].states[2].value, [{ id: 'fresh' }])
  assert.deepEqual(upserts, ['task-1'])
})

test('Models local import primary submit is disabled while pickerBusy', async () => {
  const source = await readFile(MODELS_PAGE_PATH, 'utf8')
  assert.match(source, /disabled=\{busy \|\| pickerBusy \|\| !path\.trim\(\) \|\| !base\}/)
})
