import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const APP_BOOTSTRAP_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useAppBootstrap.ts', import.meta.url))
const SELECTED_TASK_DATA_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useSelectedTaskData.ts', import.meta.url))
const TASKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/TasksPage.tsx', import.meta.url))
const COMPONENTS_CLIENT_URL = new URL('../src/clients/components.ts', import.meta.url)
const PROFILES_CLIENT_URL = new URL('../src/clients/profiles.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)
const clientUrls = new Set([COMPONENTS_CLIENT_URL.href, PROFILES_CLIENT_URL.href])

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '../transport/http' && clientUrls.has(context.parentURL)) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function componentsClient() {
  return import(COMPONENTS_CLIENT_URL.href)
}

async function profilesClient() {
  return import(PROFILES_CLIENT_URL.href)
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  }
}

test('listComponents requests the exact component URL with the default GET and returns its payload', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { items: [] }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { listComponents } = await componentsClient()

    assert.strictEqual(await listComponents(), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/components')
    assert.equal(calls[0].options.method, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('listComponentRuns requests the exact task-scoped URL with the default GET and returns its payload', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { items: [] }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { listComponentRuns } = await componentsClient()

    assert.strictEqual(await listComponentRuns('task-1'), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/components/runs/task-1')
    assert.equal(calls[0].options.method, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('getRuntimeTuningRecommendation requests the exact recommendation URL with the default GET and returns its payload', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { device: 'cpu', precision: 'fp32', updates: {} }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { getRuntimeTuningRecommendation } = await componentsClient()

    assert.strictEqual(await getRuntimeTuningRecommendation(), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/components/runtime-tuning/recommendation')
    assert.equal(calls[0].options.method, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('listBuiltinProfiles requests the exact builtin-profile URL with the default GET and returns its payload', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { items: [] }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { listBuiltinProfiles } = await profilesClient()

    assert.strictEqual(await listBuiltinProfiles(), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/components/builtin-profiles')
    assert.equal(calls[0].options.method, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('App delegates component ownership to bootstrap and selected-task hooks while TasksPage retains orchestration', async () => {
  const [app, bootstrapHook, selectedTaskDataHook, tasksPage] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(APP_BOOTSTRAP_HOOK_PATH, 'utf8'),
    readFile(SELECTED_TASK_DATA_HOOK_PATH, 'utf8'),
    readFile(TASKS_PAGE_PATH, 'utf8'),
  ])

  assert.doesNotMatch(app, /\/api\/components/)
  assert.doesNotMatch(app, /\blistComponents\b/)
  assert.doesNotMatch(app, /\blistComponentRuns\b/)
  assert.doesNotMatch(app, /setComponents\(data\.items\)/)
  assert.match(app, /from '\.\/hooks\/useAppBootstrap'/)
  assert.match(app, /components,\s*health,/)
  assert.match(app, /from '\.\/hooks\/useSelectedTaskData'/)
  assert.match(bootstrapHook, /from '\.\.\/clients\/components'/)
  assert.match(bootstrapHook, /await listComponents\(\)/)
  assert.match(bootstrapHook, /setComponents\(data\.items\)/)
  assert.match(selectedTaskDataHook, /from '\.\.\/clients\/components'/)
  assert.match(selectedTaskDataHook, /listComponentRuns\(taskId\)/)
  assert.match(selectedTaskDataHook, /Promise\.all\(\[/)

  assert.doesNotMatch(tasksPage, /\/api\/components/)
  assert.match(tasksPage, /Promise\.all\(\[listComponents\(\), listBuiltinProfiles\(\)\]\)/)
  assert.match(tasksPage, /getRuntimeTuningRecommendation\(\)/)
  assert.match(tasksPage, /isDatasetProfile\(profile\.id\)/)
  assert.match(tasksPage, /mergeRuntimeUpdates\(/)
  assert.match(tasksPage, /finally \{\s*setTuningBusy\(false\)/)
})
