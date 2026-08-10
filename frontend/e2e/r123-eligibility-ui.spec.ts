import { expect, test, type Page, type Route } from '@playwright/test'

const timestamp = '2026-08-07T00:00:00Z'
const digest = 'd'.repeat(64)

function task() {
  return {
    config: { profile: 'general', components: { 'export.dataset': { config: { mode: 'copy' }, enabled: true } } },
    config_hash: digest,
    created_at: timestamp,
    current_config_revision: 1,
    error_code: null,
    error_message: null,
    execution_epoch: 0,
    finished_at: timestamp,
    id: 'task-1',
    lease_expires_at: null,
    lease_owner: null,
    name: '已完成审核任务',
    output_root: null,
    progress_current: 4,
    progress_total: 4,
    resume_state: null,
    row_version: 3,
    source_root: 'E:/source',
    started_at: timestamp,
    status: 'completed',
    updated_at: timestamp,
  }
}

function styleAudit(decision: string) {
  return {
    approved_exclude: decision === 'approved_exclude' ? 1 : 0,
    approved_keep: decision === 'approved_keep' ? 1 : 0,
    items: [{
      artist_scope: 'artist-a',
      classification: 'strong_outlier',
      decision: decision === 'pending_review' ? null : decision,
      decision_source: decision === 'pending_review' ? 'automatic' : 'human',
      reason: 'style distance',
      relative_path: `style-${decision}.png`,
      review_eligible: true,
      sample_id: 'style-1',
      style_score: 0.91,
      threshold: 0.8,
    }],
    limit: 100,
    normal: 0,
    offset: 0,
    outlier: 0,
    pending: decision === 'pending_review' ? 1 : 0,
    strong_outlier: 1,
    total: 1,
  }
}

function aestheticAudit(decision: string) {
  return {
    approved_exclude: decision === 'approved_exclude' ? 1 : 0,
    approved_keep: decision === 'approved_keep' ? 1 : 0,
    bucket_counts: { '1.0': 0, '1.5': 0, '2.0': 0, '2.5': 1, '3.0': 0, '3.5': 0, '4.0': 0, '4.5': 0, '5.0': 0 },
    invalid_counts: { ambiguous: 0, missing: 0, non_finite: 0, out_of_range: 0, provenance_mismatch: 0 },
    items: [{
      artist_scope: 'artist-a',
      bucket: 2.5,
      decision: decision === 'pending_review' ? null : decision,
      decision_source: decision === 'pending_review' ? 'automatic' : 'human',
      reason_code: null,
      relative_path: `aesthetic-${decision}.png`,
      review_eligible: true,
      sample_id: 'aesthetic-1',
      score: 2.7,
    }],
    limit: 100,
    offset: 0,
    pending: decision === 'pending_review' ? 1 : 0,
    total: 1,
  }
}

function duplicateAudit(decision: string) {
  return {
    approved_exclude: decision === 'approved_exclude' ? 2 : 0,
    approved_keep: decision === 'approved_keep' ? 2 : 0,
    items: [{
      approved_exclude: decision === 'approved_exclude' ? 2 : 0,
      approved_keep: decision === 'approved_keep' ? 2 : 0,
      effective_retained_count: decision === 'approved_exclude' ? 0 : 2,
      evidence_type: 'exact_duplicate',
      group_key: 'exact-group',
      member_count: 2,
      members: ['duplicate-1', 'duplicate-2'].map((sampleId, index) => ({
        artist_scope: 'artist-a',
        decision: decision === 'pending_review' ? null : decision,
        decision_source: decision === 'pending_review' ? 'automatic' : 'human',
        relative_path: `duplicate-${index + 1}.png`,
        resolutions: [768],
        review_eligible: true,
        sample_id: sampleId,
        score: 1,
      })),
      pending: decision === 'pending_review' ? 2 : 0,
    }],
    limit: 100,
    offset: 0,
    pending: decision === 'pending_review' ? 2 : 0,
    total: 1,
    unresolved: 0,
  }
}

async function installApiMock(page: Page) {
  const requests: string[] = []
  const decisions: unknown[] = []
  const previews: unknown[] = []
  const creates: unknown[] = []
  const runs: unknown[] = []
  let failMedia = false
  const json = (route: Route, value: unknown, status = 200) => route.fulfill({ body: JSON.stringify(value), contentType: 'application/json', status })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    requests.push(`${request.method()} ${url.pathname}${url.search}`)
    if (url.pathname.includes('/samples/') && (url.pathname.endsWith('/thumbnail') || url.pathname.endsWith('/media'))) {
      if (url.pathname.endsWith('/media') && failMedia) return route.fulfill({ status: 404, body: 'missing media' })
      return route.fulfill({ body: '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"/>', contentType: 'image/svg+xml' })
    }
    if (url.pathname === '/api/health') return json(route, { app_version: 'test', database: {}, models: {}, runtime: {}, status: 'ok', worker: { running: false } })
    if (url.pathname === '/api/components') return json(route, { items: [], total: 0 })
    if (url.pathname.startsWith('/api/components/runs/')) return json(route, { config_hash: digest, items: [], task_id: 'task-1', total: 0 })
    if (url.pathname === '/api/task-presets') return json(route, { items: [], total: 0 })
    if (url.pathname === '/api/tasks' && request.method() === 'GET') return json(route, { items: [task()], total: 1, offset: 0, limit: 200 })
    if (url.pathname === '/api/tasks/task-1' && request.method() === 'GET') return json(route, task())
    const suffix = url.pathname.replace('/api/tasks/task-1/', '')
    if (suffix === 'overview') return json(route, { cluster_nodes: 0, evidence_codes: [{ count: 1, name: 'watermark_probability' }], leaf_clusters: 0, ready_artifacts: 0, review_counts: [], samples_total: 4, samples_valid: 4 })
    if (suffix === 'folders') return json(route, { items: [{ display_name: 'artist-a', folder_id: 'artist-a', sample_count: 4, risk_sample_count: 1 }] })
    if (suffix === 'events') return json(route, { items: [], latest_sequence: 0, next_after: 0 })
    if (suffix === 'events/stream') return route.fulfill({ body: '', contentType: 'text/event-stream' })
    if (suffix === 'reviews/curated') {
      const decision = url.searchParams.get('decision') ?? 'all'
      return json(route, { approved_exclude: 0, approved_keep: 0, items: [{ artist_scope: 'artist-a', candidate_group: null, decision: decision === 'all' || decision === 'pending_review' ? 'pending_review' : decision, decision_source: 'human', evidence_type: 'risk', reason_code: 'watermark_probability', relative_path: `risk-${decision}.png`, sample_id: 'risk-1', score: null, severity: 'high' }], limit: 100, offset: 0, pending: 1, total: 1 })
    }
    if (suffix === 'risk-samples') {
      const decision = url.searchParams.get('decision') ?? 'all'
      return json(route, { items: [{ artist_scope: 'artist-a', evidence_codes: ['watermark_probability'], evidence_count: 1, highest_severity: 'high', manually_excluded: false, relative_path: `risk-${decision}.png`, sample_id: 'risk-1' }], limit: 100, offset: 0, total: 1 })
    }
    if (suffix === 'risk-samples/risk-1') return json(route, { artist_scope: 'artist-a', evidence: [], manually_excluded: false, relative_path: 'risk-all.png', sample_id: 'risk-1' })
    if (suffix === 'reviews/style/audit') return json(route, styleAudit(url.searchParams.get('decision') ?? 'all'))
    if (suffix === 'reviews/aesthetic/audit') return json(route, aestheticAudit(url.searchParams.get('decision') ?? 'all'))
    if (suffix === 'reviews/duplicates/audit') return json(route, duplicateAudit(url.searchParams.get('decision') ?? 'all'))
    if (suffix.endsWith('/decisions') && request.method() === 'POST') {
      decisions.push({ path: suffix, payload: request.postDataJSON() })
      return json(route, { changed: 1, selected: 1 })
    }
    if (suffix === 'export-runs' && request.method() === 'GET') return json(route, { items: runs, total: runs.length, offset: 0, limit: 50 })
    if (suffix === 'export-runs/preview' && request.method() === 'POST') {
      const payload = request.postDataJSON() as { domain_minimum: number | null }
      previews.push(payload)
      if (payload.domain_minimum === 0.9) return json(route, { detail: 'domain proof unavailable' }, 409)
      return json(route, { add_repeat_prefix: true, aesthetic_minimum: null, domain_minimum: payload.domain_minimum, exclude_exact_visual_duplicates: payload.exclude_exact_visual_duplicates, exclusion_counts: { aesthetic_below_minimum: 0, domain_below_minimum: 1, duplicate_representative: 1, style_outlier: 1 }, folders: [], included_count: 2, input_digest: digest, minimum_folder_images: 1, minimum_resolution: 512, preview_digest: digest, sample_seen_mode: 'off', sample_seen_target: null, style_outlier_mode: payload.style_outlier_mode, warnings: ['duplicate representative selected'] })
    }
    if (suffix === 'export-runs' && request.method() === 'POST') {
      const payload = request.postDataJSON() as Record<string, unknown>
      creates.push(payload)
      const run = { add_repeat_prefix: true, aesthetic_identity: null, aesthetic_minimum: null, bytes_current: 0, bytes_total: null, checkpoint: {}, completed_at: null, config_hash: digest, created_at: timestamp, domain_minimum: payload.domain_minimum, error_code: null, error_message: null, exclude_exact_visual_duplicates: payload.exclude_exact_visual_duplicates, execution_epoch: 0, file_count: 0, id: 'run-1', input_digest: digest, manifest_path: null, manifest_sha256: null, minimum_folder_images: 1, minimum_resolution: 512, output_key: 'e:/new-output', output_root: 'E:/new-output', preview_digest: digest, progress_current: 0, progress_total: null, resolutions: [512], sample_seen_mode: 'off', sample_seen_target: null, selection_version: 1, settings: payload, started_at: null, status: 'queued', style_outlier_mode: payload.style_outlier_mode, summary: { exclusion_counts: { duplicate_representative: 1 }, included_count: 2, warnings: [] }, task_config_revision: 1, task_id: 'task-1', updated_at: timestamp }
      runs.unshift(run)
      return json(route, run, 202)
    }
    return route.fulfill({ status: 404, body: 'not found' })
  })
  return { creates, decisions, failMedia: () => { failMedia = true }, previews, requests }
}

async function dismissPrompt(page: Page) {
  const button = page.getByRole('button', { name: '稍后处理' })
  if (await button.isVisible()) await button.click()
}

test('completed task switches every audit state, preserves full duplicate groups, and supersedes excluded samples', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1'))
  const api = await installApiMock(page)
  await page.goto('/#style')
  await dismissPrompt(page)
  await page.getByRole('button', { name: '已排除' }).click()
  await expect(page.getByText('style-approved_exclude.png', { exact: true })).toBeVisible()
  await page.getByLabel('选择 style-approved_exclude.png', { exact: true }).check()
  await page.getByRole('button', { name: '撤销排除/保留' }).click()
  await page.getByRole('button', { name: '批准保留' }).click()
  await expect.poll(() => api.decisions).toContainEqual({ path: 'reviews/style/decisions', payload: { decision: 'approved_keep', sample_ids: ['style-1'] } })
  await page.getByRole('button', { name: '待复核' }).click()
  await expect.poll(() => api.requests.some((path) => path.includes('/reviews/style/audit?') && path.includes('decision=pending_review'))).toBe(true)

  await page.goto('/#risks')
  await page.getByRole('button', { name: '已保留' }).click()
  await expect.poll(() => api.requests.some((path) => path.includes('/reviews/curated?') && path.includes('evidence_type=risk') && path.includes('decision=approved_keep'))).toBe(true)
  await page.getByLabel('证据类型', { exact: true }).selectOption('watermark_probability')
  await expect.poll(() => api.requests.some((path) => path.includes('reason_code=watermark_probability'))).toBe(true)

  await page.goto('/#duplicates')
  await page.getByRole('button', { name: '已排除' }).click()
  await expect(page.getByText('duplicate-1.png', { exact: true })).toBeVisible()
  await expect(page.getByText('duplicate-2.png', { exact: true })).toBeVisible()
  await page.getByLabel('选择 duplicate-1.png', { exact: true }).check()
  await page.getByRole('button', { name: '撤销排除/保留' }).click()
  await page.getByRole('button', { name: '批准保留' }).click()
  await expect.poll(() => api.decisions.some((entry) => JSON.stringify(entry).includes('duplicate-1'))).toBe(true)

  await page.goto('/#aesthetics')
  await page.getByRole('button', { name: '已保留' }).click()
  await expect.poll(() => api.requests.some((path) => path.includes('/reviews/aesthetic/audit?') && path.includes('decision=approved_keep'))).toBe(true)

  for (const route of ['#risks', '#style', '#duplicates', '#aesthetics']) {
    await page.goto(`/${route}`)
    await dismissPrompt(page)
    for (const label of ['全部', '待复核', '已保留', '已排除']) {
      await page.getByRole('button', { name: label, exact: true }).click()
      await expect(page.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-pressed', 'true')
    }
  }
})

test('audit thumbnails use the shared task/sample media viewer without conflicting with risk evidence detail', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1'))
  const api = await installApiMock(page)
  await page.goto('/#style')
  await dismissPrompt(page)
  await page.getByRole('button', { name: '查看原图 style-all.png' }).click()
  const viewer = page.getByRole('dialog', { name: '原图查看' })
  await expect(viewer).toBeVisible()
  await expect(viewer.locator('img')).toHaveAttribute('src', '/api/tasks/task-1/samples/style-1/media')
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(viewer).toHaveCount(0)

  await page.goto('/#risks')
  await page.getByRole('button', { name: '查看原图 risk-all.png', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '原图查看' })).toBeVisible()
  await expect(page.getByRole('dialog', { name: '风险详情' })).toHaveCount(0)
  await expect.poll(() => api.requests.some((path) => path.includes('/samples/risk-1/media'))).toBe(true)

  await page.goto('/#duplicates')
  await dismissPrompt(page)
  api.failMedia()
  await page.getByRole('button', { name: '查看原图 duplicate-1.png' }).click()
  await expect(page.getByRole('dialog', { name: '原图查看' }).getByRole('alert')).toContainText('无法加载原图')
})

test('copy export defaults to disabled eligibility filters, invalidates preview, and sends identical first/repeat settings', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1'))
  const api = await installApiMock(page)
  await page.goto('/#exports')
  await dismissPrompt(page)
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/new-output')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect.poll(() => api.previews.length).toBe(1)
  expect(api.previews[0]).toMatchObject({ domain_minimum: null, exclude_exact_visual_duplicates: false, style_outlier_mode: 'off', aesthetic_minimum: null })
  await page.getByLabel('启用目标域最低分', { exact: true }).check()
  await expect(page.getByRole('button', { name: '创建重复导出' })).toBeDisabled()
  await page.getByRole('spinbutton', { name: '目标域最低分', exact: true }).fill('0.9')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.getByText('domain proof unavailable')).toBeVisible()
  await expect(page.getByRole('button', { name: '创建重复导出' })).toBeDisabled()
  await page.getByRole('spinbutton', { name: '目标域最低分', exact: true }).fill('0.6')
  await page.getByLabel('排除完全和视觉重复', { exact: true }).check()
  await page.getByLabel('画风离群筛选', { exact: true }).selectOption('all')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.getByText('duplicate_representative 1')).toBeVisible()
  await page.getByRole('button', { name: '创建重复导出' }).click()
  await expect.poll(() => api.creates.length).toBe(1)
  expect(api.creates[0]).toMatchObject({ domain_minimum: 0.6, exclude_exact_visual_duplicates: true, style_outlier_mode: 'all', preview_digest: digest })
  await expect(page.getByText('筛除设置')).toBeVisible()
  await expect(page.getByText('目标域 0.6，重复 完全和视觉，画风 离群与强离群')).toBeVisible()
  await expect(page.getByText('导出 2，duplicate_representative 1')).toBeVisible()
  await page.locator('.repeat-export-history').screenshot({ path: 'test-results/r12-r123-export-history-desktop.png' })
  await page.locator('.repeat-export-panel').scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'test-results/r12-r123-export-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.locator('html').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/r12-r123-export-mobile.png' })
})
