import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('Lone Trail keeps the existing audit shell while replacing its visual language', async () => {
  const [app, styles] = await Promise.all([source('App.tsx'), source('styles.css')])

  assert.match(app, /const primaryPages[\s\S]*id: 'tasks'[\s\S]*id: 'progress'/)
  assert.match(app, /const auditPages[\s\S]*id: 'risks'[\s\S]*id: 'style'[\s\S]*id: 'duplicates'[\s\S]*id: 'aesthetics'/)
  assert.match(styles, /--lone-paper:\s*#fff;/)
  assert.match(styles, /--lone-signal:\s*#FFFDAB;/)
  assert.match(styles, /\.app-shell\s*{[\s\S]*grid-template-columns:\s*220px minmax\(0, 1fr\)/)
  assert.match(styles, /\.sidebar::before\s*{[\s\S]*right:/)
  assert.match(styles, /\.nav-item\.active\s*{[\s\S]*background:\s*var\(--lone-signal\)/)
  assert.match(styles, /@media \(max-width: 860px\)\s*{[\s\S]*grid-template-columns:\s*64px minmax\(0, 1fr\)/)
  assert.doesNotMatch(styles, /background:\s*#242a27/)
})
