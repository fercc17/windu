# ISReq Analytics — Pragma frontend (issue #40)

Frontend rebuild of the ISReq dashboard on **[Canonical Pragma](https://github.com/canonical/pragma)**
(React 19 + TypeScript + Vite), evaluating Pragma as the company's new design system.

This is the **scaffold** — it stands up the app shell (top bar, nav, content panel),
loads the Pragma component library + base styles, and wires a typed API client. The
per-page charts/tables are the next step.

## Architecture (see issue #40)

- **This app (frontend)** — React + Pragma SPA. Talks to the backend over `/api`.
- **Backend (to build)** — a Python **FastAPI** layer that exposes the existing,
  audited `isreq_dashboard` metrics as JSON. The analytics logic is reused verbatim;
  render isolation (Art. X) and the rest of the constitution are preserved.

In dev, Vite proxies `/api` → `http://localhost:8010` (override with `ISREQ_API_URL`).
Until the backend exists, the header shows **"API not running (expected — backend is #40)"** —
that is the correct state for the scaffold.

## Run

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check (tsc -b) + production build to dist/
```

## Pragma packages used

- `@canonical/react-ds-core`, `@canonical/react-ds-global`, `@canonical/react-ds-global-form` — components
- `@canonical/styles` — base CSS + fonts (imported in `src/main.tsx`)

> Versions are `*-experimental` — expect churn; part of the point is to report back on rough edges.

## Layout

```
src/
  main.tsx        entry; imports Pragma styles + fonts
  App.tsx         app shell (bar + nav + content) using Pragma <Button>/<Section>
  api/client.ts   typed fetch client for the analytics API (/api/health, /api/sync, …)
  pages.ts        the dashboard views ↔ their future API endpoints
  App.css         scaffold layout (placeholder greys → Pragma tokens later)
```
