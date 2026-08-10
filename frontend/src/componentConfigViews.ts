import type { ComponentManifest, JsonSchema } from './types'

const MEDIA_SCAN_FIELDS = [
  'recursive',
  'batch_size',
  'cpu_workers',
  'bucket_step',
  'excluded_directory_names',
] as const

const TECHNICAL_METRICS_FIELDS = [
  'resolutions',
  'maximum_aspect_ratio',
  'crop_loss_warning',
  'upscale_warning',
  'metrics_max_side',
  'fft_max_side',
  'max_decode_pixels',
  'thresholds',
] as const

export type ComponentConfigView = {
  configSourceId: string
  schema: JsonSchema
}

export function componentConfigView(
  manifest: ComponentManifest,
  manifests: ComponentManifest[],
): ComponentConfigView {
  if (manifest.id === 'media.scan') {
    return {
      configSourceId: manifest.id,
      schema: projectObjectSchema(manifest.json_schema, MEDIA_SCAN_FIELDS),
    }
  }

  if (manifest.id === 'metrics.technical') {
    const scanManifest = manifests.find((item) => item.id === 'media.scan')
    if (scanManifest) {
      return {
        configSourceId: scanManifest.id,
        schema: projectObjectSchema(scanManifest.json_schema, TECHNICAL_METRICS_FIELDS),
      }
    }
  }

  return { configSourceId: manifest.id, schema: manifest.json_schema }
}

export function projectObjectSchema(
  schema: JsonSchema,
  fieldNames: readonly string[],
): JsonSchema {
  const allowed = new Set(fieldNames)
  const properties = Object.fromEntries(
    Object.entries(schema.properties ?? {}).filter(([name]) => allowed.has(name)),
  )
  return {
    ...schema,
    properties,
    ...(schema.required
      ? { required: schema.required.filter((name) => allowed.has(name)) }
      : {}),
  }
}
