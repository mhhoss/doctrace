import { useEffect, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import { IconChevronLeft, IconChevronRight } from './icons'

// Vite bundles the worker script itself and hands back a real URL to it — the
// standard way to wire pdf.js's worker under a bundler, per pdf.js's own docs.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

interface PdfViewerProps {
  /** In-browser bytes (a just-uploaded file) — takes precedence over `url` if both are given. */
  bytes?: ArrayBuffer
  /** A `GET /documents/{document_id}/file` URL (ADR-29) — used when `bytes` isn't available. */
  url?: string
  /** 1-indexed page to jump to when this changes (e.g. a citation was clicked). */
  targetPage: number | null
}

/**
 * Renders client-side via pdf.js, either from bytes already in the browser (a file
 * the user just uploaded, no server round-trip needed) or from a `GET
 * /documents/{document_id}/file` URL — the same viewer backs both the Requirements
 * view's in-session preview and a chat citation's on-demand lookup.
 */
export function PdfViewer({ bytes, url, targetPage }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const docRef = useRef<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const renderTaskRef = useRef<RenderTask | null>(null)

  // Reset to page 1 whenever the loaded source itself changes (a new upload, or a
  // different document's URL) — derived during render (React's documented pattern
  // for resetting state on a prop change) rather than a synchronous setState inside
  // the loading effect below.
  const [prevSource, setPrevSource] = useState({ bytes, url })
  if (prevSource.bytes !== bytes || prevSource.url !== url) {
    setPrevSource({ bytes, url })
    setPageNumber(1)
  }

  // Jump to a newly-clicked citation's page the same way.
  const [prevTargetPage, setPrevTargetPage] = useState(targetPage)
  if (targetPage !== prevTargetPage) {
    setPrevTargetPage(targetPage)
    if (targetPage && targetPage >= 1) setPageNumber(targetPage)
  }

  useEffect(() => {
    let cancelled = false
    // pdf.js detaches/transfers a `data` buffer it's given, so bytes need their own
    // copy — the caller may still hold the original (e.g. across re-renders).
    const loadingTask = bytes
      ? pdfjsLib.getDocument({ data: bytes.slice(0) })
      : pdfjsLib.getDocument({ url })
    loadingTask.promise
      .then((doc) => {
        if (cancelled) return
        docRef.current = doc
        setPageCount(doc.numPages)
        setLoadError(null)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : 'Could not open PDF.')
        }
      })
    return () => {
      cancelled = true
      loadingTask.destroy()
    }
  }, [bytes, url])

  useEffect(() => {
    const doc = docRef.current
    const canvas = canvasRef.current
    if (!doc || !canvas || pageNumber < 1 || pageNumber > doc.numPages) return

    let cancelled = false
    doc.getPage(pageNumber).then((page) => {
      if (cancelled) return
      const viewport = page.getViewport({ scale: 1.4 })
      canvas.width = viewport.width
      canvas.height = viewport.height
      const context = canvas.getContext('2d')
      if (!context) return
      const task = page.render({ canvas, canvasContext: context, viewport })
      renderTaskRef.current = task
      task.promise.catch(() => {
        // A superseded render (page changed again mid-render) throws on cancel —
        // not a real error, nothing to surface to the user.
      })
    })
    return () => {
      cancelled = true
      renderTaskRef.current?.cancel()
    }
  }, [pageNumber, pageCount])

  if (loadError) {
    return <div className="pdf-viewer-error">Could not open this PDF: {loadError}</div>
  }

  return (
    <div className="pdf-viewer">
      <div className="pdf-viewer-toolbar">
        <button
          type="button"
          title="Previous page"
          disabled={pageNumber <= 1}
          onClick={() => setPageNumber((n) => Math.max(1, n - 1))}
        >
          <IconChevronLeft />
        </button>
        <span>
          Page {pageNumber} of {pageCount || '…'}
        </span>
        <button
          type="button"
          title="Next page"
          disabled={pageNumber >= pageCount}
          onClick={() => setPageNumber((n) => Math.min(pageCount, n + 1))}
        >
          <IconChevronRight />
        </button>
      </div>
      <div className="pdf-viewer-canvas-wrap">
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
