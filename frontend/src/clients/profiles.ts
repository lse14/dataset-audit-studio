import { request } from '../transport/http'
import type { BuiltinProfileList } from '../types'

export function listBuiltinProfiles(): Promise<BuiltinProfileList> {
  return request<BuiltinProfileList>('/api/components/builtin-profiles')
}
