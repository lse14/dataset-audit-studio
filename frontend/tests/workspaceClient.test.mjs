import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const SELECTED_TASK_DATA_HOOK_PATH = fileURLToPath(new URL('../src/hooks/useSelectedTaskData.ts', import.meta.url))
const CLUSTERS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ClustersPage.tsx', import.meta.url))
const RISKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/RisksPage.tsx', import.meta.url))
const AUDIT_SUPPORT_PATH = fileURLToPath(new URL('../src/pages/auditPageSupport.tsx', import.meta.url))
const EXPORTS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ExportsPage.tsx', import.meta.url))
const MODELS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ModelsPage.tsx', import.meta.url))
const REVIEWS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ReviewsPage.tsx', import.meta.url))
const WORKSPACE_CLIENT_URL = new URL('../src/clients/workspace.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '../transport/http' && context.parentURL === WORKSPACE_CLIENT_URL.href) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function workspaceClient() {
  return import(WORKSPACE_CLIENT_URL.href)
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  }
}

async function withFetch(payload, callback) {
  const originalFetch = globalThis.fetch
  const calls = []
  try {
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options })
      return jsonResponse(payload)
    }
    await callback(calls)
  } finally {
    globalThis.fetch = originalFetch
  }
}

test('workspace client owns only transport and type dependencies', async () => {
  const client = await workspaceClient()
  const source = await readFile(fileURLToPath(WORKSPACE_CLIENT_URL), 'utf8')

  assert.match(source, /from '\.\.\/transport\/http'/)
  assert.match(source, /import type[\s\S]*from '\.\.\/types'/)
  assert.doesNotMatch(source, /from '\.\.\/api'/)
  assert.doesNotMatch(source, /from 'react'/)
  assert.equal(typeof client.getTaskOverview, 'function')
  assert.equal(typeof client.sampleThumbnailUrl, 'function')
})

test('workspace overview and folders use exact default GET URLs', async () => {
  const payload = { items: [] }
  await withFetch(payload, async (calls) => {
    const { getTaskOverview, listTaskFolders } = await workspaceClient()

    assert.strictEqual(await getTaskOverview('task-1'), payload)
    assert.strictEqual(await listTaskFolders('task-1'), payload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/overview',
      '/api/tasks/task-1/folders',
    ])
    assert.deepEqual(calls.map(({ options }) => options.method), [undefined, undefined])
  })
})

test('workspace coverage report uses the exact resolution URL', async () => {
  const payload = { status: 'ready', scopes: [] }
  await withFetch(payload, async (calls) => {
    const { getCoverageReport } = await workspaceClient()

    assert.strictEqual(await getCoverageReport('task-1', 1024), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks/task-1/coverage?resolution=1024')
    assert.equal(calls[0].options.method, undefined)
  })
})

test('workspace clusters preserve query order and folder encoding', async () => {
  const payload = { items: [] }
  await withFetch(payload, async (calls) => {
    const { listClusters } = await workspaceClient()

    assert.strictEqual(await listClusters('task-1', {
      offset: 25,
      limit: 50,
      folder: 'alpha / beta & gamma',
    }), payload)
    assert.equal(calls.length, 1)
    assert.equal(
      calls[0].path,
      '/api/tasks/task-1/clusters?offset=25&limit=50&folder=alpha%20%2F%20beta%20%26%20gamma',
    )
  })
})

test('workspace cluster samples preserve query order and folder encoding', async () => {
  const payload = { items: [] }
  await withFetch(payload, async (calls) => {
    const { listClusterSamples } = await workspaceClient()

    assert.strictEqual(await listClusterSamples('task-1', 'cluster-1', {
      offset: 100,
      limit: 200,
      folder: 'folder & review',
    }), payload)
    assert.equal(calls.length, 1)
    assert.equal(
      calls[0].path,
      '/api/tasks/task-1/clusters/cluster-1/samples?offset=100&limit=200&folder=folder%20%26%20review',
    )
  })
})

test('workspace risk reads preserve list and detail query behavior', async () => {
  const payload = { items: [] }
  await withFetch(payload, async (calls) => {
    const { getRiskSampleDetail, listRiskSamples } = await workspaceClient()

    assert.strictEqual(await listRiskSamples('task-1', {
      offset: 10,
      limit: 25,
      code: 'face / watermark',
      severity: 'high risk',
      folder: 'folder & review',
    }), payload)
    assert.strictEqual(await getRiskSampleDetail('task-1', 'sample-1', {
      code: 'face / watermark',
      severity: 'high risk',
    }), payload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/risk-samples?offset=10&limit=25&code=face%20%2F%20watermark&severity=high%20risk&folder=folder%20%26%20review',
      '/api/tasks/task-1/risk-samples/sample-1?code=face+%2F+watermark&severity=high+risk',
    ])
  })
})

test('workspace thumbnail builder returns the exact asset URL', async () => {
  const { sampleThumbnailUrl } = await workspaceClient()

  assert.equal(
    sampleThumbnailUrl('task-1', 'sample-1', 768),
    '/api/tasks/task-1/samples/sample-1/thumbnail?size=768',
  )
})

test('selected-task hook and independent audit pages delegate only the authorized workspace ownership', async () => {
  const [app, selectedTaskDataHook, clustersPage, risksPage, auditSupport, exportsPage, modelsPage, reviewsPage] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(SELECTED_TASK_DATA_HOOK_PATH, 'utf8'),
    readFile(CLUSTERS_PAGE_PATH, 'utf8'),
    readFile(RISKS_PAGE_PATH, 'utf8'),
    readFile(AUDIT_SUPPORT_PATH, 'utf8'),
    readFile(EXPORTS_PAGE_PATH, 'utf8'),
    readFile(MODELS_PAGE_PATH, 'utf8'),
    readFile(REVIEWS_PAGE_PATH, 'utf8'),
  ])

  assert.doesNotMatch(app, /from '\.\/api'/)
  assert.doesNotMatch(app, /\/api\/tasks\/\$\{taskId\}\/(?:overview|folders)/)
  assert.doesNotMatch(app, /\bgetTaskOverview\b/)
  assert.doesNotMatch(app, /\blistTaskFolders\b/)
  assert.match(app, /from '\.\/hooks\/useSelectedTaskData'/)
  assert.match(selectedTaskDataHook, /from '\.\.\/clients\/workspace'/)
  assert.match(selectedTaskDataHook, /getTaskOverview\(taskId\)/)
  assert.match(selectedTaskDataHook, /listTaskFolders\(taskId\)/)
  assert.match(selectedTaskDataHook, /Promise\.all\(\[/)

  for (const page of [clustersPage, risksPage]) {
    assert.doesNotMatch(page, /\/api\/tasks\/\$\{task\.id\}\/(?:coverage|clusters|risk-samples)/)
    assert.doesNotMatch(page, /\/api\/tasks\/\$\{task\?\.id\}\/clusters/)
    assert.doesNotMatch(page, /\/api\/tasks\/\$\{task\.id\}\/samples\/\$\{[^}]+\}\/thumbnail/)
    assert.doesNotMatch(page, /manual-exclusions/)
  }
  assert.match(clustersPage, /getCoverageReport\(task\.id, resolution\)/)
  assert.match(clustersPage, /listClusters\(task\.id, \{ offset, limit, folder \}\)/)
  assert.match(clustersPage, /listClusterSamples\(task\.id, detailCluster\.cluster_id/)
  assert.match(risksPage, /listRiskSamples\(task\.id, \{ offset, limit, code, severity, folder, decision \}\)/)
  assert.match(risksPage, /getRiskSampleDetail\(task\.id, detailSampleId, \{ code, severity \}\)/)
  assert.match(risksPage, /sampleThumbnailUrl\(task\.id, item\.sample_id, 224\)/)
  assert.doesNotMatch(risksPage, /watermark-review-threshold/)
  assert.doesNotMatch(exportsPage, /rewrite-backup\/restore/)
  assert.match(auditSupport, /updateManualExclusions\(task\.id, \{[\s\S]*sample_ids: sampleIds/)
  assert.match(risksPage, /setWatermarkReviewThreshold\(task\.id, threshold, task\.row_version\)/)
  assert.match(exportsPage, /restoreRewriteBackup\(task\.id, task\.row_version\)/)

  assert.doesNotMatch(reviewsPage, /\/api\/tasks\/\$\{taskId\}\/samples\/\$\{[^}]+\}\/thumbnail/)
  assert.match(reviewsPage, /sampleThumbnailUrl\(taskId, item\.sample_id, 320\)/)
  assert.match(reviewsPage, /sampleThumbnailUrl\(taskId, sample\.sample_id, 160\)/)
  assert.doesNotMatch(reviewsPage, /\/reviews\//)
  assert.match(reviewsPage, /listSaeFeatures\(task\.id, \{ offset, limit, folder \}\)/)
  assert.match(reviewsPage, /listCuratedReviews\(task\.id, \{[\s\S]*evidenceType: curatedEvidenceType/)
  assert.match(reviewsPage, /listReviewItems\(task\.id,\s*mode,\s*\{\s*offset,\s*limit,\s*decision,\s*folder,?\s*\}\)/)
  assert.doesNotMatch(modelsPage, /\/api\/models/)
  assert.match(modelsPage, /listModels\(\)/)
})
