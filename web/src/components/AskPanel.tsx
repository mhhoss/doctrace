import { useState } from 'react'
import { api, ApiError } from '../api'
import { IconSend, IconSparkle } from './icons'
import type { AnswerResponse, Citation } from '../types'

interface Exchange {
  question: string
  answer: AnswerResponse | null
  error: string | null
}

interface AskPanelProps {
  onCiteClick: (citation: Citation) => void
}

export function AskPanel({ onCiteClick }: AskPanelProps) {
  const [question, setQuestion] = useState('')
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [busy, setBusy] = useState(false)

  async function submit() {
    const q = question.trim()
    if (!q || busy) return
    setQuestion('')
    setBusy(true)
    const index = exchanges.length
    setExchanges((prev) => [...prev, { question: q, answer: null, error: null }])
    try {
      const answer = await api.query(q)
      setExchanges((prev) =>
        prev.map((e, i) => (i === index ? { ...e, answer } : e)),
      )
    } catch (error) {
      setExchanges((prev) =>
        prev.map((e, i) =>
          i === index
            ? { ...e, error: error instanceof ApiError ? error.message : 'Something went wrong.' }
            : e,
        ),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ask-panel">
      <p className="ask-note">
        Searches across every document that has been added to the knowledge base
        (ingestion), not just the one currently open — this mirrors the underlying
        API, which does not yet scope a question to a single document.
      </p>
      <div className="ask-history">
        {exchanges.length === 0 && (
          <div className="empty-state large">
            <IconSparkle />
            Ask a question to get grounded, cited answers from your knowledge base.
          </div>
        )}
        {exchanges.map((exchange, i) => (
          <div className="ask-exchange" key={i}>
            <div className="ask-bubble ask-bubble-question">{exchange.question}</div>
            {exchange.answer && (
              <div
                className={
                  exchange.answer.is_refusal
                    ? 'ask-bubble ask-refusal'
                    : 'ask-bubble ask-answer'
                }
              >
                <p>{exchange.answer.answer}</p>
                {exchange.answer.sources.length > 0 && (
                  <div className="ask-sources">
                    {exchange.answer.sources.map((source, j) => (
                      <button
                        type="button"
                        key={j}
                        className="ask-source-chip"
                        title={`Open source page in ${source.filename}`}
                        onClick={() => onCiteClick(source)}
                      >
                        [{j + 1}] {source.filename}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {exchange.error && <div className="ask-bubble ask-error">{exchange.error}</div>}
            {!exchange.answer && !exchange.error && (
              <div className="ask-bubble ask-pending">
                <span className="ask-spinner" aria-hidden="true" />
                Searching the knowledge base…
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="ask-composer">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void submit()
            }
          }}
          placeholder="Ask something about your documents… (Enter to send, Shift+Enter for a new line)"
          rows={2}
        />
        <button
          type="button"
          className="ask-submit primary"
          title="Send"
          onClick={() => void submit()}
          disabled={busy || !question.trim()}
        >
          <IconSend />
        </button>
      </div>
    </div>
  )
}
