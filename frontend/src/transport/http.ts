export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  headers.set('Accept', 'application/json')
  if (options?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...options, headers })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      detail = errorMessage((await response.json()) as unknown) ?? detail
    } catch {
      // The status code remains useful when a proxy returns a non-JSON error.
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function jsonBody(value: unknown): Pick<RequestInit, 'body'> {
  return { body: JSON.stringify(value) }
}

// FastAPI reports request validation failures as `detail: [{ loc, msg, type }]`, which would render
// as `[object Object]`; business errors keep a plain string and therefore win outright.
function errorMessage(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return null
  const detail = (payload as { detail?: unknown }).detail
  const code = (payload as { code?: unknown }).code
  if (typeof detail === 'string' && detail.trim() !== '') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map(validationMessage).filter((message): message is string => message !== null)
    if (messages.length > 0) return messages.join('；')
  }
  return typeof code === 'string' && code !== '' ? code : null
}

function validationMessage(item: unknown): string | null {
  if (typeof item !== 'object' || item === null) return null
  const record = item as { loc?: unknown; msg?: unknown }
  const message = typeof record.msg === 'string' && record.msg !== '' ? record.msg : null
  const location = Array.isArray(record.loc)
    ? record.loc.filter((part) => typeof part === 'string' || typeof part === 'number').join('.')
    : ''
  if (message === null) return location === '' ? null : location
  return location === '' ? message : `${location}: ${message}`
}
