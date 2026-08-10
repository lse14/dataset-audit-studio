import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = new URL('../src/', import.meta.url)
const legacyTerm = 'capt' + 'ion'

async function source(path) {
  return readFile(fileURLToPath(new URL(path, root)), 'utf8')
}

test('frontend exposes annotation and latent output switches without legacy output state', async () => {
  const [schemaField, types, exportPage] = await Promise.all([
    source('components/SchemaField.tsx'),
    source('types.ts'),
    source('pages/ExportsPage.tsx'),
  ])

  assert.match(schemaField, /keep_annotation_files/)
  assert.match(schemaField, /keep_latent_files/)
  assert.doesNotMatch(schemaField, /\bstages:/)
  assert.equal(types.toLowerCase().includes(`missing_${legacyTerm}`), false)
  assert.equal(exportPage.toLowerCase().includes(legacyTerm), false)
})
