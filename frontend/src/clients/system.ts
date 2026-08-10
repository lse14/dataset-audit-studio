import { request } from '../transport/http'
import type { Health } from '../types'

export function getSystemHealth(): Promise<Health> {
  return request<Health>('/api/health')
}
