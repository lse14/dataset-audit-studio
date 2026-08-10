import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const AUDIT_SUPPORT_PATH = fileURLToPath(new URL('../src/pages/auditPageSupport.tsx', import.meta.url))
const RISKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/RisksPage.tsx', import.meta.url))
const EXPORTS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ExportsPage.tsx', import.meta.url))
const TASKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/TasksPage.tsx', import.meta.url))
const PROGRESS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ProgressPage.tsx', import.meta.url))
const MODELS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ModelsPage.tsx', import.meta.url))
const REVIEWS_PAGE_PATH = fileURLToPath(new URL('../src/pages/ReviewsPage.tsx', import.meta.url))
const STYLE_PAGE_PATH = fileURLToPath(new URL('../src/pages/StylePage.tsx', import.meta.url))
const DUPLICATES_PAGE_PATH = fileURLToPath(new URL('../src/pages/DuplicatesPage.tsx', import.meta.url))
const AESTHETICS_PAGE_PATH = fileURLToPath(new URL('../src/pages/AestheticsPage.tsx', import.meta.url))
const UI_PATH = fileURLToPath(new URL('../src/ui.tsx', import.meta.url))
const REVIEWS_CLIENT_URL = new URL('../src/clients/reviews.ts', import.meta.url)
const EXPORTS_CLIENT_URL = new URL('../src/clients/exports.ts', import.meta.url)
const EXPORT_RUNS_CLIENT_URL = new URL('../src/clients/exportRuns.ts', import.meta.url)
const TASK_CLIENT_URL = new URL('../src/clients/tasks.ts', import.meta.url)
const WORKSPACE_CLIENT_URL = new URL('../src/clients/workspace.ts', import.meta.url)
const TRANSPORT_HTTP_URL = new URL('../src/transport/http.ts', import.meta.url)
const clientUrls = new Set([REVIEWS_CLIENT_URL.href, EXPORTS_CLIENT_URL.href, EXPORT_RUNS_CLIENT_URL.href, TASK_CLIENT_URL.href, WORKSPACE_CLIENT_URL.href])

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '../transport/http' && clientUrls.has(context.parentURL)) {
      return { shortCircuit: true, url: TRANSPORT_HTTP_URL.href }
    }
    return nextResolve(specifier, context)
  },
})

async function reviewsClient() {
  return import(REVIEWS_CLIENT_URL.href)
}

async function exportsClient() {
  return import(EXPORTS_CLIENT_URL.href)
}

async function exportRunsClient() {
  return import(EXPORT_RUNS_CLIENT_URL.href)
}

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

test('review and export clients own only transport and type dependencies', async () => {
  const [reviews, exports] = await Promise.all([reviewsClient(), exportsClient()])
  const [reviewsSource, exportsSource] = await Promise.all([
    readFile(fileURLToPath(REVIEWS_CLIENT_URL), 'utf8'),
    readFile(fileURLToPath(EXPORTS_CLIENT_URL), 'utf8'),
  ])

  for (const source of [reviewsSource, exportsSource]) {
    assert.match(source, /from '\.\.\/transport\/http'/)
    assert.doesNotMatch(source, /from '\.\.\/api'/)
    assert.doesNotMatch(source, /from 'react'/)
    assert.doesNotMatch(source, /from '\.\.\/clients\//)
  }
  assert.match(reviewsSource, /import type[\s\S]*from '\.\.\/types'/)
  assert.equal(typeof reviews.updateManualExclusions, 'function')
  assert.equal(typeof exports.restoreRewriteBackup, 'function')
})

test('manual exclusions use the exact POST body and return payload', async () => {
  const payload = { selected: 2, changed: 1, excluded: true }
  await withFetch([payload], async (calls) => {
    const { updateManualExclusions } = await reviewsClient()

    assert.strictEqual(await updateManualExclusions('task-1', {
      sample_ids: ['sample-1', 'sample-2'],
      excluded: true,
      context: { page: 'risks', folder_id: 'folder-1' },
    }), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks/task-1/manual-exclusions')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"sample_ids":["sample-1","sample-2"],"excluded":true,"context":{"page":"risks","folder_id":"folder-1"}}')
  })
})

test('watermark threshold uses the exact POST body and returns payload', async () => {
  const payload = { threshold: 0.75, updated: 3, candidates: 2 }
  await withFetch([payload], async (calls) => {
    const { setWatermarkReviewThreshold } = await reviewsClient()

    assert.strictEqual(await setWatermarkReviewThreshold('task-1', 0.75, 7), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks/task-1/watermark-review-threshold')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"threshold":0.75,"expected_version":7}')
  })
})

test('SAE review listing preserves the exact query order and folder encoding', async () => {
  const payload = { items: [], total: 0 }
  await withFetch([payload], async (calls) => {
    const { listSaeFeatures } = await reviewsClient()

    assert.strictEqual(await listSaeFeatures('task-1', {
      offset: 25,
      limit: 100,
      folder: 'alpha / beta & gamma',
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/reviews/sae/features?offset=25&limit=100&folder=alpha%20%2F%20beta%20%26%20gamma')
    assert.equal(calls[0].options.method, undefined)
  })
})

test('curated review listing preserves URLSearchParams order and encoding', async () => {
  const payload = { items: [], total: 0 }
  await withFetch([payload], async (calls) => {
    const { listCuratedReviews } = await reviewsClient()

    assert.strictEqual(await listCuratedReviews('task-1', {
      evidenceType: 'duplicate',
      limit: 100,
      offset: 50,
      decision: 'pending review',
      folder: 'alpha / beta & gamma',
      severity: 'high risk',
      candidateGroup: 'group / A',
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/reviews/curated?evidence_type=duplicate&limit=100&offset=50&decision=pending+review&folder=alpha+%2F+beta+%26+gamma&severity=high+risk&candidate_group=group+%2F+A')
    assert.equal(calls[0].options.method, undefined)
  })
})

test('AI and style review listing preserve the existing query assembly', async () => {
  const aiPayload = { items: [], total: 0 }
  const stylePayload = { items: [], total: 0 }
  await withFetch([aiPayload, stylePayload], async (calls) => {
    const { listReviewItems } = await reviewsClient()

    assert.strictEqual(await listReviewItems('task-1', 'ai', {
      offset: 0,
      limit: 100,
      decision: 'pending_review',
      folder: 'folder & review',
    }), aiPayload)
    assert.strictEqual(await listReviewItems('task-1', 'style', {
      offset: 100,
      limit: 100,
      decision: '',
    }), stylePayload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/reviews/ai?offset=0&limit=100&decision=pending_review&folder=folder%20%26%20review',
      '/api/tasks/task-1/reviews/style?offset=100&limit=100',
    ])
  })
})

test('style audit client uses the read-only audit route', async () => {
  const payload = {
    items: [],
    total: 0,
    normal: 0,
    outlier: 0,
    strong_outlier: 0,
    pending: 0,
    approved_keep: 0,
    approved_exclude: 0,
    offset: 0,
    limit: 100,
  }
  await withFetch([payload], async (calls) => {
    const { listStyleAudit } = await reviewsClient()
    assert.strictEqual(await listStyleAudit('task-1', {
      offset: 0,
      limit: 100,
      folder: 'artist / one',
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/reviews/style/audit?offset=0&limit=100&folder=artist+%2F+one')
  })
})

test('all audit clients forward an explicit active decision filter', async () => {
  const payload = { items: [], total: 0 }
  await withFetch([payload, payload, payload, payload], async (calls) => {
    const [reviews, workspace] = await Promise.all([reviewsClient(), workspaceClient()])
    await reviews.listStyleAudit('task-1', { offset: 0, limit: 100, decision: 'approved_keep' })
    await reviews.listAestheticAudit('task-1', { offset: 0, limit: 100, decision: 'approved_exclude' })
    await reviews.listDuplicateGroupAudit('task-1', { evidenceType: 'exact_duplicate', offset: 0, limit: 100, decision: 'pending_review' })
    await workspace.listRiskSamples('task-1', { offset: 0, limit: 100, decision: 'approved_exclude' })
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/reviews/style/audit?offset=0&limit=100&decision=approved_keep',
      '/api/tasks/task-1/reviews/aesthetic/audit?offset=0&limit=100&decision=approved_exclude',
      '/api/tasks/task-1/reviews/duplicates/audit?evidence_type=exact_duplicate&offset=0&limit=100&decision=pending_review',
      '/api/tasks/task-1/risk-samples?offset=0&limit=100&decision=approved_exclude',
    ])
  })
})

test('media URLs accept only task and sample identifiers', async () => {
  const { sampleMediaUrl } = await workspaceClient()
  assert.equal(sampleMediaUrl('task / one', 'sample & two'), '/api/tasks/task%20%2F%20one/samples/sample%20%26%20two/media')
})

test('aesthetic audit client uses the all-sample read-only route with exact filters', async () => {
  const payload = {
    approved_exclude: 0,
    approved_keep: 0,
    bucket_counts: { '1.0': 0 },
    invalid_counts: { ambiguous: 0 },
    items: [],
    limit: 100,
    offset: 100,
    pending: 0,
    total: 0,
  }
  await withFetch([payload], async (calls) => {
    const { listAestheticAudit } = await reviewsClient()
    assert.strictEqual(await listAestheticAudit('task-1', {
      bucket: 2.5,
      folder: 'artist / one',
      limit: 100,
      offset: 100,
      reasonCode: 'provenance_mismatch',
    }), payload)
    assert.equal(
      calls[0].path,
      '/api/tasks/task-1/reviews/aesthetic/audit?offset=100&limit=100&folder=artist+%2F+one&bucket=2.5&reason_code=provenance_mismatch',
    )
  })
})

test('duplicate group audit client uses the read-only group route', async () => {
  const payload = {
    approved_exclude: 0,
    approved_keep: 0,
    items: [],
    limit: 100,
    offset: 0,
    pending: 0,
    total: 0,
    unresolved: 0,
  }
  await withFetch([payload], async (calls) => {
    const { listDuplicateGroupAudit } = await reviewsClient()
    assert.strictEqual(await listDuplicateGroupAudit('task-1', {
      evidenceType: 'visual_duplicate',
      folder: 'artist / one',
      limit: 100,
      offset: 0,
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/reviews/duplicates/audit?evidence_type=visual_duplicate&offset=0&limit=100&folder=artist+%2F+one')
  })
})

test('StylePage owns independent audit markup', async () => {
  const [stylePage, reviewsPage] = await Promise.all([
    readFile(STYLE_PAGE_PATH, 'utf8'),
    readFile(REVIEWS_PAGE_PATH, 'utf8'),
  ])
  assert.match(stylePage, /listStyleAudit\(/)
  assert.match(stylePage, /review_eligible/)
  assert.match(stylePage, /strong_outlier/)
  assert.doesNotMatch(stylePage, /<ReviewsPage/)
  assert.doesNotMatch(reviewsPage, /surface="style"/)
})

test('DuplicatesPage owns the group audit view and does not wrap ReviewsPage', async () => {
  const [duplicatesPage, reviewsPage] = await Promise.all([
    readFile(DUPLICATES_PAGE_PATH, 'utf8'),
    readFile(REVIEWS_PAGE_PATH, 'utf8'),
  ])
  assert.match(duplicatesPage, /listDuplicateGroupAudit\(/)
  assert.match(duplicatesPage, /effective_retained_count/)
  assert.match(duplicatesPage, /approved_exclude/)
  assert.doesNotMatch(duplicatesPage, /<ReviewsPage/)
  assert.doesNotMatch(reviewsPage, /surface="duplicates"/)
})

test('AestheticsPage owns the all-sample audit view and does not wrap ReviewsPage', async () => {
  const [aestheticsPage, reviewsPage] = await Promise.all([
    readFile(AESTHETICS_PAGE_PATH, 'utf8'),
    readFile(REVIEWS_PAGE_PATH, 'utf8'),
  ])
  assert.match(aestheticsPage, /listAestheticAudit\(/)
  assert.match(aestheticsPage, /bucket_counts/)
  assert.match(aestheticsPage, /invalid_counts/)
  assert.match(aestheticsPage, /review_eligible/)
  assert.doesNotMatch(aestheticsPage, /<ReviewsPage/)
  assert.doesNotMatch(reviewsPage, /surface="aesthetics"/)
})

test('curated review decisions preserve their exact POST body order', async () => {
  const payload = { updated: 2 }
  await withFetch([payload], async (calls) => {
    const { submitCuratedReviewDecisions } = await reviewsClient()

    assert.strictEqual(await submitCuratedReviewDecisions('task-1', {
      decision: 'approved_exclude',
      evidence_type: 'risk',
      sample_ids: ['sample-1', 'sample-2'],
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/reviews/curated/decisions')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"decision":"approved_exclude","evidence_type":"risk","sample_ids":["sample-1","sample-2"]}')
  })
})

test('AI and style review decisions preserve their exact POST body order', async () => {
  const aiPayload = { updated: 1 }
  const stylePayload = { updated: 2 }
  await withFetch([aiPayload, stylePayload], async (calls) => {
    const { submitReviewDecisions } = await reviewsClient()

    assert.strictEqual(await submitReviewDecisions('task-1', 'ai', {
      sample_ids: ['sample-1'],
      decision: 'approved_keep',
    }), aiPayload)
    assert.strictEqual(await submitReviewDecisions('task-1', 'style', {
      sample_ids: ['sample-2'],
      decision: 'approved_exclude',
    }), stylePayload)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/reviews/ai/decisions',
      '/api/tasks/task-1/reviews/style/decisions',
    ])
    assert.deepEqual(calls.map(({ options }) => options.body), [
      '{"sample_ids":["sample-1"],"decision":"approved_keep"}',
      '{"sample_ids":["sample-2"],"decision":"approved_exclude"}',
    ])
  })
})

test('rewrite backup restore uses the exact POST body and returns payload', async () => {
  const payload = { restored_files: 3 }
  await withFetch([payload], async (calls) => {
    const { restoreRewriteBackup } = await exportsClient()

    assert.strictEqual(await restoreRewriteBackup('task-1', 7), payload)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path, '/api/tasks/task-1/rewrite-backup/restore')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"expected_version":7}')
  })
})

test('export-run client uses the only two backend routes with exact payload and pagination', async () => {
  const created = { id: 'run-1' }
  const listed = { items: [created], total: 1, offset: 20, limit: 25 }
  await withFetch([created, listed], async (calls) => {
    const { createExportRun, listExportRuns } = await exportRunsClient()

    assert.strictEqual(await createExportRun('task-1', {
      output_root: 'E:/exports/run-1',
      minimum_resolution: 768,
      domain_minimum: 0.6,
      exclude_exact_visual_duplicates: true,
      style_outlier_mode: 'all',
      aesthetic_minimum: null,
      minimum_folder_images: 3,
      add_repeat_prefix: true,
      sample_seen_mode: 'manual',
      sample_seen_target: 12,
      preview_digest: 'a'.repeat(64),
    }), created)
    assert.strictEqual(await listExportRuns('task-1', { offset: 20, limit: 25 }), listed)
    assert.deepEqual(calls.map(({ path }) => path), [
      '/api/tasks/task-1/export-runs',
      '/api/tasks/task-1/export-runs?offset=20&limit=25',
    ])
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"output_root":"E:/exports/run-1","minimum_resolution":768,"domain_minimum":0.6,"exclude_exact_visual_duplicates":true,"style_outlier_mode":"all","aesthetic_minimum":null,"minimum_folder_images":3,"add_repeat_prefix":true,"sample_seen_mode":"manual","sample_seen_target":12,"preview_digest":"' + 'a'.repeat(64) + '"}')
    assert.equal(calls[1].options.method, undefined)
  })
})

test('export-run client previews the exact single-dataset settings', async () => {
  const payload = { preview_digest: 'b'.repeat(64), folders: [], warnings: [] }
  await withFetch([payload], async (calls) => {
    const { previewExportRun } = await exportRunsClient()
    assert.strictEqual(await previewExportRun('task-1', {
      output_root: 'E:/exports/run-1',
      minimum_resolution: 1024,
      domain_minimum: 0.7,
      exclude_exact_visual_duplicates: true,
      style_outlier_mode: 'strong',
      aesthetic_minimum: 3.5,
      minimum_folder_images: 2,
      add_repeat_prefix: false,
      sample_seen_mode: 'auto',
      sample_seen_target: null,
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/export-runs/preview')
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.body, '{"output_root":"E:/exports/run-1","minimum_resolution":1024,"domain_minimum":0.7,"exclude_exact_visual_duplicates":true,"style_outlier_mode":"strong","aesthetic_minimum":3.5,"minimum_folder_images":2,"add_repeat_prefix":false,"sample_seen_mode":"auto","sample_seen_target":null}')
  })
})

test('first copy release uses the complete preview-bound payload', async () => {
  const payload = { id: 'run-first' }
  await withFetch([payload], async (calls) => {
    const { releaseCopyExport } = await import('../src/clients/tasks.ts')
    assert.strictEqual(await releaseCopyExport('task-1', 9, 'evidence_review', {
      output_root: 'E:/exports/first',
      minimum_resolution: 512,
      domain_minimum: null,
      exclude_exact_visual_duplicates: false,
      style_outlier_mode: 'off',
      aesthetic_minimum: null,
      minimum_folder_images: 1,
      add_repeat_prefix: true,
      sample_seen_mode: 'off',
      sample_seen_target: null,
      preview_digest: 'c'.repeat(64),
    }), payload)
    assert.equal(calls[0].path, '/api/tasks/task-1/review-gate/release')
    assert.equal(calls[0].options.body, '{"expected_version":9,"expected_gate":"evidence_review","output_root":"E:/exports/first","minimum_resolution":512,"domain_minimum":null,"exclude_exact_visual_duplicates":false,"style_outlier_mode":"off","aesthetic_minimum":null,"minimum_folder_images":1,"add_repeat_prefix":true,"sample_seen_mode":"off","sample_seen_target":null,"preview_digest":"' + 'c'.repeat(64) + '"}')
  })
})

test('first and repeat copy exports send identical eligibility settings', async () => {
  const payload = { id: 'run-1' }
  const settings = {
    output_root: 'E:/exports/shared',
    minimum_resolution: 768,
    domain_minimum: 0.6,
    exclude_exact_visual_duplicates: true,
    style_outlier_mode: 'strong',
    aesthetic_minimum: 3.5,
    minimum_folder_images: 2,
    add_repeat_prefix: true,
    sample_seen_mode: 'off',
    sample_seen_target: null,
    preview_digest: 'd'.repeat(64),
  }
  await withFetch([payload, payload], async (calls) => {
    const { releaseCopyExport } = await import('../src/clients/tasks.ts')
    const { createExportRun } = await exportRunsClient()
    await releaseCopyExport('task-1', 9, 'evidence_review', settings)
    await createExportRun('task-1', settings)
    const first = JSON.parse(calls[0].options.body)
    const repeat = JSON.parse(calls[1].options.body)
    delete first.expected_version
    delete first.expected_gate
    assert.deepEqual(first, repeat)
  })
})

test('R12.3 pages use shared media viewing, active decisions, and preview-bound eligibility settings', async () => {
  const [risks, style, duplicates, aesthetics, exportsPage, ui] = await Promise.all([
    readFile(RISKS_PAGE_PATH, 'utf8'),
    readFile(STYLE_PAGE_PATH, 'utf8'),
    readFile(DUPLICATES_PAGE_PATH, 'utf8'),
    readFile(AESTHETICS_PAGE_PATH, 'utf8'),
    readFile(EXPORTS_PAGE_PATH, 'utf8'),
    readFile(UI_PATH, 'utf8'),
  ])
  for (const page of [risks, style, duplicates, aesthetics]) {
    assert.match(page, /decision/)
    assert.match(page, /SampleMediaViewer/)
    assert.match(page, /approved_exclude/)
  }
  assert.match(risks, /submitCuratedReviewDecisions\(task\.id/)
  assert.match(exportsPage, /domain_minimum/)
  assert.match(exportsPage, /exclude_exact_visual_duplicates/)
  assert.match(exportsPage, /style_outlier_mode/)
  assert.match(ui, /export function SampleMediaViewer/)
  assert.match(ui, /sampleMediaUrl\(taskId, sampleId\)/)
  assert.doesNotMatch(ui, /relative_path.*media|media.*relative_path/)
})

test('independent audit/export pages and ReviewsPage delegate client ownership while model and event-hook boundaries remain', async () => {
  const [app, auditSupport, risksPage, exportsPage, modelsPage, reviewsPage] = await Promise.all([
    readFile(APP_PATH, 'utf8'),
    readFile(AUDIT_SUPPORT_PATH, 'utf8'),
    readFile(RISKS_PAGE_PATH, 'utf8'),
    readFile(EXPORTS_PAGE_PATH, 'utf8'),
    readFile(MODELS_PAGE_PATH, 'utf8'),
    readFile(REVIEWS_PAGE_PATH, 'utf8'),
  ])

  for (const page of [auditSupport, risksPage, exportsPage]) {
    assert.doesNotMatch(page, /from '\.\.\/api'/)
    assert.doesNotMatch(page, /\/api\/tasks\/\$\{task\.id\}\/(?:manual-exclusions|watermark-review-threshold|rewrite-backup\/restore)/)
  }
  assert.match(auditSupport, /updateManualExclusions\(task\.id, \{[\s\S]*sample_ids: sampleIds/)
  assert.match(risksPage, /setWatermarkReviewThreshold\(task\.id, threshold, task\.row_version\)/)
  assert.match(exportsPage, /restoreRewriteBackup\(task\.id, task\.row_version\)/)
  assert.match(exportsPage, /previewExportRun\(task\.id, input\)/)
  assert.match(exportsPage, /preview\.preview_digest/)
  assert.match(exportsPage, /minimum_folder_images/)
  assert.match(exportsPage, /sample_seen_mode/)
  assert.doesNotMatch(exportsPage, /overview\.stages/)
  assert.doesNotMatch(exportsPage, /overview\.exports/)
  assert.doesNotMatch(exportsPage, /by_resolution/)
  const [tasksPage, progressPage] = await Promise.all([
    readFile(TASKS_PAGE_PATH, 'utf8'),
    readFile(PROGRESS_PAGE_PATH, 'utf8'),
  ])
  assert.doesNotMatch(tasksPage, /分辨率档位 \$\{resolution\}/)
  assert.match(tasksPage, /isCopyExportTask\(task\)/)
  assert.match(progressPage, /isCopyExportTask\(task\)/)
  assert.match(exportsPage, /window\.confirm/)
  assert.match(risksPage, /Number\.isFinite\(threshold\)/)

  assert.doesNotMatch(reviewsPage, /from '\.\.\/api'/)
  assert.doesNotMatch(reviewsPage, /\/api\/tasks\/\$\{task\.id\}\/reviews\//)
  assert.match(reviewsPage, /listSaeFeatures\(task\.id, \{ offset, limit, folder \}\)/)
  assert.match(reviewsPage, /listCuratedReviews\(task\.id, \{[\s\S]*evidenceType: curatedEvidenceType/)
  assert.match(reviewsPage, /listReviewItems\(task\.id,\s*mode,\s*\{\s*offset,\s*limit,\s*decision,\s*folder,?\s*\}\)/)
  assert.match(reviewsPage, /submitCuratedReviewDecisions\(task\.id, \{[\s\S]*decision: confirm\.decision/)
  assert.match(reviewsPage, /submitReviewDecisions\(task\.id, mode, \{[\s\S]*sample_ids: \[\.\.\.selected\]/)
  assert.match(reviewsPage, /mode === 'sae'/)
  assert.match(reviewsPage, /sampleThumbnailUrl\(taskId, item\.sample_id, 320\)/)

  assert.doesNotMatch(modelsPage, /from '\.\.\/api'/)
  assert.doesNotMatch(modelsPage, /\/api\/models/)
  assert.match(modelsPage, /listModels\(\)/)
  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
})
