# 07 — Frontend deep dive

JobPilot's frontend is a SvelteKit 2 app written in Svelte 5 runes, styled with Tailwind, served as a fully static SPA against the FastAPI backend at `http://localhost:8000`. This report walks the entire `frontend/src/` tree, page by page and component by component, then closes with a severity-tagged critique and a file-level inventory.

## 1. Purpose & stack

The app is the only UI surface for JobPilot — a desktop-flavoured dashboard for an AI-assisted job-application pipeline. It shows new matches ("Today"), a discovery queue with batch CV-tailoring review, an application Kanban tracker, a Gmail-derived inbox, a CV manager, settings, analytics, and a per-job deep-dive page. It connects to the backend over REST (`/api/...`) and a single WebSocket at `/ws`.

Concretely:
- **SvelteKit 2** with `@sveltejs/adapter-static` and `fallback: 'index.html'` ([svelte.config.js:9](frontend/svelte.config.js#L9)). That makes the build a pure SPA — no SSR, no `+page.server.ts`, no server hooks. The HTML shell at [app.html:1-15](frontend/src/app.html#L1) just preloads Inter from Google Fonts and bootstraps SvelteKit; `data-sveltekit-preload-data="hover"` is enabled.
- **Svelte 5 runes everywhere**: `$state`, `$derived`, `$derived.by`, `$effect`, `$props`, `$bindable`. There is no legacy `export let` / `$:` reactive syntax in any component. A few components still use `createEventDispatcher` (legacy bridge to the old event model), notably [KanbanBoard.svelte:40](frontend/src/lib/components/KanbanBoard.svelte#L40) and [SetupWizard.svelte:16](frontend/src/lib/components/SetupWizard.svelte#L16) — newer components have moved to callback props (e.g. `oncomplete`, `onback`, `onLink`).
- **Tailwind 3** with `darkMode: ["class"]` and an extensive HSL token system in [tailwind.config.js:5](frontend/tailwind.config.js#L5). The dark mode toggle is the only theme switcher; `app.html` boots with `class="dark"` already on `<html>`. Custom keyframes (`fade-in-up`, `confetti-pop`, `glow-pulse`, `shimmer`, `progress-shimmer`, `gentle-bounce`, `float`, `caret-blink`) are used by the playful surfaces. The CSS variables in [app.css:6-77](frontend/src/app.css#L6) override themselves — the bottom of the `:root` block re-defines `--accent`, `--border`, `--text*`, `--bg*` as raw hex values rather than HSL triplets, which means `bg-accent` resolves via the variable shadowing path. Not a bug, but it's a footgun for anyone reading the colour system.
- **Icons** come from `lucide-svelte 0.575` — used absolutely everywhere, ~25+ icons imported across the tree.
- **Dependency surface is tiny**: `lucide-svelte` and `mode-watcher` are the only runtime deps; everything else is dev tooling. There is no UI kit (no shadcn-svelte / bits-ui despite the `accordion-up/down` keyframes referencing `--bits-accordion-content-height`), no state library, no fetch wrapper library — every modal, dropdown, toast, kanban etc. is hand-rolled.

## 2. Build / dev config

`vite.config.ts` is a stub — only the SvelteKit plugin, no proxy, no aliases ([vite.config.ts:1-7](frontend/vite.config.ts#L1)). The backend base URL is supplied via `VITE_API_BASE_URL`: production `.env` has it empty (so `apiFetch('/api/foo')` hits the same origin), and dev `.env.development` sets `http://localhost:8000` ([.env.development:1](frontend/.env.development#L1)). Two consumers read it:

1. `lib/api.ts` prepends it to every `apiFetch` URL ([api.ts:1](frontend/src/lib/api.ts#L1)).
2. `lib/stores/websocket.ts` derives the WS URL by swapping `http` → `ws` and appending `/ws`, falling back to `window.location` ([websocket.ts:44-51](frontend/src/lib/stores/websocket.ts#L44)).

Note the inconsistency: `lib/api/gmail.ts` uses bare `fetch()` calls with hardcoded `/api/...` paths ([gmail.ts:11,17,23](frontend/src/lib/api/gmail.ts#L11)) — these will only work when the SPA is served same-origin as the backend (the production scenario where `VITE_API_BASE_URL` is empty). In dev they currently work via Vite's SPA dev server proxying nothing; if you ever run the SPA on a separate port from the backend, every Gmail/inbox/correspondence call will 404 because they ignore the base URL.

`tsconfig.json` extends the generated `.svelte-kit/tsconfig.json` and turns on `strict`, `checkJs`, `rewriteRelativeImportExtensions` ([tsconfig.json:3-13](frontend/tsconfig.json#L3)). `svelte-check` is the only static-analysis gate (`npm run check`); no ESLint, no Prettier config in the repo.

`postcss.config.js` is the default Tailwind + Autoprefixer pair.

## 3. Routing map

SvelteKit file-based routing — all routes are `+page.svelte` (no `+page.ts` / `+page.server.ts` anywhere, meaning all data fetching is client-side `onMount`).

| Route | Page file | REST calls | WS messages |
|---|---|---|---|
| `/` | [+page.svelte:1](frontend/src/routes/+page.svelte#L1) | `GET /api/today` | none directly |
| `/queue` | [queue/+page.svelte:1](frontend/src/routes/queue/+page.svelte#L1) | `GET /api/queue`, `GET /api/queue/status`, `POST /api/queue/refresh`, `PATCH /api/queue/:id/skip`, `PATCH /api/queue/:id/status` | `status`, `apply_review`, `apply_result` (via `$messages`) |
| `/tracker` | [tracker/+page.svelte:1](frontend/src/routes/tracker/+page.svelte#L1) | `GET /api/applications`, `GET /api/applications?needs_follow_up=true`, `PATCH /api/applications/:id`, `POST /api/applications/:id/events`, `/api/applications/export?format=csv` | none directly |
| `/jobs/[id]` | [jobs/[id]/+page.svelte:1](frontend/src/routes/jobs/[id]/+page.svelte#L1) | `GET /api/queue/:id`, `GET /api/documents/:id/diff`, `GET /api/applications?limit=200`, `GET /api/correspondence/:appId`, `POST /api/applications/:id/apply`, `POST /api/queue/:id/enrich-description` | none |
| `/cv` | [cv/+page.svelte:1](frontend/src/routes/cv/+page.svelte#L1) | `GET /api/settings/profile`, `GET /api/documents`, `POST /api/settings/profile/cv-upload` (multipart), document PDF links | none |
| `/inbox` | [inbox/+page.svelte:1](frontend/src/routes/inbox/+page.svelte#L1) | `GET /api/correspondence/unlinked`, `POST /api/correspondence/link` | none subscribed, but `gmail_message_received` toasts via layout |
| `/settings` | [settings/+page.svelte:1](frontend/src/routes/settings/+page.svelte#L1) | many — profile, search, sites, credentials, sources, status, custom-sites, gmail status/sync/disconnect | none |
| `/analytics` | [analytics/+page.svelte:1](frontend/src/routes/analytics/+page.svelte#L1) | `GET /api/analytics/summary`, `GET /api/analytics/trends?days=30`, `GET /api/settings/status` | none |
| catch-all error | [+error.svelte:1](frontend/src/routes/+error.svelte#L1) | none | none |

The route table is the source of the navigation bar in `+layout.svelte`. The "Today" dashboard occupies `/` (the UX-BET ship), and `/queue` is reachable via the sidebar link plus a "Classic queue →" shortcut button on Today.

## 4. Layout

`+layout.svelte` is small and well-organised ([+layout.svelte:1](frontend/src/routes/+layout.svelte#L1)). It owns five responsibilities:

1. **Navigation chrome** — a 220 px sidebar with seven nav links (Today, Queue, Tracker, Inbox, CV Manager, Settings, Analytics — defined inline as `navLinks` at [:39](frontend/src/routes/+layout.svelte#L39)), the JobPilot wordmark, a WS status pill (Wifi / WifiOff / spinner), the daily-limit pill ("X / Y today" with `limitColour()` colouring), and a dark-mode toggle wired to `mode-watcher`. The pill colour breaks at gray ≤ 6 / amber 7–8 / red ≥ 9 ([dailyLimit.ts:88](frontend/src/lib/stores/dailyLimit.ts#L88)).
2. **WS connect on mount** — `connectWs()` in `onMount` ([:30-32](frontend/src/routes/+layout.svelte#L30)). The store module also self-connects on import (see §7), so this is belt-and-braces.
3. **Toast stack** — a `fixed top-4 right-4 z-[100]` queue that renders every `$toasts` entry with a colour-coded border, optional CTA link, and dismiss button ([:62-100](frontend/src/routes/+layout.svelte#L62)). `role="status"` is set; no `aria-live` region wrapper, so screen readers may not pick up new toasts predictably.
4. **Modals always mounted** — `<LoginRequiredModal />` and `<HotkeyHelp />` sit at the top of the tree and self-show based on their respective stores.
5. **Hotkey wiring** — `<svelte:window onkeydown={hotkeyHandle} />` ([:54](frontend/src/routes/+layout.svelte#L54)) plus an `$effect` that pushes the current `$page.route.id` into `setCurrentRoute()` whenever the route changes ([:35-37](frontend/src/routes/+layout.svelte#L35)).
6. The `StatusBar` component is pinned at the bottom of the main column.

The layout is in `flex h-screen overflow-hidden` mode with the `<main>` column being the only scroll container — `<main class="flex-1 overflow-y-auto p-6">`. There is no mobile collapse / hamburger / off-canvas behaviour: at < 220 px sidebar width the layout silently breaks.

## 5. Routes

### `/` — Today dashboard ([+page.svelte:1](frontend/src/routes/+page.svelte#L1))
A single `GET /api/today` powers three sections via dedicated components: `BlockedActionsStrip` (kind/count/label chips), `NewMatchesFeed` (high-confidence / worth-reviewing / skipped buckets sized by score), `WeekStats` (submitted / quota / response-rate). The header shows a refresh button and a "Classic queue →" jump to `/queue` so users have an escape hatch. Loading state is a centered spinner; on error the message renders in a red banner with a manual dismiss. The page degrades gracefully: when `data.blocked_actions.actions` is empty the strip is hidden and the divider with it; when `data.new_matches.total === 0` `NewMatchesFeed` shows a "No new matches… click Scan" placeholder rather than an empty list. No skeletons.

### `/queue` ([queue/+page.svelte:1](frontend/src/routes/queue/+page.svelte#L1))
The richest page after settings. Two phases — `select` (the per-match list with Auto/Manual/Skip mode toggles) and `review` (delegated to `CVReviewPanel`). Highlights:
- Loads `/api/queue` once, then keeps a `Map<number, ApplyMode>` of decisions in component state ([:39-40](frontend/src/routes/queue/+page.svelte#L39)).
- `Scan for Jobs` POSTs `/api/queue/refresh`; on 409 the page treats the batch as already-running and keeps the spinner spinning. Otherwise it sets a 5-minute kill-switch timer that resets the `refreshing` flag if WS never delivers a terminal `status` ([:118-122](frontend/src/routes/queue/+page.svelte#L118)).
- An `$effect` watching `$messages` reacts to `apply_review` (pops a confirm-modal with screenshot + filled fields) and to terminal `status` messages (progress ≥ 1 or < 0 → clear spinner, refetch queue). Also wires `onWsConnect(syncBatchStatus)` so a page reload during an active batch picks up the spinner state again — see §9.
- Skip is persisted to backend immediately (`PATCH /api/queue/:id/skip`) and is reversible — flipping from skip back to manual hits `PATCH /api/queue/:id/status` with `{status: "new"}` ([:152-163](frontend/src/routes/queue/+page.svelte#L152)).
- Hotkeys: j/k focus next/prev card, a/m/s set mode on focused card, Enter proceeds to review, Esc clears focus ([:231-239](frontend/src/routes/queue/+page.svelte#L231)). All registered against route id `/` — but the comment at [:229](frontend/src/routes/queue/+page.svelte#L229) flags that `CVReviewPanel` also registers `/`, so every binding self-guards on `phase`.

Edge: when `refreshing` is true the queue UI is replaced by `<BatchPipelineTracker />` — meaning if you trigger a scan you cannot browse pending matches simultaneously.

### `/tracker` ([tracker/+page.svelte:1](frontend/src/routes/tracker/+page.svelte#L1))
A drag-and-drop Kanban over `KanbanBoard`. Loads two queries in parallel: all applications and `needs_follow_up=true` (rendered behind a tab with a count badge). The component dispatches `update` and `addEvent` legacy `CustomEvent`s back to the page; the parent does an optimistic local map for status changes ([:42-44](frontend/src/routes/tracker/+page.svelte#L42)) and rolls back by reloading on failure. Rejection events trigger a milestone toast (`EasterEggToast`) when count hits 10/25/50/75/100/150/200. CSV export is a plain `<a href download>` link.

### `/jobs/[id]` ([jobs/[id]/+page.svelte:1](frontend/src/routes/jobs/[id]/+page.svelte#L1))
The per-match detail view. Loads `/api/queue/:id`, `/api/documents/:id/diff` (graceful failure if no diff yet), then fires `resolveApplicationId()` which fetches up to 200 applications and finds the one with matching `job_match_id` ([:151-169](frontend/src/routes/jobs/[id]/+page.svelte#L151)). Three tabs — Description, CV Diff, Linked Emails. Apply buttons (auto / assisted / manual) POST to `/api/applications/:id/apply`. The "Fetch Full Description" button hits `/api/queue/:id/enrich-description` if description is missing or shorter than 300 chars. The breadcrumb says "Back to Job Queue" but links to `/` (the Today dashboard, not `/queue`) — minor mismatch.

### `/cv` ([cv/+page.svelte:1](frontend/src/routes/cv/+page.svelte#L1))
Two-pane: left is a drag-and-drop dropzone for `.tex` / `.cls` uploads, right is a list of tailored CV history filtered from `/api/documents` where `doc_type === 'cv'`. The history rows link to the PDF (`/api/documents/:matchId/cv/pdf`) and to the diff tab (`/jobs/:matchId?tab=diff`). The upload calls `handleFileUpload` which builds a `FormData` and passes it to `apiFetch` — **this is broken**; see §14 critique.

### `/inbox` ([inbox/+page.svelte:1](frontend/src/routes/inbox/+page.svelte#L1))
Lists unlinked Gmail messages from `GET /api/correspondence/unlinked`. Each row has a colour-coded category chip (rejection / interview_invite / offer / ats_ack / recruiter_outreach / other) and a "Link to app…" button that opens `LinkApplicationModal`. Linking POSTs `/api/correspondence/link` and refreshes the list. No optimistic update — but the modal closes before refresh completes, so there's a brief flash where the row is still visible.

### `/settings` ([settings/+page.svelte:1](frontend/src/routes/settings/+page.svelte#L1))
The 1,127-line god-component. Seven tabs — Profile, Search, Sites, Credentials, Sources, Integrations, System — each with its own load function, save function, and form state. Every tab eagerly fetches on mount (`onMount` calls all seven loaders concurrently, even tabs the user may never open). Credentials tab has nested forms for storing site logins (Fernet-encrypted server-side per the description text). Sources tab shows Adzuna/Gemini configured state from the env. Custom sites add/delete inline. The whole file is one giant `{#if activeTab === ...}{:else if ...}` cascade. Uses `font-heading` via an inline `<style>` block that imports the `Outfit` Google Font — the only component-scoped CSS in the whole app.

### `/analytics` ([analytics/+page.svelte:1](frontend/src/routes/analytics/+page.svelte#L1))
Stat cards (Total, Response Rate, Avg Match Score, This Week) plus a hand-drawn SVG bar chart over the last 30 days. Renders `<SetupWizard>` modally if `/api/settings/status` reports `setup_complete: false`. The "Complete Setup" CTA in the header re-opens the wizard.

### catch-all error ([+error.svelte:1](frontend/src/routes/+error.svelte#L1))
A playful page — bouncy 404 + floating emoji + a rotating witty message from `getErrorMessage()` + a "Back to Job Queue" button (which actually links to `/`, not `/queue` — same minor naming drift as the breadcrumb on `/jobs/[id]`).

## 6. Components

Grouping by purpose:

### Layout & status

- **StatusBar** ([StatusBar.svelte:1](frontend/src/lib/components/StatusBar.svelte#L1)) — bottom bar that derives the latest message from `$messages`, shows a coloured dot for WS state, and renders a progress bar for `status` messages. **Carries a known bug** acknowledged in a TODO at [:1-5](frontend/src/lib/components/StatusBar.svelte#L1): it reads `scraping_progress` / `matching_progress` / `tailoring_progress` types that the backend never emits, plus `msg.data.{source,found,matched,company,message}` fields that don't exist in `WSMessage`. Result: those branches are dead code that never fire.
- **HotkeyHelp** ([HotkeyHelp.svelte:1](frontend/src/lib/components/HotkeyHelp.svelte#L1)) — the `?` modal. Groups bindings, has a `trapFocus` Svelte action ([:27-45](frontend/src/lib/components/HotkeyHelp.svelte#L27)), close on backdrop click + Escape via the global hotkey dispatcher. The trap doesn't restore focus to the previously focused element on close.
- **LoginRequiredModal** ([LoginRequiredModal.svelte:1](frontend/src/lib/components/LoginRequiredModal.svelte#L1)) — gates on the `loginPrompt` store; Done/Cancel send `login_done`/`login_cancel` over the WS. No focus trap, no Escape handler.

### Cards & feed

- **JobCard** ([JobCard.svelte:1](frontend/src/lib/components/JobCard.svelte#L1)) — a standalone card with expand-to-show-description and an apply button + dropdown for manual/assisted/auto. Dispatches `skip` and `apply` events. **Possibly dead code**: no route currently imports JobCard — the queue page renders inline cards. Worth confirming before deleting.
- **NewMatchesFeed** ([NewMatchesFeed.svelte:1](frontend/src/lib/components/NewMatchesFeed.svelte#L1)) — three bucket lists for today's matches, each row links to `/queue` (not `/jobs/:id`). Empty-state copy says "No new matches since the last X".
- **BlockedActionsStrip** ([BlockedActionsStrip.svelte:1](frontend/src/lib/components/BlockedActionsStrip.svelte#L1)) — pill row keyed by `BlockedAction.kind`, with kind-specific colours and icons.
- **WeekStats** ([WeekStats.svelte:1](frontend/src/lib/components/WeekStats.svelte#L1)) — three-stat grid. `limitColor()` is duplicated logic from `dailyLimit.ts`'s `limitColour()` but with different thresholds (ratio-based here vs absolute there) — slight semantic drift.
- **KanbanBoard** ([KanbanBoard.svelte:1](frontend/src/lib/components/KanbanBoard.svelte#L1)) — five columns, HTML5 drag/drop, optimistic move dispatched via `update` event, per-card note input, event-menu popover (heard_back/interview/offer/rejection). Renders a rejection-milestone tagline on the rejected column.
- **ScoreIndicator** ([ScoreIndicator.svelte:1](frontend/src/lib/components/ScoreIndicator.svelte#L1)) — a tiny SVG ring around a centred numeric score.

### Workflow / batch

- **BatchPipelineTracker** ([BatchPipelineTracker.svelte:1](frontend/src/lib/components/BatchPipelineTracker.svelte#L1)) — vertical 5-step timeline (Scan → Rank → Store → Fit → CV) keyed off the latest `status.progress` value. Hardcoded progress ranges per step ([:14-20](frontend/src/lib/components/BatchPipelineTracker.svelte#L14)) — if the backend ever shifts a phase boundary, the visualisation desyncs. Tracks elapsed time client-side. Beautifully detailed: glowing node for active step, sub-progress bar inside the active step, overall progress bar at bottom.
- **CVReviewPanel** ([CVReviewPanel.svelte:1](frontend/src/lib/components/CVReviewPanel.svelte#L1)) — the most behaviour-dense component (~440 lines). Three phases — `review` (split-pane job-details + diff/PDF tabs), `confirm` (summary list), `done` (results). Diffs are lazily loaded per match and cached in a `Map`. Approval / base-CV / skip decisions stored in another `Map`. Hotkeys 1/2/3/←/→. On "Run" it iterates approved matches, hits `/api/applications/:id/apply` sequentially, collects ok/err results.
- **SetupWizard** ([SetupWizard.svelte:1](frontend/src/lib/components/SetupWizard.svelte#L1)) — 3-step modal (API keys → CV upload → keywords). Same `FormData` upload pattern as `/cv`. Step 1 is purely instructional (shows `.env` snippet to copy); the wizard cannot actually set keys.

### Gmail / inbox

- **GmailConnectCard** ([GmailConnectCard.svelte:1](frontend/src/lib/components/GmailConnectCard.svelte#L1)) — connection state + sync/disconnect. Uses `window.confirm` and `alert` (blocking native dialogs).
- **LinkApplicationModal** ([LinkApplicationModal.svelte:1](frontend/src/lib/components/LinkApplicationModal.svelte#L1)) — searchable picker fed by `GET /api/applications`. Uses `$bindable` for `open` so the parent can also close it. Has Esc-to-close via `<svelte:window>` and backdrop-click-to-close. The `role="dialog"` + `aria-modal="true"` are set but there's no focus trap.

### CV diff

- **DiffBlock** ([DiffBlock.svelte:1](frontend/src/lib/components/DiffBlock.svelte#L1)) — section + reason header, then an LCS word-level inline diff using `wordDiff()`. Removed words get strikethrough + red bg, added words green bg.

### Easter eggs / playful

- **EasterEggToast** ([EasterEggToast.svelte:1](frontend/src/lib/components/EasterEggToast.svelte#L1)) — celebratory variant with shimmer gradient text for milestones at ≥ 100 rejections, default for the rest. Auto-dismiss after `duration` (default 5s).
- **FloatingEmoji** ([FloatingEmoji.svelte:1](frontend/src/lib/components/FloatingEmoji.svelte#L1)) — a one-liner: emoji wrapped in `animate-float`.
- **TypewriterText** ([TypewriterText.svelte:1](frontend/src/lib/components/TypewriterText.svelte#L1)) — rotates through messages with a caret. **Possibly dead code**: no consumer was found in the routes/components I read. Worth confirming with grep.

## 7. Stores

The repo has an excellent [stores/README.md](frontend/src/lib/stores/README.md) documenting three patterns. Each store uses one.

### Pattern 1: ref-counted writable
`dailyLimit.ts` ([dailyLimit.ts:28](frontend/src/lib/stores/dailyLimit.ts#L28)). Wraps a `writable` in a factory that returns a `Readable` only. The first subscriber starts a 60-second polling timer plus a WS-message subscription that re-fetches on `apply_result`; the last unsubscriber stops both ([:44-66](frontend/src/lib/stores/dailyLimit.ts#L44)). Calls `GET /api/applications/limit-status` — confirmed to exist on the backend (`backend/api/applications.py:235`). `limitColour(used)` ships the gray/amber/red thresholds.

### Pattern 2: module-level state
`utils/hotkeys.ts` ([hotkeys.ts:1](frontend/src/lib/utils/hotkeys.ts#L1)). Plain `let` vars (`nextId`, `registrations`, `_currentRoute`) plus two writable stores (`helpOpen`, `activeBindings`). Public API is imperative (`register`, `deregister`, `setCurrentRoute`, `handle`). The handler swallows keys when an input/textarea/select/contenteditable is focused (except Esc, which blurs).

### Pattern 3: module-level hybrid
`websocket.ts` ([websocket.ts:1](frontend/src/lib/stores/websocket.ts#L1)). `wsStatus`, `messages`, `loginPrompt` are exported writables. Module self-connects on import (`if (typeof window !== 'undefined') connectWs();` at [:135-137](frontend/src/lib/stores/websocket.ts#L135)) — and the layout calls `connectWs()` again in `onMount`, which is idempotent. `messages` is `any[]` (commented at [:4-9](frontend/src/lib/stores/websocket.ts#L4) — tightening to `WSMessage[]` would surface real bugs in `StatusBar` that the maintainers have parked for FE-01).

The toast store ([toast.ts:1](frontend/src/lib/stores/toast.ts#L1)) is the simplest pattern — a plain writable list, `pushToast()` / `dismissToast()` exported as functions. Schedules auto-dismiss via `setTimeout`.

## 8. API client

`lib/api.ts` is a 17-line wrapper ([api.ts:1](frontend/src/lib/api.ts#L1)). It does three things and only three things: prepend `VITE_API_BASE_URL`, set `Content-Type: application/json`, throw `new Error('API error N: text')` on non-2xx, return `res.json()`. There is no:
- Generic error type — errors are stringly-typed and parsed back out at call sites (e.g. `e.message?.includes('409')` in [queue/+page.svelte:126](frontend/src/routes/queue/+page.svelte#L126)).
- Retry, timeout, abort handling.
- Override of Content-Type for FormData (see §14 — this is the cv-upload bug).
- Auth header — the app currently assumes same-origin / no auth.

`lib/api/gmail.ts` ([gmail.ts:1](frontend/src/lib/api/gmail.ts#L1)) is the only typed wrapper module. It uses bare `fetch()` instead of `apiFetch` — losing the base-URL prepending — and re-defines error throwing inline. Types are co-located (`GmailStatus`, `UnlinkedItem`). Exported functions cover status, forceSync, disconnect, unlinked list, link, thread.

## 9. WebSocket layer

Single page-lifetime socket at `/ws`. The dispatcher ([websocket.ts:35-119](frontend/src/lib/stores/websocket.ts#L35)) handles open/message/close/error. On `open` it flushes a callback list (`_onConnectCallbacks`, register via `onWsConnect`) — currently `/queue` uses this to re-sync batch status after a reconnect ([queue/+page.svelte:227](frontend/src/routes/queue/+page.svelte#L227)). On `close` it calls `scheduleReconnect()`, which is a single 3-second timer (`scheduleReconnect` at [websocket.ts:121](frontend/src/lib/stores/websocket.ts#L121)) — there is no exponential backoff, no jitter, no max-attempt cap. If the backend stays down, the client will spin a reconnect every 3 s forever.

Incoming messages are JSON-parsed and run through `asWSMessage()` ([types/ws.ts:176](frontend/src/lib/types/ws.ts#L176)) for type narrowing. Unknown types are warned to console and dropped. The `messages` store keeps the last 200 messages (`msgs.slice(-199)` + new entry). Two side-effects fire inside `onmessage`:
- `login_required` → sets `loginPrompt` so the modal pops up.
- `gmail_message_received` → calls `pushToast` with a category-based tone and a deep link to `/tracker` (if linked) or `/inbox` (if unlinked).
- `gmail_sync_status` → console.debug only.

`send()` only writes when the socket is `OPEN` — quiet no-op otherwise.

## 10. WSMessage typing

`lib/types/ws.ts` ([ws.ts:1](frontend/src/lib/types/ws.ts#L1)) is a hand-maintained mirror of `backend/api/ws_models.py`. The header comments call out that there is no codegen (FE-02 open) — it must be updated in lockstep. The union covers 14 server→client messages and 4 client→server messages. Each server type has a string-literal `type` discriminator. The `asWSMessage` function narrows an `unknown` value by switch on the discriminator — anything else returns `null`. **Drift hazard documented**: `StatusBar` references three types not in the union (`scraping_progress`, `matching_progress`, `tailoring_progress`).

## 11. Hotkeys

The dispatcher ([utils/hotkeys.ts:1](frontend/src/lib/utils/hotkeys.ts#L1)) is route-aware. Components call `register(routeId, bindingMap, { group })` from `onMount` and `deregister(handle)` from `onDestroy`. Binding entries are `{ label, action }` — the label is used to build the help modal ([HotkeyHelp.svelte:5-16](frontend/src/lib/components/HotkeyHelp.svelte#L5)). Universal rules:
- Modifiers (Ctrl/Alt/Meta) always pass through ([:108](frontend/src/lib/utils/hotkeys.ts#L108)).
- Input/textarea/select/contenteditable swallow everything except Esc, which blurs ([:118-124](frontend/src/lib/utils/hotkeys.ts#L118)).
- `?` toggles the help modal regardless of route.
- Esc closes the help modal when open.

The first-match-wins iteration means if two registrations bind the same key on the same route, only the earlier registration fires — which is why `CVReviewPanel` and the queue page both register under `/` and each binding self-guards on phase.

Layout wires `<svelte:window onkeydown={hotkeyHandle} />` at [+layout.svelte:54](frontend/src/routes/+layout.svelte#L54).

## 12. Type strategy

- **Discriminated unions** are used for the WS protocol — clean and idiomatic.
- **REST response types** are mostly inlined in each component (`interface Job` in queue/+page.svelte, interface `Job` again in jobs/[id]/+page.svelte, interface `Application` in tracker AND in KanbanBoard with subtly different fields). Three duplicate `Job` definitions, two `Application` definitions, two `SetupStatus` definitions (`SetupWizard.svelte` and `settings/+page.svelte` and `analytics/+page.svelte`) — there is no `lib/types/api.ts` to centralise them.
- **`any` count**: `messages` is `any[]` (deliberate, documented); inside `KanbanBoard.svelte` `app.events` is typed but the `addEvent` callback fallback at [tracker/+page.svelte:32](frontend/src/routes/tracker/+page.svelte#L32) catches with `e: any`. Most error catches across the codebase use `e: any` to extract `.message` — there are dozens of these. Could be tightened to `unknown` + `instanceof Error`.
- **Casts**: `(value as WSMessage)` is the only structural cast (in `asWSMessage`); a few `as HTMLInputElement`/`HTMLSelectElement` casts in event handlers. No `as unknown as X` anti-patterns found.

Alignment with backend Pydantic models: WS messages are mirrored explicitly; REST shapes are reverse-engineered per component, which is the main drift surface.

## 13. A11y

- **Modals**: `LoginRequiredModal` has no `role="dialog"` / `aria-modal` and no focus trap. `HotkeyHelp` has `role="dialog" aria-modal="true"` and a focus trap on the dialog root ([HotkeyHelp.svelte:27-45](frontend/src/lib/components/HotkeyHelp.svelte#L27)), but the trap pulls focus back to the dialog root on any focusout — meaning you cannot tab between the close button and any other interactive element inside. `LinkApplicationModal` sets the ARIA but no focus trap. The queue's confirm-apply modal at [queue/+page.svelte:359](frontend/src/routes/queue/+page.svelte#L359) has no role, no aria, no Esc handler, no backdrop click — just two buttons.
- **Backdrop click to close**: implemented in `HotkeyHelp`, `LinkApplicationModal`. Not in `LoginRequiredModal`, `SetupWizard`, queue's confirm modal.
- **Keyboard Escape**: handled by global hotkey for help only; queue confirm-modal traps no keys.
- **`<label htmlFor>`**: settings inputs are all properly labelled. Other forms are inconsistent.
- **Drag-and-drop alternative**: Kanban supports drag only — no keyboard reordering.
- **Toast `role="status"`** but no `aria-live="polite"` wrapper.
- **`<svelte-ignore>` comments** appear at [HotkeyHelp.svelte:50,60,61](frontend/src/lib/components/HotkeyHelp.svelte#L50) for `a11y_no_noninteractive_element_interactions`, `a11y_click_events_have_key_events`, `a11y_no_static_element_interactions` — the lints are suppressed rather than fixed.

## 14. Critique (severity-tagged)

### CRITICAL — `apiFetch` breaks every multipart upload
[lib/api.ts:6-9](frontend/src/lib/api.ts#L6) hardcodes `'Content-Type': 'application/json'` and spreads `options.headers` *after* it. That is the wrong order for the FormData case: the call sites in [cv/+page.svelte:62](frontend/src/routes/cv/+page.svelte#L62) and [SetupWizard.svelte:40](frontend/src/lib/components/SetupWizard.svelte#L40) pass `{ method: 'POST', body: fd }` without headers, so the request goes out as `application/json` with a multipart body and no boundary. FastAPI's `UploadFile = File(...)` parser will reject it. **The "PG-PRE fix" referenced in the task description does not appear to be in this code**: the cv-upload still calls `apiFetch` which still forces the JSON header. To verify: the backend endpoint at [backend/api/settings.py:387-410](backend/api/settings.py#L387) does accept bytes correctly — the bug is purely client-side. Fix: change `apiFetch` to omit `Content-Type` when `body instanceof FormData`, or call `fetch()` directly for uploads. The "CV registered at: {path}" success message will only ever fire when the upload somehow succeeds (it would not in a clean curl-equivalent).

### HIGH — `StatusBar` reads message types the backend never emits
Acknowledged TODO ([StatusBar.svelte:1-5](frontend/src/lib/components/StatusBar.svelte#L1)): the branches for `scraping_progress`, `matching_progress`, `tailoring_progress` are dead, as is the `msg.data?.{source,found,matched,company,message}` access (those keys don't exist on any typed message). Only the `status` branch can ever fire today. The bottom bar therefore looks correct only because of the fallback `<span>Ready</span>` path. Tightening the `messages` store to `WSMessage[]` would force this fix.

### HIGH — Settings is a 1,127-line god component
[settings/+page.svelte:1-1127](frontend/src/routes/settings/+page.svelte#L1). Seven concerns in one file. Each tab has its own loader, form state, save handler, plus chip helpers, plus an inline `<style>` block that imports a Google Font. Onload fires all seven loaders concurrently — six of them will never be needed because the user only sees the active tab. Splitting per tab (`Settings/Profile.svelte`, `Settings/Search.svelte`, etc.) would shed ~70% of the cognitive load.

### HIGH — WS reconnect has no backoff and no give-up
[websocket.ts:121-127](frontend/src/lib/stores/websocket.ts#L121) reconnects on a fixed 3-second timer indefinitely. After the backend restarts the client recovers — that part is correct — but if the backend is offline the client floods reconnect attempts forever, with no jitter, no max retries, and no user-visible signal beyond the sidebar pill turning red. Adding exponential backoff + max-attempt would prevent log spam and CPU drain. The `onWsConnect` callback list does properly re-sync after reconnect (queue page uses it), so the recovery contract itself is good.

### MEDIUM — Duplicated type definitions across routes
`Job`, `QueueMatch`, `Application`, `SetupStatus`, `Document`, `DiffEntry` are all redefined per file. They should live in `lib/types/api.ts` (matching the pattern of `lib/types/today.ts` and `lib/types/ws.ts`). Drift between `KanbanBoard`'s `Application` interface and the tracker page's local one is already visible — `KanbanBoard` types `events: ApplicationEvent[]` as required; the tracker uses `app.events ?? []` fallbacks suggesting the backend may send `null`.

### MEDIUM — Optimistic UI without proper rollback
Tracker's `handleUpdate` does an optimistic local map on status change and rolls back via a full `load()` on failure ([tracker/+page.svelte:42-61](frontend/src/routes/tracker/+page.svelte#L42)). That works but the spinner flashes the whole board. Worse: `handleAddEvent` does **not** roll back on failure — the request fires, success appends to events, failure shows a toast but no UI revert ([tracker/+page.svelte:64-95](frontend/src/routes/tracker/+page.svelte#L64)).

### MEDIUM — Toast/dialog use of `alert` and `confirm`
[GmailConnectCard.svelte:34,53](frontend/src/lib/components/GmailConnectCard.svelte#L34) uses `confirm()` for disconnect and `alert()` to display sync results. Blocking native dialogs in an otherwise polished UI. Should use the toast queue + a custom confirm modal.

### MEDIUM — A11y holes
Three of four modals lack proper `role="dialog"` + focus trap + Esc handler (covered in §13). The Kanban is drag-only with no keyboard alternative. Many SVG-only buttons in `/cv` and `/inbox` lack `title` or visible labels for assistive tech.

### MEDIUM — Mobile responsiveness
The shell is `flex h-screen` with a fixed 220 px sidebar. Below ~600 px the sidebar consumes 35-40% of the viewport. The settings tabs row uses `flex-wrap` so it survives, but inputs on Sources/Credentials tabs have explicit `min-w-[140px]` / `min-w-[200px]` which forces horizontal scroll on narrow screens. No off-canvas nav, no `md:` breakpoints in the layout itself.

### MEDIUM — Loading states are inconsistent
Some pages have skeletons (`/settings`, `/tracker`, `/analytics`, `/cv`), others a spinner (`/`, `/queue`, `/jobs/[id]`), others nothing (Today's `BlockedActionsStrip` and `WeekStats` render blank if data missing rather than skeleton). The `/inbox` page does distinguish initial load vs refresh ([inbox/+page.svelte:14-21](frontend/src/routes/inbox/+page.svelte#L14)) — a nice pattern that should be applied elsewhere.

### LOW — Dead / suspicious components
- `JobCard.svelte` doesn't appear to be imported by any route I read — the queue renders inline.
- `TypewriterText.svelte` doesn't appear to be imported anywhere.
Both should be either deleted or wired up. A `grep -r "JobCard\|TypewriterText"` would confirm.

### LOW — Naming drift Today vs Queue
The `/+error.svelte` button says "Back to Job Queue" but links to `/`. The `/jobs/[id]` breadcrumb says "Job Queue" and links to `/`. Since `/` is now Today (UX-BET), these are misleading. Either change the labels to "Back to Today" or change the hrefs to `/queue`.

### LOW — No frontend tests
No `*.test.ts` / `*.spec.ts` outside `node_modules`. No vitest, no playwright in `devDependencies`. The audit checklist asked. The answer is: nothing. For a 41-file UI tree with this much interaction surface (drag-and-drop kanban, hotkey dispatcher, WS state machine, multi-phase review flow), that's a meaningful gap.

### LOW — `WeekStats` quota colour ≠ sidebar quota colour
`WeekStats.limitColor` uses ratio thresholds (70% / 90%) while `dailyLimit.limitColour` uses absolute counts (7 / 9 out of a default 10). They agree for the default limit but diverge as soon as the user changes daily_limit in Settings. Pick one and share it.

### LOW — `setInterval` inside `BatchPipelineTracker`
[BatchPipelineTracker.svelte:68-74](frontend/src/lib/components/BatchPipelineTracker.svelte#L68) creates an elapsed-time interval inside an `$effect`. The cleanup is returned correctly, so no leak — but the stores/README explicitly recommends moving intervals into stores. It's a minor exception.

### Checklist of asks from the prompt

- **Setup wizard CV upload (PG-PRE fix)** — *Not fixed in `apiFetch`.* The wizard's call still goes through the wrapper that forces `Content-Type: application/json`, so the multipart upload is broken at the client. See CRITICAL above.
- **Daily-limit budget meter (UX-LIMIT)** — *Yes, it reads `/api/applications/limit-status` correctly* ([dailyLimit.ts:37](frontend/src/lib/stores/dailyLimit.ts#L37)), the backend endpoint exists ([backend/api/applications.py:235](backend/api/applications.py#L235)), and it refreshes on every `apply_result` WS message + 60 s poll.
- **Today dashboard at `/` (UX-BET)** — *Yes, `/` is Today.* `/queue` is still accessible via the sidebar and via a "Classic queue →" button on Today's header. Today degrades gracefully: blocked-actions hides when empty, new-matches shows a placeholder, week-stats renders zeros without error.
- **WS reconnect after server restart** — *Recovers.* `scheduleReconnect` retries every 3 s; on reconnect, `_onConnectCallbacks` fires, queue re-syncs batch state. No backoff (see HIGH above).
- **Mobile responsiveness** — minimal; see MEDIUM above.

## 15. Inventory

- [frontend/package.json](frontend/package.json) — SvelteKit 2 + Svelte 5 + Tailwind 3 deps; only `lucide-svelte` and `mode-watcher` as runtime deps.
- [frontend/svelte.config.js](frontend/svelte.config.js) — adapter-static, SPA fallback to index.html.
- [frontend/vite.config.ts](frontend/vite.config.ts) — vanilla SvelteKit Vite config.
- [frontend/tsconfig.json](frontend/tsconfig.json) — strict TS, extends `.svelte-kit/tsconfig.json`.
- [frontend/tailwind.config.js](frontend/tailwind.config.js) — HSL token system, dark mode via class, custom keyframes.
- [frontend/postcss.config.js](frontend/postcss.config.js) — tailwind + autoprefixer.
- [frontend/.env](frontend/.env) — production: empty `VITE_API_BASE_URL`.
- [frontend/.env.development](frontend/.env.development) — dev: `http://localhost:8000`.
- [frontend/src/app.html](frontend/src/app.html) — HTML shell, Inter font preload, default `class="dark"`.
- [frontend/src/app.d.ts](frontend/src/app.d.ts) — empty App namespace.
- [frontend/src/app.css](frontend/src/app.css) — Tailwind layers + HSL variable theme.
- [frontend/src/lib/api.ts](frontend/src/lib/api.ts) — `apiFetch<T>` wrapper. BUG: hardcodes JSON Content-Type.
- [frontend/src/lib/index.ts](frontend/src/lib/index.ts) — empty barrel.
- [frontend/src/lib/utils.ts](frontend/src/lib/utils.ts) — `cn()` helper via clsx + tailwind-merge.
- [frontend/src/lib/api/gmail.ts](frontend/src/lib/api/gmail.ts) — typed Gmail/correspondence API wrappers; bypasses `apiFetch`.
- [frontend/src/lib/types/today.ts](frontend/src/lib/types/today.ts) — types for `GET /api/today`.
- [frontend/src/lib/types/ws.ts](frontend/src/lib/types/ws.ts) — WS message union + `asWSMessage` narrower.
- [frontend/src/lib/utils/easterEggs.ts](frontend/src/lib/utils/easterEggs.ts) — playful message banks (rejection milestones, empty states, loading lines, batch outcomes, CV toasts, profile completion, apply confirmations, 404 quips).
- [frontend/src/lib/utils/hotkeys.ts](frontend/src/lib/utils/hotkeys.ts) — global hotkey dispatcher (pattern 2: module-level state).
- [frontend/src/lib/utils/wordDiff.ts](frontend/src/lib/utils/wordDiff.ts) — LCS-based word diff for `DiffBlock`.
- [frontend/src/lib/stores/dailyLimit.ts](frontend/src/lib/stores/dailyLimit.ts) — ref-counted store polling `/api/applications/limit-status` + WS invalidation.
- [frontend/src/lib/stores/toast.ts](frontend/src/lib/stores/toast.ts) — global toast queue.
- [frontend/src/lib/stores/websocket.ts](frontend/src/lib/stores/websocket.ts) — module-level WS lifecycle, `messages` / `wsStatus` / `loginPrompt`, `onWsConnect` callbacks.
- [frontend/src/lib/stores/README.md](frontend/src/lib/stores/README.md) — documents the three store patterns.
- [frontend/src/lib/components/BatchPipelineTracker.svelte](frontend/src/lib/components/BatchPipelineTracker.svelte) — 5-step pipeline visualisation driven by `status` WS messages.
- [frontend/src/lib/components/BlockedActionsStrip.svelte](frontend/src/lib/components/BlockedActionsStrip.svelte) — Today: kind-coloured action chips.
- [frontend/src/lib/components/CVReviewPanel.svelte](frontend/src/lib/components/CVReviewPanel.svelte) — review/confirm/done flow for tailored CVs; diff + PDF tabs; hotkeys 1/2/3/←/→.
- [frontend/src/lib/components/DiffBlock.svelte](frontend/src/lib/components/DiffBlock.svelte) — section-headed word-level diff card.
- [frontend/src/lib/components/EasterEggToast.svelte](frontend/src/lib/components/EasterEggToast.svelte) — milestone toast with celebration shimmer.
- [frontend/src/lib/components/FloatingEmoji.svelte](frontend/src/lib/components/FloatingEmoji.svelte) — animated emoji.
- [frontend/src/lib/components/GmailConnectCard.svelte](frontend/src/lib/components/GmailConnectCard.svelte) — Gmail OAuth connect + sync/disconnect. Uses native `confirm`/`alert`.
- [frontend/src/lib/components/HotkeyHelp.svelte](frontend/src/lib/components/HotkeyHelp.svelte) — `?` modal with grouped bindings + focus trap.
- [frontend/src/lib/components/JobCard.svelte](frontend/src/lib/components/JobCard.svelte) — standalone job card with apply menu. Likely unused.
- [frontend/src/lib/components/KanbanBoard.svelte](frontend/src/lib/components/KanbanBoard.svelte) — 5-column drag-drop board with per-card notes + event menu.
- [frontend/src/lib/components/LinkApplicationModal.svelte](frontend/src/lib/components/LinkApplicationModal.svelte) — searchable application picker for /inbox.
- [frontend/src/lib/components/LoginRequiredModal.svelte](frontend/src/lib/components/LoginRequiredModal.svelte) — pops on `login_required` WS; Done/Cancel sent back.
- [frontend/src/lib/components/NewMatchesFeed.svelte](frontend/src/lib/components/NewMatchesFeed.svelte) — Today: high/worth/low match buckets.
- [frontend/src/lib/components/ScoreIndicator.svelte](frontend/src/lib/components/ScoreIndicator.svelte) — circular score ring.
- [frontend/src/lib/components/SetupWizard.svelte](frontend/src/lib/components/SetupWizard.svelte) — 3-step onboarding modal; CV upload broken by `apiFetch` bug.
- [frontend/src/lib/components/StatusBar.svelte](frontend/src/lib/components/StatusBar.svelte) — bottom bar; contains dead branches (FE-01 TODO).
- [frontend/src/lib/components/TypewriterText.svelte](frontend/src/lib/components/TypewriterText.svelte) — rotating typewriter text. Likely unused.
- [frontend/src/lib/components/WeekStats.svelte](frontend/src/lib/components/WeekStats.svelte) — Today: 3-card weekly summary.
- [frontend/src/routes/+layout.svelte](frontend/src/routes/+layout.svelte) — sidebar nav, WS connect, toasts, hotkeys, theme toggle, daily-limit pill.
- [frontend/src/routes/+page.svelte](frontend/src/routes/+page.svelte) — Today dashboard at `/`.
- [frontend/src/routes/+error.svelte](frontend/src/routes/+error.svelte) — playful 404 with bouncy number + emoji.
- [frontend/src/routes/queue/+page.svelte](frontend/src/routes/queue/+page.svelte) — pending matches list + review handoff + hotkeys.
- [frontend/src/routes/tracker/+page.svelte](frontend/src/routes/tracker/+page.svelte) — application Kanban with all/follow-up tabs.
- [frontend/src/routes/jobs/[id]/+page.svelte](frontend/src/routes/jobs/[id]/+page.svelte) — job detail with description / CV diff / linked emails tabs.
- [frontend/src/routes/cv/+page.svelte](frontend/src/routes/cv/+page.svelte) — base CV upload (broken via `apiFetch`) + tailored history.
- [frontend/src/routes/inbox/+page.svelte](frontend/src/routes/inbox/+page.svelte) — unlinked Gmail items + Link-to-app modal.
- [frontend/src/routes/settings/+page.svelte](frontend/src/routes/settings/+page.svelte) — 7-tab god component (1,127 lines).
- [frontend/src/routes/analytics/+page.svelte](frontend/src/routes/analytics/+page.svelte) — stat cards + SVG bar chart + setup wizard gate.
