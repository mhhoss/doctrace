import { useRef } from 'react'
import type { LocalDocument } from '../types'
import { IconFileDoc, IconFilePdf, IconFileText, IconInbox, IconTrash, IconUpload } from './icons'

interface DocumentListProps {
  documents: LocalDocument[]
  selectedId: string | null
  onSelect: (localId: string) => void
  onUpload: (files: FileList) => void
  onDelete: (localId: string) => void
}

function statusLabel(doc: LocalDocument): string {
  if (doc.extractionStatus === 'running') return 'Extracting…'
  if (doc.extractionStatus === 'failed') return 'Extraction failed'
  if (doc.extractionStatus === 'done') return 'Ready'
  if (doc.ingestStatus === 'uploading') return 'Uploading…'
  return 'Added'
}

function FileIcon({ fileType }: { fileType: LocalDocument['fileType'] }) {
  if (fileType === 'pdf') return <IconFilePdf />
  if (fileType === 'docx') return <IconFileDoc />
  return <IconFileText />
}

export function DocumentList({
  documents,
  selectedId,
  onSelect,
  onUpload,
  onDelete,
}: DocumentListProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <div className="document-list">
      <div className="document-list-header">
        <h2>Documents</h2>
        <button
          type="button"
          className="document-add-button"
          onClick={() => inputRef.current?.click()}
        >
          <IconUpload style={{ width: 13, height: 13 }} />
          Add
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
        <div className="document-list-empty">
          <IconInbox />
          <div>Add a PDF, DOCX, or TXT to extract its requirements and ask questions about it.</div>
        </div>
      ) : (
        <ul>
          {documents.map((doc) => (
            <li
              key={doc.localId}
              className={doc.localId === selectedId ? 'selected' : ''}
              onClick={() => onSelect(doc.localId)}
            >
              <span className="document-file-icon">
                <FileIcon fileType={doc.fileType} />
              </span>
              <div className="document-row">
                <div className="document-row-top">
                  <span className="document-filename" title={doc.filename}>
                    {doc.filename}
                  </span>
                  <button
                    type="button"
                    className="document-delete"
                    title={`Delete ${doc.filename}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (
                        window.confirm(
                          `Delete "${doc.filename}"? This removes it from the knowledge base.`,
                        )
                      ) {
                        onDelete(doc.localId)
                      }
                    }}
                  >
                    <IconTrash />
                  </button>
                </div>
                <span className={`document-status status-${doc.extractionStatus}`}>
                  {statusLabel(doc)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
