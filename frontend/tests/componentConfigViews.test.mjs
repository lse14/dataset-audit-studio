import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  componentConfigView,
  projectObjectSchema,
} from '../src/componentConfigViews.ts'

async function source(path) {
  return readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
}

function manifest(id, jsonSchema) {
  return { id, json_schema: jsonSchema }
}

const scanSchema = {
  type: 'object',
  $defs: {
    MetricThresholds: {
      type: 'object',
      properties: { minimum_rgb_entropy: { type: 'number' } },
    },
  },
  properties: {
    recursive: { type: 'boolean' },
    batch_size: { type: 'integer' },
    cpu_workers: { type: 'integer' },
    bucket_step: { type: 'integer' },
    excluded_directory_names: { type: 'array' },
    resolutions: { type: 'array' },
    maximum_aspect_ratio: { type: 'number' },
    crop_loss_warning: { type: 'number' },
    upscale_warning: { type: 'number' },
    metrics_max_side: { type: 'integer' },
    fft_max_side: { type: 'integer' },
    max_decode_pixels: { type: 'integer' },
    thresholds: { $ref: '#/$defs/MetricThresholds' },
  },
}

const scanManifest = manifest('media.scan', scanSchema)
const technicalManifest = manifest('metrics.technical', {
  type: 'object',
  properties: {},
})
const manifests = [scanManifest, technicalManifest]

test('projects input discovery fields for the media scan view', () => {
  const view = componentConfigView(scanManifest, manifests)
  assert.equal(view.configSourceId, 'media.scan')
  assert.deepEqual(Object.keys(view.schema.properties), [
    'recursive',
    'batch_size',
    'cpu_workers',
    'bucket_step',
    'excluded_directory_names',
  ])
})

test('projects technical fields while retaining media.scan as the config source', () => {
  const view = componentConfigView(technicalManifest, manifests)
  assert.equal(view.configSourceId, 'media.scan')
  assert.deepEqual(Object.keys(view.schema.properties), [
    'resolutions',
    'maximum_aspect_ratio',
    'crop_loss_warning',
    'upscale_warning',
    'metrics_max_side',
    'fft_max_side',
    'max_decode_pixels',
    'thresholds',
  ])
  assert.equal(view.schema.$defs, scanSchema.$defs)
  assert.equal(view.schema.properties.thresholds.$ref, '#/$defs/MetricThresholds')
})

test('filters required fields without mutating the source schema', () => {
  const schema = {
    type: 'object',
    required: ['kept', 'hidden'],
    properties: {
      kept: { type: 'string' },
      hidden: { type: 'string' },
    },
  }
  const projected = projectObjectSchema(schema, ['kept'])
  assert.deepEqual(projected.required, ['kept'])
  assert.deepEqual(Object.keys(projected.properties), ['kept'])
  assert.deepEqual(schema.required, ['kept', 'hidden'])
  assert.deepEqual(Object.keys(schema.properties), ['kept', 'hidden'])
})

test('falls back to the component schema if media.scan is unavailable', () => {
  const view = componentConfigView(technicalManifest, [technicalManifest])
  assert.equal(view.configSourceId, 'metrics.technical')
  assert.equal(view.schema, technicalManifest.json_schema)
})

test('task creation loads profile-owned component configs from the API', async () => {
  const [tasksPage, editor, profiles, types] = await Promise.all([
    source('pages/TasksPage.tsx'),
    source('components/ComponentConfigEditor.tsx'),
    source('clients/profiles.ts'),
    source('types.ts'),
  ])

  assert.match(profiles, /\/api\/components\/builtin-profiles/)
  assert.match(tasksPage, /listBuiltinProfiles\(\)/)
  assert.match(tasksPage, /mergeBuiltinProfileComponents\(/)
  assert.match(tasksPage, /profile_owned_component_ids/)
  assert.match(types, /profile\.components/)
  assert.match(types, /profile_owned_config_fields/)
  assert.doesNotMatch(tasksPage, /artist_concept|character_concept|general/)
  assert.doesNotMatch(editor, /PROFILE_OWNED_COMPONENT_IDS/)
  assert.match(editor, /profileOwnedComponentIds/)
})

test('profile-locked optional components preserve their materialized enabled state', async () => {
  const editor = await source('components/ComponentConfigEditor.tsx')

  assert.match(editor, /const required = manifest\.activation === 'required'/)
  assert.match(editor, /checked=\{required \|\| value\}/)
  assert.match(editor, /\{required \? '必需' : value \? '启用' : '关闭'\}/)
  assert.doesNotMatch(editor, /checked=\{locked \|\| value\}/)
})

test('reapplying a builtin profile preserves user-managed settings', async () => {
  const types = await source('types.ts')
  assert.match(types, /export function mergeBuiltinProfileComponents/)

  const { mergeBuiltinProfileComponents } = await import('../src/types.ts')
  const profile = {
    id: 'character_concept',
    display_name: 'Character concept',
    description: 'test profile',
    scope_mode: 'concept',
    profile_owned_component_ids: [
      'style.artist',
      'embedding.semantic',
      'cluster.hierarchy',
    ],
    profile_owned_config_fields: {
      'style.artist': ['enabled'],
      'cluster.hierarchy': ['scope_mode'],
    },
    components: {
      'style.artist': { enabled: false, config: { enabled: false, device: 'auto', batch_size: 4 } },
      'embedding.semantic': { enabled: true, config: { device: 'auto', batch_size: 8 } },
      'cluster.hierarchy': { enabled: true, config: { scope_mode: 'concept', target_leaf_size: 128 } },
      'detect.ai': { enabled: false, config: { candidate_threshold: 0.7 } },
      'export.dataset': { enabled: true, config: { keep_annotation_files: true } },
    },
  }
  const current = {
    'style.artist': { enabled: true, config: { enabled: true, device: 'cuda', batch_size: 12 } },
    'embedding.semantic': { enabled: false, config: { device: 'cuda', batch_size: 16 } },
    'cluster.hierarchy': { enabled: false, config: { scope_mode: 'global', target_leaf_size: 256 } },
    'detect.ai': { enabled: true, config: { candidate_threshold: 0.9 } },
    'export.dataset': { enabled: true, config: { keep_annotation_files: false } },
  }

  const initial = mergeBuiltinProfileComponents(current, profile, false)
  assert.equal(initial['detect.ai'].enabled, false)

  const reapplied = mergeBuiltinProfileComponents(current, profile, true)
  assert.deepEqual(reapplied['style.artist'], {
    enabled: false,
    config: { enabled: false, device: 'cuda', batch_size: 12 },
  })
  assert.deepEqual(reapplied['embedding.semantic'], {
    enabled: true,
    config: { device: 'cuda', batch_size: 16 },
  })
  assert.deepEqual(reapplied['cluster.hierarchy'], {
    enabled: true,
    config: { scope_mode: 'concept', target_leaf_size: 256 },
  })
  assert.equal(Object.hasOwn(reapplied, 'selection.three_stage'), false)
  assert.deepEqual(reapplied['detect.ai'], current['detect.ai'])
  assert.deepEqual(reapplied['export.dataset'], current['export.dataset'])
})
