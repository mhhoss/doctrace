# Frontend (ADR-28)

React + TypeScript + Vite. Replaces Streamlit as the primary UI — see ADR-28 in
`docs/ARCHITECTURE.md` for why, and `docs/REQUIREMENTS.md`/`README.md` for the product
this is the UI for.

## Development

```
npm install
npm run dev
```

Runs a dev server on `http://localhost:5173` that proxies API calls to a separately
running backend on `:8000` (`uv run uvicorn app.main:app --reload`, from the repo
root). No CORS configuration needed on either side — see `vite.config.ts`.

## Production build

```
npm run build
```

Writes to `web/dist/`. `app/main.py` mounts this directory automatically (if it
exists) when the FastAPI app starts, so the built frontend and the API are served by
one process on one port — `uv run uvicorn app.main:app` alone is enough once this has
been built at least once. If `web/dist/` does not exist, the API still runs fine; the
frontend is simply absent (the old Streamlit UI, `streamlit_app.py`, still works
independently in the meantime).

## Structure

- `src/api.ts` — the entire HTTP contract with the backend, typed against
  `app/schemas/api.py` (`src/types.ts`). No other file talks to the network.
- `src/App.tsx` — layout and state; no client-side router — the app is one page with
  tab state, not a multi-route SPA, since there is nothing here that needs a URL of
  its own yet.
- `src/components/PdfViewer.tsx` — renders a PDF **entirely client-side**, from bytes
  already in the browser (the file the user just uploaded), with no server round-trip
  for a same-session preview — see this component's own docstring and ADR-28. The
  backend also persists and serves original files (ADR-29, `GET
  /documents/{document_id}/file`); `PdfViewer` falls back to fetching from that URL
  whenever client-side bytes aren't available (a page reload, or a chat citation via
  `CitationViewer.tsx`, ADR-30), so a past extraction's source survives a reload too.

## Known scope limits (by design, not oversight)

- **No highlight overlay on the PDF page itself** — clicking a requirement jumps the
  viewer to its page and shows the supporting quote in a side panel, rather than
  drawing a highlight box over the exact text on the canvas. A real improvement, left
  for later rather than rushed.
- **`Ask` searches the whole knowledge base**, not just the open document — this
  matches `POST /query`'s actual behavior today; the UI says so rather than implying a
  scoping the API does not do.
