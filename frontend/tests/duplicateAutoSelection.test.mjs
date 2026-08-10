import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { selectDuplicateMembersForExclusion } from '../src/duplicateSelection.ts'

const DUPLICATES_PAGE_PATH = fileURLToPath(new URL('../src/pages/DuplicatesPage.tsx', import.meta.url))
const TYPES_PATH = fileURLToPath(new URL('../src/types.ts', import.meta.url))

function member({ id, path, pixelArea, reviewEligible = true, decision = null }) {
  return {
    sample_id: id,
    relative_path: path,
    pixel_area: pixelArea,
    review_eligible: reviewEligible,
    decision,
  }
}

function group(members) {
  return { members }
}

test('auto selection selects only eligible non-representatives from every displayed group', () => {
  const selected = selectDuplicateMembersForExclusion([
    group([
      member({ id: 'largest-a', path: 'a/largest.png', pixelArea: 1024 * 1024 }),
      member({ id: 'lower-a', path: 'a/lower.png', pixelArea: 768 * 768 }),
      member({ id: 'locked-a', path: 'a/locked.png', pixelArea: 512 * 512, reviewEligible: false, decision: 'approved_keep' }),
    ]),
    group([
      member({ id: 'largest-b', path: 'b/largest.png', pixelArea: 2048 * 2048 }),
      member({ id: 'lower-b', path: 'b/lower.png', pixelArea: 1024 * 1024 }),
    ]),
  ])

  assert.deepEqual([...selected], ['lower-a', 'lower-b'])
})

test('auto selection keeps the first relative path and then sample ID on equal pixel areas', () => {
  const selected = selectDuplicateMembersForExclusion([
    group([
      member({ id: 'sample-z', path: 'zeta.png', pixelArea: 1024 * 1024 }),
      member({ id: 'sample-b', path: 'alpha.png', pixelArea: 1024 * 1024 }),
      member({ id: 'sample-a', path: 'alpha.png', pixelArea: 1024 * 1024 }),
    ]),
  ])

  assert.deepEqual([...selected], ['sample-z', 'sample-b'])
})

test('auto selection ranks missing dimensions below known pixel areas and uses the stable fallback', () => {
  const selected = selectDuplicateMembersForExclusion([
    group([
      member({ id: 'known', path: 'known.png', pixelArea: 1024 * 1024 }),
      member({ id: 'missing', path: 'missing.png', pixelArea: null }),
    ]),
    group([
      member({ id: 'missing-z', path: 'zeta.png', pixelArea: null }),
      member({ id: 'missing-a', path: 'alpha.png', pixelArea: null }),
    ]),
  ])

  assert.deepEqual([...selected], ['missing', 'missing-z'])
})

test('duplicate page uses automatic exclusion selection and exposes pixel area in its member type', async () => {
  const [page, types] = await Promise.all([
    readFile(DUPLICATES_PAGE_PATH, 'utf8'),
    readFile(TYPES_PATH, 'utf8'),
  ])

  assert.match(page, /import \{ selectDuplicateMembersForExclusion \} from '..\/duplicateSelection'/)
  assert.match(page, /selectDuplicateMembersForExclusion\(audit\?\.items \?\? \[\]\)/)
  assert.match(page, /本页自动选择可排除成员/)
  assert.match(types, /pixel_area: number \| null/)
})
