import assert from 'node:assert/strict'
import { access, readFile } from 'node:fs/promises'
import test from 'node:test'

const root = new URL('../src/', import.meta.url)

const source = (path) => readFile(new URL(path, root), 'utf8')

test('UI-1 removes the guide route and its page module', async () => {
  const app = await source('App.tsx')

  assert.doesNotMatch(app, /GuidePage|id: 'guide'|page === 'guide'/)
  await assert.rejects(access(new URL('pages/GuidePage.tsx', root)), /ENOENT/)
})

test('UI-1 restores click-to-open explanations for task configuration fields', async () => {
  const [fieldHelp, schemaField, componentEditor, tasksPage] = await Promise.all([
    source('components/FieldHelp.tsx'),
    source('components/SchemaField.tsx'),
    source('components/ComponentConfigEditor.tsx'),
    source('pages/TasksPage.tsx'),
  ])
  const { componentHelpText, schemaFieldHelp, taskConfigHelp } = await import('../src/taskConfigHelp.ts')

  assert.match(fieldHelp, /aria-expanded/)
  assert.match(fieldHelp, /role="tooltip"/)
  assert.match(fieldHelp, /keydown/)
  assert.match(fieldHelp, /event\.stopPropagation\(\)/)
  assert.match(fieldHelp, /useLayoutEffect/)
  assert.match(fieldHelp, /translate\(\$\{offset\.x\}px, \$\{offset\.y\}px\)/)
  assert.match(schemaField, /schemaFieldHelp\(name, label, resolved\)/)
  assert.match(schemaField, /<FieldHelp/)
  assert.match(componentEditor, /componentHelpText\(manifest\.id, manifest\.display_name\)/)
  assert.match(tasksPage, /taskConfigHelp\('task_name'\)/)
  assert.equal(schemaFieldHelp('recursive', '遍历子文件夹', { type: 'boolean' }), '开启后会扫描源数据目录下的所有子文件夹。')
  assert.match(schemaFieldHelp('unmapped_field', '未知配置', { minimum: 1, maximum: 8, type: 'integer' }), /允许范围 1 至 8/)
  assert.match(componentHelpText('media.scan', '媒体扫描'), /扫描可处理媒体/)
  assert.match(taskConfigHelp('task_name'), /任务的显示名称/)
  assert.match(schemaField, /field-error/)
})

test('UI-1 maps display-only names while preserving opaque identifiers', async () => {
  const { profileDisplayName, workspaceDisplayName } = await import('../src/profileWorkspace.ts')

  assert.deepEqual(
    ['artist_concept', 'character_concept', 'general'].map((value) => profileDisplayName(value)),
    ['画师概念', '角色概念', '通用数据'],
  )
  assert.equal(workspaceDisplayName('broad'), '全量工作区')
  assert.equal(workspaceDisplayName('score_x2_floor'), '按美学评分分档')
  assert.equal(workspaceDisplayName('opaque_config_id'), 'opaque_config_id')
})

test('R9.1 navigation follows the audit workflow and preserves legacy hashes', async () => {
  const [app, types, stylePage, duplicatesPage, aestheticsPage] = await Promise.all([
    source('App.tsx'),
    source('types.ts'),
    source('pages/StylePage.tsx'),
    source('pages/DuplicatesPage.tsx'),
    source('pages/AestheticsPage.tsx'),
  ])

  assert.match(
    types,
    /'tasks'[\s\S]*'progress'[\s\S]*'risks'[\s\S]*'style'[\s\S]*'duplicates'[\s\S]*'aesthetics'[\s\S]*'exports'[\s\S]*'models'[\s\S]*'system'/,
  )
  assert.doesNotMatch(types, /\| 'reviews'|\| 'clusters'/)
  assert.match(app, /const primaryPages[\s\S]*id: 'tasks'[\s\S]*id: 'progress'/)
  assert.match(app, /const auditPages[\s\S]*id: 'risks'[\s\S]*id: 'style'[\s\S]*id: 'duplicates'[\s\S]*id: 'aesthetics'/)
  assert.match(app, /const utilityPages[\s\S]*id: 'models'[\s\S]*id: 'system'/)
  assert.match(app, /className="nav-section-label">审计<\/span>/)
  assert.match(app, /className="nav-divider"/)
  assert.doesNotMatch(app, /label: '复核'|label: '聚类'/)
  assert.match(app, /reviews: 'risks'/)
  assert.match(app, /clusters: 'duplicates'/)
  assert.match(app, /onClick=\{\(\) => navigate\('tasks'\)\}/)

  assert.match(stylePage, /listStyleAudit\(taskId,/)
  assert.match(stylePage, /classificationLabel/)
  assert.match(stylePage, /review_eligible/)
  assert.match(duplicatesPage, /listDuplicateGroupAudit\(/)
  assert.match(duplicatesPage, /submitCuratedReviewDecisions\(/)
  assert.doesNotMatch(duplicatesPage, /<ReviewsPage/)
  assert.match(aestheticsPage, /listAestheticAudit\(/)
  assert.match(aestheticsPage, /bucket_counts/)
  assert.match(aestheticsPage, /invalid_counts/)
  assert.doesNotMatch(aestheticsPage, /<ReviewsPage/)
  for (const page of [stylePage, duplicatesPage, aestheticsPage]) {
    assert.doesNotMatch(page, /AI 候选|SAE 特征/)
  }
})
