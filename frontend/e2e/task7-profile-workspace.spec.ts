import { expect, test, type Page } from '@playwright/test'

type ProfileId = 'artist_concept' | 'character_concept' | 'general'

const timestamp = '2026-08-01T00:00:00Z'
const expectedRoot = Buffer.from('5f5f726f6f745f5f', 'hex').toString('ascii')

function profileComponents(profile: ProfileId) {
  return {
    'export.dataset': {
      config: {
        aesthetic_bins: 'disabled',
        keep_annotation_files: true,
        keep_latent_files: true,
      },
      enabled: true,
    },
    'media.scan': {
      config: { recursive: false, resolutions: [512, 768, 1024, 1216, 1536] },
      enabled: true,
    },
  }
}

function task(
  profile: ProfileId,
  status = 'completed',
  resumeState: string | null = null,
) {
  return {
    config: { profile, components: profileComponents(profile) },
    config_hash: 'a'.repeat(64),
    created_at: timestamp,
    current_config_revision: 1,
    error_code: null,
    error_message: null,
    execution_epoch: 0,
    finished_at: null,
    id: 'task-7',
    lease_expires_at: null,
    lease_owner: null,
    name: 'Profile task',
    output_root: 'E:/output',
    progress_current: 3,
    progress_total: 3,
    resume_state: resumeState,
    row_version: 1,
    source_root: 'E:/source',
    started_at: timestamp,
    status,
    updated_at: timestamp,
  }
}

function legacyTask() {
  return {
    ...task('artist_concept'),
    config: {},
    id: 'legacy-task',
    name: 'Legacy task',
  } as ReturnType<typeof task>
}

const manifests = [
  {
    activation: 'required',
    config_schema: 'media.scan.v1',
    consumes: [],
    default_config: { recursive: false, resolutions: [512, 768, 1024, 1216, 1536] },
    default_enabled: true,
    display_name: '媒体扫描',
    execution: 'cpu_inline',
    failure_policy: 'stop',
    id: 'media.scan',
    json_schema: {
      properties: {
        recursive: { type: 'boolean' },
        resolutions: { items: { type: 'integer' }, type: 'array' },
      },
      type: 'object',
    },
    model_ids: [],
    phase_order: 1,
    produces: [],
    recommended_enabled: true,
    ui_group: 'input',
    version: '1.0.0',
  },
  {
    activation: 'required',
    config_schema: 'export.dataset.v1',
    consumes: [],
    default_config: {
      aesthetic_bins: 'disabled',
      keep_annotation_files: true,
      keep_latent_files: true,
    },
    default_enabled: true,
    display_name: '数据集导出',
    execution: 'cpu_inline',
    failure_policy: 'stop',
    id: 'export.dataset',
    json_schema: {
      properties: {
        aesthetic_bins: { enum: ['disabled', 'score_x2_floor'], type: 'string' },
        keep_annotation_files: { type: 'boolean' },
        keep_latent_files: { type: 'boolean' },
      },
      type: 'object',
    },
    model_ids: [],
    phase_order: 2,
    produces: [],
    recommended_enabled: true,
    ui_group: 'output',
    version: '1.0.0',
  },
]

const profiles = (['artist_concept', 'character_concept', 'general'] as const).map((id) => ({
  components: profileComponents(id),
  description: `${id} profile`,
  display_name: id,
  id,
  profile_owned_component_ids: [],
  profile_owned_config_fields: {},
  scope_mode: id === 'general' ? 'global' : 'concept',
}))

const overview = {
  cluster_nodes: 1,
  evidence_codes: [],
  exports: [],
  latent_entries: 0,
  leaf_clusters: 1,
  ready_artifacts: 1,
  review_counts: [],
  samples_total: 1,
  samples_valid: 1,
  stages: [],
}

const health = {
  app_version: 'test',
  database: {},
  models: {},
  runtime: {},
  status: 'ok',
  worker: {},
}

function coverage(scopeId = expectedRoot, profile: ProfileId = 'artist_concept') {
  return {
    coverage_type: 'visual_semantic_coverage_proxy',
    identity_assessment: 'not_performed',
    profile,
    resolution: 512,
    schema_version: 'coverage-report/v1',
    scope_count: 1,
    scope_size_distribution_status: 'available',
    scope_size_histogram: [2],
    scopes: [{
      bottom_half_leaf_sample_share: 1,
      broad_sample_count: 2,
      embedding_count: 2,
      embedding_status: 'available',
      hierarchy_status: 'available',
      largest_leaf_sample_share: 1,
      leaf_assigned_count: 2,
      leaf_count: 1,
      leaf_coverage_status: 'available',
      leaf_size_histogram: [2],
      missing_embedding_count: 0,
      scope_id: scopeId,
      single_leaf: true,
      singleton_leaf_count: 0,
      singleton_sample_share: 0,
      style_summary: null,
      top_five_leaf_sample_share: 1,
      unassigned_count: 0,
    }],
    single_leaf_scope_count: 1,
    single_leaf_scope_share: 1,
    single_leaf_scope_status: 'available',
    status: 'ready',
  }
}

async function installApiMock(
  page: Page,
  currentTask = task('artist_concept'),
  options: {
    coverageError?: boolean
    createdTask?: ReturnType<typeof task>
    controlTask?: ReturnType<typeof task>
  } = {},
) {
  const createdPayloads: unknown[] = []
  const exclusionPayloads: unknown[] = []
  const curatedDecisionPayloads: unknown[] = []
  let listedTask = currentTask

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const json = (value: unknown) => route.fulfill({
      body: JSON.stringify(value),
      contentType: 'application/json',
    })
    const taskPath = `/api/tasks/${currentTask.id}`

    if (url.pathname === '/api/health') return json(health)
    if (url.pathname === '/api/components') return json({ items: manifests, total: manifests.length })
    if (url.pathname === '/api/components/builtin-profiles') return json({ items: profiles, total: profiles.length })
    if (url.pathname === '/api/task-presets') return json({ items: [], total: 0 })
    if (url.pathname === '/api/tasks' && request.method() === 'GET') {
      return json({ items: [listedTask], limit: 50, offset: 0, total: 1 })
    }
    if (url.pathname === '/api/tasks' && request.method() === 'POST') {
      createdPayloads.push(request.postDataJSON())
      return json(options.createdTask ?? currentTask)
    }
    if (url.pathname === `${taskPath}/queue` && request.method() === 'POST') {
      const controlledTask = options.controlTask ?? currentTask
      listedTask = controlledTask
      return json(controlledTask)
    }
    if (url.pathname === taskPath) return json(currentTask)
    if (url.pathname === `${taskPath}/overview`) return json(overview)
    if (url.pathname === `${taskPath}/folders`) return json({ items: [] })
    if (url.pathname === `${taskPath}/events`) return json({ items: [], latest_sequence: 0, next_after: 0 })
    if (url.pathname === `${taskPath}/events/stream`) {
      return route.fulfill({ body: '', contentType: 'text/event-stream' })
    }
    if (url.pathname === `/api/components/runs/${currentTask.id}`) {
      return json({ config_hash: currentTask.config_hash, items: [], task_id: currentTask.id, total: 0 })
    }
    if (url.pathname === `${taskPath}/coverage`) {
      if (options.coverageError) return route.fulfill({ body: '{"detail":"coverage unavailable"}', status: 503 })
      const profile = currentTask.config.profile
      return json(coverage(profile === 'general' ? '__global__' : expectedRoot, profile))
    }
    if (url.pathname === `${taskPath}/clusters`) return json({ items: [], limit: 100, offset: 0, total: 0 })
    if (url.pathname === `${taskPath}/risk-samples`) {
      return json({
        items: [{
          artist_scope: expectedRoot,
          evidence_codes: ['watermark_probability'],
          evidence_count: 1,
          highest_severity: 'medium',
          manually_excluded: false,
          relative_path: 'sample.png',
          sample_id: 'sample-1',
        }],
        limit: 100,
        offset: 0,
        total: 1,
      })
    }
    if (url.pathname === `${taskPath}/manual-exclusions` && request.method() === 'POST') {
      const payload = request.postDataJSON()
      exclusionPayloads.push(payload)
      return json({ changed: 1, excluded: payload.excluded, selected: 1 })
    }
    if (url.pathname === `${taskPath}/reviews/duplicates/audit` && request.method() === 'GET') {
      const evidenceType = url.searchParams.get('evidence_type') ?? 'exact_duplicate'
      return json({
        approved_exclude: 0,
        approved_keep: 0,
        items: [{
          approved_exclude: 0,
          approved_keep: 0,
          effective_retained_count: 1,
          evidence_type: evidenceType,
          group_key: 'curated-group',
          member_count: 2,
          members: [{
            artist_scope: expectedRoot,
            decision: null,
            decision_source: 'automatic',
            relative_path: 'curated-sample.png',
            resolutions: [512],
            review_eligible: true,
            sample_id: 'curated-sample',
            score: 0.9,
          }, {
            artist_scope: expectedRoot,
            decision: null,
            decision_source: 'automatic',
            relative_path: 'curated-peer.png',
            resolutions: [512],
            review_eligible: true,
            sample_id: 'curated-peer',
            score: 0.8,
          }],
          pending: 1,
        }],
        limit: 100,
        offset: 0,
        pending: 1,
        total: 1,
        unresolved: 0,
      })
    }
    if (url.pathname === `${taskPath}/reviews/curated` && request.method() === 'GET') {
      return json({
        approved_exclude: 0,
        approved_keep: 0,
        items: [{
          artist_scope: expectedRoot,
          candidate_group: 'visual-group',
          decision: 'pending_review',
          decision_created_at: null,
          decision_id: null,
          decision_source: 'automatic',
          evidence_type: url.searchParams.get('evidence_type') ?? 'aesthetic',
          reason_code: 'duplicate_visual',
          relative_path: 'curated-sample.png',
          sample_id: 'curated-sample',
          score: 0.9,
          severity: 'medium',
        }],
        limit: 100,
        offset: 0,
        pending: 1,
        total: 1,
      })
    }
    if (url.pathname === `${taskPath}/reviews/sae/features` && request.method() === 'GET') {
      return json({
        cache_key: 'b'.repeat(64),
        items: [{
          feature_id: 0,
          representative_samples: [{
            relative_path: 'scope/sample.png',
            sample_id: 'sample-1',
          }],
          threshold: 0.25,
          top_sample_ids: ['sample-1'],
        }],
        limit: 100,
        offset: 0,
        total: 1,
      })
    }
    if (url.pathname === `${taskPath}/reviews/curated/decisions` && request.method() === 'POST') {
      const payload = request.postDataJSON()
      curatedDecisionPayloads.push(payload)
      return json({ changed: 1, decision: payload.decision, selected: 1 })
    }
    return route.fulfill({ body: `Unhandled API request: ${request.method()} ${url.pathname}`, status: 404 })
  })

  return { createdPayloads, exclusionPayloads, curatedDecisionPayloads }
}

test('Task 7 profile creation keeps resolution tiers internal and exposes general-only export bins', async ({ page }) => {
  const api = await installApiMock(page)
  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('Profile export')
  await page.getByRole('textbox', { name: '源数据目录', exact: true }).fill('E:/source')
  await page.getByRole('textbox', { name: /输出目录/ }).fill('E:/output')

  await page.getByLabel('数据集配置', { exact: true }).selectOption('artist_concept')
  await expect(page.getByText('全量工作区')).toBeVisible()
  await expect(page.getByLabel('按美学评分分档', { exact: true })).toHaveCount(0)
  await page.getByLabel('数据集配置', { exact: true }).selectOption('general')
  await expect(page.getByLabel('按美学评分分档', { exact: true })).not.toBeChecked()
  await page.getByLabel('按美学评分分档', { exact: true }).check()
  await expect(page.getByLabel('按美学评分分档', { exact: true })).toBeChecked()
  await page.getByLabel('数据集配置', { exact: true }).selectOption('artist_concept')
  await expect(page.getByLabel(/分辨率档位/)).toHaveCount(0)
  await page.screenshot({ path: 'test-results/r10-r101-recovery-task7-desktop-20260806-01.png' })
  await page.getByRole('button', { name: '创建任务' }).click()
  await expect.poll(() => api.createdPayloads.length).toBe(1)
  expect(api.createdPayloads[0]).toMatchObject({
    components: { 'media.scan': { config: { resolutions: [512, 768, 1024, 1216, 1536] } } },
  })
  expect((api.createdPayloads[0] as { components: Record<string, { config: Record<string, unknown> }> })
    .components['export.dataset'].config).not.toHaveProperty('aesthetic_bins')
})

test('only the general profile exposes the dedicated score_x2_floor control', async ({ page }) => {
  await installApiMock(page)
  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByLabel('数据集配置', { exact: true }).selectOption('artist_concept')
  await page.getByRole('button', { name: /^数据集导出/ }).click()

  await expect(page.getByText('aesthetic bins', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('按美学评分分档', { exact: true })).toHaveCount(0)

  await page.getByLabel('数据集配置', { exact: true }).selectOption('character_concept')
  await expect(page.getByText('aesthetic bins', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('按美学评分分档', { exact: true })).toHaveCount(0)

  await page.getByLabel('数据集配置', { exact: true }).selectOption('general')
  await expect(page.getByText('aesthetic bins', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('按美学评分分档', { exact: true })).not.toBeChecked()
})

test('profile-free tasks cannot enter the active frontend flow', async ({ page }) => {
  const legacyRequests: string[] = []
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (pathname.startsWith('/api/tasks/legacy-task')) legacyRequests.push(pathname)
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'legacy-task')
  })
  await installApiMock(page, legacyTask())
  await page.goto('/#tasks')

  await expect(page.getByText('Legacy task', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('任务').locator('option[value="legacy-task"]')).toHaveCount(0)
  await expect(page.getByLabel('任务')).toHaveValue('')
  await page.waitForTimeout(100)
  expect(legacyRequests).toEqual([])
})

test('stored built-in profile tasks restore into the active frontend workspace', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  await installApiMock(page)
  await page.goto('/#clusters')

  await expect(page.getByLabel('任务')).toHaveValue('task-7')
  await expect(page).toHaveURL(/#duplicates$/)
  await expect(page.getByRole('heading', { name: '重复审计' })).toBeVisible()
})

test('profile-free create responses cannot enter the active frontend flow', async ({ page }) => {
  const legacyRequests: string[] = []
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (pathname.startsWith('/api/tasks/legacy-task')) legacyRequests.push(pathname)
  })
  const api = await installApiMock(page, task('general'), { createdTask: legacyTask() })
  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('Invalid create response')
  await page.getByRole('textbox', { name: '源数据目录', exact: true }).fill('E:/source')
  await page.getByRole('textbox', { name: /输出目录/ }).fill('E:/output')
  await page.getByLabel('数据集配置', { exact: true }).selectOption('artist_concept')
  await page.getByRole('button', { name: '创建任务' }).click()

  await expect.poll(() => api.createdPayloads.length).toBe(1)
  await expect(page.getByText('Legacy task', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('任务').locator('option[value="legacy-task"]')).toHaveCount(0)
  await expect(page.getByLabel('任务')).toHaveValue('')
  await page.waitForTimeout(100)
  expect(legacyRequests).toEqual([])
})

test('profile-free control responses cannot enter the active frontend flow', async ({ page }) => {
  let recordLegacyRequests = false
  const legacyRequests: string[] = []
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (
      recordLegacyRequests
      && pathname.startsWith('/api/tasks/legacy-task')
      && pathname !== '/api/tasks/legacy-task/queue'
    ) {
      legacyRequests.push(pathname)
    }
  })
  const activeTask = {
    ...task('general', 'draft'),
    id: 'legacy-task',
    name: 'Active profile task',
  } as ReturnType<typeof task>
  await installApiMock(page, activeTask, { controlTask: legacyTask() })
  await page.goto('/#tasks')
  await page.getByRole('button', { name: /Active profile task/ }).click()
  await expect(page.getByLabel('任务')).toHaveValue('legacy-task')
  await page.waitForTimeout(100)
  recordLegacyRequests = true
  await page.getByTitle('加入队列').click()

  await expect(page.getByText('Legacy task', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('任务').locator('option[value="legacy-task"]')).toHaveCount(0)
  await expect(page.getByLabel('任务')).toHaveValue('')
  await page.waitForTimeout(100)
  expect(legacyRequests).toEqual([])
})

test('new task creation stays disabled until a built-in profile is selected', async ({ page }) => {
  const api = await installApiMock(page)
  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('Profile export')
  await page.getByRole('textbox', { name: '源数据目录', exact: true }).fill('E:/source')
  await page.getByRole('textbox', { name: /输出目录/ }).fill('E:/output')

  await expect(page.getByRole('button', { name: '创建任务' })).toBeDisabled()
  expect(api.createdPayloads).toHaveLength(0)
})

test('profile settings remain reachable without overflow at a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installApiMock(page)
  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByLabel('数据集配置', { exact: true }).selectOption('general')
  const settings = page.locator('.profile-task-settings')
  await expect(settings).toBeVisible()
  const layout = await settings.evaluate((element) => {
    const inputs = [...element.querySelectorAll('input')].map((input) => input.getBoundingClientRect())
    const overlaps = inputs.some((left, index) => inputs.slice(index + 1).some((right) => (
      left.left < right.right
      && left.right > right.left
      && left.top < right.bottom
      && left.bottom > right.top
    )))
    return {
      clientWidth: element.clientWidth,
      overlaps,
      scrollWidth: element.scrollWidth,
    }
  })
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth)
  expect(layout.overlaps).toBe(false)
  await page.screenshot({ path: 'test-results/r10-r101-recovery-task7-mobile-20260806-01.png' })
})

test('legacy cluster bookmark redirects to duplicate audit without requesting coverage', async ({ page }) => {
  const coverageRequests: string[] = []
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (pathname.includes('/coverage/')) coverageRequests.push(pathname)
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  await installApiMock(page)
  await page.goto('/#clusters')

  await expect(page).toHaveURL(/#duplicates$/)
  await expect(page.getByRole('heading', { name: '重复审计' })).toBeVisible()
  await expect(page.locator('[data-coverage-scope]')).toHaveCount(0)
  await expect(page.getByLabel('一级文件夹', { exact: true })).toHaveCount(0)
  expect(coverageRequests).toEqual([])
})

test('general task uses the same focused duplicate audit route', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  await installApiMock(page, task('general'))
  await page.goto('/#clusters')

  await expect(page).toHaveURL(/#duplicates$/)
  await expect(page.getByLabel('任务')).toHaveValue('task-7')
  await expect(page.getByRole('heading', { name: '重复审计' })).toBeVisible()
  await expect(page.getByLabel('一级文件夹', { exact: true })).toHaveCount(0)
})

test('profile task submits an approved exclusion while paused for curated confirmation', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  const api = await installApiMock(page, task('general', 'paused', 'evidence_review'))
  await page.goto('/#risks')
  await page.getByLabel('选择 sample.png', { exact: true }).check()
  await page.getByRole('button', { name: '排除', exact: true }).click()
  await page.getByRole('button', { name: '批准排除' }).click()
  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  await expect(page.getByLabel('选择 sample.png', { exact: true })).not.toBeChecked()
  expect(api.curatedDecisionPayloads[0]).toEqual({
    decision: 'approved_exclude',
    evidence_type: 'risk',
    sample_ids: ['sample-1'],
  })
})

test('evidence review direct audit route submits a scoped overlay decision', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  const api = await installApiMock(page, task('artist_concept', 'evidence_review'))
  await page.goto('/#reviews')

  await expect(page).toHaveURL(/#risks$/)
  await expect(page.getByRole('dialog', { name: '任务等待人工复核' })).toHaveCount(0)
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '重复', exact: true }).click()
  await page.getByRole('button', { name: '视觉重复' }).click()
  await expect(page.getByText('curated-sample.png')).toBeVisible()
  await page.getByLabel('选择 curated-sample.png', { exact: true }).check()
  await page.getByRole('button', { name: '排除', exact: true }).click()
  await page.getByRole('button', { name: '批准排除' }).click()
  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  expect(api.curatedDecisionPayloads[0]).toEqual({
    decision: 'approved_exclude',
    evidence_type: 'visual_duplicate',
    sample_ids: ['curated-sample'],
  })

  await page.setViewportSize({ width: 390, height: 844 })
  const cardLayout = await page.locator('.duplicate-member').filter({ hasText: 'curated-sample.png' }).evaluate((card) => {
    const metric = card.querySelector('.duplicate-member-body b')
    const cardRect = card.getBoundingClientRect()
    const metricRect = metric?.getBoundingClientRect()
    return {
      cardRight: cardRect.right,
      clientWidth: card.clientWidth,
      metricRight: metricRect?.right ?? null,
      scrollWidth: card.scrollWidth,
    }
  })
  expect(cardLayout.scrollWidth).toBeLessThanOrEqual(cardLayout.clientWidth)
  expect(cardLayout.metricRight).not.toBeNull()
  expect(cardLayout.metricRight).toBeLessThanOrEqual(cardLayout.cardRight)
})

test('SAE feature evidence stays outside the primary audit UI', async ({ page }) => {
  const saeRequests: string[] = []
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname
    if (pathname.includes('/reviews/sae/')) saeRequests.push(pathname)
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  await installApiMock(page, task('artist_concept', 'evidence_review'))
  await page.goto('/#reviews')
  await expect(page.getByRole('dialog', { name: '任务等待人工复核' })).toHaveCount(0)

  const navigation = page.getByRole('navigation', { name: '主导航' })
  await expect(navigation.getByText('SAE 特征')).toHaveCount(0)
  await expect(page.getByText('特征 0', { exact: true })).toHaveCount(0)
  expect(saeRequests).toEqual([])
})

test('legacy cluster bookmark remains usable when the retired coverage endpoint is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-7')
  })
  await installApiMock(page, task('character_concept', 'completed'), { coverageError: true })
  await page.goto('/#clusters')
  await expect(page).toHaveURL(/#duplicates$/)
  await expect(page.getByRole('heading', { name: '重复审计' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('UI-1 redirects the removed guide route and restores click-to-open configuration explanations', async ({ page }) => {
  await installApiMock(page)
  await page.goto('/#guide')

  await expect.soft(page).toHaveURL(/#tasks$/)
  await expect.soft(page.getByRole('button', { name: '使用说明' })).toHaveCount(0)
  await expect.soft(page.getByText('保持后端 PowerShell 窗口打开')).toHaveCount(0)

  await page.goto('/#tasks')
  await expect(page.getByRole('heading', { name: '任务' })).toBeVisible()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '新建任务' }).click()

  const profileSelect = page.getByLabel('数据集配置', { exact: true })
  await expect(profileSelect.locator('option[value="artist_concept"]')).toHaveText('画师概念')
  await expect(profileSelect.locator('option[value="character_concept"]')).toHaveText('角色概念')
  await expect(profileSelect.locator('option[value="general"]')).toHaveText('通用数据')
  await expect(profileSelect.locator('option[value="artist_concept"]')).not.toHaveAttribute('title')

  await expect(page.getByText('扫描可处理媒体并建立分辨率、路径和基础输入记录。')).toHaveCount(0)
  const componentHelp = page.getByRole('button', { name: '查看媒体扫描说明' })
  await expect(componentHelp).toHaveAttribute('aria-expanded', 'false')
  await componentHelp.click()
  await expect(page.getByRole('tooltip')).toHaveText('扫描可处理媒体并建立分辨率、路径和基础输入记录。')
  await expect(componentHelp).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: '新建训练集处理任务' })).toBeVisible()

  const mediaScanExpand = page.locator('.component-expand').filter({ hasText: '媒体扫描' })
  await expect(mediaScanExpand).toBeEnabled()
  await mediaScanExpand.click()
  const fieldHelp = page.getByRole('button', { name: '查看遍历子文件夹说明' })
  await expect(fieldHelp).toHaveAttribute('aria-expanded', 'false')
  await fieldHelp.click()
  await expect(page.getByRole('tooltip')).toHaveText('开启后会扫描源数据目录下的所有子文件夹。')
  await expect(fieldHelp).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: '新建训练集处理任务' })).toBeVisible()
  await expect(page.locator('.field-help')).not.toHaveCount(0)

  await profileSelect.selectOption('general')
  await expect(page.getByText('全量工作区')).toBeVisible()
  await expect(page.getByLabel('按美学评分分档', { exact: true })).not.toBeChecked()
  const refresh = page.getByRole('button', { name: '刷新当前页面' })
  await expect(refresh).toHaveAttribute('title', '刷新')
  await refresh.focus()
  await expect(refresh).toBeFocused()
  await page.screenshot({ path: 'test-results/r10-r101-recovery-ui-desktop-20260806-01.png', fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: 'test-results/r10-r101-recovery-ui-mobile-20260806-01.png', fullPage: true })
})

test('UI-1 keeps raw profile and aesthetic identifiers in every creation payload', async ({ page }) => {
  const api = await installApiMock(page)
  await page.goto('/#tasks')

  const submit = async (name: string, profile: ProfileId, aesthetic = false) => {
    const previousPayloadCount = api.createdPayloads.length
    await page.getByRole('button', { name: '新建任务' }).click()
    await page.getByRole('textbox', { name: '任务名称', exact: true }).fill(name)
    await page.getByRole('textbox', { name: '源数据目录', exact: true }).fill('E:/source')
    await page.getByRole('textbox', { name: /输出目录/ }).fill('E:/output')
    await page.getByLabel('数据集配置', { exact: true }).selectOption(profile)
    if (aesthetic) await page.getByLabel('按美学评分分档', { exact: true }).check()
    await page.getByRole('button', { name: '创建任务' }).click()
    await expect.poll(() => api.createdPayloads.length).toBe(previousPayloadCount + 1)
  }

  await submit('Artist payload', 'artist_concept')
  await submit('Character payload', 'character_concept')
  await submit('General payload', 'general', true)

  const payloads = api.createdPayloads as Array<{
    profile: ProfileId
    components: Record<string, { config: Record<string, unknown>; enabled: boolean }>
  }>
  expect(payloads.map((payload) => payload.profile)).toEqual([
    'artist_concept',
    'character_concept',
    'general',
  ])
  expect(payloads.map((payload) => payload.components['export.dataset'].config.aesthetic_bins)).toEqual([
    undefined,
    undefined,
    'score_x2_floor',
  ])
})
