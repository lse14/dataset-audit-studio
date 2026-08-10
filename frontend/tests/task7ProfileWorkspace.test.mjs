import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  coverageRequestState,
  coverageScopeIdentity,
  currentTaskProfile,
  isDatasetProfile,
  profileTaskSubmissionComponents,
  profileWorkspaceSummary,
  toggleProfileResolution,
  validateExportRunAestheticMinimum,
  validateStyleArtistWeights,
} from '../src/profileWorkspace.ts'

const expectedRoot = Buffer.from('5f5f726f6f745f5f', 'hex').toString('ascii')
const wrongStarRoot = Buffer.from('2a2a726f6f742a2a', 'hex').toString('ascii')
const root = new URL('../src/', import.meta.url)

const source = (path) => readFile(new URL(path, root), 'utf8')

function task(profile, status = 'completed', resumeState = null) {
  return {
    id: 'task-7',
    name: 'Task 7 fixture',
    status,
    resume_state: resumeState,
    config: profile
      ? {
          profile,
        }
      : {},
  }
}

function components(aestheticBins = 'score_x2_floor') {
  return {
    'export.dataset': {
      enabled: true,
      config: {
        aesthetic_bins: aestheticBins,
        keep_annotation_files: true,
      },
    },
    'media.scan': {
      enabled: true,
      config: { resolutions: [512, 768, 1024, 1216, 1536] },
    },
  }
}

test('recognizes the three active built-in profiles', () => {
  for (const profile of ['artist_concept', 'character_concept', 'general']) {
    assert.equal(currentTaskProfile(task(profile)), profile)
    assert.deepEqual(profileWorkspaceSummary(task(profile)), {
      profile,
      broadOnly: true,
      bypassesReviewGates: false,
    })
  }

  assert.equal(currentTaskProfile(task(null)), null)
  assert.equal(profileWorkspaceSummary(task(null)), null)
})

test('runtime profile guard accepts only built-in profiles and filters the catalog response', async () => {
  for (const profile of ['artist_concept', 'character_concept', 'general']) {
    assert.equal(isDatasetProfile(profile), true)
  }
  for (const value of ['legacy', null, 'arbitrary']) {
    assert.equal(isDatasetProfile(value), false)
  }

  const tasksPage = await source('pages/TasksPage.tsx')
  assert.match(
    tasksPage,
    /profiles\.items\.filter\(\(profile\) => isDatasetProfile\(profile\.id\)\)/,
  )
})

test('only general task submission retains an explicit score_x2_floor option', () => {
  const general = profileTaskSubmissionComponents(components(), 'general')
  assert.equal(general['export.dataset'].config.aesthetic_bins, 'score_x2_floor')

  for (const profile of ['artist_concept', 'character_concept']) {
    const submitted = profileTaskSubmissionComponents(components(), profile)
    assert.equal(Object.hasOwn(submitted['export.dataset'].config, 'aesthetic_bins'), false)
  }
})

test('profile resolution control supports the five configured tiers as a sorted multi-select', () => {
  assert.deepEqual(toggleProfileResolution([512, 1024], 768), [512, 768, 1024])
  assert.deepEqual(toggleProfileResolution([512, 768], 768), [512])
  assert.throws(() => toggleProfileResolution([512], 512), /at least one/i)
  assert.throws(() => toggleProfileResolution([512], 640), /supported/i)
})

test('repeat export aesthetic minimum has explicit disabled and half-step validation', () => {
  assert.equal(validateExportRunAestheticMinimum(false, 'not-a-number'), null)
  assert.match(validateExportRunAestheticMinimum(true, '1.25') ?? '', /0\.5/)
  assert.match(validateExportRunAestheticMinimum(true, '5.5') ?? '', /1\.0.*5\.0/)
  assert.equal(validateExportRunAestheticMinimum(true, '3.5'), null)
})

test('style artist weights require finite bounded values and an exact backend-compatible sum', () => {
  const valid = {
    'style.artist': {
      enabled: true,
      config: { lsnet_weight: 0.4, gram_weight: 0.4, dino_weight: 0.2 },
    },
  }
  assert.equal(validateStyleArtistWeights(valid), null)
  assert.match(validateStyleArtistWeights({
    ...valid,
    'style.artist': { enabled: true, config: { lsnet_weight: 0.4, gram_weight: 0.4, dino_weight: 0.1 } },
  }) ?? '', /总和.*1/)
  assert.match(validateStyleArtistWeights({
    ...valid,
    'style.artist': { enabled: true, config: { lsnet_weight: 1.1, gram_weight: 0, dino_weight: 0 } },
  }) ?? '', /0.*1/)
  assert.equal(validateStyleArtistWeights({
    'style.artist': { enabled: false, config: { lsnet_weight: 'invalid' } },
  }), null)
})

test('coverage scope identity keeps the API value opaque and checks the flat root by repr and ASCII hex', () => {
  const identity = coverageScopeIdentity(expectedRoot)
  assert.equal(identity.scopeId, expectedRoot)
  assert.equal(identity.repr, JSON.stringify(expectedRoot))
  assert.equal(identity.asciiHex, '5f5f726f6f745f5f')
  assert.notEqual(identity.scopeId, wrongStarRoot)

  const opaque = coverageScopeIdentity('1_repeat-kept')
  assert.equal(opaque.scopeId, '1_repeat-kept')
  assert.equal(opaque.repr, JSON.stringify('1_repeat-kept'))
})

test('coverage presentation distinguishes not-ready, paused, exporting, and ready profile tasks', () => {
  assert.equal(coverageRequestState(task('general', 'draft')), 'not_ready')
  assert.equal(coverageRequestState(task('general', 'paused', 'exporting')), 'paused')
  assert.equal(coverageRequestState(task('general', 'exporting')), 'exporting')
  assert.equal(coverageRequestState(task('general', 'completed')), 'ready')
})

test('Task 7 surfaces use the shared profile contract instead of profile literals or coverage rewrites', async () => {
  const [tasksPage, progressPage, reviewsPage, clustersPage, auditSupport, profileWorkspace] = await Promise.all([
    source('pages/TasksPage.tsx'),
    source('pages/ProgressPage.tsx'),
    source('pages/ReviewsPage.tsx'),
    source('pages/ClustersPage.tsx'),
    source('pages/auditPageSupport.tsx'),
    source('profileWorkspace.ts'),
  ])

  assert.match(tasksPage, /ProfileTaskSettings/)
  assert.match(tasksPage, /profileTaskSubmissionComponents/)
  assert.match(progressPage, /ProfileWorkspaceBanner/)
  assert.match(reviewsPage, /isCuratedConfirmationWindow/)
  assert.match(clustersPage, /CoverageReportPanel/)
  assert.match(auditSupport, /ManualActionBar/)
  assert.match(profileWorkspace, /TextEncoder/)
  assert.doesNotMatch(profileWorkspace, /\*\*root\*\*/)
})

test('R2 fails closed for profile-free presets and component submission', async () => {
  const [profileWorkspace, types, tasksPage] = await Promise.all([
    source('profileWorkspace.ts'),
    source('types.ts'),
    source('pages/TasksPage.tsx'),
  ])

  assert.doesNotMatch(profileWorkspace, /profile: DatasetProfile \| null/)
  assert.doesNotMatch(profileWorkspace, /not_applicable/)
  assert.match(types, /profile: DatasetProfile \| null/)
  assert.match(tasksPage, /const profileId = selected\.profile \?\? ''/)
  assert.match(tasksPage, /if \(!profileId\) return/)
})

test('R2 removes profile-free task creation and data-page presentation branches', async () => {
  const [tasksPage, clustersPage, risksPage] = await Promise.all([
    source('pages/TasksPage.tsx'),
    source('pages/ClustersPage.tsx'),
    source('pages/RisksPage.tsx'),
  ])

  assert.doesNotMatch(tasksPage, /selectedBuiltinProfile\?\.id \?\? null/)
  assert.doesNotMatch(tasksPage, /旧版\/自定义/)
  assert.match(tasksPage, /Object\.keys\(components\)\.length === manifests\.length/)
  assert.doesNotMatch(
    tasksPage,
    /const next = selected\s*\?\s*cloneComponents\(selected\.components\)\s*:\s*buildDefaultComponentConfig\(manifests\)/,
  )
  assert.match(
    tasksPage,
    /const selected = presets\.find\(\(item\) => item\.id === selectedPresetId\)\s*if \(!selected\) return/,
  )
  assert.doesNotMatch(clustersPage, /isBuiltinProfileTask|profileTask/)
  assert.doesNotMatch(risksPage, /isBuiltinProfileTask|profileTask/)
})

test('R2 keeps SAE evidence read-only and never submits obsolete review decisions', async () => {
  const [reviewsPage, types] = await Promise.all([
    source('pages/ReviewsPage.tsx'),
    source('types.ts'),
  ])

  assert.doesNotMatch(reviewsPage, /\/reviews\/sae\/features\/decisions/)
  assert.doesNotMatch(reviewsPage, /setConfirm\(\{ decision: 'pending_review'/)
  assert.doesNotMatch(reviewsPage, /label="正常"|label="风险"|label="忽略"/)
  assert.doesNotMatch(types, /'normal' \| 'risk' \| 'ignore'/)
})
