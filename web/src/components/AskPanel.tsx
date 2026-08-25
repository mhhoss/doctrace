import { useState } from 'react'
import { api, ApiError } from '../api'
import type { AnswerResponse } from '../types'

interface Exchange {
  question: string
  answer: AnswerResponse | null
  error: string | null
}

export function AskPanel() {
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
        {exchanges.map((exchange, i) => (
          <div className="ask-exchange" key={i}>
            <div className="ask-question">{exchange.question}</div>
            {exchange.answer && (
              <div className={exchange.answer.is_refusal ? 'ask-refusal' : 'ask-answer'}>
                <p>{exchange.answer.answer}</p>
                {exchange.answer.sources.length > 0 && (
                  <ul className="ask-sources">
                    {exchange.answer.sources.map((source, j) => (
                      <li key={j}>{source.filename}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {exchange.error && <div className="ask-error">{exchange.error}</div>}
            {!exchange.answer && !exchange.error && (
              <div className="ask-pending">Searching…</div>
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
          placeholder="Ask something about your documents…"
          rows={2}
        />
        <button type="button" onClick={() => void submit()} disabled={busy || !question.trim()}>
          Ask
        </button>
      </div>
    </div>
  )
}
