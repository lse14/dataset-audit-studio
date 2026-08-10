import { jsonBody, request } from '../transport/http'
import type { DirectoryListing, DirectorySelection } from '../types'

export function listDirectories(path?: string): Promise<DirectoryListing> {
  const trimmed = path?.trim()
  const query = trimmed ? `?path=${encodeURIComponent(trimmed)}` : ''
  return request<DirectoryListing>(`/api/filesystem/directories${query}`)
}

export function selectDirectory(
  purpose: 'source' | 'output',
  initialPath: string,
): Promise<DirectorySelection> {
  return request<DirectorySelection>('/api/filesystem/select-directory', {
    method: 'POST',
    ...jsonBody({ purpose, initial_path: initialPath.trim() || null }),
  })
}

export function selectFile(
  purpose: 'model',
  initialPath: string,
): Promise<DirectorySelection> {
  return request<DirectorySelection>('/api/filesystem/select-file', {
    method: 'POST',
    ...jsonBody({ purpose, initial_path: initialPath.trim() || null }),
  })
}
