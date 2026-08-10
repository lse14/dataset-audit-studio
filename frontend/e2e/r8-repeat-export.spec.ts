import { expect, test, type Page } from '@playwright/test'

const timestamp = '2026-08-05T00:00:00Z'

const completedTask = {
  config: {
    profile: 'general',
    components: {},
  },
  config_hash: 'a'.repeat(64),
  created_at: timestamp,
  current_config_revision: 1,
  error_code: null,
  error_message: null,
  execution_epoch: 0,
  finished_at: timestamp,
  id: 'task-r8',
  lease_expires_at: null,
  lease_owner: null,
  name: 'Completed export task',
  output_root: 'E:/old-output',
  progress_current: 10,
  progress_total: 10,
  resume_state: null,
  row_version: 2,
  source_root: 'E:/source',
  started_at: timestamp,
  status: 'completed',
  updated_at: timestamp,
}

const overview = {
  cluster_nodes: 3,
  evidence_codes: [],
  exports: [],
  latent_entries: 6,
  leaf_clusters: 4,
  ready_artifacts: 1,
  review_counts: [],
  samples_total: 10,
  samples_valid: 10,
  stages: [
    { stage: 1, resolution: 512, included: 8, excluded: 2, manual_excluded: 1 },
    { stage: 1, resolution: 768, included: 0, excluded: 10, manual_excluded: 0 },
    { stage: 1, resolution: 1024, included: 6, excluded: 4, manual_excluded: 0 },
    { stage: 1, resolution: 1216, included: 3, excluded: 7, manual_excluded: 0 },
    { stage: 1, resolution: 1536, included: 0, excluded: 10, manual_excluded: 0 },
  ],
}

const emptyReviewList = {
  approved_exclude: 0,
  approved_keep: 0,
  items: [],
  limit: 100,
  offset: 0,
  pending: 0,
  total: 0,
}

const manifests = [
  {
    activation: 'optional', config_schema: 'style.artist.v1', consumes: [], default_config: { lsnet_weight: 0.4, gram_weight: 0.4, dino_weight: 0.2 }, default_enabled: true, display_name: '画风分析', execution: 'cpu_inline', failure_policy: 'stop', id: 'style.artist', json_schema: { properties: { dino_weight: { maximum: 1, minimum: 0, type: 'number' }, gram_weight: { maximum: 1, minimum: 0, type: 'number' }, lsnet_weight: { maximum: 1, minimum: 0, type: 'number' } }, type: 'object' }, model_ids: [], phase_order: 1, produces: [], recommended_enabled: true, ui_group: 'analysis', version: '1.0.0',
  },
]

const profiles = [{
  components: {
    'style.artist': { config: { lsnet_weight: 0.4, gram_weight: 0.4, dino_weight: 0.2 }, enabled: true },
  },
  description: 'general profile',
  display_name: '通用数据',
  id: 'general',
  profile_owned_component_ids: [],
  profile_owned_config_fields: {},
  scope_mode: 'global',
}]

function exportRun(id: string, status: string, outputRoot: string) {
  return {
    aesthetic_identity: null,
    aesthetic_minimum: null,
    bytes_current: status === 'completed' ? 640 : 320,
    bytes_total: 640,
    checkpoint: {},
    completed_at: status === 'completed' ? timestamp : null,
    config_hash: completedTask.config_hash,
    created_at: timestamp,
    error_code: null,
    error_message: null,
    execution_epoch: 0,
    file_count: status === 'completed' ? 2 : 0,
    id,
    input_digest: 'b'.repeat(64),
    manifest_path: status === 'completed' ? `${outputRoot}/manifest.json` : null,
    manifest_sha256: status === 'completed' ? 'c'.repeat(64) : null,
    minimum_resolution: 1024,
    output_key: outputRoot.toLowerCase(),
    output_root: outputRoot,
    progress_current: status === 'completed' ? 2 : 1,
    progress_total: 2,
    resolutions: [1024, 1216],
    selection_version: 1,
    settings: {},
    started_at: timestamp,
    status,
    summary: status === 'completed' ? { total: { included: 2, manual_exclude: 1 }, by_resolution: { '1024': { included: 2, manual_exclude: 1 } } } : null,
    task_config_revision: 1,
    task_id: completedTask.id,
    updated_at: timestamp,
  }
}

function exportRunForTask(
  task: Pick<typeof completedTask, 'config_hash' | 'id'>,
  id: string,
  status: string,
  outputRoot: string,
) {
  return { ...exportRun(id, status, outputRoot), config_hash: task.config_hash, task_id: task.id }
}

async function installApiMock(page: Page) {
  const createdPayloads: unknown[] = []
  const runs = [exportRun('run-old', 'completed', 'E:/repeat-old')]
  let failRunListing = false
  let runListRequests = 0
  let activeRunListReads = 0
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const json = (value: unknown, status = 200) => route.fulfill({ body: JSON.stringify(value), contentType: 'application/json', status })
    const taskPath = `/api/tasks/${completedTask.id}`
    if (url.pathname === '/api/health') return json({ app_version: 'test', database: {}, models: {}, runtime: {}, status: 'ok', worker: { running: false } })
    if (url.pathname === '/api/components') return json({ items: manifests, total: manifests.length })
    if (url.pathname === '/api/components/builtin-profiles') return json({ items: profiles, total: profiles.length })
    if (url.pathname === '/api/task-presets') return json({ items: [], total: 0 })
    if (url.pathname === '/api/tasks' && request.method() === 'GET') return json({ items: [completedTask], total: 1, offset: 0, limit: 50 })
    if (url.pathname === taskPath) return json(completedTask)
    if (url.pathname === `${taskPath}/overview`) return json(overview)
    if (url.pathname === `${taskPath}/folders`) return json({ items: [] })
    if (url.pathname === `${taskPath}/events`) return json({ items: [], latest_sequence: 0, next_after: 0 })
    if (url.pathname === `${taskPath}/events/stream`) return route.fulfill({ body: '', contentType: 'text/event-stream' })
    if (url.pathname === `/api/components/runs/${completedTask.id}`) return json({ config_hash: completedTask.config_hash, items: [], task_id: completedTask.id, total: 0 })
    if (url.pathname === `${taskPath}/reviews/style`) return json(emptyReviewList)
    if (url.pathname === `${taskPath}/reviews/curated`) return json(emptyReviewList)
    if (url.pathname === `${taskPath}/export-runs` && request.method() === 'GET') {
      runListRequests += 1
      if (failRunListing) return route.fulfill({ body: '{"detail":"history unavailable"}', contentType: 'application/json', status: 503 })
      if (runs[0]?.status === 'copying') activeRunListReads += 1
      if (activeRunListReads >= 2 && runs[0]?.status === 'copying') {
        runs[0] = exportRun(runs[0].id, 'completed', runs[0].output_root)
      }
      return json({ items: runs, total: runs.length, offset: 0, limit: 50 })
    }
    if (url.pathname === `${taskPath}/export-runs/preview` && request.method() === 'POST') {
      const payload = request.postDataJSON()
      return json({ task_id: completedTask.id, minimum_resolution: payload.minimum_resolution, aesthetic_minimum: payload.aesthetic_minimum, minimum_folder_images: payload.minimum_folder_images, add_repeat_prefix: payload.add_repeat_prefix, sample_seen_mode: payload.sample_seen_mode, sample_seen_target: payload.sample_seen_target, preview_digest: 'a'.repeat(64), input_digest: 'b'.repeat(64), included_count: 2, exclusion_counts: {}, folder_below_minimum: { folder_count: 0, image_count: 0 }, folders: [], warnings: [] })
    }
    if (url.pathname === `${taskPath}/export-runs` && request.method() === 'POST') {
      createdPayloads.push(request.postDataJSON())
      activeRunListReads = 0
      const next = exportRun(`run-${runs.length + 1}`, 'copying', (createdPayloads.at(-1) as { output_root: string }).output_root)
      runs.unshift(next)
      return json(next, 202)
    }
    if (url.pathname === '/api/filesystem/select-directory' && request.method() === 'POST') return json({ cancelled: false, path: `E:/repeat-new-${createdPayloads.length + 1}` })
    return route.fulfill({ body: `Unhandled ${request.method()} ${url.pathname}`, status: 404 })
  })
  return {
    createdPayloads,
    get runListRequests() { return runListRequests },
    runs,
    setFailRunListing(value: boolean) { failRunListing = value },
  }
}

async function installTaskSwitchApiMock(page: Page) {
  const taskA = { ...completedTask, id: 'task-a', name: 'Completed export task A', output_root: 'E:/a-output' }
  const taskB = { ...completedTask, id: 'task-b', name: 'Completed export task B', output_root: 'E:/b-output' }
  const tasks = [taskA, taskB]
  const aRuns = [exportRunForTask(taskA, 'run-a-active', 'copying', 'E:/a-active')]
  const bRuns = [exportRunForTask(taskB, 'run-b-active', 'copying', 'E:/b-active')]
  let resolveAPost: (() => void) | null = null
  let resolveBList: (() => void) | null = null
  const aPostGate = new Promise<void>((resolve) => { resolveAPost = resolve })
  const bListGate = new Promise<void>((resolve) => { resolveBList = resolve })
  let aListReads = 0
  let aPostRequests = 0
  let bListFails = false
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const json = (value: unknown, status = 200) => route.fulfill({ body: JSON.stringify(value), contentType: 'application/json', status })
    if (url.pathname === '/api/health') return json({ app_version: 'test', database: {}, models: {}, runtime: {}, status: 'ok', worker: { running: false } })
    if (url.pathname === '/api/components') return json({ items: manifests, total: manifests.length })
    if (url.pathname === '/api/components/builtin-profiles') return json({ items: profiles, total: profiles.length })
    if (url.pathname === '/api/task-presets') return json({ items: [], total: 0 })
    if (url.pathname === '/api/tasks' && request.method() === 'GET') return json({ items: tasks, total: tasks.length, offset: 0, limit: 50 })
    for (const task of tasks) {
      const taskPath = `/api/tasks/${task.id}`
      if (url.pathname === taskPath) return json(task)
      if (url.pathname === `${taskPath}/overview`) return json(overview)
      if (url.pathname === `${taskPath}/folders`) return json({ items: [] })
      if (url.pathname === `${taskPath}/events`) return json({ items: [], latest_sequence: 0, next_after: 0 })
      if (url.pathname === `${taskPath}/events/stream`) return route.fulfill({ body: '', contentType: 'text/event-stream' })
      if (url.pathname === `/api/components/runs/${task.id}`) return json({ config_hash: task.config_hash, items: [], task_id: task.id, total: 0 })
      if (url.pathname === `${taskPath}/export-runs` && request.method() === 'GET') {
        if (task.id === taskA.id) {
          aListReads += 1
          return json({ items: aRuns, total: aRuns.length, offset: 0, limit: 50 })
        }
        await bListGate
        if (bListFails) return route.fulfill({ body: '{"detail":"B history unavailable"}', contentType: 'application/json', status: 503 })
        return json({ items: bRuns, total: bRuns.length, offset: 0, limit: 50 })
      }
      if (url.pathname === `${taskPath}/export-runs/preview` && request.method() === 'POST') return json({ task_id: task.id, minimum_resolution: 512, aesthetic_minimum: null, minimum_folder_images: 1, add_repeat_prefix: true, sample_seen_mode: 'off', sample_seen_target: null, preview_digest: 'a'.repeat(64), input_digest: 'b'.repeat(64), included_count: 1, exclusion_counts: {}, folder_below_minimum: { folder_count: 0, image_count: 0 }, folders: [], warnings: [] })
      if (url.pathname === `${taskPath}/export-runs` && request.method() === 'POST' && task.id === taskA.id) {
        aPostRequests += 1
        await aPostGate
        return json(exportRunForTask(taskA, 'run-a-late', 'copying', 'E:/a-late'), 202)
      }
    }
    if (url.pathname === '/api/filesystem/select-directory' && request.method() === 'POST') return json({ cancelled: false, path: 'E:/unused' })
    return route.fulfill({ body: `Unhandled ${request.method()} ${url.pathname}`, status: 404 })
  })
  return {
    get aListReads() { return aListReads },
    get aPostRequests() { return aPostRequests },
    releaseAPost() { resolveAPost?.() },
    releaseBList() { resolveBList?.() },
    setBListFails(value: boolean) { bListFails = value },
  }
}

test('completed task exposes a repeat-export creation workspace', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-r8'))
  await installApiMock(page)
  await page.goto('/#exports')
  await expect(page.getByRole('heading', { name: '创建重复导出' })).toBeVisible()
})

test('repeat export output picker backfills the selected native path', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-r8'))
  await installApiMock(page)
  await page.goto('/#exports')

  const input = page.getByRole('textbox', { name: '导出目录', exact: true })
  const picker = page.getByRole('button', { name: '选择导出目录', exact: true })
  await expect(input).toHaveValue('')
  await picker.click()
  await expect(input).toHaveValue('E:/repeat-new-1')
  await expect(picker).toBeEnabled()
})

test('audit and export routes each load their independent owner', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-r8'))
  await installApiMock(page)
  for (const [route, title] of [
    ['risks', '风险证据'],
    ['style', '画风审计'],
    ['duplicates', '重复审计'],
    ['aesthetics', '美学审计'],
    ['exports', '导出'],
  ] as const) {
    await page.goto(`/#${route}`)
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
  }
})

test('audit navigation follows workflow and keeps legacy bookmarks usable', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-r8'))
  await installApiMock(page)
  await page.goto('/#duplicates')

  const navigation = page.getByRole('navigation', { name: '主导航' })
  await expect(navigation.getByText('审计', { exact: true })).toBeVisible()
  await expect(navigation.getByRole('button')).toHaveText([
    '任务', '进度', '风险', '画风', '重复', '美学', '导出', '模型', '系统',
  ])
  await expect(page.getByRole('heading', { name: '重复审计' })).toBeVisible()
  const duplicateModes = page.getByRole('group', { name: '重复类型' })
  await expect(duplicateModes).toBeVisible()
  const modeWidths = await duplicateModes.evaluate((element) => ({
    client: element.clientWidth,
    scroll: element.scrollWidth,
  }))
  expect(modeWidths.scroll).toBeLessThanOrEqual(modeWidths.client)
  await expect(page.getByText('AI 候选')).toHaveCount(0)
  await expect(page.getByText('SAE 特征')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/r10-r101-recovery-audit-navigation-desktop-20260806-01.png' })

  await page.goto('/#reviews')
  await expect(page).toHaveURL(/#risks$/)
  await expect(page.getByRole('heading', { name: '风险证据' })).toBeVisible()
  await page.goto('/#clusters')
  await expect(page).toHaveURL(/#duplicates$/)
  await expect(page.getByRole('heading', { name: '重复审计' })).toBeVisible()

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(navigation.getByRole('button', { name: '重复', exact: true })).toBeVisible()
  const controlHeights = await page.locator('.review-toolbar > input, .review-toolbar > select').evaluateAll(
    (elements) => elements.map((element) => element.getBoundingClientRect().height),
  )
  expect(Math.max(...controlHeights)).toBeLessThanOrEqual(48)
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }))
  expect(widths.document).toBeLessThanOrEqual(widths.viewport)
  await page.screenshot({ path: 'test-results/r10-r101-recovery-audit-navigation-mobile-20260806-01.png' })
})

test('switching completed tasks isolates repeat-export state and preserves only current task history', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-a'))
  const api = await installTaskSwitchApiMock(page)
  await page.goto('/#exports')
  await expect(page.locator('.repeat-export-run').filter({ hasText: 'E:/a-active' })).toBeVisible()
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/a-form')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.locator('.repeat-export-panel').getByRole('button', { name: '创建重复导出' })).toBeEnabled()
  await page.locator('.repeat-export-panel').getByRole('button', { name: '创建重复导出' }).click()
  await expect.poll(() => api.aPostRequests).toBe(1)

  await page.locator('.task-selector select').selectOption('task-b')
  await expect(page.getByRole('textbox', { name: '导出目录', exact: true })).toHaveValue('')
  await expect(page.locator('.repeat-export-run').filter({ hasText: 'E:/a-active' })).toHaveCount(0)
  const aListReadsAtSwitch = api.aListReads
  await page.waitForTimeout(2300)
  expect(api.aListReads).toBe(aListReadsAtSwitch)

  api.releaseAPost()
  await expect(page.locator('.repeat-export-run').filter({ hasText: 'E:/a-late' })).toHaveCount(0)
  api.releaseBList()
  const bRun = page.locator('.repeat-export-run').filter({ hasText: 'E:/b-active' })
  await expect(bRun).toBeVisible()
  api.setBListFails(true)
  await expect(page.getByText('B history unavailable')).toBeVisible({ timeout: 6000 })
  await expect(bRun).toBeVisible()
})

test('new task creation rejects style artist weights that do not sum to one', async ({ page }) => {
  await installApiMock(page)
  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('Style validation')
  await page.getByRole('textbox', { name: '源数据目录', exact: true }).fill('E:/source')
  await page.getByRole('textbox', { name: /输出目录/ }).fill('E:/output')
  await page.getByLabel('数据集配置', { exact: true }).selectOption('general')
  await page.getByRole('button', { name: /^画风分析/ }).click()
  await page.getByLabel('LSNet 风格特征权重', { exact: true }).fill('0.2')
  await page.getByLabel('纹理特征权重', { exact: true }).fill('0.2')
  await page.getByLabel('语义特征权重', { exact: true }).fill('0.2')
  await expect(page.getByText('画风三项权重总和必须等于 1')).toBeVisible()
  await expect(page.getByRole('button', { name: '创建任务' })).toBeDisabled()
})

test('repeat export uses one selected tier, previews settings, preserves history, and stops polling after leaving', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-r8'))
  const api = await installApiMock(page)
  await page.goto('/#exports')
  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/repeat-new-1')
  await page.getByLabel('最低分辨率', { exact: true }).selectOption('1024')

  await page.getByLabel('启用美学最低分', { exact: true }).check()
  await page.getByRole('spinbutton', { name: '美学最低分', exact: true }).fill('1.25')
  await expect(page.getByText('美学最低分必须按 0.5 递进')).toBeVisible()
  await expect(page.locator('.repeat-export-panel').getByRole('button', { name: '创建重复导出' })).toBeDisabled()
  await page.getByRole('spinbutton', { name: '美学最低分', exact: true }).fill('3.5')
  await page.getByRole('button', { name: '预览导出' }).click()
  await expect(page.locator('.repeat-export-panel').getByRole('button', { name: '创建重复导出' })).toBeEnabled()
  await page.locator('.repeat-export-panel').getByRole('button', { name: '创建重复导出' }).click()
  await expect.poll(() => api.createdPayloads.length).toBe(1)
  expect(api.createdPayloads[0]).toMatchObject({ output_root: 'E:/repeat-new-1', minimum_resolution: 1024, aesthetic_minimum: 3.5, preview_digest: 'a'.repeat(64) })
  const firstRun = page.locator('.repeat-export-run').filter({ hasText: 'E:/repeat-new-1' })
  await expect(firstRun).toContainText('copying')
  await expect(firstRun.getByText('已完成')).toBeVisible({ timeout: 6000 })
  await expect(page.locator('.repeat-export-run').filter({ hasText: 'E:/repeat-old' })).toBeVisible()
  await page.locator('.repeat-export-panel').scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'test-results/r10-r101-recovery-repeat-export-desktop-20260806-01.png' })

  await page.getByRole('textbox', { name: '导出目录', exact: true }).fill('E:/repeat-new-2')
  await page.getByRole('button', { name: '预览导出' }).click()
  await page.locator('.repeat-export-panel').getByRole('button', { name: '创建重复导出' }).click()
  await expect.poll(() => api.createdPayloads.length).toBe(2)
  await expect(page.locator('.repeat-export-run').filter({ hasText: 'E:/repeat-new-2' })).toBeVisible()
  await expect(page.locator('.repeat-export-run')).toHaveCount(3)
  await expect(page.locator('.repeat-export-panel').getByRole('button', { name: /暂停|恢复|取消|复写/ })).toHaveCount(0)

  api.setFailRunListing(true)
  await expect(page.getByText('history unavailable')).toBeVisible({ timeout: 6000 })
  await expect(page.locator('.repeat-export-run').filter({ hasText: 'E:/repeat-old' })).toBeVisible()
  const requestsBeforeLeave = api.runListRequests
  await page.goto('/#tasks')
  await page.waitForTimeout(2300)
  expect(api.runListRequests).toBe(requestsBeforeLeave)
})

test('repeat export layout has no horizontal overflow at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-r8'))
  await installApiMock(page)
  await page.goto('/#exports')
  const panel = page.locator('.repeat-export-panel')
  await expect(panel).toBeVisible()
  const layout = await panel.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    viewportScrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }))
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth)
  expect(layout.viewportScrollWidth).toBeLessThanOrEqual(layout.viewportWidth)
  const toggleLayout = await panel.locator('.repeat-aesthetic-toggle').first().evaluate((element) => {
    const input = element.querySelector('input')?.getBoundingClientRect()
    const label = element.querySelector('span')?.getBoundingClientRect()
    return { gap: input && label ? label.left - input.right : Number.POSITIVE_INFINITY, inputWidth: input?.width ?? 0 }
  })
  expect(toggleLayout.inputWidth).toBeLessThanOrEqual(24)
  expect(toggleLayout.gap).toBeLessThanOrEqual(16)
  const createButton = panel.getByRole('button', { name: '创建重复导出' })
  await expect(createButton).toBeVisible()
  await expect(createButton).toHaveAccessibleName('创建重复导出')
  await expect(createButton).toContainText('创建重复导出')
  await createButton.scrollIntoViewIfNeeded()
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await page.screenshot({ path: 'test-results/r10-r101-recovery-repeat-export-mobile-20260806-01.png' })
  await createButton.screenshot({ path: 'test-results/r10-r101-recovery-repeat-export-mobile-button-20260806-01.png' })
})
