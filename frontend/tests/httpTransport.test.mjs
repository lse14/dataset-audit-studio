import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const API_PATH = fileURLToPath(new URL('../src/api.ts', import.meta.url))
const CANONICAL_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)

async function canonicalHttp() {
  return import(CANONICAL_HTTP_URL.href)
}

test('jsonBody serializes an object into the exact body-only RequestInit fragment', async () => {
  const { jsonBody } = await canonicalHttp()

  assert.deepEqual(jsonBody({ enabled: true, count: 2 }), {
    body: '{"enabled":true,"count":2}',
  })
})

test('request forwards request options and returns the exact JSON payload', async () => {
  const originalFetch = globalThis.fetch
  const signal = new AbortController().signal
  const payload = { taskId: 'task-1' }
  const calls = []

  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return {
        ok: true,
        status: 200,
        json: async () => payload,
      }
    }
    const { request } = await canonicalHttp()
    const result = await request('/api/tasks', {
      method: 'POST',
      body: '{"source":"fixture"}',
      signal,
      headers: {
        'Content-Type': 'application/custom+json',
        'X-Request-Id': 'request-1',
      },
    })

    assert.strictEqual(result, payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"source":"fixture"}')
    assert.strictEqual(calls[0].options.signal, signal)
    assert.equal(calls[0].options.headers.get('Accept'), 'application/json')
    assert.equal(calls[0].options.headers.get('Content-Type'), 'application/custom+json')
    assert.equal(calls[0].options.headers.get('X-Request-Id'), 'request-1')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('request returns undefined for 204 without parsing JSON or adding Content-Type', async () => {
  const originalFetch = globalThis.fetch
  let jsonCalled = false
  let options

  try {
    globalThis.fetch = async (_path, receivedOptions) => {
      options = receivedOptions
      return {
        ok: true,
        status: 204,
        json: async () => {
          jsonCalled = true
          return { unexpected: true }
        },
      }
    }
    const { request } = await canonicalHttp()
    const result = await request('/api/tasks/task-1', {
      method: 'DELETE',
    })

    assert.strictEqual(result, undefined)
    assert.equal(jsonCalled, false)
    assert.equal(options.headers.get('Accept'), 'application/json')
    assert.equal(options.headers.get('Content-Type'), null)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('request raises canonical ApiError messages from detail, code, and HTTP fallback', async () => {
  const originalFetch = globalThis.fetch

  try {
    const { ApiError, request } = await canonicalHttp()
    const cases = [
      {
        response: {
          ok: false,
          status: 422,
          json: async () => ({ detail: 'profile is required', code: 'ignored-code' }),
        },
        status: 422,
        message: 'profile is required',
      },
      {
        response: {
          ok: false,
          status: 409,
          json: async () => ({ code: 'selection_conflict' }),
        },
        status: 409,
        message: 'selection_conflict',
      },
      {
        response: {
          ok: false,
          status: 503,
          json: async () => {
            throw new SyntaxError('not-json')
          },
        },
        status: 503,
        message: 'HTTP 503',
      },
    ]

    for (const item of cases) {
      globalThis.fetch = async () => item.response
      await assert.rejects(
        request('/api/tasks/task-1'),
        (error) => error instanceof ApiError && error.status === item.status && error.message === item.message,
      )
    }
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('legacy API facade is absent', () => {
  assert.equal(existsSync(API_PATH), false)
})
