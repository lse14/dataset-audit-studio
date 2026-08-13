import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const HOOK_URL = new URL('../src/hooks/useAppBootstrap.ts', import.meta.url)
const SELECTED_TASK_DATA_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useSelectedTaskData.ts', import.meta.url))
const SYSTEM_CLIENT_URL = new URL('../src/clients/system.ts', import.meta.url)
const TASKS_CLIENT_URL = new URL('../src/clients/tasks.ts', import.meta.url)
const COMPONENTS_CLIENT_URL = new URL('../src/clients/components.ts', import.meta.url)
const PROFILE_WORKSPACE_URL = new URL('../src/profileWorkspace.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)
const hookDependencies = new Map([
  ['../clients/system', SYSTEM_CLIENT_URL],
  ['../clients/tasks', TASKS_CLIENT_URL],
  ['../clients/components', COMPONENTS_CLIENT_URL],
  ['../profileWorkspace', PROFILE_WORKSPACE_URL],
])
const clientUrls = new Set([
  SYSTEM_CLIENT_URL.href,
  TASKS_CLIENT_URL.href,
  COMPONENTS_CLIENT_URL.href,
])

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL === HOOK_URL.href && hookDependencies.has(specifier)) {
      return { shortCircuit: true, url: hookDependencies.get(specifier).href }
    }
    if (specifier === '../transport/http' && clientUrls.has(context.parentURL)) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function appBootstrapHook() {
  return import(HOOK_URL.href)
}

async function hookSource() {
  return readFile(fileURLToPath(HOOK_URL), 'utf8')
}

test('bootstrap hook has the bounded client and React dependency surface', async () => {
  const hook = await appBootstrapHook()
  const source = await hookSource()

  assert.equal(typeof hook.useAppBootstrap, 'function')
  assert.match(source, /from 'react'/)
  assert.match(source, /from '\.\.\/clients\/system'/)
  assert.match(source, /from '\.\.\/clients\/tasks'/)
  assert.match(source, /from '\.\.\/clients\/components'/)
  assert.doesNotMatch(source, /\.\.\/clients\/(?:workspace|reviews|exports|models|presets|profiles|filesystem)/)
  assert.doesNotMatch(source, /\.\.\/transport\/taskEvents/)
  assert.doesNotMatch(source, /taskRefreshPolicy/)
  assert.doesNotMatch(source, /\/api/)
})

test('bootstrap hook owns health state and system client error handling', async () => {
  await appBootstrapHook()
  const source = await hookSource()

  assert.match(source, /\[health, setHealth\]/)
  assert.match(source, /\[healthError, setHealthError\]/)
  assert.match(source, /\[healthLoading, setHealthLoading\]/)
  assert.match(source, /setHealth\(await getSystemHealth\(\)\)/)
  assert.match(source, /无法连接后端/)
  assert.match(source, /reloadHealth/)
})

test('bootstrap hook owns the task and component collection loads', async () => {
  await appBootstrapHook()
  const source = await hookSource()

  assert.match(source, /\[tasks, setTasks\]/)
  assert.match(source, /\[components, setComponents\]/)
  assert.match(source, /await listTasks\(\)/)
  assert.match(source, /await listComponents\(\)/)
  assert.match(source, /setComponents\(data\.items\)/)
  assert.match(source, /无法读取任务/)
  assert.match(source, /无法读取组件清单/)
  assert.match(source, /reloadTasks/)
  assert.match(source, /reloadComponents/)
})

test('bootstrap hook filters profile tasks, reconciles selection, and exposes one task upsert', async () => {
  await appBootstrapHook()
  const source = await hookSource()

  assert.match(source, /data\.items\.filter\(isBuiltinProfileTask\)/)
  assert.match(source, /reconcileSelectedTask\(profileTasks\)/)
  assert.match(source, /setTaskListReady\(true\)/)
  assert.match(source, /upsertTask/)
  assert.match(source, /current\.some\(\(item\) => item\.id === task\.id\)/)
})

test('bootstrap hook performs the initial three loads and manages the 15000ms health timer', async () => {
  await appBootstrapHook()
  const source = await hookSource()

  assert.match(source, /void reloadHealth\(\)/)
  assert.match(source, /void reloadTasks\(\)/)
  assert.match(source, /void reloadComponents\(\)/)
  assert.match(source, /window\.setInterval\(\(\) => void reloadHealth\(\), 15000\)/)
  assert.match(source, /return \(\) => window\.clearInterval\(timer\)/)
})

test('App delegates bootstrap work while retaining selected-task, storage, and task-event responsibilities', async () => {
  const [app, selectedTaskDataHook] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(SELECTED_TASK_DATA_HOOK_PATH, 'utf8'),
  ])

  assert.match(app, /useAppBootstrap\(\{[\s\S]*notify,[\s\S]*reconcileSelectedTask/)
  assert.doesNotMatch(app, /from '\.\/clients\/system'/)
  assert.doesNotMatch(app, /\blistTasks\b/)
  assert.doesNotMatch(app, /\blistComponents\b/)
  assert.doesNotMatch(app, /setHealth\(await getSystemHealth\(\)\)/)
  assert.doesNotMatch(app, /window\.setInterval\(\(\) => void loadHealth\(\), 15000\)/)
  assert.match(app, /from '\.\/hooks\/useSelectedTaskData'/)
  assert.match(app, /useSelectedTaskData\(\{/)
  assert.doesNotMatch(app, /const loadTaskData = useCallback/)
  assert.match(selectedTaskDataHook, /const loadTaskData = useCallback/)
  assert.match(app, /window\.localStorage/)
  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
  assert.match(app, /useTaskEventRefresh\(\{/)
  assert.match(app, /\[selectedTaskId, setSelectedTaskId\]/)
})

test('audit pages remount when selected task changes', async () => {
  const app = await readFile(APP_PATH, 'utf8')
  const remountKey = /key=\{selectedTaskId \?\? 'none'\}/

  for (const page of ['RisksPage', 'StylePage', 'DuplicatesPage', 'AestheticsPage', 'ExportsPage']) {
    assert.match(
      app,
      new RegExp(`<${page}[\\s\\S]*?${remountKey.source}`),
      `${page} should remount on task selection changes`,
    )
  }
})
