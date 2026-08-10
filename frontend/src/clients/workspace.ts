import { request } from '../transport/http'
import type {
  ClusterList,
  ClusterSampleList,
  CoverageReport,
  FolderList,
  RiskSampleDetail,
  RiskSampleList,
  TaskOverview,
} from '../types'

type FolderPageQuery = {
  offset: number
  limit: number
  folder?: string
}

type RiskListQuery = FolderPageQuery & {
  code?: string
  severity?: string
  decision?: 'all' | 'pending_review' | 'approved_keep' | 'approved_exclude'
}

type RiskDetailQuery = {
  code?: string
  severity?: string
}

function folderQuery(folder?: string) {
  return folder ? `&folder=${encodeURIComponent(folder)}` : ''
}

export function getTaskOverview(taskId: string): Promise<TaskOverview> {
  return request<TaskOverview>(`/api/tasks/${taskId}/overview`)
}

export function listTaskFolders(taskId: string): Promise<FolderList> {
  return request<FolderList>(`/api/tasks/${taskId}/folders`)
}

export function getCoverageReport(taskId: string, resolution: number): Promise<CoverageReport> {
  return request<CoverageReport>(`/api/tasks/${taskId}/coverage?resolution=${resolution}`)
}

export function listClusters(taskId: string, { offset, limit, folder }: FolderPageQuery): Promise<ClusterList> {
  return request<ClusterList>(
    `/api/tasks/${taskId}/clusters?offset=${offset}&limit=${limit}${folderQuery(folder)}`,
  )
}

export function listClusterSamples(
  taskId: string,
  clusterId: string,
  { offset, limit, folder }: FolderPageQuery,
): Promise<ClusterSampleList> {
  return request<ClusterSampleList>(
    `/api/tasks/${taskId}/clusters/${clusterId}/samples?offset=${offset}&limit=${limit}${folderQuery(folder)}`,
  )
}

export function listRiskSamples(
  taskId: string,
  { offset, limit, code, severity, folder, decision }: RiskListQuery,
): Promise<RiskSampleList> {
  const codeQuery = code ? `&code=${encodeURIComponent(code)}` : ''
  const severityQuery = severity ? `&severity=${encodeURIComponent(severity)}` : ''
  const decisionQuery = decision && decision !== 'all' ? `&decision=${decision}` : ''
  return request<RiskSampleList>(
    `/api/tasks/${taskId}/risk-samples?offset=${offset}&limit=${limit}${codeQuery}${severityQuery}${folderQuery(folder)}${decisionQuery}`,
  )
}

export function getRiskSampleDetail(
  taskId: string,
  sampleId: string,
  { code, severity }: RiskDetailQuery,
): Promise<RiskSampleDetail> {
  const query = new URLSearchParams()
  if (code) query.set('code', code)
  if (severity) query.set('severity', severity)
  const evidenceQuery = query.size > 0 ? `?${query.toString()}` : ''
  return request<RiskSampleDetail>(`/api/tasks/${taskId}/risk-samples/${sampleId}${evidenceQuery}`)
}

export function sampleThumbnailUrl(taskId: string, sampleId: string, size: number): string {
  return `/api/tasks/${taskId}/samples/${sampleId}/thumbnail?size=${size}`
}

export function sampleMediaUrl(taskId: string, sampleId: string): string {
  return `/api/tasks/${encodeURIComponent(taskId)}/samples/${encodeURIComponent(sampleId)}/media`
}
