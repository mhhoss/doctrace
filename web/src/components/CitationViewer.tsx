import { useEffect, useState } from 'react'
import { api, documentFileUrl } from '../api'
import { PdfViewer } from './PdfViewer'
import { IconClose, IconFileDoc, IconFilePdf, IconFileText } from './icons'
import type { Citation } from '../types'

interface CitationViewerProps {
  citation: Citation
  onClose: () => void
}

/** Reuses the Requirements view's PdfViewer + quote-panel pattern for a chat
 * citation, resolving its page on demand (ADR-30) instead of a stored page number. */
export function CitationViewer({ citation, onClose }: CitationViewerProps) {
  const [page, setPage] = useState<number | null>(null)
  const [status, setStatus] = useState<'loading' | 'located' | 'unlocated' | 'error'>(
    citation.file_type === 'pdf' ? 'loading' : 'unlocated',
  )

  // Reset when a new citation is opened — derived during render (React's documented
  // pattern for resetting state on a prop change), so the effect below only ever
  // runs the actual async lookup, never a synchronous reset.
  const [prevCitation, setPrevCitation] = useState(citation)
  if (prevCitation !== citation) {
    setPrevCitation(citation)
    setPage(null)
    setStatus(citation.file_type === 'pdf' ? 'loading' : 'unlocated')
  }

  useEffect(() => {
    if (citation.file_type !== 'pdf') return
    let cancelled = false
    api
      .locateQuote(citation.document_id, citation.excerpt)
      .then((result) => {
        if (cancelled) return
        if (result.located && result.page_start !== null) {
          setPage(result.page_start)
          setStatus('located')
        } else {
          setStatus('unlocated')
        }
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [citation])

  return (
    <div className="citation-overlay" onClick={onClose}>
      <div className="citation-modal" onClick={(e) => e.stopPropagation()}>
        <div className="citation-modal-header">
          <span className="citation-modal-title">
            {citation.file_type === 'pdf' ? (
              <IconFilePdf />
            ) : citation.file_type === 'docx' ? (
              <IconFileDoc />
            ) : (
              <IconFileText />
            )}
            {citation.filename}
          </span>
          <button type="button" className="icon-button" title="Close" onClick={onClose}>
            <IconClose />
          </button>
        </div>
        {status === 'located' && (
          <PdfViewer url={documentFileUrl(citation.document_id)} targetPage={page} />
        )}
        {status === 'loading' && (
          <div className="empty-state">
            <span className="ask-spinner" style={{ display: 'block', margin: '0 auto 8px' }} />
            <div>Locating source page…</div>
          </div>
        )}
        {(status === 'unlocated' || status === 'error') && (
          <div className="empty-state">
            {citation.file_type === 'pdf'
              ? "Couldn't locate this excerpt's exact page — the passage below is the source."
              : `No page preview for ${citation.file_type.toUpperCase()} files — the passage below is the source.`}
          </div>
        )}
        <div className="quote-panel">
          <div className="quote-label">Source passage</div>
          <p dir="auto">{citation.excerpt}</p>
        </div>
      </div>
    </div>
  )
}
