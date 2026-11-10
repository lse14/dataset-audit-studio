import assert from 'node:assert/strict'
import test from 'node:test'

import { parseApiTime } from '../src/apiTime.ts'

test('parseApiTime reads a naive backend timestamp as UTC instead of local time', () => {
  assert.equal(parseApiTime('2026-10-28T05:21:39.337662').toISOString(), '2026-10-28T05:21:39.337Z')
  assert.equal(parseApiTime('2026-10-28T05:21:39').toISOString(), '2026-10-28T05:21:39.000Z')
})

test('parseApiTime keeps an explicit zone and tolerates surrounding whitespace', () => {
  assert.equal(parseApiTime('2026-10-28T05:21:39Z').toISOString(), '2026-10-28T05:21:39.000Z')
  assert.equal(parseApiTime('2026-10-28T13:21:39+08:00').toISOString(), '2026-10-28T05:21:39.000Z')
  assert.equal(parseApiTime('2026-10-28T13:21:39+0800').toISOString(), '2026-10-28T05:21:39.000Z')
  assert.equal(parseApiTime(' 2026-10-28T05:21:39.337662 ').toISOString(), '2026-10-28T05:21:39.337Z')
})
