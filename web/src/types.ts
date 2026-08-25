// Mirrors app/schemas/api.py exactly — field names and shapes, not renamed or
// restructured, so a change there is a deliberate, visible change here too.

export type ItemCategory =
  | 'obligation'
  | 'requirement'
  | 'deadline'
  | 'prohibition'
  | 'definition'
  | 'other'

export type VerificationStatus = 'verified' | 'unverified' | 'failed' | 'not_run'

export interface ExtractedItem {
  item_id: string
  document_id: string
  category: ItemCategory
  text: string
  quote: string
  section_label: string | null
  page_start: number | null
  page_end: number | null
  located: boolean
  confidence: number
  verification_status: VerificationStatus
  verification_note: string | null
}

export interface SectionOutcome {
  section_id: string
  label: string | null
  status: 'succeeded' | 'failed'
  item_count: number
  error: string | null
}

export interface ExtractionResponse {
  document_id: string
  filename: string
  status: 'succeeded' | 'failed'
  items: ExtractedItem[]
  sections: SectionOutcome[]
  error: string | null
}

export interface DocumentSummary {
  document_id: string
  filename: string
  file_type: string
  chunk_count: number
}

export interface JobFileProgress {
  filename: string
  status: 'queued' | 'processing' | 'indexed' | 'already_indexed' | 'failed' | 'skipped'
  document_id: string | null
  chunk_count: number
  error: string | null
}

export interface IngestionJobResponse {
  job_id: string
  status: 'queued' | 'running' | 'completed'
  total: number
  completed: number
  current_filename: string | null
  eta_seconds: number | null
  files: JobFileProgress[]
}

export interface Citation {
  document_id: string
  filename: string
  file_type: string
  chunk_id: string
  excerpt: string
}

export interface AnswerResponse {
  answer: string
  sources: Citation[]
  is_refusal: boolean
}

// Local-only: a document as this UI tracks it. The backend has no single "document"
// concept spanning both ingestion (Chroma) and extraction (ExtractionStore) — this UI
// is what ties one uploaded file to both, and to the in-browser bytes the PDF viewer
// reads from (the API stores nothing it could serve back for a preview).
export interface LocalDocument {
  localId: string
  filename: string
  fileType: 'pdf' | 'docx' | 'txt' | 'other'
  bytes: ArrayBuffer
  ingestDocumentId: string | null
  ingestStatus: 'idle' | 'uploading' | 'indexed' | 'failed'
  extraction: ExtractionResponse | null
  extractionStatus: 'idle' | 'running' | 'done' | 'failed'
  extractionError: string | null
}
