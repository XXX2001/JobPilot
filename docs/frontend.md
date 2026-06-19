# Frontend (Svelte)

The `frontend/` directory is a SvelteKit 2 + Svelte 5 single-page app, statically built and served by the FastAPI backend, that drives JobPilot's job-search dashboard, application queue, CV/letter editors, and Gmail inbox.

## Stack

| Concern | Choice |
| --- | --- |
| Framework | SvelteKit 2 (`@sveltejs/kit ^2.50.2`) on Svelte 5 (runes: `$state`, `$props`, `$effect`) |
| Build/dev | Vite 7 (`frontend/vite.config.ts`) |
| Output | `@sveltejs/adapter-static` with SPA fallback (`frontend/svelte.config.js`) |
| Styling | Tailwind CSS 3 with CSS-variable theme + dark mode (`frontend/tailwind.config.js`) |
| Dark mode | `mode-watcher` (`ModeWatcher` / `toggleMode`), defaults to dark |
| Icons | `lucide-svelte` |
| Tests | Vitest (`frontend/vitest.config.ts`); `*.test.ts` for pure utils |

Runtime dependencies are deliberately tiny — only `lucide-svelte` and `mode-watcher`. Everything else (Tailwind, tailwind-variants, clsx) is dev-only.

## Build and serving

The app is a fully static SPA. There is no Node server in production.

```bash
cd frontend
npm ci          # install exact deps
npm run build   # vite build -> frontend/build/ (index.html + _app/immutable/*)
```

`svelte.config.js` uses `adapter-static` with `fallback: 'index.html'`, so every unmatched path resolves to `index.html` for client-side routing.

FastAPI then mounts that directory. In `backend/main.py:467-497`, a `SPAStaticFiles(StaticFiles)` subclass (`backend/main.py:468`) is mounted at `/` from `PROJECT_ROOT / "frontend" / "build"` (`backend/main.py:489-491`). Its `get_response` (`backend/main.py:469`) falls back to `index.html` on any miss (client-side routing) and sets cache headers:

- `/_app/immutable/*` (hashed assets) → `cache-control: public, max-age=31536000, immutable`
- everything else (including `index.html`) → `cache-control: no-cache`

If `frontend/build/` does not exist, the mount is skipped with a warning (`backend/main.py:492-495`) — the API still runs, but there is no UI until you build.

### Dev mode

`npm run dev` runs Vite on its own port. Cross-origin calls are configurable via `VITE_API_BASE_URL`: `apiFetch` prefixes it (`frontend/src/lib/api.ts:1`) and the WebSocket client rewrites its scheme to `ws`/`wss` (`frontend/src/lib/stores/websocket.ts:48-55`). When unset (the production/static case) both default to same-origin.

## Routes (pages)

SvelteKit file-based routing under `frontend/src/routes/`. The root layout `+layout.svelte` wraps every page with a fixed left sidebar.

| Route | File | Purpose |
| --- | --- | --- |
| `/` | `routes/+page.svelte` | **Today** dashboard — blocked actions, new matches, week stats, source health. Also the first-run gate. |
| `/queue` | `routes/queue/+page.svelte` | Classic apply **queue** — pick auto/manual/skip per match, run a batch, live apply-review modal. |
| `/tracker` | `routes/tracker/+page.svelte` | Kanban **application tracker**. |
| `/inbox` | `routes/inbox/+page.svelte` | Unlinked Gmail **inbox**. |
| `/cv` | `routes/cv/+page.svelte` | CV manager / tailoring review. |
| `/letters` | `routes/letters/+page.svelte` | Cover-letter management. |
| `/settings` | `routes/settings/+page.svelte` | Tabbed settings (profile, search, sources, sites, credentials, integrations, system). |
| `/analytics` | `routes/analytics/+page.svelte` | Stats / charts. |
| `/onboarding` | `routes/onboarding/+page.svelte` | Four-step first-run wizard. |
| `/jobs/[id]` | `routes/jobs/[id]/+page.svelte` | Per-job detail view (largest page). |
| — | `routes/+error.svelte` | Error boundary. |

### Layout shell

`routes/+layout.svelte` defines the sidebar `navLinks` (`+layout.svelte:40-49`), renders the toast stack (top-right, fed by `pushToast`), mounts global `LoginRequiredModal` and `HotkeyHelp`, wires `<svelte:window onkeydown={hotkeyHandle}>` to the hotkey dispatcher, and shows three persistent status widgets:

- **WebSocket status** pill (`Connected` / `Reconnecting…` / `Offline`) bound to `$wsStatus`.
- **Daily limit** pill `used / limit today`, colour-coded by `limitColour` (`+layout.svelte:145-151`).
- **Dark/light** toggle via `mode-watcher`.

`onMount` calls `connectWs()`, and an `$effect` keeps the hotkey dispatcher's current route in sync with `$page.route.id` (`+layout.svelte:31-38`).

## Talking to the backend

### REST — `apiFetch`

All HTTP goes through the single helper `apiFetch<T>(path, options)` in `frontend/src/lib/api.ts`:

- Prefixes `import.meta.env.VITE_API_BASE_URL` (empty → same-origin).
- Sets `Content-Type: application/json` **unless** the body is `FormData`, in which case it leaves the header off so the browser sets the `multipart/form-data` boundary itself (`api.ts:7-12`) — important for CV/file uploads.
- Throws `Error("API error <status>: <body>")` on non-2xx; callers commonly sniff `e.message.includes('409')` to treat "batch already running" as success (e.g. `onboarding/+page.svelte:154`, `queue/+page.svelte:119`).

Typed response shapes live in `frontend/src/lib/types/` (`api.ts`, `today.ts`, `ws.ts`). Gmail-specific calls are grouped in `frontend/src/lib/api/gmail.ts`.

### WebSocket — `$lib/stores/websocket.ts`

The realtime channel is a singleton WebSocket store (`frontend/src/lib/stores/websocket.ts`), connecting to `/ws` (served by `backend/api/ws.py:105`). Highlights:

- **URL resolution** (`websocket.ts:48-55`): derives `ws(s)://<host>/ws` from `window.location`, or rewrites `VITE_API_BASE_URL` for dev.
- **Reconnect** with exponential backoff + full jitter, capped at 30s, via the pure `nextBackoffDelay` helper (`frontend/src/lib/utils/backoff.ts:32`). `reconnectAttempt` resets to 0 on `onopen`.
- **`onWsConnect(cb)`** lets pages re-fetch state after a (re)connection (e.g. the queue re-syncs batch status).
- **Message handling** (`websocket.ts:73-111`): each frame is JSON-parsed and validated by `asWSMessage` (`types/ws.ts:188`), which narrows by the string `type` discriminator and drops unknown messages. Valid messages are pushed into the `messages` store (capped at the last 200). `login_required` populates the `loginPrompt` store; `gmail_message_received` raises a deep-linked toast (`/tracker` if matched, else `/inbox`).
- **`send(data: ClientMessage)`** serializes client→server frames (`confirm_submit`, `cancel_apply`, `patch_fields`, `login_done`, `login_cancel`).

The full protocol is mirrored from the backend's Pydantic union in `backend/api/ws_models.py` into the discriminated-union types in `frontend/src/lib/types/ws.ts` (server→client: `status`, `job_progress`, `apply_review`, `apply_result`, `login_required`, `captcha_detected`, `gmail_sync_status`, etc.). There is no codegen — the two files must be kept in lockstep by hand.

Pages consume the stream reactively. For example, `queue/+page.svelte:52-73` runs an `$effect` over `$messages`: an `apply_review` frame opens the confirm modal (and persists the pending job id via `pendingReview` utils so a review survives reload), and a terminal `status` frame (`progress >= 1.0` or `< 0`) ends the refreshing spinner and reloads the queue.

### Stores

| Store | File | Role |
| --- | --- | --- |
| `wsStatus`, `messages`, `loginPrompt`, `connectWs`, `send`, `onWsConnect` | `stores/websocket.ts` | Realtime channel + connection state. |
| `dailyLimit`, `limitColour` | `stores/dailyLimit.ts` | Polls `GET /api/applications/limit-status` every 60s and re-fetches on every `apply_result` WS message; ref-counted start/stop. |
| `toasts`, `pushToast`, `dismissToast` | `stores/toast.ts` | Global toast queue with auto-dismiss (default 5s); rendered once in the layout. |

## Key components (`src/lib/components/`)

- **`SetupWizard.svelte`** — embeddable setup wizard (the `/onboarding` page mirrors its CV-upload and keyword-save logic).
- **`KanbanBoard.svelte`** — drag-style tracker board used by `/tracker`.
- **`CVReviewPanel.svelte`** — tailored-CV diff/review surface (uses `DiffBlock.svelte`, `ScoreIndicator.svelte`).
- **`BatchPipelineTracker.svelte`** — visualizes scrape→match→tailor batch progress from WS `status` frames.
- **`LoginRequiredModal.svelte`** — driven by the `loginPrompt` store; sends `login_done` / `login_cancel`.
- **`GmailConnectCard.svelte`** — Gmail OAuth connect flow (paired with `lib/api/gmail.ts`).
- **`NewMatchesFeed.svelte`, `BlockedActionsStrip.svelte`, `WeekStats.svelte`, `SourceHealthPills.svelte`** — the Today dashboard widgets.
- **`StatusBar.svelte`** — bottom status bar reading the WS message stream.
- **`HotkeyHelp.svelte`** — the `?` shortcut overlay.
- **`LinkApplicationModal.svelte`** — links an inbox message to an application.
- Settings tabs under `components/settings/`: `ProfileTab`, `SearchTab`, `SourcesTab`, `SitesTab`, `CredentialsTab`, `IntegrationsTab`, `SystemTab`.
- Flourishes: `FloatingEmoji.svelte`, `EasterEggToast.svelte` (see `utils/easterEggs.ts`).

### Utilities (`src/lib/utils/`)

`hotkeys.ts` (route-aware single-key dispatcher; ignores keys while inputs are focused, `?` opens help), `backoff.ts` (reconnect delay), `focusTrap.ts` (modal focus), `onboarding.ts`, `pendingReview.ts`, `scoreBreakdown.ts`, `wordDiff.ts`, `easterEggs.ts`. Several ship with co-located Vitest specs (`*.test.ts`).

## Onboarding wizard

First-run gating lives in two places, coordinated through `sessionStorage` key `jobpilot_onboarding_dismissed` (`utils/onboarding.ts:19`):

1. **The gate** — `routes/+page.svelte:27-51` calls `maybeRedirectToOnboarding()` on mount: it fetches `GET /api/settings/status` and, if `shouldAutoRedirect(status, dismissed)` (i.e. `!setup_complete` and not yet dismissed), sets the session flag and `goto('/onboarding')`. The flag guarantees **at most one** auto-redirect per session, avoiding a loop.

2. **The wizard** — `routes/onboarding/+page.svelte` is a 4-step stepper:
   1. **API keys** (instructional) — shows whether `llm_key_set`, `adzuna_key_set`, `tectonic_found` are satisfied and how to set them in `.env`; keys are not editable from the UI.
   2. **CV upload** — `POST /api/settings/profile/cv-upload` (multipart, `.tex`/`.cls`).
   3. **Keywords** — chip input saved via `PUT /api/settings/search` (`{ keywords: { include: [...] } }`).
   4. **First batch** — enables a source (`PUT /api/settings/sites/{name}`) then kicks off `POST /api/queue/refresh`; a `409` is treated as success.

`firstIncompleteStep(status)` (`utils/onboarding.ts:29`) resumes the wizard at the first unmet step (only steps 1–2 are encoded in `SetupStatus`; it falls through to step 3). "Do this later" / "Skip" / "Finish" all call `finish()`, which sets the session flag and returns to `/`.

## Related

- [README](../README.md)
- [Backend / API](./api-reference.md)
- [Architecture](./architecture.md)
- [WebSocket protocol](./api-reference.md#websocket---ws)
