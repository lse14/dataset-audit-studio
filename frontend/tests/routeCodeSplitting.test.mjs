import assert from 'node:assert/strict'
import { access, readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const appPath = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const dataPagesPath = fileURLToPath(new URL('../src/pages/DataPages.tsx', import.meta.url))
const owners = [
  ['RisksPage.tsx', 'RisksPage'],
  ['StylePage.tsx', 'StylePage'],
  ['DuplicatesPage.tsx', 'DuplicatesPage'],
  ['AestheticsPage.tsx', 'AestheticsPage'],
  ['ExportsPage.tsx', 'ExportsPage'],
]

test('defers page modules behind React lazy boundaries', async () => {
  const app = await readFile(appPath, 'utf8')

  assert.match(app, /\{[^}]*lazy[^}]*Suspense[^}]*\} from 'react'/)
  assert.match(app, /const TasksPage = lazy\(/)
  assert.match(app, /const RisksPage = lazy\(/)
  assert.match(app, /const StylePage = lazy\(/)
  assert.match(app, /const DuplicatesPage = lazy\(/)
  assert.match(app, /const AestheticsPage = lazy\(/)
  assert.match(app, /const ExportsPage = lazy\(/)
  assert.match(app, /import\('\.\/pages\/RisksPage'\)/)
  assert.match(app, /import\('\.\/pages\/StylePage'\)/)
  assert.match(app, /import\('\.\/pages\/DuplicatesPage'\)/)
  assert.match(app, /import\('\.\/pages\/AestheticsPage'\)/)
  assert.match(app, /import\('\.\/pages\/ExportsPage'\)/)
  assert.doesNotMatch(app, /const ReviewsPage = lazy\(/)
  assert.doesNotMatch(app, /const ClustersPage = lazy\(/)
  assert.doesNotMatch(app, /pages\/DataPages/)
  assert.match(app, /<Suspense fallback=/)
  assert.doesNotMatch(app, /^import \{ .*Page.* \} from '\.\/pages\//m)

  await assert.rejects(access(dataPagesPath))
  for (const [fileName, exportName] of owners) {
    const owner = await readFile(fileURLToPath(new URL(`../src/pages/${fileName}`, import.meta.url)), 'utf8')
    assert.match(owner, new RegExp(`export function ${exportName}`))
    assert.doesNotMatch(owner, /DataPages/)
  }
})
