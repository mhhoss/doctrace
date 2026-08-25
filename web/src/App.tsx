import { useState } from 'react'
import { api, ApiError, getStoredApiKey, pollIngestionJob, setStoredApiKey } from './api'
import { DocumentList } from './components/DocumentList'
import { RequirementsTable } from './components/RequirementsTable'
import { PdfViewer } from './components/PdfViewer'
import { AskPanel } from './components/AskPanel'
import type { ExtractedItem, LocalDocument } from './types'

function fileType(filename: string): LocalDocument['fileType'] {
  const ext = filename.toLowerCase().split('.').pop()
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx') return 'docx'
  if (ext === 'txt') return 'txt'
  return 'other'
}

function makeLocalId(): string {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [documents, setDocuments] = useState<LocalDocument[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState<'requirements' | 'ask'>('requirements')
  const [selectedItem, setSelectedItem] = useState<ExtractedItem | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [apiKeyInput, setApiKeyInput] = useState(getStoredApiKey())

  const selectedDocument = documents.find((d) => d.localId === selectedId) ?? null

  function updateDocument(localId: string, patch: Partial<LocalDocument>) {
    setDocuments((prev) =>
      prev.map((d) => (d.localId === localId ? { ...d, ...patch } : d)),
    )
  }

  async function handleUpload(files: FileList) {
    for (const file of Array.from(files)) {
      const bytes = await file.arrayBuffer()
      const localId = makeLocalId()
      const doc: LocalDocument = {
        localId,
        filename: file.name,
        fileType: fileType(file.name),
        bytes,
        ingestDocumentId: null,
        ingestStatus: 'uploading',
        extraction: null,
        extractionStatus: 'running',
        extractionError: null,
      }
      setDocuments((prev) => [...prev, doc])
      setSelectedId(localId)
      setSelectedItem(null)
      setTab('requirements')

      void runExtraction(localId, file)
      void runIngestion(localId, file)
    }
  }

  async function runExtraction(localId: string, file: File) {
    try {
      const result = await api.extractDocument(file.name, file)
      updateDocument(localId, {
        extraction: result,
        extractionStatus: result.status === 'succeeded' ? 'done' : 'failed',
        extractionError: result.status === 'failed' ? result.error : null,
      })
    } catch (error) {
      updateDocument(localId, {
        extractionStatus: 'failed',
        extractionError: error instanceof ApiError ? error.message : 'Extraction failed.',
      })
    }
  }

  async function runIngestion(localId: string, file: File) {
    try {
      const job = await api.startIngestion(file.name, file)
      const finished = await pollIngestionJob(job.job_id, () => {})
      const outcome = finished.files[0]
      updateDocument(localId, {
        ingestDocumentId: outcome?.document_id ?? null,
        ingestStatus: outcome?.status === 'failed' ? 'failed' : 'indexed',
      })
    } catch {
      updateDocument(localId, { ingestStatus: 'failed' })
    }
  }

  function handleSelectItem(item: ExtractedItem) {
    setSelectedItem(item)
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">Document Intelligence</span>
        <span className="app-subtitle">
          Page-cited requirement extraction — not a chat wrapper
        </span>
        <button
          type="button"
          className="settings-button"
          onClick={() => setShowSettings((v) => !v)}
        >
          ⚙
        </button>
      </header>

      {showSettings && (
        <div className="settings-panel">
          <label>
            API key (only needed if the server has <code>API_KEY</code> configured)
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              onBlur={() => setStoredApiKey(apiKeyInput)}
              placeholder="Leave blank if auth is disabled"
            />
          </label>
        </div>
      )}

      <div className="app-body">
        <aside className="app-sidebar">
          <DocumentList
            documents={documents}
            selectedId={selectedId}
            onSelect={(id) => {
              setSelectedId(id)
              setSelectedItem(null)
            }}
            onUpload={(files) => void handleUpload(files)}
          />
        </aside>

        <main className="app-main">
          {!selectedDocument ? (
            <div className="empty-state large">
              Add a document to get started.
            </div>
          ) : (
            <>
              <div className="workspace-tabs">
                <button
                  type="button"
                  className={tab === 'requirements' ? 'active' : ''}
                  onClick={() => setTab('requirements')}
                >
                  Requirements
                </button>
                <button
                  type="button"
                  className={tab === 'ask' ? 'active' : ''}
                  onClick={() => setTab('ask')}
                >
                  Ask
                </button>
              </div>

              {tab === 'requirements' && (
                <div className="requirements-workspace">
                  <div className="requirements-pane">
                    {selectedDocument.extractionStatus === 'running' && (
                      <div className="empty-state">Extracting requirements…</div>
                    )}
                    {selectedDocument.extractionStatus === 'failed' && (
                      <div className="empty-state error">
                        {selectedDocument.extractionError ?? 'Extraction failed.'}
                      </div>
                    )}
                    {selectedDocument.extraction && (
                      <RequirementsTable
                        items={selectedDocument.extraction.items}
                        selectedItemId={selectedItem?.item_id ?? null}
                        onSelect={handleSelectItem}
                      />
                    )}
                  </div>
                  <div className="source-pane">
                    {selectedDocument.fileType === 'pdf' ? (
                      <PdfViewer
                        bytes={selectedDocument.bytes}
                        targetPage={selectedItem?.page_start ?? null}
                      />
                    ) : (
                      <div className="empty-state">
                        No page preview for {selectedDocument.fileType.toUpperCase()}{' '}
                        files (only PDF has a real page concept) — the quote below is
                        the closest available source reference.
                      </div>
                    )}
                    {selectedItem && (
                      <div className="quote-panel">
                        <div className="quote-label">Source passage</div>
                        <p dir="auto">{selectedItem.quote}</p>
                        {selectedItem.verification_note && (
                          <p className="verification-note">
                            {selectedItem.verification_note}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {tab === 'ask' && <AskPanel />}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
