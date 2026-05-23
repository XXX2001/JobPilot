# Frontend File Map

Every application-code file in `frontend/src/`, its single responsibility, what it imports internally, and what backend endpoints or stores it uses.

Generated files (`.svelte-kit/`, `node_modules/`) and config files (`app.html`, `app.d.ts`, `vite.config.ts`) are excluded.

---

## Routes (`frontend/src/routes/`)

| File | Responsibility | Depends on (internal) | Uses |
|------|---------------|----------------------|------|
| `routes/+layout.svelte` | Root shell: sidebar nav, dark-mode toggle, WS status indicator, daily-limit pill, hotkey wire-up, global modals | `stores/websocket`, `stores/dailyLimit`, `components/StatusBar`, `components/LoginRequiredModal`, `components/HotkeyHelp`, `utils/hotkeys` | `GET /api/applications/limit-status` (via store); WS `/ws` |
| `routes/+page.svelte` | Job Queue page: card list, Auto/Manual/Skip mode selector, batch-scan trigger, keyboard navigation, auto-apply confirm modal | `lib/api`, `stores/websocket`, `components/CVReviewPanel`, `components/FloatingEmoji`, `components/BatchPipelineTracker`, `utils/easterEggs`, `utils/hotkeys` | `GET /api/queue`, `POST /api/queue/refresh`, `GET /api/queue/status`, `PATCH /api/queue/{id}/skip`, `PATCH /api/queue/{id}/status`; WS `apply_review` / `status` messages |
| `routes/+error.svelte` | SvelteKit error boundary: displays HTTP status + playful error copy | `components/FloatingEmoji`, `utils/easterEggs` | — |
| `routes/tracker/+page.svelte` | Application Tracker: tabbed Kanban (All / Needs follow-up), status drag-and-drop, event log, rejection milestone toasts, CSV export link | `lib/api`, `components/KanbanBoard`, `components/FloatingEmoji`, `components/EasterEggToast`, `utils/easterEggs` | `GET /api/applications`, `PATCH /api/applications/{id}`, `POST /api/applications/{id}/events` |
| `routes/cv/+page.svelte` | CV Manager: drag-and-drop `.tex` upload, tailored CV history list with PDF/diff links | `lib/api`, `components/FloatingEmoji`, `utils/easterEggs` | `GET /api/settings/profile`, `POST /api/settings/profile/cv-upload`, `GET /api/documents` |
| `routes/settings/+page.svelte` | Settings page: profile, search settings, API credentials, job sources, custom sites CRUD | `lib/api`, `utils/easterEggs` | `GET/PATCH /api/settings/profile`, `GET/PATCH /api/settings/search`, `GET/PATCH /api/settings/sources`, `GET/POST/DELETE /api/settings/credentials`, `GET /api/settings/status` |
| `routes/analytics/+page.svelte` | Analytics dashboard: summary stats, 30-day daily trend chart, setup wizard gate | `lib/api`, `components/SetupWizard` | `GET /api/analytics/summary`, `GET /api/analytics/trends`, `GET /api/settings/status` |
| `routes/jobs/[id]/+page.svelte` | Job detail view: full description, score indicator, apply controls, tailored-CV diff tab | `lib/api`, `components/ScoreIndicator`, `utils/easterEggs` | `GET /api/queue/{id}`, `POST /api/applications/{match_id}/apply`, `GET /api/documents/{id}/diff` |

---

## Components (`frontend/src/lib/components/`)

| File | Responsibility | Depends on (internal) | Uses |
|------|---------------|----------------------|------|
| `components/StatusBar.svelte` | Bottom status bar: last WS message, batch-progress narration | `stores/websocket`, `utils/easterEggs` | WS `status` / `scraping_status` messages (note: FE-01 — reads some legacy type names) |
| `components/LoginRequiredModal.svelte` | Modal prompted by `login_required` WS message; sends `login_done` / `login_cancel` back | `stores/websocket` | WS `login_required`; sends `login_done` / `login_cancel` |
| `components/HotkeyHelp.svelte` | `?`-triggered modal listing active keybindings grouped by route | `utils/hotkeys` (`helpOpen`, `activeBindings`) | — |
| `components/BatchPipelineTracker.svelte` | Animated step-progress display shown while a batch scan is running | `stores/websocket` | WS `status` messages (`progress` field drives step states) |
| `components/CVReviewPanel.svelte` | Step-through review panel: shows tailored CV diff per job, triggers apply, registers route hotkeys | `lib/api`, `components/DiffBlock`, `components/EasterEggToast`, `utils/easterEggs`, `utils/hotkeys` | `GET /api/queue/{id}`, `POST /api/applications/{match_id}/apply`; `GET /api/documents/{id}/diff` |
| `components/KanbanBoard.svelte` | Drag-and-drop Kanban with status columns, card expansion, event log entry | `lib/api`, `components/ScoreIndicator`, `stores/websocket`, `utils/easterEggs` | `GET /api/queue/{id}` (for job title/company); emits `update` / `addEvent` events to parent |
| `components/ScoreIndicator.svelte` | SVG ring gauge displaying a match score (0–100) in green / yellow / red | — | — |
| `components/DiffBlock.svelte` | Side-by-side or inline word-diff view for a single CV section | `utils/wordDiff` | — |
| `components/EasterEggToast.svelte` | Auto-dismissing toast with emoji for milestones / fun messages | — | — |
| `components/FloatingEmoji.svelte` | Animated floating emoji for empty states and error pages | — | — |
| `components/JobCard.svelte` | Rich job card with score ring, salary/location chips, expand-to-description, apply action | `lib/api`, `components/ScoreIndicator`, `stores/websocket`, `utils/easterEggs` | `POST /api/applications/{match_id}/apply`; WS `send()` for confirm/cancel |
| `components/SetupWizard.svelte` | Guided first-run wizard: API key entry, keyword setup, CV upload, tectonic check | `lib/api` | `POST /api/settings/credentials`, `POST /api/settings/search`, `POST /api/settings/profile/cv-upload`, `GET /api/settings/status` |
| `components/TypewriterText.svelte` | Typewriter animation cycling through a list of strings | — | — |

---

## Stores (`frontend/src/lib/stores/`)

| File | Responsibility | Depends on (internal) | Uses |
|------|---------------|----------------------|------|
| `stores/websocket.ts` | WebSocket singleton: `connectWs()`, exponential-backoff reconnect, `messages` readable, `wsStatus` writable, `loginPrompt` writable, typed `send()`, `onWsConnect()` callback registry | `types/ws` (`asWSMessage`, `ClientMessage`) | WS `/ws` endpoint |
| `stores/dailyLimit.ts` | Lazy-loaded readable for daily application limit; polls every 60 s and refreshes on `apply_result` WS message; `limitColour()` helper | `lib/api`, `stores/websocket`, `types/ws` | `GET /api/applications/limit-status` |

---

## Utils (`frontend/src/lib/utils/`)

| File | Responsibility | Depends on (internal) | Uses |
|------|---------------|----------------------|------|
| `utils/hotkeys.ts` | Global single-key hotkey dispatcher: `register()` / `deregister()` per route, `handle()` keydown listener, `helpOpen` / `activeBindings` stores, `setCurrentRoute()` for layout sync | — | — |
| `utils/easterEggs.ts` | Pure string-lookup helpers for playful copy: rejection milestones, empty-state messages, loading quips, batch-completion toasts, CV toasts, profile status | — | — |
| `utils/wordDiff.ts` | LCS-based word-level diff: `wordDiff(original, edited) → DiffSpan[]` | — | — |

---

## Types (`frontend/src/lib/types/`)

| File | Responsibility | Depends on (internal) | Uses |
|------|---------------|----------------------|------|
| `types/ws.ts` | TypeScript mirror of `backend/api/ws_models.py`: `WSMessage` discriminated union (server→client), `ClientMessage` union (client→server), `asWSMessage()` narrowing guard | — | — (type-only; mirrors backend Pydantic models) |

---

## Top-level lib (`frontend/src/lib/`)

| File | Responsibility | Depends on (internal) | Uses |
|------|---------------|----------------------|------|
| `lib/api.ts` | `apiFetch<T>(path, options)` — thin `fetch` wrapper: prepends `VITE_API_BASE_URL`, sets `Content-Type: application/json`, throws on non-2xx | — | All REST endpoints (called by every page and several components) |
| `lib/index.ts` | SvelteKit `$lib` package marker (empty, boilerplate) | — | — |
