import { expect, test, type Page, type Route } from '@playwright/test'

const timestamp = '2026-08-07T00:00:00Z'
const digest = 'a'.repeat(64)

function task(id: string, status: string) {
  return {
    config: { profile: 'general', components: { 'export.dataset': { config: { mode: 'copy' }, enabled: true } } },
    config_hash: digest,
    created_at: timestamp,
    current_config_revision: 1,
    error_code: null,
    error_message: null,
    execution_epoch: 0,
    finished_at: status === 'completed' ? timestamp : null,
    id,
    lease_expires_at: null,
    lease_owner: null,
    name: id === 'task-first' ? '首次导出任务' : '重复导出任务',
    output_root: null,
    progress_current: 10,
    progress_total: 10,
    resume_state: null,
    row_version: 2,
    source_root: 'E:/source',
    started_at: timestamp,
    status,
    updated_at: timestamp,
  }
}

function run(id: string, taskId: string, outputRoot: string, status = 'queued') {
  return {
    id,
    task_id: taskId,
    task_config_revision: 1,
    config_hash: digest,
    selection_version: 1,
    output_root: outputRoot,
    output_key: outputRoot.toLowerCase(),
    minimum_resolution: 768,
    resolutions: [768],
    aesthetic_minimum: null,
    minimum_folder_images: 1,
    add_repeat_prefix: true,
    sample_seen_mode: 'off',
    sample_seen_target: null,
    preview_digest: digest,
    settings: {},
    aesthetic_identity: null,
    status,
    checkpoint: {},
    input_digest: digest,
    execution_epoch: 0,
    progress_current: 0,
    progress_total: null,
    bytes_current: 0,
    bytes_total: null,
    file_count: 0,
    manifest_path: null,
    manifest_sha256: null,
    summary: { included_count: 2, exclusion_counts: { manual_exclude: 1 }, warnings: ['repeat_approximate'] },
    error_code: null,
    error_message: null,
    created_at: timestamp,
    updated_at: timestamp,
    started_at: null,
    completed_at: null,
  }
}

async function installApiMock(page: Page) {
  const tasks = [task('task-first', 'evidence_review'), task('task-complete', 'completed')]
  const runs: Record<string, ReturnType<typeof run>[]> = { 'task-first': [], 'task-complete': [] }
  const previews: unknown[] = []
  const creates: unknown[] = []
  let failNextPreview = false
  const json = (route: Route, value: unknown, status = 200) => route.fulfill({ body: JSON.stringify(value), contentType: 'application/json', status })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/health') return json(route, { app_version: 'test', database: {}, models: {}, runtime: {}, status: 'ok', worker: { running: false } })
    if (url.pathname === '/api/components') return json(route, { items: [], total: 0 })
    if (url.pathname.startsWith('/api/components/runs/')) return json(route, { config_hash: digest, items: [], task_id: url.pathname.split('/').at(-1), total: 0 })
    if (url.pathname === '/api/task-presets') return json(route, { items: [], total: 0 })
    if (url.pathname === '/api/tasks' && request.method() === 'GET') return json(route, { items: tasks, total: tasks.length, offset: 0, limit: 200 })
    const match = url.pathname.match(/^\/api\/tasks\/([^/]+)(?:\/(.*))?$/)
    if (!match) return route.fulfill({ status: 404, body: 'not found' })
    const current = tasks.find((item) => item.id === match[1])
    if (!current) return route.fulfill({ status: 404, body: 'not found' })
    const suffix = match[2] ?? ''
    if (!suffix) return json(route, current)
    if (suffix === 'overview') return json(route, { cluster_nodes: 0, evidence_codes: [], leaf_clusters: 0, ready_artifacts: 0, review_counts: [], samples_total: 2, samples_valid: 2 })
    if (suffix === 'folders') return json(route, { items: [] })
    if (suffix === 'events') return json(route, { items: [], latest_sequence: 0, next_after: 0 })
    if (suffix === 'events/stream') return route.fulfill({ body: '', contentType: 'text/event-stream' })
    if (suffix === 'export-runs' && request.method() === 'GET') return json(route, { items: runs[current.id], total: runs[current.id].length, offset: 0, limit: 50 })
    if (suffix === 'export-runs/preview' && request.method() === 'POST') {
      previews.push(request.postDataJSON())
      if (failNextPreview) {
        failNextPreview = false
        return json(route, { detail: 'preview stale' }, 409)
      }
      return json(route, { task_id: current.id, minimum_resolution: 768, aesthetic_minimum: null, minimum_folder_images: 1, add_repeat_prefix: true, sample_seen_mode: 'off', sample_seen_target: null, preview_digest: digest, input_digest: digest, included_count: 2, exclusion_counts: { manual_exclude: 1 }, folder_below_minimum: { folder_count: 0, image_count: 0 }, folders: [{ source_identifier: 'artist-a', output_name: 'artist-a', image_count: 2, excluded: false, warning_codes: [] }], warnings: ['repeat_approximate'] })
    }
    if (suffix === 'export-runs' && request.method() === 'POST') {
      creates.push({ route: 'repeat', payload: request.postDataJSON() })
      const payload = request.postDataJSON() as { image_format?: string, output_root: string }
      const created = run(`run-${runs[current.id].length + 1}`, current.id, payload.output_root)
      created.settings = { image_format: payload.image_format ?? 'original' }
      runs[current.id].unshift(created)
      return json(route, created, 202)
    }
    if (suffix === 'review-gate/release' && request.method() === 'POST') {
      creates.push({ route: 'first', payload: request.postDataJSON() })
      const payload = request.postDataJSON() as { image_format?: string, output_root: string }
      const created = run('run-first', current.id, payload.output_root)
      created.settings = { image_format: payload.image_format ?? 'original' }
      runs[current.id].unshift(created)
      return json(route, created, 202)
    }
    return route.fulfill({ status: 404, body: 'not found' })
  })
  return { creates, previews, runs, failNextPreview: () => { failNextPreview = true } }
}

test('first copy export previews folders and releases the matching digest payload', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-first'))
  const api = await installApiMock(page)
  await page.goto('/#exports')
  await page.getByRole('button', { name: '稍后处理' }).click()
  await expect(page.getByRole('heading', { name: '配置首次导出' })).toBeVisible()
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/first-new')
  await expect(page.getByLabel('导出图像格式', { exact: true })).toHaveValue('original')
  await page.getByLabel('导出图像格式', { exact: true }).selectOption('webp')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.getByText('artist-a')).toBeVisible()
  expect(api.previews[0]).toMatchObject({ image_format: 'webp' })
  await page.screenshot({ path: 'test-results/r10-r1033-export-first-desktop-20260807-01.png' })
  await expect(page.getByRole('button', { name: '完成复核并创建导出' })).toBeEnabled()
  await page.getByRole('button', { name: '完成复核并创建导出' }).click()
  await expect.poll(() => api.creates.length).toBe(1)
  expect(api.creates[0]).toMatchObject({ route: 'first', payload: { expected_gate: 'evidence_review', output_root: 'E:/first-new', image_format: 'webp', preview_digest: digest } })
  await expect(page.locator('.repeat-export-run').getByText('WebP', { exact: true })).toBeVisible()
})

test('input changes invalidate preview and completed copy export uses the same form', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-complete'))
  const api = await installApiMock(page)
  await page.goto('/#exports')
  await expect(page.getByRole('heading', { name: '创建重复导出' })).toBeVisible()
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/repeat-new')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.getByText('artist-a')).toBeVisible()
  await page.getByLabel('最低分辨率', { exact: true }).selectOption('1024')
  await expect(page.getByRole('button', { name: '创建重复导出' })).toBeDisabled()
  await page.getByRole('button', { name: '预览导出' }).click()
  await page.getByRole('button', { name: '创建重复导出' }).click()
  await expect.poll(() => api.creates.length).toBe(1)
  expect(api.creates[0]).toMatchObject({ route: 'repeat', payload: { minimum_resolution: 1024, preview_digest: digest } })
})

test('preview errors invalidate an earlier digest and keep creation disabled', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-complete'))
  const api = await installApiMock(page)
  await page.goto('/#exports')
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/repeat-stale')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.getByText('artist-a')).toBeVisible()
  api.failNextPreview()
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.getByText('preview stale')).toBeVisible()
  await expect(page.getByRole('button', { name: '创建重复导出' })).toBeDisabled()
})

test('switching tasks clears export form state and the 390px layout does not overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-first'))
  await installApiMock(page)
  await page.goto('/#exports')
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/stale')
  await page.getByRole('combobox').first().selectOption('task-complete')
  await expect(page.getByRole('textbox', { name: '导出目录', exact: true })).toHaveValue('')
  const layout = await page.locator('.repeat-export-panel').evaluate((element) => ({ client: element.clientWidth, scroll: element.scrollWidth, viewport: document.documentElement.scrollWidth }))
  expect(layout.scroll).toBeLessThanOrEqual(layout.client)
  expect(layout.viewport).toBeLessThanOrEqual(390)
  await page.screenshot({ path: 'test-results/r10-r1033-export-mobile-20260807-01.png' })
})
