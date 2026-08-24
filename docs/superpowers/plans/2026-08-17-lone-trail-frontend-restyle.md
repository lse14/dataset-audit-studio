# Lone Trail Frontend Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the existing dataset-audit WebUI with the approved Lone Trail paper-white visual system while preserving every route, control, data surface, and responsive layout.

**Architecture:** Keep all product code and page composition unchanged. Add one source-level presentation contract, then centralize the visual change in the existing global stylesheet using shared Lone Trail tokens and late-cascade overrides. The `App.tsx` shell is not changed unless rendered alignment proves a CSS-only divider cannot share the topbar's Y coordinate.

**Tech Stack:** React 19, TypeScript, Vite, CSS, Node test runner, Playwright.

---

## File Structure

- Modify: `frontend/src/styles.css` — retains every layout track and responsive breakpoint while replacing the shared visual language.
- Create: `frontend/tests/loneTrailPresentation.test.mjs` — locks visual tokens, sidebar structure, desktop/mobile shell geometry, and the absence of deprecated dark-shell styling.
- Modify: `frontend/package.json` — includes the new presentation contract in the existing frontend unit-test command.
- Modify: `ROADMAP.md` and `MEMORY.md` — records scope, status, verification evidence, and the final visual decision.

### Task 1: Lock the Approved Visual Boundary

**Files:**
- Create: `frontend/tests/loneTrailPresentation.test.mjs`
- Modify: `frontend/package.json`

- [x] **Step 1: Write the failing visual contract**

```js
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('Lone Trail keeps the existing audit shell while replacing its visual language', async () => {
  const [app, styles] = await Promise.all([source('App.tsx'), source('styles.css')])

  assert.match(app, /const primaryPages[\s\S]*id: 'tasks'[\s\S]*id: 'progress'/)
  assert.match(app, /const auditPages[\s\S]*id: 'risks'[\s\S]*id: 'style'[\s\S]*id: 'duplicates'[\s\S]*id: 'aesthetics'/)
  assert.match(styles, /--lone-paper:\s*#fff;/)
  assert.match(styles, /--lone-signal:\s*#FFFDAB;/)
  assert.match(styles, /\.app-shell\s*{[\s\S]*grid-template-columns:\s*220px minmax\(0, 1fr\)/)
  assert.match(styles, /\.sidebar::before\s*{[\s\S]*right:/)
  assert.match(styles, /\.nav-item\.active\s*{[\s\S]*background:\s*var\(--lone-signal\)/)
  assert.match(styles, /@media \(max-width: 860px\)\s*{[\s\S]*grid-template-columns:\s*64px minmax\(0, 1fr\)/)
  assert.doesNotMatch(styles, /background:\s*#242a27/)
})
```

- [x] **Step 2: Add the test to the existing unit-test command**

Add `tests/loneTrailPresentation.test.mjs` to the explicit `node --experimental-strip-types --test` list in `frontend/package.json`.

- [x] **Step 3: Run the focused test and verify it fails**

Run: `npm test -- --test-name-pattern="Lone Trail keeps"`

Expected: FAIL because the approved CSS tokens and sidebar baseline do not yet exist; no existing source or API test is changed.

### Task 2: Apply the Shared Lone Trail Visual Layer

**Files:**
- Modify: `frontend/src/styles.css`
- Test: `frontend/tests/loneTrailPresentation.test.mjs`

- [x] **Step 1: Add shared CSS tokens and preserve the shell tracks**

Append a late-cascade visual layer after existing rules. Define only the approved design tokens, preserving the existing `220px` desktop and `64px` narrow sidebar tracks:

```css
:root {
  --lone-paper: #fff;
  --lone-ground: #f4f4ef;
  --lone-signal: #FFFDAB;
  --lone-ink: #171717;
  --lone-muted: #6b6b63;
  --lone-rule: #d8d8cd;
  --lone-shadow: 5px 5px 0 #171717;
  --lone-header-height: 82px;
}
```

Do not alter `.app-shell` grid columns, page-specific grid tracks, route classes, DOM order, input names, or media-query breakpoints.

- [x] **Step 2: Restyle the shell and navigation**

Make the sidebar paper-white and retain a single right-side baseline through `.sidebar::before`. Set `.brand-mark` and `.topbar` to `min-height: var(--lone-header-height)` and give both their lower structural rule, so their Y coordinate is shared. Keep the current nav grouping and labels; add the lower menu rule through `.nav-item::after`, ending before the one baseline. Use `var(--lone-signal)` for `.nav-item.active`; do not add tick marks, a second baseline, dark blocks, or new navigation items.

- [x] **Step 3: Restyle shared operational surfaces without changing their geometry**

Use paper-white backgrounds, quiet neutral rules, sharp or `2px` corners, tabular/monospace metadata, and a `4-7px` right/down shadow only for main anchors (`.task-command-bar`, primary `.panel` surfaces, `.modal`). Keep tables, virtual lists, data grids, form fields, dialogs, tooltips, focus rings, status semantics, and error colors functional. Primary actions and progress tracks receive the yellow signal; destructive states stay semantic red rather than becoming decorative black.

- [x] **Step 4: Preserve narrow layouts with the same content behavior**

At `860px`, retain the icon-only sidebar and `64px` track; suppress the baseline pseudo-element if it would overlap the icon-only controls. At `620px`, keep the existing stacked topbar, toolbar, summary-grid, form, and table rules. Add only visual overrides in these media queries, including a thinner `2-5px` hard shadow and visible focus outlines.

- [x] **Step 5: Run the focused visual contract and the full frontend unit suite**

Run: `npm test -- --test-name-pattern="Lone Trail keeps"`

Expected: PASS.

Run: `npm test`

Expected: all existing frontend contracts plus the new presentation contract pass.

### Task 3: Build and Render the Existing Workflows

**Files:**
- Modify: `ROADMAP.md`
- Modify: `MEMORY.md`

- [x] **Step 1: Build the unchanged frontend application**

Run: `npm run build`

Expected: TypeScript compilation and Vite build complete successfully; no API/client code is modified.

- [x] **Step 2: Verify the task shell at desktop and mobile widths**

Run the existing local WebUI or Vite preview, inspect `/#tasks` at `1440x900` and `390x844`, then measure or inspect bounding boxes for the brand divider and topbar divider. Confirm a single sidebar baseline, current-task selection, controls, input focus, no horizontal overflow, and no clipped labels.

- [x] **Step 3: Verify one data-dense audit page and one progress page**

Inspect `/#progress` and one audit route with current/mock data. Confirm table and virtual-row geometry remain intact, status colors still map to real state, primary action state works, and no visual rule introduces false metrics or unusable controls.

- [x] **Step 4: Record verified results**

Update the roadmap item to `[x]` only after the unit suite, production build, and both viewport checks succeed. Add the exact commands, inspected viewports, and any remaining limitation to `MEMORY.md`.

## Plan Self-Review

- Scope coverage: the plan preserves page IDs/order, routes, controls, tables, virtual lists, mobile breakpoints, interaction states, and API boundaries while applying every confirmed Lone Trail visual requirement.
- Placeholder scan: no deferred implementation or unowned files remain; test, CSS, build, browser evidence, and documentation updates have explicit owners.
- Type consistency: no TypeScript type, component prop, API client, or backend contract changes are introduced.
