import assert from 'node:assert/strict'
import test from 'node:test'

import { createReviewGatePrompt } from '../src/reviewGatePrompt.ts'

function task(status, rowVersion = 7) {
  return {
    id: 'task-1',
    name: '测试任务',
    status,
    row_version: rowVersion,
  }
}

test('does not create a prompt for the removed AI review gate', () => {
  assert.equal(createReviewGatePrompt(task('awaiting_ai_review')), null)
})

test('creates the curated review prompt', () => {
  const prompt = createReviewGatePrompt(task('evidence_review'))
  assert.equal(prompt?.targetPage, 'risks')
  assert.equal(prompt?.actionLabel, '进入审计')
  assert.match(prompt?.detail ?? '', /审计/)
})

test('ignores non-review states and changes the key with row version', () => {
  assert.equal(createReviewGatePrompt(task('model_scoring')), null)
  assert.equal(createReviewGatePrompt(null), null)
  assert.notEqual(
    createReviewGatePrompt(task('evidence_review', 7))?.key,
    createReviewGatePrompt(task('evidence_review', 8))?.key,
  )
})
