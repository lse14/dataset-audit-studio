import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const MODELS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ModelsPage.tsx', import.meta.url))
const MODELS_CLIENT_URL = new URL('../src/clients/models.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '../transport/http' && context.parentURL === MODELS_CLIENT_URL.href) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function modelClient() {
  return import(MODELS_CLIENT_URL.href)
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  }
}

async function withFetch(payloads, callback) {
  const originalFetch = globalThis.fetch
  const calls = []
  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payloads[calls.length - 1])
    }
    await callback(calls)
  } finally {
    globalThis.fetch = originalFetch
  }
}

test('model client owns only transport and type dependencies', async () => {
  const models = await modelClient()
  const source = await readFile(fileURLToPath(MODELS_CLIENT_URL), 'utf8')

  assert.match(source, /from '\.\.\/transport\/http'/)
  assert.match(source, /import type[\s\S]*from '\.\.\/types'/)
  assert.doesNotMatch(source, /from '\.\.\/api'/)
  assert.doesNotMatch(source, /from 'react'/)
  assert.doesNotMatch(source, /from '\.\.\/clients\//)
  assert.equal(typeof models.listModels, 'function')
  assert.equal(typeof models.runModelAction, 'function')
  assert.equal(typeof models.downloadAllModels, 'function')
  assert.equal(typeof models.registerLocalModel, 'function')
})

test('listModels uses the exact GET URL and returns its payload', async () => {
  const payload = { items: [], total: 0 }
  await withFetch([payload], async (calls) => {
    const { listModels } = await modelClient()

    assert.strictEqual(await listModels(), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/models?limit=200')
    assert.equal(calls[0].options.method, undefined)
    assert.equal(calls[0].options.headers.get('Accept'), 'application/json')
    assert.equal(calls[0].options.headers.get('Content-Type'), null)
  })
})

test('model actions preserve download JSON and verify or cancel empty POST options', async () => {
  await withFetch([{ status: 'queued' }, { status: 'verifying' }, { status: 'cancelled' }], async (calls) => {
    const { runModelAction } = await modelClient()

    await runModelAction('model-1', 'download')
    await runModelAction('model-1', 'verify')
    await runModelAction('model-1', 'cancel')

    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/models/model-1/download',
      '/api/models/model-1/verify',
      '/api/models/model-1/cancel',
    ])
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"include_dependencies":true}')
    assert.equal(calls[0].options.headers.get('Content-Type'), 'application/json')
    assert.equal(calls[1].options.method, 'POST')
    assert.equal(calls[1].options.body, undefined)
    assert.equal(calls[1].options.headers.get('Content-Type'), null)
    assert.equal(calls[2].options.method, 'POST')
    assert.equal(calls[2].options.body, undefined)
    assert.equal(calls[2].options.headers.get('Content-Type'), null)
  })
})

test('downloadAllModels uses the exact bodyless POST', async () => {
  await withFetch([{ queued: 12 }], async (calls) => {
    const { downloadAllModels } = await modelClient()

    await downloadAllModels()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/models/download-all')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, undefined)
    assert.equal(calls[0].options.headers.get('Content-Type'), null)
  })
})

test('registerLocalModel trims paths and preserves its snake-case body', async () => {
  const payload = { id: 'local-model', installation_status: 'ready' }
  await withFetch([payload], async (calls) => {
    const { registerLocalModel } = await modelClient()

    assert.strictEqual(
      await registerLocalModel('base-model', '  D:\\models\\local.safetensors  ', '   '),
      payload,
    )
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/models/local')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(
      calls[0].options.body,
      '{"base_model_id":"base-model","source_path":"D:\\\\models\\\\local.safetensors","display_name":null}',
    )
  })
})

test('ModelsPage delegates model ownership while the event hook owns the R5.12 boundary', async () => {
  const [app, modelsPage] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(MODELS_PAGE_PATH, 'utf8'),
  ])

  assert.doesNotMatch(modelsPage, /from '\.\.\/api'/)
  assert.doesNotMatch(modelsPage, /\/api\/models/)
  assert.match(modelsPage, /listModels\(\)/)
  assert.match(modelsPage, /runModelAction\(model\.id, name\)/)
  assert.match(modelsPage, /downloadAllModels\(\)/)
  assert.match(modelsPage, /registerLocalModel\(base, path, displayName\)/)
  assert.match(modelsPage, /selectFile\('model', path\)/)
  assert.match(modelsPage, /选择本地模型文件/)
  assert.match(modelsPage, /window\.setInterval\(\(\) => void load\(\), 2000\)/)
  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
})
