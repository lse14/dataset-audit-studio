import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const APP_BOOTSTRAP_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useAppBootstrap.ts', import.meta.url))
const SELECTED_TASK_DATA_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useSelectedTaskData.ts', import.meta.url))
const TASKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/TasksPage.tsx', import.meta.url))
const PROGRESS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ProgressPage.tsx', import.meta.url))
const TASKS_CLIENT_URL = new URL('../src/clients/tasks.ts', import.meta.url)
const PRESETS_CLIENT_URL = new URL('../src/clients/presets.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)
const clientUrls = new Set([TASKS_CLIENT_URL.href, PRESETS_CLIENT_URL.href])

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '../transport/http' && clientUrls.has(context.parentURL)) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function tasksClient() {
  return import(TASKS_CLIENT_URL.href)
}

async function presetsClient() {
  return import(PRESETS_CLIENT_URL.href)
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  }
}

test('task client reads use the exact list, task, and ordinary event URLs', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const taskList = { items: [], total: 0, offset: 0, limit: 200 }
  const task = { id: 'task-1' }
  const eventList = { items: [], next_after: 17, latest_sequence: 17 }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse([taskList, task, eventList][calls.length - 1])
    }
    const { getTask, listTaskEvents, listTasks } = await tasksClient()

    assert.strictEqual(await listTasks(), taskList)
    assert.strictEqual(await getTask('task-1'), task)
    assert.strictEqual(await listTaskEvents('task-1'), eventList)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks?limit=200',
      '/api/tasks/task-1',
      '/api/tasks/task-1/events?limit=200',
    ])
    assert.deepEqual(calls.map(({ options }) => options.method), [undefined, undefined, undefined])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('task creation posts the exact snake-case task payload', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { id: 'task-1' }
  const input = {
    name: 'Dataset task',
    source_root: 'C:\\source',
    output_root: 'D:\\output',
    components: { 'media.scan': { enabled: true, config: {} } },
  }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { createTask } = await tasksClient()

    assert.strictEqual(await createTask(input), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, JSON.stringify(input))
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('task control preserves action URLs, expected version, review gate, and terminate extras', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { id: 'task-1' }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { controlTask } = await tasksClient()

    assert.strictEqual(await controlTask('task-1', 'queue', 7), payload)
    assert.strictEqual(
      await controlTask('task-1', 'review-gate/release', 8, { expected_gate: 'evidence_review' }),
      payload,
    )
    assert.strictEqual(
      await controlTask('task-1', 'terminate', 9, { force: false, reason: 'WebUI request' }),
      payload,
    )
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/queue',
      '/api/tasks/task-1/review-gate/release',
      '/api/tasks/task-1/terminate',
    ])
    assert.deepEqual(calls.map(({ options }) => options.method), ['POST', 'POST', 'POST'])
    assert.deepEqual(calls.map(({ options }) => options.body), [
      '{"expected_version":7}',
      '{"expected_version":8,"expected_gate":"evidence_review"}',
      '{"expected_version":9,"force":false,"reason":"WebUI request"}',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('task deletion uses the exact DELETE URL and expected-version body', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { task_id: 'task-1', cache_cleared: true, cache_cleanup_error: null }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { deleteTask } = await tasksClient()

    assert.strictEqual(await deleteTask('task-1', 7), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks/task-1')
    assert.equal(calls[0].options.method, 'DELETE')
    assert.equal(calls[0].options.body, '{"expected_version":7}')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('task preset listing uses the exact default GET URL', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { items: [], total: 0 }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { listTaskPresets } = await presetsClient()

    assert.strictEqual(await listTaskPresets(), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/task-presets')
    assert.equal(calls[0].options.method, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('task preset creation and update use their exact methods and bodies', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { id: 'preset-1' }
  const components = { 'media.scan': { enabled: true, config: {} } }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { createTaskPreset, updateTaskPreset } = await presetsClient()

    assert.strictEqual(await createTaskPreset('Draft', components), payload)
    assert.strictEqual(await updateTaskPreset('preset-1', 'Updated', components, 7), payload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/task-presets',
      '/api/task-presets/preset-1',
    ])
    assert.deepEqual(calls.map(({ options }) => options.method), ['POST', 'PUT'])
    assert.deepEqual(calls.map(({ options }) => options.body), [
      '{"name":"Draft","components":{"media.scan":{"enabled":true,"config":{}}}}',
      '{"name":"Updated","components":{"media.scan":{"enabled":true,"config":{}}},"expected_version":7}',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('task preset deletion and from-task creation use their exact methods and bodies', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const deleted = { preset_id: 'preset-1' }
  const created = { id: 'preset-2' }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse([deleted, created][calls.length - 1])
    }
    const { createTaskPresetFromTask, deleteTaskPreset } = await presetsClient()

    assert.strictEqual(await deleteTaskPreset('preset-1', 7), deleted)
    assert.strictEqual(await createTaskPresetFromTask('task-1', 'From task'), created)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/task-presets/preset-1',
      '/api/task-presets/from-task/task-1',
    ])
    assert.deepEqual(calls.map(({ options }) => options.method), ['DELETE', 'POST'])
    assert.deepEqual(calls.map(({ options }) => options.body), [
      '{"expected_version":7}',
      '{"name":"From task"}',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('App delegates bootstrap and selected-task ownership while retaining workspace, preset, and control boundaries', async () => {
  const [app, bootstrapHook, selectedTaskDataHook, tasksPage, progressPage] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(APP_BOOTSTRAP_HOOK_PATH, 'utf8'),
    readFile(SELECTED_TASK_DATA_HOOK_PATH, 'utf8'),
    readFile(TASKS_PAGE_PATH, 'utf8'),
    readFile(PROGRESS_PAGE_PATH, 'utf8'),
  ])

  assert.doesNotMatch(app, /request<TaskList>\('\/api\/tasks\?limit=200'\)/)
  assert.doesNotMatch(app, /request<TaskEventList>\(`\/api\/tasks\/\$\{taskId\}\/events\?limit=200`\)/)
  assert.doesNotMatch(app, /request<Task>\(`\/api\/tasks\/\$\{taskId\}`\)/)
  assert.doesNotMatch(app, /\blistTasks\b/)
  assert.match(app, /from '\.\/hooks\/useAppBootstrap'/)
  assert.match(app, /tasks,\s*upsertTask,/)
  assert.doesNotMatch(app, /\blistTaskEvents\b/)
  assert.doesNotMatch(app, /\bgetTask\b/)
  assert.doesNotMatch(app, /request<TaskOverview>\(`\/api\/tasks\/\$\{taskId\}\/overview`\)/)
  assert.doesNotMatch(app, /request<FolderList>\(`\/api\/tasks\/\$\{taskId\}\/folders`\)/)
  assert.doesNotMatch(app, /\/api\/tasks\/\$\{taskId\}\/(?:overview|folders)/)
  assert.doesNotMatch(app, /\bgetTaskOverview\b/)
  assert.doesNotMatch(app, /\blistTaskFolders\b/)
  assert.match(bootstrapHook, /from '\.\.\/clients\/tasks'/)
  assert.match(bootstrapHook, /await listTasks\(\)/)
  assert.match(bootstrapHook, /data\.items\.filter\(isBuiltinProfileTask\)/)
  assert.match(bootstrapHook, /setTasks\(profileTasks\)/)
  assert.match(bootstrapHook, /reconcileSelectedTask\(profileTasks\)/)
  assert.match(selectedTaskDataHook, /from '\.\.\/clients\/tasks'/)
  assert.match(selectedTaskDataHook, /from '\.\.\/clients\/workspace'/)
  assert.match(selectedTaskDataHook, /listTaskEvents\(taskId\)/)
  assert.match(selectedTaskDataHook, /getTask\(taskId\)/)
  assert.match(selectedTaskDataHook, /getTaskOverview\(taskId\)/)
  assert.match(selectedTaskDataHook, /listTaskFolders\(taskId\)/)

  assert.doesNotMatch(tasksPage, /from '\.\.\/api'/)
  assert.doesNotMatch(tasksPage, /\/api\/task-presets/)
  assert.doesNotMatch(tasksPage, /request<Task>\('\/api\/tasks'/)
  assert.match(tasksPage, /controlTask\(task\.id, action, task\.row_version, extra\)/)
  assert.match(tasksPage, /deleteTask\(task\.id, task\.row_version\)/)
  assert.match(tasksPage, /listTaskPresets\(\)/)
  assert.match(tasksPage, /createTaskPreset\(cleaned, components, selectedBuiltinProfile!\.id\)/)
  assert.match(tasksPage, /updateTaskPreset\(selected\.id, cleaned, components, selected\.row_version, selectedBuiltinProfile!\.id\)/)
  assert.match(tasksPage, /deleteTaskPreset\(deletingPreset\.id, deletingPreset\.row_version\)/)
  assert.match(tasksPage, /createTask\(\{\s*name: name\.trim\(\)/)
  assert.match(tasksPage, /createTaskPresetFromTask\(task\.id, name\.trim\(\)\)/)
  assert.match(tasksPage, /profileTaskSubmissionComponents\(/)
  assert.match(tasksPage, /finally \{\s*setPresetBusy\(false\)/)

  assert.doesNotMatch(progressPage, /from '\.\.\/api'/)
  assert.doesNotMatch(progressPage, /\/api\/tasks\/\$\{task\.id\}\/\$\{action\}/)
  assert.match(progressPage, /controlTask\(task\.id, action, task\.row_version, extra\)/)
  assert.match(progressPage, /review-gate\/release/)
  assert.match(progressPage, /force: false, reason: 'WebUI request'/)
})
