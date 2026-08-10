import assert from 'node:assert/strict'
import { existsSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { API } from 'typescript/unstable/sync'
import * as ts from 'typescript/unstable/ast'

const FRONTEND_ROOT = fileURLToPath(new URL('../', import.meta.url))
const SOURCE_ROOT = path.join(FRONTEND_ROOT, 'src')
const TSCONFIG_PATH = path.join(FRONTEND_ROOT, 'tsconfig.app.json')

function compareStrings(left, right) {
  if (left < right) return -1
  if (left > right) return 1
  return 0
}

function normalizePath(filePath) {
  return path.relative(FRONTEND_ROOT, filePath).split(path.sep).join('/')
}

function typescriptFiles(directory) {
  const files = []
  for (const entry of readdirSync(directory, { withFileTypes: true }).sort((left, right) => compareStrings(left.name, right.name))) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...typescriptFiles(entryPath))
      continue
    }
    if (entry.isFile() && (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx'))) {
      files.push(entryPath)
    }
  }
  return files.sort((left, right) => compareStrings(normalizePath(left), normalizePath(right)))
}

function resolveRelativeImport(importerPath, specifier) {
  if (!specifier.startsWith('.')) return null

  const basePath = path.resolve(path.dirname(importerPath), specifier)
  const candidates = [
    basePath,
    `${basePath}.ts`,
    `${basePath}.tsx`,
    `${basePath}.d.ts`,
    path.join(basePath, 'index.ts'),
    path.join(basePath, 'index.tsx'),
  ]
  const resolved = candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile())
  if (!resolved) return null

  const normalized = normalizePath(resolved)
  return normalized.startsWith('src/') ? normalized : null
}

function recordKey(record) {
  return [
    record.file,
    String(record.line).padStart(8, '0'),
    String(record.column).padStart(8, '0'),
    record.kind,
    record.value,
  ].join('\u0000')
}

function formatRecords(records) {
  return records.length === 0
    ? '  <none>'
    : records.map((record) => `  ${record.file}:${record.line}:${record.column} ${record.kind} ${record.value}`).join('\n')
}

function collectArchitecture() {
  const records = []
  const api = new API({ cwd: FRONTEND_ROOT })
  let snapshot

  function addRecord(sourceFile, node, kind, value) {
    const position = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile))
    records.push({
      file: normalizePath(sourceFile.fileName),
      line: position.line + 1,
      column: position.character + 1,
      kind,
      value,
    })
  }

  try {
    snapshot = api.updateSnapshot({ openProjects: [TSCONFIG_PATH] })
    const project = snapshot.getProject(TSCONFIG_PATH)
    assert.ok(project, `Missing TypeScript project: ${TSCONFIG_PATH}`)

    for (const filePath of typescriptFiles(SOURCE_ROOT)) {
      const sourceFile = project.program.getSourceFile(filePath)
      assert.ok(sourceFile, `Missing TypeScript source file: ${filePath}`)

      function visit(node) {
        if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
          const specifier = node.moduleSpecifier.text
          const resolved = resolveRelativeImport(filePath, specifier)
          addRecord(sourceFile, node.moduleSpecifier, 'import', specifier)
          if (resolved === 'src/api.ts') {
            addRecord(sourceFile, node.moduleSpecifier, 'legacy-api-import', `${specifier} -> ${resolved}`)
          }
          if (resolved?.startsWith('src/transport/')) {
            addRecord(sourceFile, node.moduleSpecifier, 'transport-import', `${specifier} -> ${resolved}`)
          }
        }

        if (ts.isCallExpression(node)) {
          const callee = node.expression
          const isBareFetch = ts.isIdentifier(callee) && callee.text === 'fetch'
          const isGlobalFetch = ts.isPropertyAccessExpression(callee)
            && ts.isIdentifier(callee.expression)
            && (callee.expression.text === 'window' || callee.expression.text === 'globalThis')
            && callee.name.text === 'fetch'
          if (isBareFetch || isGlobalFetch) {
            addRecord(sourceFile, callee, 'raw-fetch', callee.getText(sourceFile))
          }
        }

        if (ts.isNewExpression(node)) {
          const constructor = node.expression
          const isBareEventSource = ts.isIdentifier(constructor) && constructor.text === 'EventSource'
          const isGlobalEventSource = ts.isPropertyAccessExpression(constructor)
            && ts.isIdentifier(constructor.expression)
            && (constructor.expression.text === 'window' || constructor.expression.text === 'globalThis')
            && constructor.name.text === 'EventSource'
          if (isBareEventSource || isGlobalEventSource) {
            addRecord(sourceFile, constructor, 'new-event-source', constructor.getText(sourceFile))
          }
        }

        if (
          ts.isStringLiteral(node)
          || ts.isNoSubstitutionTemplateLiteral(node)
          || ts.isTemplateHead(node)
        ) {
          if (node.text.startsWith('/api')) {
            addRecord(sourceFile, node, 'api-literal', node.text)
          }
        }

        node.forEachChild(visit)
      }

      visit(sourceFile)
    }

    return records.sort((left, right) => compareStrings(recordKey(left), recordKey(right)))
  } finally {
    try {
      snapshot?.dispose()
    } finally {
      api.close()
    }
  }
}

function ownerFiles(records, kind) {
  return [...new Set(records.filter((record) => record.kind === kind).map((record) => record.file))]
    .sort(compareStrings)
}

test('architecture inventory is deterministic and sorted', () => {
  const first = collectArchitecture()
  const second = collectArchitecture()

  assert.deepEqual(first, second, 'repeated TypeScript AST inventory must be deterministic')
  assert.deepEqual(
    first,
    [...first].sort((left, right) => compareStrings(recordKey(left), recordKey(right))),
    `architecture records must be sorted by a stable composite key:\n${formatRecords(first)}`,
  )
  assert.deepEqual(
    typescriptFiles(SOURCE_ROOT).map(normalizePath),
    [...typescriptFiles(SOURCE_ROOT).map(normalizePath)].sort(compareStrings),
    'frontend TypeScript file paths must be sorted',
  )
})

test('raw fetch has the sole transport owner', () => {
  const records = collectArchitecture()
  const fetches = records.filter((record) => record.kind === 'raw-fetch')

  assert.ok(fetches.length > 0, `raw fetch inventory is empty:\n${formatRecords(records)}`)
  assert.deepEqual(
    ownerFiles(records, 'raw-fetch'),
    ['src/transport/http.ts'],
    `raw fetch owners must be the HTTP transport:\n${formatRecords(fetches)}`,
  )
})

test('task events have the sole EventSource owner', () => {
  const records = collectArchitecture()
  const eventSources = records.filter((record) => record.kind === 'new-event-source')

  assert.ok(eventSources.length > 0, `EventSource inventory is empty:\n${formatRecords(records)}`)
  assert.deepEqual(
    ownerFiles(records, 'new-event-source'),
    ['src/transport/taskEvents.ts'],
    `EventSource owners must be the task-event transport:\n${formatRecords(eventSources)}`,
  )
})

test('App shell owns no raw API transport or endpoint', () => {
  const records = collectArchitecture()
  const appViolations = records.filter((record) => (
    record.file === 'src/App.tsx'
    && ['legacy-api-import', 'transport-import', 'raw-fetch', 'new-event-source', 'api-literal'].includes(record.kind)
  ))

  assert.deepEqual(
    appViolations,
    [],
    `App shell must not own raw API transport or endpoints:\n${formatRecords(appViolations)}`,
  )
})

test('pages and UI do not import legacy API or transport directly', () => {
  const records = collectArchitecture()
  const directImports = records.filter((record) => (
    (record.file.startsWith('src/pages/') || record.file.startsWith('src/components/') || record.file === 'src/ui.tsx')
    && ['legacy-api-import', 'transport-import'].includes(record.kind)
  ))

  assert.deepEqual(
    directImports,
    [],
    `pages and UI must not import the legacy API or transport directly:\n${formatRecords(directImports)}`,
  )
})

test('API endpoint literals live only in clients or transport', () => {
  const records = collectArchitecture()
  const endpointViolations = records.filter((record) => (
    record.kind === 'api-literal'
    && !record.file.startsWith('src/clients/')
    && !record.file.startsWith('src/transport/')
  ))

  assert.deepEqual(
    endpointViolations,
    [],
    `API endpoint literals must live in clients or transport:\n${formatRecords(endpointViolations)}`,
  )
})
