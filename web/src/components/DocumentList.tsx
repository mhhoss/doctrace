import { useRef } from 'react'
import type { LocalDocument } from '../types'

interface DocumentListProps {
  documents: LocalDocument[]
  selectedId: string | null
  onSelect: (localId: string) => void
  onUpload: (files: FileList) => void
}

function statusLabel(doc: LocalDocument): string {
  if (doc.extractionStatus === 'running') return 'Extracting…'
  if (doc.extractionStatus === 'failed') return 'Extraction failed'
  if (doc.extractionStatus === 'done') return 'Ready'
  if (doc.ingestStatus === 'uploading') return 'Uploading…'
  return 'Added'
}

export function DocumentList({
  documents,
  selectedId,
  onSelect,
  onUpload,
}: DocumentListProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <div className="document-list">
      <div className="document-list-header">
        <h2>Documents</h2>
        <button type="button" onClick={() => inputRef.current?.click()}>
          + Add
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.txt"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) onUpload(e.target.files)
            e.target.value = ''
          }}
        />
      </div>
      {documents.length === 0 ? (
        <div className="empty-state">
          Add a PDF, DOCX, or TXT to extract its requirements and ask questions about it.
        </div>
      ) : (
        <ul>
          {documents.map((doc) => (
            <li
              key={doc.localId}
              className={doc.localId === selectedId ? 'selected' : ''}
              onClick={() => onSelect(doc.localId)}
            >
              <span className="document-filename">{doc.filename}</span>
              <span className={`document-status status-${doc.extractionStatus}`}>
                {statusLabel(doc)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
