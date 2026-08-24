# Lone Trail Workbench Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recompose the complete Dataset Audit Studio frontend into the approved Lone Trail workbench without changing task, audit, export, model, or runtime behavior.

**Architecture:** Keep all page components, routes, hooks, clients, and backend contracts. Restructure only `App.tsx` to render grouped numbered navigation, a real-data summary strip, and a context region; replace the late Lone Trail CSS layer with the approved reference-led composition. Extend the source contract before making each implementation step, then verify existing workflows through production rendering.

**Tech Stack:** React 19, TypeScript, CSS, Vite, Node test runner, Playwright.

---

## File Structure

- Modify: `frontend/src/App.tsx` - grouped navigation, header, summary strip, and primary/context layout.
- Modify: `frontend/src/styles.css` - workbench geometry, surfaces, controls, and responsive rules.
- Modify: `frontend/tests/loneTrailPresentation.test.mjs` - source-level workbench composition contract.
- Modify: `ROADMAP.md` - approved scope and verified results.
- Modify: `MEMORY.md` - visual reference, preserved boundaries, and residual risk.

### Task 1: Lock The Workbench Contract

**Files:**
- Modify: `frontend/tests/loneTrailPresentation.test.mjs`
- Test: `frontend/src/App.tsx`
- Test: `frontend/src/styles.css`

- [ ] **Step 1: Replace the old preserve-layout contract with this failing expectation**

```js
test('Lone Trail implements the approved workbench composition without changing routes', async () => {
  const [app, styles] = await Promise.all([source('App.tsx'), source('styles.css')])

  assert.match(app, /const navigationGroups[\s\S]*id: 'mission'[\s\S]*id: 'analysis'[\s\S]*id: 'output'[\s\S]*id: 'system'/)
  assert.match(app, /className="workspace-summary"/)
  assert.match(app, /className="workbench-grid"/)
  assert.match(app, /className="workbench-context"/)
  assert.match(app, /statusLabel\(selectedTask\.status\)/)
  assert.match(app, /id: 'tasks'[\s\S]*id: 'progress'/)
  assert.match(app, /id: 'risks'[\s\S]*id: 'style'[\s\S]*id: 'duplicates'[\s\S]*id: 'aesthetics'/)
  assert.match(styles, /--workbench-sidebar:\s*192px;/)
  assert.match(styles, /--workbench-header:\s*98px;/)
  assert.match(styles, /\.app-shell\s*{[\s\S]*grid-template-columns:\s*var\(--workbench-sidebar\) minmax\(0, 1fr\)/)
  assert.match(styles, /\.brand-mark[\s\S]*border-bottom:\s*1px solid var\(--lone-ink\)/)
  assert.match(styles, /\.topbar[\s\S]*border-bottom:\s*1px solid var\(--lone-ink\)/)
  assert.match(styles, /\.workbench-grid\s*{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\) 224px/)
  assert.match(styles, /@media \(max-width: 860px\)[\s\S]*grid-template-columns:\s*64px minmax\(0, 1fr\)/)
  assert.doesNotMatch(styles, /background:\s*#242a27/)
})
```

- [ ] **Step 2: Prove the contract is red before implementation**

Run: `npm test -- --test-name-pattern="approved workbench"`

Expected: failure because `navigationGroups`, workbench markup, and `--workbench-*` tokens do not exist.

- [ ] **Step 3: Commit the failing contract**

```bash
git add frontend/tests/loneTrailPresentation.test.mjs
git commit -m "test: define lone trail workbench contract"
```

### Task 2: Recompose The Shared React Shell

**Files:**
- Modify: `frontend/src/App.tsx:31-483`
- Test: `frontend/tests/loneTrailPresentation.test.mjs`

- [ ] **Step 1: Add route group metadata while retaining every existing route and alias**

```tsx
type PageDefinition = {
  id: PageId
  label: string
  title: string
  subtitle: string
  section: 'MISSION' | 'ANALYSIS' | 'OUTPUT' | 'SYSTEM'
  sequence: string
  icon: typeof Gauge
}

const navigationGroups = [
  { id: 'mission', label: 'MISSION', items: primaryPages },
  { id: 'analysis', label: 'ANALYSIS', items: auditPages },
  { id: 'output', label: 'OUTPUT', items: [exportPage] },
  { id: 'system', label: 'SYSTEM', items: utilityPages },
] as const
```

Set sequences `01` through `09` in existing workflow order. Keep `pages`, `auditPageIds`, and `legacyPageAliases` behavior unchanged.

- [ ] **Step 2: Render grouped numbered navigation with the existing buttons and names**

```tsx
const renderNavItem = (item: PageDefinition) => {
  const Icon = item.icon
  return (
    <button aria-current={page === item.id ? 'page' : undefined} className={page === item.id ? 'nav-item active' : 'nav-item'} key={item.id} onClick={() => navigate(item.id)} title={item.label} type="button">
      <span aria-hidden="true" className="nav-sequence">{item.sequence}</span>
      <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
      <span>{item.label}</span>
    </button>
  )
}
```

Render each `navigationGroups` item inside `nav[aria-label="主导航"]` as `section.nav-section` with its own `nav-section-label`. Do not change button names, click handlers, or route hashes.

- [ ] **Step 3: Move task selection into a real-data context column and add the summary strip**

Import `statusLabel` from `./ui`, then derive values only from existing state:

```tsx
const selectedTaskName = selectedTask?.name ?? '未选择任务'
const selectedTaskStatus = selectedTask ? statusLabel(selectedTask.status) : '等待任务选择'
const workerStatus = health?.worker.running ? '在线' : '停止'
```

Use this structure while keeping the existing `Suspense` route switch and all page props inside `workbench-primary` unchanged:

```tsx
<main className="workspace-main">
  <header className="topbar">
    <div className="page-title"><span>{active.section} / {active.label}</span><h1>{active.title}</h1></div>
    <button aria-label="刷新当前页面" className="icon-button" onClick={() => void refreshAll()} title="刷新" type="button"><RefreshCw size={18} /></button>
  </header>
  <section aria-label="当前工作摘要" className="workspace-summary">
    <div><span>当前任务</span><strong>{selectedTaskName}</strong></div>
    <div><span>任务状态</span><strong>{selectedTaskStatus}</strong></div>
    <div><span>Worker</span><strong>{workerStatus}</strong></div>
  </section>
  <div className="content">
    <div className="workbench-grid">
      <section className="workbench-primary">
        <Suspense fallback={<LoadingBlock label="正在加载页面" />}>
          {page === 'system' ? <SystemPage error={healthError} health={health} loading={healthLoading} /> : null}
          {page === 'tasks' ? <TasksPage notify={notify} onChanged={onTaskChanged} onDeleted={onTaskDeleted} onSelect={setSelectedTaskId} selectedTaskId={selectedTaskId} tasks={tasks} /> : null}
          {page === 'progress' ? <ProgressPage components={components} componentRuns={componentRuns} events={events} health={health} notify={notify} onChanged={onTaskChanged} overview={overview} sseConnected={sseConnected} task={selectedTask} /> : null}
          {page === 'style' ? <StylePage key={selectedTaskId ?? 'none'} folder={selectedFolder} folders={folders} notify={notify} onFolderChange={setSelectedFolder} task={selectedTask} /> : null}
          {page === 'duplicates' ? <DuplicatesPage key={selectedTaskId ?? 'none'} folder={selectedFolder} folders={folders} notify={notify} onFolderChange={setSelectedFolder} task={selectedTask} /> : null}
          {page === 'aesthetics' ? <AestheticsPage key={selectedTaskId ?? 'none'} folder={selectedFolder} folders={folders} notify={notify} onFolderChange={setSelectedFolder} task={selectedTask} /> : null}
          {page === 'risks' ? <RisksPage key={selectedTaskId ?? 'none'} folder={selectedFolder} folders={folders} notify={notify} onChanged={onAuditChanged} onFolderChange={setSelectedFolder} overview={overview} task={selectedTask} /> : null}
          {page === 'exports' ? <ExportsPage key={selectedTaskId ?? 'none'} task={selectedTask} /> : null}
          {page === 'models' ? <ModelsPage components={components} componentRuns={componentRuns} notify={notify} task={selectedTask} /> : null}
        </Suspense>
      </section>
      <aside aria-label="当前上下文" className="workbench-context">
        <span className="context-eyebrow">CURRENT CONTEXT</span><h2>{active.title}</h2>
        <dl><div><dt>Task ID</dt><dd>{selectedTask?.id ?? '--'}</dd></div><div><dt>Task state</dt><dd>{selectedTaskStatus}</dd></div><div><dt>Worker</dt><dd>{workerStatus}</dd></div></dl>
        {page !== 'system' ? (
          <label className="task-selector"><span>任务</span><select onChange={(event) => setSelectedTaskId(event.target.value || null)} value={selectedTaskId ?? ''}>
            <option value="">未选择</option>
            {tasks.map((task) => <option key={task.id} value={task.id}>{task.name}</option>)}
          </select></label>
        ) : null}
      </aside>
    </div>
  </div>
</main>
```

No new count, ETA, record, status, or endpoint is permitted. The existing task `select` keeps its label, option values, change handler, and local-storage behavior.

- [ ] **Step 4: Verify the shell turns the focused contract green and remains buildable**

Run: `npm test -- --test-name-pattern="approved workbench"`

Expected: pass.

Run: `npm run build`

Expected: TypeScript and Vite exit `0`.

- [ ] **Step 5: Commit the shell change**

```bash
git add frontend/src/App.tsx frontend/tests/loneTrailPresentation.test.mjs
git commit -m "feat: compose lone trail workbench shell"
```

### Task 3: Implement Reference-Led Layout And Responsive Rules

**Files:**
- Modify: `frontend/src/styles.css:3633-4100`
- Test: `frontend/tests/loneTrailPresentation.test.mjs`

- [ ] **Step 1: Replace the old late Lone Trail override with the new geometry layer**

```css
:root {
  --lone-paper: #fff;
  --lone-ground: #f5f4ee;
  --lone-signal: #FFFDAB;
  --lone-ink: #171717;
  --lone-muted: #66665d;
  --lone-rule: #d8d7cc;
  --lone-soft-rule: #e6e5dc;
  --lone-shadow: 5px 5px 0 var(--lone-ink);
  --workbench-sidebar: 192px;
  --workbench-header: 98px;
}

.app-shell { grid-template-columns: var(--workbench-sidebar) minmax(0, 1fr); background: var(--lone-ground); }
.sidebar { width: var(--workbench-sidebar); }
.brand-mark,
.topbar { min-height: var(--workbench-header); border-bottom: 1px solid var(--lone-ink); }
```

Make `.sidebar::before` the sole desktop baseline. Use `.nav-item::after` for short lower rules that end before the baseline. Keep inactive navigation paper white and active navigation `var(--lone-signal)`.

- [ ] **Step 2: Compose the header, summary, primary surface, and context region**

```css
.workspace-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); background: var(--lone-paper); border-bottom: 1px solid var(--lone-rule); }
.workspace-summary > div { display: grid; gap: 6px; min-height: 84px; padding: 20px 30px; }
.workspace-summary > div + div { border-left: 1px solid var(--lone-rule); }
.workbench-grid { display: grid; grid-template-columns: minmax(0, 1fr) 224px; gap: 30px; align-items: start; }
.workbench-context { display: grid; gap: 18px; padding-left: 20px; border-left: 1px solid var(--lone-ink); }
```

Use muted labels and tabular metadata. Retain every page's actual body. Change shared panel surfaces to whitespace and thin separators; apply the hard shadow only to the first primary work anchor, task command bar, and modal.

- [ ] **Step 3: Restyle existing controls without changing semantics**

Keep current inputs, selects, buttons, checkboxes, status pills, tables, virtual rows, dialogs, tooltips, error blocks, and toasts in their current DOM positions. Use sharp corners, visible black focus outlines, paper backgrounds, yellow primary actions/progress, semantic red errors, and the existing reduced-motion behavior.

- [ ] **Step 4: Add narrow-screen rules that preserve access to every control**

```css
@media (max-width: 1080px) {
  .workbench-grid { grid-template-columns: 1fr; }
  .workbench-context { grid-template-columns: repeat(3, minmax(0, 1fr)); padding: 20px 0 0; border-top: 1px solid var(--lone-ink); border-left: 0; }
}
@media (max-width: 860px) {
  .app-shell { grid-template-columns: 64px minmax(0, 1fr); }
  .sidebar { width: 64px; }
  .sidebar::before { display: none; }
  .nav-sequence, .nav-item > span:last-child, .nav-section-label, .brand-mark > span { display: none; }
}
@media (max-width: 620px) {
  :root { --lone-shadow: 3px 3px 0 var(--lone-ink); }
  .workspace-summary, .workbench-context { grid-template-columns: 1fr; }
  .workspace-summary > div + div { border-top: 1px solid var(--lone-rule); border-left: 0; }
}
```

Keep existing page-level media queries and virtual-list positioning rules untouched.

- [ ] **Step 5: Verify and commit the visual layer**

Run: `npm test -- --test-name-pattern="approved workbench"`

Expected: pass.

Run: `npm test`

Expected: all frontend contracts pass.

Run: `npm run build`

Expected: exit `0`.

```bash
git add frontend/src/styles.css frontend/tests/loneTrailPresentation.test.mjs
git commit -m "style: rebuild lone trail workbench layout"
```

### Task 4: Render, Interact, And Record Evidence

**Files:**
- Modify: `ROADMAP.md`
- Modify: `MEMORY.md`
- Test: `frontend/e2e/task-workflows.spec.ts`
- Test: `frontend/e2e/task7-profile-workspace.spec.ts`
- Test: `frontend/e2e/r1033-export-ui.spec.ts`

- [ ] **Step 1: Run the existing browser regression suite**

Run: `npm run test:e2e`

Expected: task creation, task selection, route aliases, audit decisions, export controls, and mobile settings retain existing behavior.

- [ ] **Step 2: Verify the main checkout production build at desktop and mobile sizes**

Run: `npm run build`

Run: `start_webui.bat`

At `http://127.0.0.1:7865`, inspect `/#tasks`, `/#progress`, one audit route, and `/#exports` at `1440x900` and `390x844`. Capture page identity, meaningful DOM, console warnings/errors, screenshot, and `scrollWidth <= clientWidth` for the visible work surface.

- [ ] **Step 3: Check the shell changes through the real interaction path**

Select an existing task in the context selector, use the refresh icon, then navigate through Progress, an audit page, and Exports using numbered sidebar buttons. Verify the hash, page heading, selector value, and browser console after each transition. Measure `.brand-mark` and `.topbar` bottom edges on desktop; they must match.

- [ ] **Step 4: Record only completed verification and clean task-owned QA artifacts**

Append `## Lone Trail Workbench Restructure (2026-08-17)` to `ROADMAP.md` and `MEMORY.md`. Record the approved asset path, preserved boundaries, commands, URLs, viewports, divider measurement, interaction evidence, and any untested state. Mark the roadmap item `[x]` only after all commands and browser checks pass. Delete only screenshots and temporary probes created for this task; do not remove user-owned `.tmp-*` directories or a running WebUI.

- [ ] **Step 5: Commit the verification record**

```bash
git add ROADMAP.md MEMORY.md
git commit -m "docs: record lone trail workbench verification"
```

## Plan Self-Review

- Spec coverage: Tasks 1-3 implement the reference-led sidebar, divider alignment, grouped navigation, summary strip, asymmetric primary/context layout, visual rules, and responsive behavior. Task 4 validates task, progress, audit, and export workflows.
- Scope check: No API, backend, stored data, route, field-name, or product-capability change is planned.
- Type consistency: `PageDefinition.section`, `PageDefinition.sequence`, `navigationGroups`, `statusLabel`, `workspace-summary`, `workbench-grid`, and `workbench-context` use the same names throughout.
- Placeholder scan: All tasks identify exact files, code, commands, and expected results.
