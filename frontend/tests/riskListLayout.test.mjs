import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const STYLES_PATH = fileURLToPath(new URL('../src/styles.css', import.meta.url))
const RISKS_PAGE_PATH = fileURLToPath(new URL('../src/pages/RisksPage.tsx', import.meta.url))

test('risk rows stay in normal document flow instead of using virtual-list positioning', async () => {
  const styles = await readFile(STYLES_PATH, 'utf8')
  const virtualRows = styles.match(/\.event-row,[\s\S]*?\n\}/)?.[0]
  const baseRiskRow = styles.match(/^\.risk-row \{[\s\S]*?^\}$/m)?.[0]

  assert.ok(virtualRows, 'virtual-list row positioning rule must exist')
  assert.doesNotMatch(virtualRows, /\.risk-row/)
  assert.ok(baseRiskRow, 'base .risk-row rule must exist')
  assert.doesNotMatch(baseRiskRow, /position:\s*absolute/)
})

test('virtualized risk rows may use transform offsets without stacking unoffset content', async () => {
  const styles = await readFile(STYLES_PATH, 'utf8')
  const page = await readFile(RISKS_PAGE_PATH, 'utf8')

  assert.match(page, /from '@tanstack\/react-virtual'/)
  assert.match(page, /useVirtualizer\(/)
  assert.match(page, /getScrollElement:/)
  assert.match(page, /className="risk-list"/)
  assert.match(page, /translateY\(\$\{/)
  assert.match(page, /const limit = 100/)
  assert.doesNotMatch(page, /className="risk-list"[^>]*>\{items\.map/)

  assert.match(
    styles,
    /\.risk-list[\s\S]{0,180}\.risk-row[\s\S]{0,120}position:\s*absolute/,
    'virtualized .risk-row must use dedicated absolute positioning, not the shared event-row rule',
  )
})
