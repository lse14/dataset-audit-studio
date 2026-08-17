import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('approved workbench composition is present in the shared shell', async () => {
  const [app, styles] = await Promise.all([source('App.tsx'), source('styles.css')])

  assert.match(app, /const navigationGroups[\s\S]*mission[\s\S]*analysis[\s\S]*output[\s\S]*system/i)
  assert.match(app, /className=["']workspace-summary["']/)
  assert.match(app, /className=["']workbench-grid["']/)
  assert.match(app, /className=["']workbench-context["']/)
  assert.match(app, /statusLabel\(selectedTask\.status\)/)

  for (const routeId of [
    'tasks',
    'progress',
    'risks',
    'style',
    'duplicates',
    'aesthetics',
    'exports',
    'models',
    'system',
  ]) {
    assert.match(app, new RegExp(`id:\\s*['"]${routeId}['"]`))
  }

  assert.match(styles, /--workbench-sidebar:\s*192px/)
  assert.match(styles, /--workbench-header:\s*98px/)
  assert.match(styles, /\.app-shell\s*{[\s\S]*grid-template-columns:\s*var\(--workbench-sidebar\)/)
  assert.match(styles, /\.brand-mark\s*{[\s\S]*border-bottom:\s*[^;]*#(?:000|0f0f0f|151a17)/i)
  assert.match(styles, /\.topbar\s*{[\s\S]*border-bottom:\s*[^;]*#(?:000|0f0f0f|151a17)/i)
  assert.match(styles, /\.workbench-grid\s*{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)\s+224px/)
  assert.match(styles, /@media\s*\(max-width:\s*860px\)[\s\S]*grid-template-columns:\s*64px\s+minmax\(0, 1fr\)/)
  assert.doesNotMatch(styles, /#242a27/i)
})
