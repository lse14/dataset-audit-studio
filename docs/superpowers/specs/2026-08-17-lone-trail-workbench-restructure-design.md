# Lone Trail Workbench Restructure Design

**Date:** 2026-08-17

## Goal

Recompose the complete Dataset Audit Studio frontend around the approved Lone Trail desktop reference. The application becomes a paper-white audit workbench with a ruler sidebar, aligned header divider, real task summary strip, asymmetric content region, and a contextual secondary column. The redesign changes composition and visual hierarchy throughout the application; it does not add product capabilities.

## Evidence And Reference

- Approved reference: `C:\Users\lse\.codex\skills\design-lone-trail-frontend\assets\approved-layout.png`, inspected at its full `1440x900` size.
- The reference establishes structural intent only: one sidebar ruler baseline, short navigation rules, a shared sidebar-brand and page-header divider Y coordinate, a three-cell summary strip, a broad primary data region, and a narrow right context region.
- Placeholder rows, labels, values, and empty-state copy in the reference will not be copied into the product.

## Scope

### Changes

- Rebuild the shared application shell and every route's information composition.
- Group existing routes as Mission, Analysis, Output, and System with stable visible sequence numbers.
- Add a shell-level summary strip sourced only from existing selected-task and runtime values.
- Give every page a primary data region and a responsive contextual region without inventing metrics, records, alerts, or task status.
- Rework desktop and mobile CSS so desktop follows the reference geometry and mobile remains usable without horizontal overflow.

### Preserved Boundaries

- Existing routes, hash aliases, API clients, data types, backend behavior, task states, and keyboard behavior.
- Existing forms, labels, controls, field names, validation, table/virtual-list semantics, review decisions, and export workflow.
- Existing responsive capabilities, with only structural layout changes needed to present the same controls on narrow widths.
- No dependencies, images, game assets, fabricated operational data, or product affiliation claims.

## Shared Shell

The desktop shell uses a paper-white sidebar at a reference-led compact width and one black right-edge baseline. The brand is a two-line workspace identity. Navigation groups use small uppercase group labels, fixed ordinal numbers, thin lower rules that end before the baseline, and `#FFFDAB` only for the active route. The groups are:

- Mission: Tasks, Progress.
- Analysis: Risks, Style, Duplicates, Aesthetics.
- Output: Exports.
- System: Models, System.

The page header begins on the same Y coordinate as the sidebar brand divider. It contains a compact route breadcrumb and title, plus only the current page's existing primary command. Directly below it, the summary strip has three equal desktop cells. It is populated from existing values only: selected task identity, its current real status or the current page scope, and Worker/runtime state. Labels sit above values with a fixed 6px gap.

## Page Composition

All pages use a wide primary work area plus a narrow context column on desktop. The context area contains only existing data, empty-state guidance, filters, or controls already present in that route. It flows below the primary region on narrow screens.

- Tasks: task queue or configuration work remains primary; task selection and operational status form the context.
- Progress: real phase, event, and component-run data remain primary; task controls and runtime status form the context.
- Audit routes: current filters, selection state, review table/virtual list, and batch controls remain primary; selected-task/folder context remains secondary.
- Exports: current configuration, preview, and export history remain primary; task eligibility and export state remain secondary.
- Models and System: current inventories and health data remain primary; actions and concise runtime context remain secondary.

## Visual Rules

- Canvas and large surfaces are paper white or quiet neutral. Yellow is a local signal for the active route, selected count, primary progress, and one primary action.
- Typography, separators, and status ink are black or near-black. Metadata uses tabular or monospaced numerals where the existing data changes over time.
- Panels are delineated by whitespace and thin rules rather than heavy four-sided frames. Only page anchors use a restrained down-right black hard shadow.
- No dark canvas, glow, cyan HUD, gradients, warning stripes, decorative ruler ticks, thick black panel frames, or new fictional data.

## Responsive Behavior

- Desktop reproduces the reference hierarchy at `1440x900` without copying reference content.
- At narrow widths, navigation becomes a compact icon rail or accessible compact control; summary cells stack; the context region follows the primary region; controls remain reachable and labels do not clip.
- Focus styles remain visible; reduced-motion preferences remain honored; virtual rows retain their existing positioning behavior.

## Acceptance Criteria

- Rendered desktop screenshots show one sidebar baseline, navigation lower rules stopping before it, and exact Y alignment between sidebar-brand and page-header dividers.
- The screenshot structure visibly matches the approved reference while showing only real product data and labels.
- The application passes existing frontend contracts plus new layout contracts covering the shared shell, route grouping, summary source boundaries, and mobile layout rules.
- Production build succeeds; Playwright verifies desktop and mobile task, progress, one audit route, and export route with no relevant console errors or horizontal overflow.
