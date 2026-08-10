import { jsonBody, request } from '../transport/http'
import type { ExportRun, ExportRunCreateInput, ExportRunList, ExportRunPreview, ExportRunSettings } from '../types'

export function createExportRun(taskId: string, input: ExportRunCreateInput): Promise<ExportRun> {
  return request<ExportRun>(`/api/tasks/${taskId}/export-runs`, {
    method: 'POST',
    ...jsonBody({
      output_root: input.output_root,
      minimum_resolution: input.minimum_resolution,
      domain_minimum: input.domain_minimum,
      exclude_exact_visual_duplicates: input.exclude_exact_visual_duplicates,
      style_outlier_mode: input.style_outlier_mode,
      aesthetic_minimum: input.aesthetic_minimum,
      minimum_folder_images: input.minimum_folder_images,
      add_repeat_prefix: input.add_repeat_prefix,
      sample_seen_mode: input.sample_seen_mode,
      sample_seen_target: input.sample_seen_target,
      preview_digest: input.preview_digest,
    }),
  })
}

export function previewExportRun(taskId: string, input: ExportRunSettings): Promise<ExportRunPreview> {
  return request<ExportRunPreview>(`/api/tasks/${taskId}/export-runs/preview`, {
    method: 'POST',
    ...jsonBody(input),
  })
}

export function listExportRuns(
  taskId: string,
  { offset, limit }: { offset: number; limit: number },
): Promise<ExportRunList> {
  return request<ExportRunList>(`/api/tasks/${taskId}/export-runs?offset=${offset}&limit=${limit}`)
}
