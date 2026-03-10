# Module: Frontend

## Purpose

The frontend is a SvelteKit single-page application that provides the user interface for JobPilot, an automated job-hunting system. It connects to the FastAPI backend over both REST and WebSocket, enabling users to browse the daily matched-job queue, trigger automated or assisted applications, track application outcomes on a Kanban board, manage their LaTeX CV template, view per-job CV diffs, configure job-search preferences and site credentials, and monitor system health. The frontend itself contains no business logic; it is a thin reactive shell over the backend API.

---

## Key Components

### `routes/+layout.svelte`

The root layout that wraps every page. It renders a fixed 220 px sidebar containing the "JobPilot" wordmark, five navigation links (Job Queue `/`, Tracker `/tracker`, CV Manager `/cv`, Settings `/settings`, Analytics `/analytics`), a WebSocket status indicator (Connected / Reconnecting / Offline with color-coded icons), and a dark/light mode toggle. The main content area occupies the remaining width and includes a `<StatusBar>` component at the bottom. On mount it calls `connectWs()` to establish the shared WebSocket connection. It also unconditionally renders `<LoginRequiredModal>` (hidden unless triggered by a 401 response).

### `routes/+page.svelte` — Job Queue

The landing page. Shows the list of today's matched jobs returned by the queue API. Each job card displays the AI match score (color-coded green/yellow/red), job title, company, location, and three mode buttons: **Auto**, **Manual**, **Skip**. Users select a mode per job then click "Review & Apply" to advance to the `CVReviewPanel` sub-flow. A "Scan for Jobs" button triggers a background refresh. The page listens to WebSocket messages: an `apply_review` message surfaces a confirmation modal with a screenshot and pre-filled form fields; a `status` message with `progress >= 1.0` clears the refreshing state and reloads the queue.

### `routes/analytics/+page.svelte`

Displays application activity statistics. On mount it fires three parallel API calls and shows four stat cards (Total Applications, Response Rate, Avg Match Score, This Week) plus an inline SVG bar chart of applications-per-day for the last 30 days. If the setup is incomplete (`setup_complete: false`) a `SetupWizard` modal is shown automatically with a shortcut button in the header to reopen it.

### `routes/cv/+page.svelte` — CV Manager

Split into two panels. Left: a drag-and-drop / click-to-browse upload zone for the base `.tex` CV template; on file selection it registers the filename via a `PUT /api/settings/profile` call using a hardcoded `uploads/<filename>` path (no actual binary upload to the server). Right: a "Tailored CV History" list fetched from `/api/documents` (filtered to `doc_type === 'cv'`), showing match ID, relative age, a PDF preview link (`/api/documents/{match_id}/cv/pdf`), and a CV diff link (`/jobs/{match_id}?tab=diff`).

### `routes/settings/+page.svelte`

A multi-tab settings hub with six tabs:

- **Profile** — form for full name, email, phone, location, and a free-form JSON textarea for additional application answers. Saves via `PUT /api/settings/profile`.
- **Search** — chip-based keyword/location/excluded-keywords/excluded-companies inputs plus numeric controls for minimum salary, daily apply limit, batch schedule time, minimum match score (slider), and remote-only toggle. Saves via `PUT /api/settings/search`.
- **Sites** — card grid listing all configured scraping sites with enable/disable toggles. Each toggle fires `PUT /api/settings/sites/{name}`. Session status badges are shown inline.
- **Credentials** — expand-in-place forms for per-site email/password credentials stored encrypted on the backend. Supports adding, updating, and clearing browser sessions via `PUT /api/settings/credentials/{site}` and `DELETE /api/settings/credentials/{site}/session`.
- **Sources** — read-only status cards for Adzuna API and Google Gemini showing configured/missing state. Also contains the Custom Target URLs sub-section for adding/deleting arbitrary scraping targets via `/api/settings/custom-sites`.
- **System** — checklist of four prerequisites (Gemini API key, Adzuna API keys, Tectonic LaTeX engine, base CV uploaded) with ready/action-required badges, fetched from `/api/settings/status`.

### `routes/tracker/+page.svelte`

Renders `<KanbanBoard>` with all tracked applications fetched from `/api/applications`. Supports two interactions propagated via Svelte custom events: dragging/moving a card emits an `update` event which fires `PATCH /api/applications/{id}` with a new status, and adding a timeline event fires `POST /api/applications/{id}/events`. Both use optimistic updates with rollback on error. Event types `heard_back`, `interview`, `offer`, and `rejected` also automatically trigger a status column move.

### `routes/jobs/[id]/+page.svelte` — Job Detail

Detail view for a single queue match, accessed via a breadcrumb from Job Queue. Fetches match data from `/api/queue/{id}` and CV diff from `/api/documents/{id}/diff`. Displays job title, company, location, salary (formatted as `£Xk – £Yk`), posting age, and apply method badge. Provides three apply action buttons that all POST to `/api/applications/{matchId}/apply` with a `method` field:
- **Auto Apply** (shown only when `apply_method` is `easy_apply` or `auto`)
- **Assisted Apply**
- **Open & Apply** (manual — opens the listing in a new tab)

Two tabs switch between the raw job description and a **CV Diff** view that renders each changed section as a red-strikethrough / green-addition block, with a "View Tailored CV (PDF)" link at the bottom. The URL hash parameter `?tab=diff` is referenced by the CV Manager page but is not implemented as a reactive tab selector — `activeTab` defaults to `'description'` on every page load.

---

## Public Interface

### `routes/+layout.svelte`

| Type | Endpoint | Notes |
|------|----------|-------|
| WebSocket | `ws://<host>/ws` | Persistent connection, reconnects every 3 s on drop |

### `routes/+page.svelte` (Job Queue)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/queue` | Load pending matches (`{ matches, total }`) |
| POST | `/api/queue/refresh` | Trigger background job scan |

WebSocket messages consumed:
- `apply_review` — opens the confirm-submit modal with `filled_fields` and `screenshot_base64`
- `status` with `progress >= 1.0` — marks refresh complete, reloads queue

WebSocket messages sent:
- `{ type: "confirm_submit", job_id }` — approve an auto-apply form submission
- `{ type: "cancel_apply", job_id }` — reject a pending submission

User actions: set per-job mode (Auto / Manual / Skip), trigger scan, proceed to CV review panel, confirm or cancel auto-apply modal.

### `routes/analytics/+page.svelte`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/analytics/summary` | Total apps, response rate, avg match score, this-week count |
| GET | `/api/analytics/trends?days=30` | Daily application counts array |
| GET | `/api/settings/status` | Setup completeness check |

User actions: open/close setup wizard.

### `routes/cv/+page.svelte`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings/profile` | Read `base_cv_path` |
| PUT | `/api/settings/profile` | Register uploaded CV filename |
| GET | `/api/documents` | List tailored documents |
| GET | `/api/documents/{match_id}/cv/pdf` | Download tailored CV PDF (external link) |

User actions: drag-and-drop or click-browse a `.tex` file, view PDF, navigate to CV diff page.

### `routes/settings/+page.svelte`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings/profile` | Load profile |
| PUT | `/api/settings/profile` | Save profile |
| GET | `/api/settings/search` | Load search settings |
| PUT | `/api/settings/search` | Save search settings |
| GET | `/api/settings/sources` | Load API source status |
| GET | `/api/settings/status` | Load system setup checklist |
| GET | `/api/settings/sites` | List scraping sites |
| PUT | `/api/settings/sites/{name}` | Toggle site enabled |
| GET | `/api/settings/credentials` | List credential items |
| PUT | `/api/settings/credentials/{site}` | Save credentials |
| DELETE | `/api/settings/credentials/{site}/session` | Clear browser session |
| GET | `/api/settings/custom-sites` | List custom target URLs |
| POST | `/api/settings/custom-sites` | Add custom URL |
| DELETE | `/api/settings/custom-sites/{id}` | Delete custom URL |

User actions: edit and save profile, manage keyword/location chips, tune salary/score/schedule sliders, toggle sites and remote-only, add/update/clear site credentials, manage custom scraping URLs.

### `routes/tracker/+page.svelte`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/applications` | Load all applications |
| PATCH | `/api/applications/{id}` | Update application status |
| POST | `/api/applications/{id}/events` | Add a timeline event |

User actions: drag cards between Kanban columns, add timeline events (interview, offer, rejection, etc.).

### `routes/jobs/[id]/+page.svelte`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/queue/{id}` | Load match + embedded job data |
| GET | `/api/documents/{id}/diff` | Load CV diff entries |
| POST | `/api/applications/{matchId}/apply` | Trigger apply (`{ method: "auto" | "assisted" | "manual" }`) |
| GET | `/api/documents/{matchId}/cv/pdf` | View tailored CV PDF (external link) |

User actions: choose apply method, switch between description and CV diff tabs.

---

## Data Flow

All HTTP calls go through the `apiFetch<T>` helper in `frontend/src/lib/api.ts`, which prepends `VITE_API_BASE_URL` (empty by default, meaning same-origin), sets `Content-Type: application/json`, and throws on non-2xx responses.

The WebSocket connection is managed in `frontend/src/lib/stores/websocket.ts`. `connectWs()` is called once in `+layout.svelte`'s `onMount`. The store maintains:
- `wsStatus` — a Svelte writable (`'connected' | 'disconnected' | 'reconnecting'`) consumed by the sidebar indicator.
- `messages` — a readable store of the last 200 parsed JSON messages, consumed by `+page.svelte` via a Svelte `$effect` that reacts to new entries.

Reactive state uses Svelte 5 runes (`$state`, `$derived`, `$effect`). Data is loaded with `onMount` and stored in local `$state` variables; there is no shared global application state beyond the WebSocket stores. Components receive data as props or read it from the websocket store directly.

---

## Configuration

| Variable | Location | Default | Purpose |
|----------|----------|---------|---------|
| `VITE_API_BASE_URL` | `.env` / Vite env | `''` (empty string) | Base URL prepended to all `apiFetch` calls. Empty means same-origin (used when the SvelteKit dev server proxies to the backend, or when served from the same host). |

The WebSocket URL is derived from `VITE_API_BASE_URL` by replacing `http` with `ws`. If `VITE_API_BASE_URL` is empty, it falls back to `window.location.host` with protocol detection (`ws` or `wss`). If that also fails, it hard-falls back to `ws://localhost:8000/ws`.

Dark/light mode is powered by `mode-watcher` with `defaultMode="dark"`. Preference is persisted in `localStorage` by the library.

The Settings > Sources tab instructs users to set `GOOGLE_API_KEY`, `ADZUNA_APP_ID`, and `ADZUNA_APP_KEY` in the backend `.env` file; these are never read or sent by the frontend itself.

---

## Known Limitations / TODOs

- **CV upload is fake.** `routes/cv/+page.svelte` constructs a hardcoded `uploads/<filename>` path and saves it via profile PUT without actually transmitting the file bytes. A comment in the code reads: `// Save profile with a placeholder path (real upload path is server-side)`. There is no `multipart/form-data` POST; the `.tex` content is never sent to the server.

- **`?tab=diff` query parameter is not implemented.** The CV Manager page links to `/jobs/{match_id}?tab=diff` but `routes/jobs/[id]/+page.svelte` initialises `activeTab` as `'description'` unconditionally and never reads `$page.url.searchParams`. The diff tab will not be pre-selected when following that link.

- **Source breakdown comment.** In `routes/analytics/+page.svelte` a comment reads: `// Source breakdown (computed from summary; actual sources TBD)`. No per-source breakdown is currently displayed.

- **WebSocket message buffer is unbounded in time.** The `messages` store keeps a rolling window of 200 entries (`slice(-199)`) but is never cleared between page navigations, so stale events from a previous scan can re-trigger the `apply_review` modal if the component re-mounts.

- **Salary currency is hardcoded to GBP (`£`).** The `routes/jobs/[id]/+page.svelte` salary formatter always formats as `£Xk – £Yk` with no locale or currency field from the API.

- **Settings page loads all six data sources on mount regardless of active tab.** Every call (`loadProfile`, `loadSearch`, `loadSources`, `loadSystem`, `loadSites`, `loadCredentials`, `loadCustomSites`) fires in parallel in `onMount`, even for tabs the user may never visit.

- **Google Fonts loaded via external `@import` in `settings/+page.svelte`.** The Outfit font is fetched from `fonts.googleapis.com` at runtime inside a `<style>` block, introducing a remote dependency and a potential privacy/CSP concern. No other page does this.

- **No pagination on queue or applications.** `GET /api/queue` and `GET /api/applications` are fetched in their entirety; there is no client-side pagination or virtual scrolling.
