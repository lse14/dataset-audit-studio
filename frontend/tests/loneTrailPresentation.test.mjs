import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('approved workbench composition is present in the shared shell', async () => {
  const [app, styles] = await Promise.all([source('App.tsx'), source('styles.css')])
  const cssBlocks = (selector, css = styles) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    return [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^{}]*)\\}`, 'g'))].map((match) => match[1])
  }

  assert.match(app, /const navigationGroups[\s\S]*mission[\s\S]*analysis[\s\S]*output[\s\S]*system/i)
  assert.match(app, /className=["']workspace-summary["']/)
  assert.match(app, /className=["']workbench-grid["']/)
  assert.match(app, /className=["']workbench-context["']/)
  assert.match(app, /statusLabel\(selectedTask\.status\)/)

  const primaryDefinition = app.match(/const primaryPages[\s\S]*?(?=\nconst auditPages)/)?.[0] ?? ''
  const auditDefinition = app.match(/const auditPages[\s\S]*?(?=\nconst exportPage)/)?.[0] ?? ''
  const exportDefinition = app.match(/const exportPage[\s\S]*?(?=\nconst utilityPages)/)?.[0] ?? ''
  const utilityDefinition = app.match(/const utilityPages[\s\S]*?(?=\nconst pages)/)?.[0] ?? ''
  assert.match(primaryDefinition, /id:\s*['"]tasks['"][\s\S]*id:\s*['"]progress['"]'/)
  assert.match(auditDefinition, /id:\s*['"]risks['"][\s\S]*id:\s*['"]style['"][\s\S]*id:\s*['"]duplicates['"][\s\S]*id:\s*['"]aesthetics['"]'/)
  assert.match(exportDefinition, /id:\s*['"]exports['"]'/)
  assert.match(utilityDefinition, /id:\s*['"]models['"][\s\S]*id:\s*['"]system['"]'/)

  assert.match(styles, /--workbench-sidebar:\s*192px/)
  assert.match(styles, /--workbench-header:\s*98px/)
  assert.ok(cssBlocks('.app-shell').some((block) => /grid-template-columns:\s*var\(--workbench-sidebar\)/.test(block)))
  assert.ok(cssBlocks('.brand-mark').some((block) => /border-bottom(?:-color)?:\s*[^;]*(?:var\(--workbench-ink\)|var\(--lone-ink\)|#(?:000|111|151a17)|black)/i.test(block)))
  assert.ok(cssBlocks('.topbar').some((block) => /border-bottom(?:-color)?:\s*[^;]*(?:var\(--workbench-ink\)|var\(--lone-ink\)|#(?:000|111|151a17)|black)/i.test(block)))
  assert.ok(cssBlocks('.workbench-grid').some((block) => /grid-template-columns:\s*minmax\(0, 1fr\)\s+224px/.test(block)))
  const last860 = styles.lastIndexOf('@media (max-width: 860px)')
  const next620 = styles.indexOf('@media (max-width: 620px)', last860)
  const mobileStyles = styles.slice(last860, next620 > last860 ? next620 : styles.length)
  assert.ok(cssBlocks('.app-shell', mobileStyles).some((block) => /grid-template-columns:\s*64px\s+minmax\(0, 1fr\)/.test(block)))
  assert.doesNotMatch(styles, /#242a27/i)
})
