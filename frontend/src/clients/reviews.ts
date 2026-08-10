import { jsonBody, request } from '../transport/http'
import type {
  AestheticAuditList,
  AestheticAuditReason,
  AIReviewItem,
  CuratedReviewItem,
  DuplicateGroupAuditList,
  ManualExclusionResult,
  ReviewList,
  SAEFeatureList,
  StyleReviewItem,
  StyleAuditList,
  WatermarkReviewThresholdResult,
} from '../types'

export type ManualExclusionsInput = {
  sample_ids: string[]
  excluded: boolean
  context: {
    page: 'clusters' | 'risks'
    folder_id: string | null
  }
}

export type SaeFeaturesQuery = {
  offset: number
  limit: number
  folder?: string
}

export type CuratedReviewsQuery = {
  evidenceType: string
  limit: number
  offset: number
  decision?: string
  folder?: string
  reasonCode?: string
  severity?: string
  candidateGroup?: string
}

export type ReviewItemsQuery = {
  offset: number
  limit: number
  decision?: string
  folder?: string
}

export type StyleAuditQuery = {
  offset: number
  limit: number
  folder?: string
  decision?: 'all' | 'pending_review' | 'approved_keep' | 'approved_exclude'
}

export type AestheticAuditQuery = {
  offset: number
  limit: number
  folder?: string
  bucket?: number
  reasonCode?: AestheticAuditReason
  decision?: 'all' | 'pending_review' | 'approved_keep' | 'approved_exclude'
}

export type DuplicateGroupAuditQuery = {
  evidenceType: 'exact_duplicate' | 'visual_duplicate' | 'semantic_duplicate'
  offset: number
  limit: number
  folder?: string
  decision?: 'all' | 'pending_review' | 'approved_keep' | 'approved_exclude'
}

export type CuratedReviewDecisionsInput = {
  decision: string
  evidence_type: string
  sample_ids: string[]
}

export type ReviewDecisionsInput = {
  sample_ids: string[]
  decision: string
}

export function updateManualExclusions(
  taskId: string,
  input: ManualExclusionsInput,
): Promise<ManualExclusionResult> {
  return request<ManualExclusionResult>(
    `/api/tasks/${taskId}/manual-exclusions`,
    { method: 'POST', ...jsonBody(input) },
  )
}

export function setWatermarkReviewThreshold(
  taskId: string,
  threshold: number,
  expectedVersion: number,
): Promise<WatermarkReviewThresholdResult> {
  return request<WatermarkReviewThresholdResult>(
    `/api/tasks/${taskId}/watermark-review-threshold`,
    {
      method: 'POST',
      ...jsonBody({ threshold, expected_version: expectedVersion }),
    },
  )
}

export function listSaeFeatures(
  taskId: string,
  { offset, limit, folder }: SaeFeaturesQuery,
): Promise<SAEFeatureList> {
  const folderFilter = folder ? `&folder=${encodeURIComponent(folder)}` : ''
  return request<SAEFeatureList>(
    `/api/tasks/${taskId}/reviews/sae/features?offset=${offset}&limit=${limit}${folderFilter}`,
  )
}

export function listCuratedReviews(
  taskId: string,
  {
    evidenceType,
    limit,
    offset,
    decision,
    folder,
    reasonCode,
    severity,
    candidateGroup,
  }: CuratedReviewsQuery,
): Promise<ReviewList<CuratedReviewItem>> {
  const params = new URLSearchParams()
  params.set('evidence_type', evidenceType)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (decision) params.set('decision', decision)
  if (folder) params.set('folder', folder)
  if (reasonCode) params.set('reason_code', reasonCode)
  if (severity) params.set('severity', severity)
  if (candidateGroup) params.set('candidate_group', candidateGroup)
  return request<ReviewList<CuratedReviewItem>>(
    `/api/tasks/${taskId}/reviews/curated?${params.toString()}`,
  )
}

export function listReviewItems(
  taskId: string,
  mode: 'ai' | 'style',
  { offset, limit, decision, folder }: ReviewItemsQuery,
): Promise<ReviewList<AIReviewItem | StyleReviewItem>> {
  const decisionFilter = decision ? `&decision=${decision}` : ''
  const folderFilter = folder ? `&folder=${encodeURIComponent(folder)}` : ''
  return request<ReviewList<AIReviewItem | StyleReviewItem>>(
    `/api/tasks/${taskId}/reviews/${mode}?offset=${offset}&limit=${limit}${decisionFilter}${folderFilter}`,
  )
}

export function listStyleAudit(
  taskId: string,
  { offset, limit, folder, decision }: StyleAuditQuery,
): Promise<StyleAuditList> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (folder) params.set('folder', folder)
  if (decision && decision !== 'all') params.set('decision', decision)
  return request<StyleAuditList>(
    `/api/tasks/${taskId}/reviews/style/audit?${params.toString()}`,
  )
}

export function listAestheticAudit(
  taskId: string,
  { offset, limit, folder, bucket, reasonCode, decision }: AestheticAuditQuery,
): Promise<AestheticAuditList> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (folder) params.set('folder', folder)
  if (bucket !== undefined) params.set('bucket', String(bucket))
  if (reasonCode) params.set('reason_code', reasonCode)
  if (decision && decision !== 'all') params.set('decision', decision)
  return request<AestheticAuditList>(
    `/api/tasks/${taskId}/reviews/aesthetic/audit?${params.toString()}`,
  )
}

export function listDuplicateGroupAudit(
  taskId: string,
  { evidenceType, offset, limit, folder, decision }: DuplicateGroupAuditQuery,
): Promise<DuplicateGroupAuditList> {
  const params = new URLSearchParams({
    evidence_type: evidenceType,
    offset: String(offset),
    limit: String(limit),
  })
  if (folder) params.set('folder', folder)
  if (decision && decision !== 'all') params.set('decision', decision)
  return request<DuplicateGroupAuditList>(
    `/api/tasks/${taskId}/reviews/duplicates/audit?${params.toString()}`,
  )
}

export function submitCuratedReviewDecisions(
  taskId: string,
  input: CuratedReviewDecisionsInput,
) {
  return request(`/api/tasks/${taskId}/reviews/curated/decisions`, {
    method: 'POST',
    ...jsonBody(input),
  })
}

export function submitReviewDecisions(
  taskId: string,
  mode: 'ai' | 'style',
  input: ReviewDecisionsInput,
) {
  return request(`/api/tasks/${taskId}/reviews/${mode}/decisions`, {
    method: 'POST',
    ...jsonBody(input),
  })
}
