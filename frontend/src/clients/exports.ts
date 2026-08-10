import { jsonBody, request } from '../transport/http'

export type RewriteBackupRestoreResult = {
  restored_files: number
}

export function restoreRewriteBackup(
  taskId: string,
  expectedVersion: number,
): Promise<RewriteBackupRestoreResult> {
  return request<RewriteBackupRestoreResult>(
    `/api/tasks/${taskId}/rewrite-backup/restore`,
    {
      method: 'POST',
      ...jsonBody({ expected_version: expectedVersion }),
    },
  )
}
