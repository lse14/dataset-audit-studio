import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = new URL('../src/', import.meta.url)
const legacyTerm = 'capt' + 'ion'

async function source(path) {
  return readFile(fileURLToPath(new URL(path, root)), 'utf8')
}

test('frontend exposes the annotation output switch without latent or legacy output state', async () => {
  const [schemaField, types, exportPage, help] = await Promise.all([
    source('components/SchemaField.tsx'),
    source('types.ts'),
    source('pages/ExportsPage.tsx'),
    source('taskConfigHelp.ts'),
  ])

  assert.match(schemaField, /keep_annotation_files/)
  for (const item of [schemaField, types, exportPage, help]) assert.doesNotMatch(item, /latent|mikazuki/i)
  assert.match(exportPage, /keep_annotation_files/)
  assert.doesNotMatch(exportPage, /不带标注/)
  assert.doesNotMatch(schemaField, /\bstages:/)
  assert.equal(types.toLowerCase().includes(`missing_${legacyTerm}`), false)
  assert.equal(exportPage.toLowerCase().includes(legacyTerm), false)
})
