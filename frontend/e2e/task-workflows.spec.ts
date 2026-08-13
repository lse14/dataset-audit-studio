import { expect, test, type Page } from '@playwright/test'

const timestamp = '2026-07-30T00:00:00Z'
const task = {
  config: {
    profile: 'general',
    components: {
      'embedding.semantic': { enabled: true, config: {} },
      'cluster.hierarchy': { enabled: true, config: {} },
    },
  },
  config_hash: 'a'.repeat(64),
  created_at: timestamp,
  current_config_revision: 1,
  error_code: null,
  error_message: null,
  execution_epoch: 0,
  finished_at: null,
  id: 'task-1',
  lease_expires_at: null,
  lease_owner: null,
  name: '已有任务',
  output_root: 'E:/output',
  progress_current: 0,
  progress_total: null,
  resume_state: null,
  row_version: 1,
  source_root: 'E:/source',
  started_at: null,
  status: 'evidence_review',
  updated_at: timestamp,
}

const secondTask = {
  ...task,
  id: 'task-2',
  name: '第二个任务',
}

const exportManifest = {
  activation: 'required',
  config_schema: 'export.dataset.v1',
  consumes: [],
  default_config: {
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
      keep_annotation_files: {
        default: true,
        title: '保留源标注文件',
        type: 'boolean',
      },
      keep_latent_files: {
        default: true,
        title: '保留 Latent 缓存',
        type: 'boolean',
      },
    },
    type: 'object',
  },
  model_ids: [],
  phase_order: 1,
  produces: [],
  recommended_enabled: true,
  ui_group: 'export',
  version: '1.0.0',
}

const generalProfile = {
  components: {
    'export.dataset': {
      config: {
        keep_annotation_files: true,
        keep_latent_files: true,
      },
      enabled: true,
    },
  },
  description: 'Test profile',
  display_name: 'General dataset',
  id: 'general',
  profile_owned_component_ids: [],
  profile_owned_config_fields: {},
  scope_mode: 'global',
}

const overview = {
  cluster_nodes: 0,
  evidence_codes: [],
  exports: [],
  latent_entries: 0,
  leaf_clusters: 0,
  ready_artifacts: 0,
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

type StyleAuditMockResponse = {
  payload: Record<string, unknown>
  status?: number
}

type AestheticAuditMockResponse = {
  payload: Record<string, unknown>
  status?: number
}

type DuplicateAuditMockResponse = {
  payload: Record<string, unknown>
  status?: number
}

type ApiMockOptions = {
  foldersByTask?: Record<string, Array<{ display_name: string; folder_id: string }>>
  onAestheticAudit?: (input: { taskId: string; url: URL }) =>
    AestheticAuditMockResponse | Promise<AestheticAuditMockResponse>
  onDuplicateAudit?: (input: { taskId: string; url: URL }) =>
    DuplicateAuditMockResponse | Promise<DuplicateAuditMockResponse>
  onStyleAudit?: (input: { taskId: string; url: URL }) =>
    StyleAuditMockResponse | Promise<StyleAuditMockResponse>
  tasks?: typeof task[]
}

function defaultStyleAuditPayload() {
  return {
    approved_exclude: 0,
    approved_keep: 0,
    items: [
      {
        artist_scope: 'artist-a',
        classification: 'strong_outlier',
        decision: null,
        decision_source: 'model',
        reason: 'high distance',
        relative_path: 'strong.png',
        review_eligible: true,
        sample_id: 'strong',
        style_score: 0.9,
        threshold: 0.3,
      },
      {
        artist_scope: 'artist-a',
        classification: 'outlier',
        decision: null,
        decision_source: 'model',
        reason: 'medium distance',
        relative_path: 'outlier.png',
        review_eligible: true,
        sample_id: 'outlier',
        style_score: 0.4,
        threshold: 0.3,
      },
      {
        artist_scope: 'artist-a',
        classification: 'normal',
        decision: null,
        decision_source: 'none',
        reason: null,
        relative_path: 'normal.png',
        review_eligible: false,
        sample_id: 'normal',
        style_score: 0.1,
        threshold: 0.3,
      },
    ],
    limit: 100,
    normal: 1,
    offset: 0,
    outlier: 1,
    pending: 2,
    strong_outlier: 1,
    total: 3,
  }
}

function defaultAestheticAuditPayload() {
  return {
    approved_exclude: 0,
    approved_keep: 1,
    bucket_counts: {
      '1.0': 0,
      '1.5': 0,
      '2.0': 0,
      '2.5': 1,
      '3.0': 0,
      '3.5': 0,
      '4.0': 0,
      '4.5': 1,
      '5.0': 0,
    },
    invalid_counts: {
      ambiguous: 1,
      missing: 1,
      non_finite: 1,
      out_of_range: 1,
      provenance_mismatch: 1,
    },
    items: [
      {
        artist_scope: 'artist-a',
        bucket: 2.5,
        decision: null,
        decision_source: 'none',
        reason_code: null,
        relative_path: 'artist-a/candidate.png',
        review_eligible: true,
        sample_id: 'aesthetic-candidate',
        score: 2.9,
      },
      {
        artist_scope: 'artist-a',
        bucket: 4.5,
        decision: null,
        decision_source: 'none',
        reason_code: null,
        relative_path: 'artist-a/ordinary.png',
        review_eligible: false,
        sample_id: 'aesthetic-ordinary',
        score: 4.99,
      },
      {
        artist_scope: 'artist-b',
        bucket: null,
        decision: 'approved_keep',
        decision_source: 'human',
        reason_code: 'missing',
        relative_path: 'artist-b/overlay.png',
        review_eligible: true,
        sample_id: 'aesthetic-overlay',
        score: null,
      },
      {
        artist_scope: 'artist-b',
        bucket: null,
        decision: null,
        decision_source: 'none',
        reason_code: 'non_finite',
        relative_path: 'artist-b/nonfinite.png',
        review_eligible: false,
        sample_id: 'aesthetic-nonfinite',
        score: null,
      },
      {
        artist_scope: 'artist-c',
        bucket: null,
        decision: null,
        decision_source: 'none',
        reason_code: 'out_of_range',
        relative_path: 'artist-c/out-of-range.png',
        review_eligible: false,
        sample_id: 'aesthetic-out-of-range',
        score: null,
      },
      {
        artist_scope: 'artist-c',
        bucket: null,
        decision: null,
        decision_source: 'none',
        reason_code: 'provenance_mismatch',
        relative_path: 'artist-c/mismatch.png',
        review_eligible: false,
        sample_id: 'aesthetic-mismatch',
        score: null,
      },
      {
        artist_scope: 'artist-c',
        bucket: null,
        decision: null,
        decision_source: 'none',
        reason_code: 'ambiguous',
        relative_path: 'artist-c/ambiguous.png',
        review_eligible: false,
        sample_id: 'aesthetic-ambiguous',
        score: null,
      },
    ],
    limit: 100,
    offset: 0,
    pending: 1,
    total: 7,
  }
}

function defaultDuplicateAuditPayload() {
  return {
    approved_exclude: 1,
    approved_keep: 0,
    items: [{
      approved_exclude: 1,
      approved_keep: 0,
      effective_retained_count: 1,
      evidence_type: 'exact_duplicate',
      group_key: 'exact-a',
      member_count: 2,
      members: [
        {
          artist_scope: 'artist-a',
          decision: null,
          decision_source: 'automatic',
          pixel_area: 1024 * 1024,
          relative_path: 'artist-a/alpha.png',
          resolutions: [512, 1024],
          review_eligible: true,
          sample_id: 'alpha',
          score: 0.9,
        },
        {
          artist_scope: 'artist-b',
          decision: 'approved_exclude',
          decision_source: 'human',
          pixel_area: 768 * 768,
          relative_path: 'artist-b/bravo.png',
          resolutions: [768],
          review_eligible: true,
          sample_id: 'bravo',
          score: 0.7,
        },
      ],
      pending: 1,
    }],
    limit: 100,
    offset: 0,
    pending: 1,
    total: 1,
    unresolved: 1,
  }
}

async function installApiMock(page: Page, options: ApiMockOptions = {}) {
  const createdPayloads: unknown[] = []
  const exclusionPayloads: unknown[] = []
  const curatedDecisionPayloads: unknown[] = []
  const aestheticAuditRequests: string[] = []
  const duplicateAuditRequests: string[] = []
  const styleAuditRequests: string[] = []
  const styleDecisionPayloads: unknown[] = []
  const tasks = options.tasks ?? [task]

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const json = (value: unknown, status = 200) => route.fulfill({
      body: JSON.stringify(value),
      contentType: 'application/json',
      status,
    })
    const matchingTask = tasks.find((item) => url.pathname.startsWith(`/api/tasks/${item.id}`))
    const taskPath = matchingTask ? `/api/tasks/${matchingTask.id}` : ''

    if (url.pathname === '/api/health') return json(health)
    if (url.pathname === '/api/components') return json({ items: [exportManifest], total: 1 })
    if (url.pathname === '/api/components/builtin-profiles') {
      return json({ items: [generalProfile], total: 1 })
    }
    if (url.pathname === '/api/task-presets') return json({ items: [], total: 0 })
    if (url.pathname === '/api/tasks' && request.method() === 'GET') {
      return json({ items: tasks, limit: 50, offset: 0, total: tasks.length })
    }
    if (url.pathname === '/api/tasks' && request.method() === 'POST') {
      createdPayloads.push(request.postDataJSON())
      return json({ ...task, id: 'task-created', name: '导出任务', status: 'draft' })
    }
    if (matchingTask && url.pathname === taskPath) return json(matchingTask)
    if (url.pathname === `${taskPath}/overview`) return json(overview)
    if (url.pathname === `${taskPath}/folders`) {
      return json({ items: options.foldersByTask?.[matchingTask?.id ?? ''] ?? [] })
    }
    if (url.pathname === `${taskPath}/reviews/style/audit`) {
      styleAuditRequests.push(url.pathname + url.search)
      const response = options.onStyleAudit
        ? await options.onStyleAudit({ taskId: matchingTask?.id ?? '', url })
        : { payload: defaultStyleAuditPayload() }
      return json(response.payload, response.status)
    }
    if (url.pathname === `${taskPath}/reviews/aesthetic/audit`) {
      aestheticAuditRequests.push(url.pathname + url.search)
      const response = options.onAestheticAudit
        ? await options.onAestheticAudit({ taskId: matchingTask?.id ?? '', url })
        : { payload: defaultAestheticAuditPayload() }
      return json(response.payload, response.status)
    }
    if (url.pathname === `${taskPath}/reviews/duplicates/audit`) {
      duplicateAuditRequests.push(url.pathname + url.search)
      const response = options.onDuplicateAudit
        ? await options.onDuplicateAudit({ taskId: matchingTask?.id ?? '', url })
        : { payload: defaultDuplicateAuditPayload() }
      return json(response.payload, response.status)
    }
    if (url.pathname === `${taskPath}/reviews/style/decisions` && request.method() === 'POST') {
      styleDecisionPayloads.push(request.postDataJSON())
      return json({ changed: 1, decision: 'approved_exclude', selected: 1 })
    }
    if (url.pathname === `${taskPath}/reviews/style`) {
      return json({
        approved_exclude: 0,
        approved_keep: 0,
        items: [],
        limit: 100,
        offset: 0,
        pending: 0,
        total: 0,
      })
    }
    if (url.pathname === `${taskPath}/reviews/curated/decisions` && request.method() === 'POST') {
      curatedDecisionPayloads.push(request.postDataJSON())
      return json({ changed: 1, decision: 'approved_keep', selected: 1 })
    }
    if (url.pathname === `${taskPath}/reviews/curated`) {
      return json({ approved_exclude: 0, approved_keep: 0, items: [], limit: 100, offset: 0, pending: 0, total: 0 })
    }
    if (url.pathname === `${taskPath}/events`) {
      return json({ items: [], latest_sequence: 0, next_after: 0 })
    }
    if (url.pathname === `${taskPath}/events/stream`) {
      return route.fulfill({ body: '', contentType: 'text/event-stream' })
    }
    if (url.pathname === `/api/components/runs/${task.id}`) {
      return json({ config_hash: task.config_hash, items: [], task_id: task.id, total: 0 })
    }
    if (url.pathname === `${taskPath}/risk-samples`) {
      return json({
        items: [{
          artist_scope: '__root__',
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
      exclusionPayloads.push(request.postDataJSON())
      return json({ changed: 1, excluded: true, task_id: task.id })
    }
    return route.fulfill({ body: `Unhandled API request: ${request.method()} ${url.pathname}`, status: 404 })
  })

  return {
    aestheticAuditRequests,
    createdPayloads,
    curatedDecisionPayloads,
    duplicateAuditRequests,
    exclusionPayloads,
    styleAuditRequests,
    styleDecisionPayloads,
  }
}

async function dismissReviewPrompt(page: Page) {
  const button = page.getByRole('button', { name: '稍后处理' })
  if (await button.isVisible()) await button.click()
}

test('creates a task with source annotation retention disabled', async ({ page }) => {
  const api = await installApiMock(page)

  await page.goto('/#tasks')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('导出任务')
  await page.getByRole('textbox', { name: '源数据目录', exact: true }).fill('E:/source')
  await page.getByRole('textbox', { name: /输出目录/ }).fill('E:/output')
  await page.getByLabel('数据集配置', { exact: true }).selectOption('general')
  await page.getByRole('button', { name: /^数据集导出/ }).click()
  await page.getByLabel('保留同名标注文件', { exact: true }).uncheck()
  await page.getByRole('button', { name: '创建任务' }).click()

  await expect.poll(() => api.createdPayloads.length).toBe(1)
  expect(api.createdPayloads[0]).toMatchObject({
    components: {
      'export.dataset': {
        config: {
          keep_annotation_files: false,
          keep_latent_files: true,
        },
        enabled: true,
      },
    },
    name: '导出任务',
    output_root: 'E:/output',
    source_root: 'E:/source',
  })
})

test('submits an approved exclusion for the selected risk sample', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const api = await installApiMock(page)

  await page.goto('/#risks')
  await dismissReviewPrompt(page)
  await page.getByLabel('选择 sample.png', { exact: true }).check()
  await page.getByRole('button', { name: '排除', exact: true }).click()
  await page.getByRole('button', { name: '批准排除' }).click()

  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  expect(api.curatedDecisionPayloads[0]).toEqual({
    decision: 'approved_exclude',
    evidence_type: 'risk',
    sample_ids: ['sample-1'],
  })
})

test('does not cover direct audit selection with an evidence review prompt', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  await installApiMock(page)

  for (const [route, label] of [
    ['#risks', '选择 sample.png'],
    ['#style', '选择 outlier.png'],
    ['#duplicates', '选择 artist-a/alpha.png'],
    ['#aesthetics', '选择 artist-a/candidate.png'],
  ] as const) {
    await page.goto(`/${route}`)
    await expect(page.getByLabel(label, { exact: true })).toBeVisible()
    await expect(page.getByRole('dialog', { name: '任务等待人工复核' })).toHaveCount(0)
    await page.getByLabel(label, { exact: true }).check()
    await expect(page.getByText('已选 1', { exact: true })).toBeVisible()
  }
})

test('compares normal style context and reuses style decisions without mobile overflow', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const api = await installApiMock(page)

  await page.goto('/#style')
  await dismissReviewPrompt(page)
  await expect.poll(() => api.styleAuditRequests.length).toBeGreaterThan(0)
  expect(new Set(api.styleAuditRequests)).toEqual(new Set([
    '/api/tasks/task-1/reviews/style/audit?offset=0&limit=100',
  ]))
  await expect(page.getByText('强离群', { exact: true })).toBeVisible()
  await expect(page.getByText('离群', { exact: true })).toBeVisible()
  await expect(page.getByLabel('选择 normal.png', { exact: true })).toHaveCount(0)
  await page.getByLabel('选择 outlier.png', { exact: true }).check()
  await page.getByRole('button', { name: '排除', exact: true }).click()
  await page.getByRole('button', { name: '批准排除' }).click()
  await expect.poll(() => api.styleDecisionPayloads.length).toBe(1)
  expect(api.styleDecisionPayloads[0]).toEqual({
    decision: 'approved_exclude',
    sample_ids: ['outlier'],
  })

  await page.screenshot({ path: '../test-results/r10-r101-recovery-style-audit-desktop-20260806-01.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.locator('html').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.screenshot({ path: '../test-results/r10-r101-recovery-style-audit-mobile-20260806-01.png' })
})

test('keeps a delayed prior task style audit from replacing the selected task', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const delayedResponses: Array<(response: StyleAuditMockResponse) => void> = []
  const api = await installApiMock(page, {
    onStyleAudit: ({ taskId }) => {
      if (taskId === 'task-1') {
        return new Promise<StyleAuditMockResponse>((resolve) => delayedResponses.push(resolve))
      }
      return {
        payload: {
          ...defaultStyleAuditPayload(),
          items: [{
            ...defaultStyleAuditPayload().items[0],
            relative_path: 'task-2-current.png',
            sample_id: 'task-2-current',
          }],
          normal: 0,
          outlier: 0,
          pending: 1,
          strong_outlier: 1,
          total: 1,
        },
      }
    },
    tasks: [task, secondTask],
  })

  await page.goto('/#style')
  await dismissReviewPrompt(page)
  await expect.poll(() => api.styleAuditRequests.some((path) => path.includes('/task-1/'))).toBe(true)
  await page.getByLabel('任务').selectOption('task-2')
  await expect(page.getByText('task-2-current.png', { exact: true })).toBeVisible()

  for (const resolve of delayedResponses) {
    resolve({ payload: defaultStyleAuditPayload() })
  }
  await expect(page.getByText('task-2-current.png', { exact: true })).toBeVisible()
  await expect(page.getByText('strong.png', { exact: true })).toHaveCount(0)
})

test('shows folder empty and style audit error states', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  await installApiMock(page, {
    foldersByTask: {
      'task-1': [{ display_name: '空文件夹', folder_id: 'empty-folder' }],
    },
    onStyleAudit: ({ taskId, url }) => {
      if (taskId === 'task-2') return { payload: { detail: '画风读取失败' }, status: 503 }
      if (url.searchParams.get('folder') === 'empty-folder') {
        return {
          payload: {
            approved_exclude: 0,
            approved_keep: 0,
            items: [],
            limit: 100,
            normal: 0,
            offset: 0,
            outlier: 0,
            pending: 0,
            strong_outlier: 0,
            total: 0,
          },
        }
      }
      return { payload: defaultStyleAuditPayload() }
    },
    tasks: [task, secondTask],
  })

  await page.goto('/#style')
  await dismissReviewPrompt(page)
  await page.getByLabel('子文件夹筛选', { exact: true }).selectOption('empty-folder')
  await expect(page.getByText('当前子文件夹没有画风证据', { exact: true })).toBeVisible()

  await page.getByLabel('任务').selectOption('task-2')
  await expect(page.getByText('画风读取失败', { exact: true })).toBeVisible()
})

test('audits all aesthetic samples by backend bucket and only reuses curated decisions for eligible samples', async ({ page }) => {
  const runtimeMessages: string[] = []
  const thumbnailFallbackResponses: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'warning' || (
      message.type() === 'error'
      && !message.text().includes('Failed to load resource')
    )) {
      runtimeMessages.push(`${message.type()}: ${message.text()}`)
    }
  })
  page.on('pageerror', (error) => runtimeMessages.push(`pageerror: ${error.message}`))
  page.on('response', (response) => {
    if (response.status() === 404 && new URL(response.url()).pathname.includes('/thumbnail')) {
      thumbnailFallbackResponses.push(response.url())
    }
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const api = await installApiMock(page, {
    foldersByTask: {
      'task-1': [
        { display_name: '画师 A', folder_id: 'artist-a' },
        { display_name: '画师 B', folder_id: 'artist-b' },
      ],
    },
  })

  await page.goto('/#aesthetics')
  await expect(page).toHaveTitle('Dataset Audit Studio')
  await expect(page.locator('#root')).not.toBeEmpty()
  await dismissReviewPrompt(page)
  await expect.poll(() => api.aestheticAuditRequests.length).toBeGreaterThan(0)
  expect(api.aestheticAuditRequests).toContain(
    '/api/tasks/task-1/reviews/aesthetic/audit?offset=0&limit=100',
  )
  for (const path of [
    'artist-a/candidate.png',
    'artist-a/ordinary.png',
    'artist-b/overlay.png',
    'artist-b/nonfinite.png',
    'artist-c/out-of-range.png',
    'artist-c/mismatch.png',
    'artist-c/ambiguous.png',
  ]) {
    await expect(page.getByText(path, { exact: true })).toBeVisible()
  }
  await expect(page.locator('.aesthetic-audit-counts')).toContainText(/缺失\s*1/)
  await expect(page.locator('.aesthetic-audit-counts')).toContainText(/非有限\s*1/)
  await expect(page.locator('.aesthetic-audit-counts')).toContainText(/越界\s*1/)
  await expect(page.locator('.aesthetic-audit-counts')).toContainText(/来源不匹配\s*1/)
  await expect(page.locator('.aesthetic-audit-counts')).toContainText(/歧义\s*1/)
  await expect(page.getByLabel('选择 artist-a/ordinary.png', { exact: true })).toHaveCount(0)
  await page.screenshot({
    fullPage: true,
    path: '../test-results/r10-r101-recovery-aesthetic-audit-desktop-20260806-01.png',
  })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.locator('html').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.screenshot({
    fullPage: true,
    path: '../test-results/r10-r101-recovery-aesthetic-audit-mobile-20260806-01.png',
  })
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.getByLabel('选择 artist-a/candidate.png', { exact: true }).check()
  await page.getByLabel('选择 artist-b/overlay.png', { exact: true }).check()
  await page.getByRole('button', { name: '保留', exact: true }).click()
  await page.getByRole('button', { name: '批准保留' }).click()
  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  expect(api.curatedDecisionPayloads[0]).toEqual({
    decision: 'approved_keep',
    evidence_type: 'aesthetic',
    sample_ids: ['aesthetic-candidate', 'aesthetic-overlay'],
  })

  await page.getByLabel('美学分档', { exact: true }).selectOption('2.5')
  await expect.poll(() => api.aestheticAuditRequests.some((path) => path.includes('bucket=2.5'))).toBe(true)
  await page.getByLabel('子文件夹筛选', { exact: true }).selectOption('artist-a')
  await expect.poll(() => api.aestheticAuditRequests.some((path) => (
    path.includes('folder=artist-a') && path.includes('bucket=2.5')
  ))).toBe(true)
  expect(runtimeMessages).toEqual([])
  expect(thumbnailFallbackResponses.length).toBeGreaterThan(0)
})

test('keeps a delayed prior task aesthetic audit from replacing the selected task', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const delayedResponses: Array<(response: AestheticAuditMockResponse) => void> = []
  const api = await installApiMock(page, {
    onAestheticAudit: ({ taskId }) => {
      if (taskId === 'task-1') {
        return new Promise<AestheticAuditMockResponse>((resolve) => delayedResponses.push(resolve))
      }
      return {
        payload: {
          ...defaultAestheticAuditPayload(),
          items: [{
            ...defaultAestheticAuditPayload().items[0],
            relative_path: 'task-2-current-aesthetic.png',
            sample_id: 'task-2-current-aesthetic',
          }],
          pending: 1,
          total: 1,
        },
      }
    },
    tasks: [task, secondTask],
  })

  await page.goto('/#aesthetics')
  await dismissReviewPrompt(page)
  await expect.poll(() => api.aestheticAuditRequests.some((path) => path.includes('/task-1/'))).toBe(true)
  await page.getByLabel('任务').selectOption('task-2')
  const deferPrompt = page.getByRole('button', { name: '稍后处理' })
  if (await deferPrompt.isVisible()) await deferPrompt.click()
  await expect(page.getByText('task-2-current-aesthetic.png', { exact: true })).toBeVisible()

  for (const resolve of delayedResponses) resolve({ payload: defaultAestheticAuditPayload() })
  await expect(page.getByText('task-2-current-aesthetic.png', { exact: true })).toBeVisible()
  await expect(page.getByText('artist-a/candidate.png', { exact: true })).toHaveCount(0)
})

test('shows folder empty and aesthetic audit error states', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  await installApiMock(page, {
    foldersByTask: {
      'task-1': [{ display_name: '空文件夹', folder_id: 'empty-folder' }],
    },
    onAestheticAudit: ({ taskId, url }) => {
      if (taskId === 'task-2') return { payload: { detail: '美学审计读取失败' }, status: 503 }
      if (url.searchParams.get('folder') === 'empty-folder') {
        return {
          payload: {
            ...defaultAestheticAuditPayload(),
            approved_keep: 0,
            bucket_counts: {
              '1.0': 0,
              '1.5': 0,
              '2.0': 0,
              '2.5': 0,
              '3.0': 0,
              '3.5': 0,
              '4.0': 0,
              '4.5': 0,
              '5.0': 0,
            },
            invalid_counts: {
              ambiguous: 0,
              missing: 0,
              non_finite: 0,
              out_of_range: 0,
              provenance_mismatch: 0,
            },
            items: [],
            pending: 0,
            total: 0,
          },
        }
      }
      return { payload: defaultAestheticAuditPayload() }
    },
    tasks: [task, secondTask],
  })

  await page.goto('/#aesthetics')
  await dismissReviewPrompt(page)
  await page.getByLabel('子文件夹筛选', { exact: true }).selectOption('empty-folder')
  await expect(page.getByText('当前子文件夹没有美学结果', { exact: true })).toBeVisible()

  await page.getByLabel('任务').selectOption('task-2')
  await expect(page.getByText('美学审计读取失败', { exact: true })).toBeVisible()
})

test('automatically selects lower-resolution duplicate members for batch exclusion', async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const api = await installApiMock(page, {
    onDuplicateAudit: () => ({
      payload: {
        approved_exclude: 0,
        approved_keep: 0,
        items: [{
          approved_exclude: 0,
          approved_keep: 0,
          effective_retained_count: 3,
          evidence_type: 'exact_duplicate',
          group_key: 'exact-auto-select',
          member_count: 3,
          members: [
            {
              artist_scope: 'artist-a',
              decision: null,
              decision_source: 'automatic',
              pixel_area: 1024 * 1024,
              relative_path: 'artist-a/largest.png',
              resolutions: [512, 768, 1024],
              review_eligible: true,
              sample_id: 'largest',
              score: 0.9,
            },
            {
              artist_scope: 'artist-a',
              decision: null,
              decision_source: 'automatic',
              pixel_area: 768 * 768,
              relative_path: 'artist-a/lower.png',
              resolutions: [512, 768],
              review_eligible: true,
              sample_id: 'lower',
              score: 0.8,
            },
            {
              artist_scope: 'artist-a',
              decision: null,
              decision_source: 'automatic',
              pixel_area: 512 * 512,
              relative_path: 'artist-a/smallest.png',
              resolutions: [512],
              review_eligible: true,
              sample_id: 'smallest',
              score: 0.7,
            },
          ],
          pending: 3,
        }],
        limit: 100,
        offset: 0,
        pending: 3,
        total: 1,
        unresolved: 0,
      },
    }),
  })

  await page.goto('/#duplicates')
  await dismissReviewPrompt(page)
  await expect(page.getByText('artist-a/largest.png', { exact: true })).toBeVisible()
  await page.getByLabel('本页自动选择可排除成员', { exact: true }).check()
  await expect(page.getByText('已选 2', { exact: true })).toBeVisible()
  await expect(page.getByLabel('选择 artist-a/largest.png', { exact: true })).not.toBeChecked()
  await expect(page.getByLabel('选择 artist-a/lower.png', { exact: true })).toBeChecked()
  await expect(page.getByLabel('选择 artist-a/smallest.png', { exact: true })).toBeChecked()
  await page.screenshot({ path: testInfo.outputPath('duplicate-auto-selection-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.locator('html').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('duplicate-auto-selection-mobile.png') })

  await page.getByRole('button', { name: '排除', exact: true }).click()
  await page.getByRole('button', { name: '批准排除', exact: true }).click()
  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  expect(api.curatedDecisionPayloads[0]).toEqual({
    decision: 'approved_exclude',
    evidence_type: 'exact_duplicate',
    sample_ids: ['lower', 'smallest'],
  })
})

test('audits complete visual and semantic duplicate groups without allowing a final exclusion', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const api = await installApiMock(page, {
    foldersByTask: {
      'task-1': [{ display_name: '画师 B', folder_id: 'artist-b' }],
    },
  })

  await page.goto('/#duplicates')
  await dismissReviewPrompt(page)
  await expect.poll(() => api.duplicateAuditRequests.length).toBeGreaterThan(0)
  expect(api.duplicateAuditRequests).toContain(
    '/api/tasks/task-1/reviews/duplicates/audit?evidence_type=exact_duplicate&offset=0&limit=100',
  )
  await expect(page.getByText('artist-a/alpha.png', { exact: true })).toBeVisible()
  await expect(page.getByText('artist-b/bravo.png', { exact: true })).toBeVisible()
  await expect(page.getByText('档位 512 / 1024', { exact: true })).toBeVisible()
  await page.screenshot({ path: '../test-results/r10-r101-recovery-duplicate-audit-desktop-20260806-01.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.locator('html').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  expect(await page.locator('.page-title span').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await page.screenshot({ path: '../test-results/r10-r101-recovery-duplicate-audit-mobile-20260806-01.png' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByLabel('选择 artist-a/alpha.png', { exact: true }).check()
  await page.getByRole('button', { name: '排除', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('至少保留一张')
  expect(api.curatedDecisionPayloads).toEqual([])

  await page.getByRole('button', { name: '保留', exact: true }).click()
  await page.getByRole('button', { name: '批准保留' }).click()
  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  expect(api.curatedDecisionPayloads[0]).toEqual({
    decision: 'approved_keep',
    evidence_type: 'exact_duplicate',
    sample_ids: ['alpha'],
  })

  await page.getByRole('button', { name: '视觉重复' }).click()
  await expect.poll(() => api.duplicateAuditRequests.some((path) => path.includes('evidence_type=visual_duplicate'))).toBe(true)
  await page.getByRole('button', { name: '语义重复' }).click()
  await expect.poll(() => api.duplicateAuditRequests.some((path) => path.includes('evidence_type=semantic_duplicate'))).toBe(true)
  await page.getByLabel('子文件夹筛选', { exact: true }).selectOption('artist-b')
  await expect.poll(() => api.duplicateAuditRequests.some((path) => path.includes('folder=artist-b'))).toBe(true)

})

test('shows existing fully excluded groups as recoverable and isolates delayed duplicate responses', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  const delayed: Array<(response: DuplicateAuditMockResponse) => void> = []
  const api = await installApiMock(page, {
    onDuplicateAudit: ({ taskId }) => {
      if (taskId === 'task-1') {
        return new Promise<DuplicateAuditMockResponse>((resolve) => delayed.push(resolve))
      }
      return {
        payload: {
          ...defaultDuplicateAuditPayload(),
          approved_exclude: 2,
          items: [{
            ...defaultDuplicateAuditPayload().items[0],
            effective_retained_count: 0,
            group_key: 'recovery',
            members: defaultDuplicateAuditPayload().items[0].members.map((member) => ({
              ...member,
              decision: 'approved_exclude',
              decision_source: 'human',
              relative_path: member.sample_id === 'alpha' ? 'task-2-recovery.png' : member.relative_path,
            })),
            pending: 0,
          }],
          pending: 0,
          total: 1,
        },
      }
    },
    tasks: [task, secondTask],
  })

  await page.goto('/#duplicates')
  await dismissReviewPrompt(page)
  await expect.poll(() => api.duplicateAuditRequests.some((path) => path.includes('/task-1/'))).toBe(true)
  await page.getByLabel('任务').selectOption('task-2')
  const deferPrompt = page.getByRole('button', { name: '稍后处理' })
  if (await deferPrompt.isVisible()) await deferPrompt.click()
  await expect(page.getByText('task-2-recovery.png', { exact: true })).toBeVisible()
  await expect(page.getByText('当前组已全部排除', { exact: true })).toBeVisible()
  await page.getByLabel('选择 task-2-recovery.png', { exact: true }).check()
  await page.getByRole('button', { name: '保留', exact: true }).click()
  await page.getByRole('button', { name: '批准保留' }).click()
  await expect.poll(() => api.curatedDecisionPayloads.length).toBe(1)
  expect(api.curatedDecisionPayloads[0]).toMatchObject({
    decision: 'approved_keep',
    evidence_type: 'exact_duplicate',
    sample_ids: ['alpha'],
  })

  for (const resolve of delayed) resolve({ payload: defaultDuplicateAuditPayload() })
  await expect(page.getByText('task-2-recovery.png', { exact: true })).toBeVisible()
  await expect(page.getByText('artist-a/alpha.png', { exact: true })).toHaveCount(0)
})

test('shows folder empty and duplicate audit error states', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dataset-audit-selected-task-v2', 'task-1')
  })
  await installApiMock(page, {
    foldersByTask: {
      'task-1': [{ display_name: '空文件夹', folder_id: 'empty-folder' }],
    },
    onDuplicateAudit: ({ taskId, url }) => {
      if (taskId === 'task-2') return { payload: { detail: '重复组读取失败' }, status: 503 }
      if (url.searchParams.get('folder') === 'empty-folder') {
        return {
          payload: {
            approved_exclude: 0,
            approved_keep: 0,
            items: [],
            limit: 100,
            offset: 0,
            pending: 0,
            total: 0,
            unresolved: 0,
          },
        }
      }
      return { payload: defaultDuplicateAuditPayload() }
    },
    tasks: [task, secondTask],
  })

  await page.goto('/#duplicates')
  await dismissReviewPrompt(page)
  await page.getByLabel('子文件夹筛选', { exact: true }).selectOption('empty-folder')
  await expect(page.getByText('当前子文件夹没有重复组', { exact: true })).toBeVisible()
  await page.getByLabel('任务').selectOption('task-2')
  await expect(page.getByText('重复组读取失败', { exact: true })).toBeVisible()
})
