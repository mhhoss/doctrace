import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  documentFileUrl,
  getStoredApiKey,
  pollIngestionJob,
  setStoredApiKey,
} from './api'
import { DocumentList } from './components/DocumentList'
import { RequirementsTable } from './components/RequirementsTable'
import { PdfViewer } from './components/PdfViewer'
import { AskPanel } from './components/AskPanel'
import { CitationViewer } from './components/CitationViewer'
import { IconInbox, IconSettings, IconSparkle } from './components/icons'
import type { Citation, DocumentSummary, ExtractedItem, LocalDocument } from './types'

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

// Mirrors `app.documents.loader.compute_document_id` exactly (SHA-256, first 32 hex
// chars) so a duplicate can be caught client-side, before spending an upload and an
// LLM extraction call on content already in the knowledge base (ADR-3's identity).
async function computeDocumentId(content: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', content)
  const hex = Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
  return hex.slice(0, 32)
}

export default function App() {
  const [documents, setDocuments] = useState<LocalDocument[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState<'requirements' | 'ask'>('requirements')
  const [selectedItem, setSelectedItem] = useState<ExtractedItem | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [apiKeyInput, setApiKeyInput] = useState(getStoredApiKey())
  const [openCitation, setOpenCitation] = useState<Citation | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const selectedDocument = documents.find((d) => d.localId === selectedId) ?? null

  // Restore the knowledge base's document list on load (ADR-29 persists jobs,
  // extraction results, and original files server-side, so this survives a reload
  // instead of only living in this component's in-memory state).
  useEffect(() => {
    let cancelled = false
    async function restore() {
      let summaries: DocumentSummary[]
      try {
        summaries = (await api.listDocuments()).documents
      } catch {
        // API unreachable at load time — the sidebar just starts empty, same as
        // before this restore existed; the user can still retry via a reload.
        return
      }
      const restored = await Promise.all(
        summaries.map(async (summary): Promise<LocalDocument> => {
          const base: LocalDocument = {
            localId: `remote-${summary.document_id}`,
            filename: summary.filename,
            fileType: fileType(summary.filename),
            ingestDocumentId: summary.document_id,
            ingestStatus: 'indexed',
            extraction: null,
            extractionStatus: 'idle',
            extractionError: null,
          }
          try {
            const extraction = await api.getExtraction(summary.document_id)
            return {
              ...base,
              extraction,
              extractionStatus: extraction.status === 'succeeded' ? 'done' : 'failed',
              extractionError: extraction.status === 'failed' ? extraction.error : null,
            }
          } catch {
            // No extraction result for this document (never ran, or predates it) —
            // it still belongs in the list; the Requirements tab just stays empty.
            return base
          }
        }),
      )
      if (!cancelled) setDocuments(restored)
    }
    void restore()
    return () => {
      cancelled = true
    }
  }, [])

  function updateDocument(localId: string, patch: Partial<LocalDocument>) {
    setDocuments((prev) =>
      prev.map((d) => (d.localId === localId ? { ...d, ...patch } : d)),
    )
  }

  async function handleDelete(localId: string) {
    const doc = documents.find((d) => d.localId === localId)
    setDocuments((prev) => prev.filter((d) => d.localId !== localId))
    if (selectedId === localId) {
      setSelectedId(null)
      setSelectedItem(null)
    }
    // Both routes derive document_id from content (ADR-3), so either is the same id —
    // prefer whichever one actually completed.
    const documentId = doc?.ingestDocumentId ?? doc?.extraction?.document_id
    if (documentId) {
      try {
        await api.deleteDocument(documentId)
      } catch {
        // The document is already gone from this list; a failed backend cleanup
        // isn't worth blocking on or re-adding the row for.
      }
    }
  }

  async function handleUpload(files: FileList) {
    // Tracks content ids already in (or about to enter) the knowledge base, so two
    // copies of the same file selected together are caught too, not just a re-upload
    // against what was already indexed before this call.
    const knownIds = new Set(
      documents.map((d) => d.ingestDocumentId ?? d.extraction?.document_id).filter(Boolean),
    )

    for (const file of Array.from(files)) {
      const bytes = await file.arrayBuffer()
      const contentId = await computeDocumentId(bytes)
      if (knownIds.has(contentId)) {
        setNotice(`"${file.name}" is already in the knowledge base — skipped.`)
        continue
      }
      knownIds.add(contentId)

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

  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), 4000)
    return () => clearTimeout(timer)
  }, [notice])

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <span className="app-mark">
            <IconSparkle />
          </span>
          <div className="app-titles">
            <span className="app-title">DocTrace</span>
            <span className="app-subtitle">
              Private document intelligence with page-level citations
            </span>
          </div>
        </div>
        <button
          type="button"
          className="icon-button"
          title="Settings"
          onClick={() => setShowSettings((v) => !v)}
        >
          <IconSettings />
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
            onDelete={(id) => void handleDelete(id)}
          />
        </aside>

        <main className="app-main">
          {!selectedDocument ? (
            <div className="empty-state large">
              <IconInbox />
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
                        url={
                          selectedDocument.bytes
                            ? undefined
                            : selectedDocument.ingestDocumentId
                              ? documentFileUrl(selectedDocument.ingestDocumentId)
                              : undefined
                        }
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

              {tab === 'ask' && <AskPanel onCiteClick={setOpenCitation} />}
            </>
          )}
        </main>
      </div>

      {openCitation && (
        <CitationViewer citation={openCitation} onClose={() => setOpenCitation(null)} />
      )}

      {notice && <div className="toast">{notice}</div>}
    </div>
  )
}
