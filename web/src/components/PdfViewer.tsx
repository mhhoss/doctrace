import { useEffect, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'

// Vite bundles the worker script itself and hands back a real URL to it — the
// standard way to wire pdf.js's worker under a bundler, per pdf.js's own docs.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

interface PdfViewerProps {
  bytes: ArrayBuffer
  /** 1-indexed page to jump to when this changes (e.g. a citation was clicked). */
  targetPage: number | null
}

/**
 * Renders entirely client-side from bytes already in the browser (the file the user
 * just uploaded) — the API stores nothing it could serve back for a preview, so there
 * is no server round-trip here. This is a deliberate scope choice for the first
 * version: reopening a *past* extraction's source across a page reload needs the
 * backend to persist original files, which it does not yet do (a separate, later
 * change) — for now the viewer only works for documents uploaded in this session.
 */
export function PdfViewer({ bytes, targetPage }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const docRef = useRef<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const renderTaskRef = useRef<RenderTask | null>(null)

  useEffect(() => {
    let cancelled = false
    // pdf.js detaches/transfers the buffer it's given, so each load needs its own copy
    // — the caller may still hold `bytes` (e.g. across re-renders).
    const loadingTask = pdfjsLib.getDocument({ data: bytes.slice(0) })
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
  }, [bytes])

  useEffect(() => {
    if (targetPage && targetPage >= 1) setPageNumber(targetPage)
  }, [targetPage])

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
          disabled={pageNumber <= 1}
          onClick={() => setPageNumber((n) => Math.max(1, n - 1))}
        >
          Prev
        </button>
        <span>
          Page {pageNumber} of {pageCount || '…'}
        </span>
        <button
          type="button"
          disabled={pageNumber >= pageCount}
          onClick={() => setPageNumber((n) => Math.min(pageCount, n + 1))}
        >
          Next
        </button>
      </div>
      <div className="pdf-viewer-canvas-wrap">
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
