import type {
  AnswerResponse,
  DocumentSummary,
  ExtractionResponse,
  IngestionJobResponse,
} from './types'

export class ApiError extends Error {}

const API_KEY_STORAGE_KEY = 'pka-api-key'

export function getStoredApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setStoredApiKey(key: string): void {
  try {
    localStorage.setItem(API_KEY_STORAGE_KEY, key)
  } catch {
    // Private-mode/blocked storage: the key just won't persist across reloads.
  }
}

async function request<T>(
  method: string,
  path: string,
  options: { json?: unknown; formData?: FormData } = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  const apiKey = getStoredApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey

  let body: BodyInit | undefined
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.json)
  } else if (options.formData) {
    body = options.formData
  }

  let response: Response
  try {
    response = await fetch(path, { method, headers, body })
  } catch {
    throw new ApiError(
      'Could not reach the API. Confirm the server is running and try again.',
    )
  }

  if (!response.ok) {
    let detail = `Request failed (HTTP ${response.status}).`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
        detail = body.detail[0].msg
      }
    } catch {
      // Non-JSON error body — keep the generic message.
    }
    throw new ApiError(detail)
  }
  return (await response.json()) as T
}

export const api = {
  listDocuments: () =>
    request<{ documents: DocumentSummary[] }>('GET', '/documents'),

  startIngestion: (filename: string, file: Blob) => {
    const formData = new FormData()
    formData.append('files', file, filename)
    return request<IngestionJobResponse>('POST', '/documents', { formData })
  },

  getIngestionJob: (jobId: string) =>
    request<IngestionJobResponse>('GET', `/documents/jobs/${jobId}`),

  deleteDocument: (documentId: string) =>
    request<{ document_id: string; deleted: boolean }>(
      'DELETE',
      `/documents/${documentId}`,
    ),

  extractDocument: (filename: string, file: Blob) => {
    const formData = new FormData()
    formData.append('file', file, filename)
    return request<ExtractionResponse>('POST', '/extract', { formData })
  },

  query: (query: string) =>
    request<AnswerResponse>('POST', '/query', { json: { query } }),

  health: () => request<{ ok: boolean; checks: unknown[] }>('GET', '/health'),
}

export async function pollIngestionJob(
  jobId: string,
  onUpdate: (job: IngestionJobResponse) => void,
): Promise<IngestionJobResponse> {
  for (;;) {
    const job = await api.getIngestionJob(jobId)
    onUpdate(job)
    if (job.status === 'completed') return job
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
}
