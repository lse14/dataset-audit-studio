import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const STYLES_PATH = fileURLToPath(new URL('../src/styles.css', import.meta.url))

test('risk rows stay in normal document flow instead of using virtual-list positioning', async () => {
  const styles = await readFile(STYLES_PATH, 'utf8')
  const virtualRows = styles.match(/\.event-row,[\s\S]*?\n\}/)?.[0]

  assert.ok(virtualRows, 'virtual-list row positioning rule must exist')
  assert.doesNotMatch(virtualRows, /\.risk-row/)
})
