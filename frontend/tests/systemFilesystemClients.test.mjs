import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const APP_BOOTSTRAP_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useAppBootstrap.ts', import.meta.url))
const TASKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/TasksPage.tsx', import.meta.url))
const UI_PATH = fileURLToPath(new URL('../src/ui.tsx', import.meta.url))
const SYSTEM_CLIENT_URL = new URL('../src/clients/system.ts', import.meta.url)
const FILESYSTEM_CLIENT_URL = new URL('../src/clients/filesystem.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)
const clientUrls = new Set([SYSTEM_CLIENT_URL.href, FILESYSTEM_CLIENT_URL.href])

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '../transport/http' && clientUrls.has(context.parentURL)) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function systemClient() {
  return import(SYSTEM_CLIENT_URL.href)
}

async function filesystemClient() {
  return import(FILESYSTEM_CLIENT_URL.href)
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  }
}

test('system client requests the exact health URL with the default GET and returns its payload', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { status: 'ok' }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { getSystemHealth } = await systemClient()

    assert.strictEqual(await getSystemHealth(), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/health')
    assert.equal(calls[0].options.method, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('listDirectories omits its query for undefined and blank paths', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { current: null, parent: null, entries: [] }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { listDirectories } = await filesystemClient()

    assert.strictEqual(await listDirectories(), payload)
    assert.strictEqual(await listDirectories('   '), payload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/filesystem/directories',
      '/api/filesystem/directories',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('listDirectories trims and URL-encodes a supplied path', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { current: 'C:\\work', parent: 'C:\\', entries: [] }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { listDirectories } = await filesystemClient()

    assert.strictEqual(await listDirectories(' C:\\work & review '), payload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/filesystem/directories?path=C%3A%5Cwork%20%26%20review',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('selectDirectory posts the exact snake-case payload and returns its response', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { cancelled: false, path: 'D:\\output' }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { selectDirectory } = await filesystemClient()

    assert.strictEqual(await selectDirectory('output', ' D:\\initial '), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/filesystem/select-directory')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"purpose":"output","initial_path":"D:\\\\initial"}')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('selectFile posts the model purpose and preserves the initial path', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const payload = { cancelled: false, path: 'D:\\models\\replacement.safetensors' }

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    const { selectFile } = await filesystemClient()

    assert.strictEqual(await selectFile('model', ' D:\\models\\old.safetensors '), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/filesystem/select-file')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"purpose":"model","initial_path":"D:\\\\models\\\\old.safetensors"}')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('App, bootstrap hook, and path fields use only their authorized endpoint ownership', async () => {
  const [app, appBootstrapHook, tasksPage, ui] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(APP_BOOTSTRAP_HOOK_PATH, 'utf8'),
    readFile(TASKS_PAGE_PATH, 'utf8'),
    readFile(UI_PATH, 'utf8'),
  ])

  assert.doesNotMatch(app, /\/api\/health/)
  assert.doesNotMatch(app, /from '\.\/clients\/system'/)
  assert.doesNotMatch(app, /setHealth\(await getSystemHealth\(\)\)/)
  assert.doesNotMatch(app, /window\.setInterval\(\(\) => void loadHealth\(\), 15000\)/)
  assert.match(app, /useAppBootstrap\(/)
  assert.match(appBootstrapHook, /getSystemHealth\(\)/)
  assert.match(appBootstrapHook, /\[health, setHealth\]/)
  assert.match(appBootstrapHook, /\[healthError, setHealthError\]/)
  assert.match(appBootstrapHook, /\[healthLoading, setHealthLoading\]/)
  assert.match(appBootstrapHook, /window\.setInterval\(\(\) => void reloadHealth\(\), 15000\)/)
  assert.match(appBootstrapHook, /return \(\) => window\.clearInterval\(timer\)/)

  assert.doesNotMatch(tasksPage, /\/api\/filesystem\/select-directory/)
  assert.match(tasksPage, /selectDirectory\(target, initialPath\)/)
  assert.match(tasksPage, /if \(picker !== null \|\| busy \|\| presetBusy\) return/)
  assert.match(tasksPage, /finally \{\s*setPicker\(null\)/)

  assert.doesNotMatch(ui, /\/api\/filesystem\/directories/)
  assert.doesNotMatch(ui, /from '\.\/api'/)
  assert.doesNotMatch(ui, /export function DirectoryPicker/)
  assert.doesNotMatch(ui, /listDirectories\(next\)/)
})
