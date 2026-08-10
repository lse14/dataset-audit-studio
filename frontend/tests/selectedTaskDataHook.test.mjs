import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_PATH = fileURLToPath(new URL('../src/App.tsx', import.meta.url))
const HOOK_PATH = fileURLToPath(new URL('../src/hooks/useSelectedTaskData.ts', import.meta.url))

async function hookSource() {
  return readFile(HOOK_PATH, 'utf8')
}

test('selected-task data hook exports the bounded React and client dependency surface', async () => {
  const source = await hookSource()

  assert.match(source, /export function useSelectedTaskData/)
  assert.match(source, /from 'react'/)
  assert.match(source, /from '\.\.\/clients\/tasks'/)
  assert.match(source, /from '\.\.\/clients\/components'/)
  assert.match(source, /from '\.\.\/clients\/workspace'/)
  assert.match(source, /from '\.\.\/profileWorkspace'/)
  assert.match(source, /from '\.\.\/types'/)
  assert.doesNotMatch(source, /transport\/taskEvents|taskRefreshPolicy|localStorage|\/api\/|from '\.\.\/pages\//)
})

test('selected-task data hook owns detail state and masks non-current task data', async () => {
  const source = await hookSource()

  assert.match(source, /\[overview, setOverview\]/)
  assert.match(source, /\[folders, setFolders\]/)
  assert.match(source, /\[events, setEvents\]/)
  assert.match(source, /\[componentRuns, setComponentRuns\]/)
  assert.match(source, /\[taskDataTaskId, setTaskDataTaskId\]/)
  assert.match(source, /taskDataTaskId === selectedTaskId/)
  assert.match(source, /overview: hasCurrentTaskData \? overview : null/)
  assert.match(source, /folders: hasCurrentTaskData \? folders : null/)
  assert.match(source, /events: hasCurrentTaskData \? events : \[\]/)
  assert.match(source, /componentRuns: hasCurrentTaskData \? componentRuns : \[\]/)
})

test('selected-task data hook keeps five independent reads in one Promise.all', async () => {
  const source = await hookSource()

  assert.match(source, /const \[eventList, task, taskOverview, runList, folderList\] = await Promise\.all\(\[/)
  assert.match(source, /listTaskEvents\(taskId\)/)
  assert.match(source, /getTask\(taskId\)/)
  assert.match(source, /getTaskOverview\(taskId\)/)
  assert.match(source, /listComponentRuns\(taskId\)/)
  assert.match(source, /listTaskFolders\(taskId\)/)
})

test('selected-task data hook preserves the profile success path and latest sequence', async () => {
  const source = await hookSource()

  assert.match(source, /isBuiltinProfileTask\(task\)/)
  assert.match(source, /upsertTask\(task\)/)
  assert.match(source, /setOverview\(taskOverview\)/)
  assert.match(source, /setFolders\(folderList\)/)
  assert.match(source, /setEvents\(eventList\.items\)/)
  assert.match(source, /setComponentRuns\(runList\.items\)/)
  assert.match(source, /setTaskDataTaskId\(taskId\)/)
  assert.match(source, /return eventList\.latest_sequence/)
})

test('selected-task data hook clears an invalid selected task and preserves error notification', async () => {
  const source = await hookSource()

  assert.match(source, /clearSelectedTask\(taskId\)/)
  assert.match(source, /无法读取任务详情/)
  assert.match(source, /return null/)
})

test('selected-task data hook invalidates and clears stale detail on selection changes', async () => {
  const source = await hookSource()

  assert.match(source, /useEffect\(\(\) => \{\s*setTaskDataTaskId\(null\)/)
  assert.match(source, /if \(!selectedTaskId\) \{[\s\S]*setOverview\(null\)/)
  assert.match(source, /setFolders\(null\)/)
  assert.match(source, /setEvents\(\[\]\)/)
  assert.match(source, /setComponentRuns\(\[\]\)/)
})

test('App delegates selected-task data while retaining storage and task-event orchestration', async () => {
  const app = await readFile(APP_PATH, 'utf8')

  assert.match(app, /from '\.\/hooks\/useSelectedTaskData'/)
  assert.match(app, /useSelectedTaskData\(\{/)
  assert.doesNotMatch(app, /const loadTaskData = useCallback/)
  assert.doesNotMatch(app, /listTaskEvents\(taskId\)|getTask\(taskId\)|getTaskOverview\(taskId\)|listComponentRuns\(taskId\)|listTaskFolders\(taskId\)/)
  assert.match(app, /window\.localStorage/)
  assert.match(app, /from '\.\/hooks\/useTaskEventRefresh'/)
  assert.match(app, /useTaskEventRefresh\(\{/)
  assert.match(app, /\[selectedFolder, setSelectedFolder\]/)
})
