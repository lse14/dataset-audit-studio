import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createCompletionPrompt,
  isCompletionTransition,
} from '../src/taskStatusPrompt.ts'

function task(status, rowVersion = 12) {
  return {
    id: 'task-complete',
    name: '导出任务',
    status,
    row_version: rowVersion,
  }
}

test('creates a completion prompt that points to exports', () => {
  const prompt = createCompletionPrompt(task('completed'))
  assert.equal(prompt?.key, 'task-complete:completed:12')
  assert.equal(prompt?.targetPage, 'exports')
  assert.equal(prompt?.actionLabel, '查看导出')
  assert.match(prompt?.detail ?? '', /导出任务.*配置并预览 copy 导出/)
})

test('ignores tasks that are not completed', () => {
  assert.equal(createCompletionPrompt(task('exporting')), null)
  assert.equal(createCompletionPrompt(null), null)
})

test('changes the completion prompt key when the task version changes', () => {
  assert.notEqual(
    createCompletionPrompt(task('completed', 12))?.key,
    createCompletionPrompt(task('completed', 13))?.key,
  )
})

test('only treats a known non-completed state as a completion transition', () => {
  assert.equal(isCompletionTransition(task('completed'), 'exporting'), true)
  assert.equal(isCompletionTransition(task('completed'), 'completed'), false)
  assert.equal(isCompletionTransition(task('completed'), undefined), false)
  assert.equal(isCompletionTransition(task('exporting'), 'scanning'), false)
})
