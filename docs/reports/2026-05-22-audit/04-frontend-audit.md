# JobPilot Frontend Audit — 2026-05-22

Scope: `/home/mouad/Web-automation/frontend/` (SvelteKit 2.50 + Svelte 5.51 + TS 5.9 + Tailwind 3.4, adapter-static SPA). ~4,280 lines across 28 source files. Backend Python audit is out of scope.

---

## TL;DR — Top 5 Issues

1. **WebSocket protocol is broken on both ends.** Backend `broadcast_status` emits `{type: "status"}`; the Pydantic `ws_models.py` declares `scraping_status / matching_status / tailoring_status`; the frontend `StatusBar.svelte` branches on `scraping_progress / matching_progress / tailoring_progress`. Three different vocabularies, none consistent. Most of `StatusBar.svelte`'s status rendering is dead code. (FE-01)
2. **No API typing layer.** All 38 `apiFetch<T>()` call-sites hand-write their own response shape inline (`Job`, `QueueMatch`, `Profile`, `SearchSettings`, `Document`, `Summary`, `Application`, ...). Backend has FastAPI + Pydantic and exposes an OpenAPI schema but no generation step is wired up. Same shape (`QueueMatch`, `Job`, `DiffEntry`) is redeclared in 4+ places with subtle drift (e.g. `location: string` vs `location?: string`). (FE-02)
3. **`catch (e: any)` is the only error story.** 18 occurrences. `apiFetch` throws an `Error` containing the raw response body — the UI then string-matches `e.message?.includes('409')` (`routes/+page.svelte:120`). No discriminated error type, no toast/error-boundary primitive beyond inline red banners copy-pasted in every route. (FE-03)
4. **Settings page is a 1,115-line god-component.** `routes/settings/+page.svelte` holds 6 tabs, 7 loaders, 7 savers, ~20 `$state` variables, inline `<style>` font import — there is no tab-component decomposition. Same file also embeds an `@import url(...fonts.googleapis...)` inside `<style>` which duplicates the Inter import already in `app.html`. (FE-04)
5. **State-management is half-migrated to Svelte 5.** Components use the new runes (`$state`, `$derived`, `$props`, `$effect` — 147 usages, 0 `$:` reactive statements) yet three child components (`KanbanBoard`, `JobCard`, `SetupWizard`) still emit events via `createEventDispatcher` + `on:event` and parents use the Svelte 4 `on:update={...}` syntax. The websocket store uses Svelte's classic `writable()` instead of a `$state` rune-based class. Inconsistent contract makes refactoring risky. (FE-05)

---

## Findings Table

| ID | Title | Severity | File:line | Fix Sketch |
|---|---|---|---|---|
| FE-01 | WebSocket message-type triple drift (frontend / Pydantic / actual broadcast) | **High** | `lib/components/StatusBar.svelte:25-35`, `backend/api/ws_models.py:43-72`, `backend/api/ws.py:198` | Pick one vocabulary (recommend the `ws_models.py` schema), align backend broadcasters and frontend listeners; codegen TS from Pydantic |
| FE-02 | No typed API client; types duplicated across routes | **High** | `lib/api.ts:1-17`, every `routes/*/+page.svelte` script block | Run `openapi-typescript` against FastAPI's `/openapi.json` into `src/lib/api/schema.ts`; wrap `apiFetch` with `openapi-fetch` |
| FE-03 | Error handling: raw `Error.message` string-matching, no envelope | **High** | `routes/+page.svelte:120` (`e.message?.includes('409')`), all `catch (e: any)` sites | Parse status from `Response`; define `class ApiError extends Error { status; body; code }`; centralize a toast store |
| FE-04 | `routes/settings/+page.svelte` is 1,115 lines, six tabs in one file | **High** | `routes/settings/+page.svelte:1-1115` | Split each tab into `lib/components/settings/<Tab>.svelte`; lift shared loaders into a `settingsStore` |
| FE-05 | Mixed Svelte 4 events (`createEventDispatcher`/`on:`) and Svelte 5 callback props | **High** | `lib/components/KanbanBoard.svelte:40`, `JobCard.svelte:30`, `SetupWizard.svelte:16`; consumed at `routes/tracker/+page.svelte:145`, `routes/analytics/+page.svelte:112` | Convert all 3 to callback-prop pattern (`onupdate`, `onclose`, etc.) like `CVReviewPanel` already does |
| FE-06 | `messages` store typed `any[]` — every consumer downcasts | **High** | `lib/stores/websocket.ts:7-8` | Import `WSMessage` type from `ws_models.py` (after FE-01); use `writable<WSMessage[]>` |
| FE-07 | WebSocket reconnect: fixed 3 s, no backoff, no ping, no jitter | **Med** | `lib/stores/websocket.ts:88-94` | Exponential backoff (1s → 2 → 4 → 8 → 16 s cap) + jitter; emit periodic `{type:"ping"}` (backend already handles it: `ws.py:179`) |
| FE-08 | `messages` store grows unboundedly capped at 200 entries; subscribed components re-render on every msg | **Med** | `lib/stores/websocket.ts:63,71` | Use a derived `lastStatus` store (only emits when `type==='status'`); keep history out of hot path |
| FE-09 | No SvelteKit `load` functions used anywhere; everything fetched in `onMount` after JS boot | **Med** | every route | OK for adapter-static SPA, but a `+page.ts` `load()` would let SvelteKit dedupe / prefetch on hover (`app.html` already has `data-sveltekit-preload-data="hover"` — currently no-op) |
| FE-10 | Dead components shipped to bundle | **Med** | `lib/components/JobCard.svelte` (185 lines, never imported), `lib/components/TypewriterText.svelte` (59 lines, never imported) | Delete or wire up |
| FE-11 | Dead derived state | **Low** | `routes/jobs/[id]/+page.svelte:130` (`isEasyApply` declared, never read) | Delete |
| FE-12 | `routes/cv/+page.svelte` upload "stores a placeholder path" — file is **not** actually uploaded | **High** | `routes/cv/+page.svelte:62-72` | Either implement multipart upload to a backend endpoint, or remove the misleading drop-zone UI |
| FE-13 | Random message generators (`getEmptyState`, `getApplyConfirmation`) called inside `$derived` / template — causes new pick on every reactive recompute | **Med** | `routes/+page.svelte:160`, `routes/tracker/+page.svelte:15`, `routes/cv/+page.svelte:20`, `routes/jobs/[id]/+page.svelte:131` | Wrap with `$state(getEmptyState(...))` once, or memoize in module scope |
| FE-14 | Hardcoded `text-zinc-*` / `border-zinc-*` colors bypass the design-token system | **Med** | `lib/components/DiffBlock.svelte:33`, `JobCard.svelte:119,130`, `CVReviewPanel.svelte:239,249,253,261` | Use `text-foreground / text-muted-foreground / border-border` like the rest of the codebase |
| FE-15 | `app.css` defines tokens **twice** (HSL `--accent` then a raw hex `#6366f1` later) — second one silently wins | **Med** | `src/app.css:21-41` (`.dark` block: 58-72) | Pick one system (HSL alpha-channel or hex). Currently breaks `bg-accent/<alpha-value>` because the second declaration is not HSL-formatted |
| FE-16 | `<html class="dark">` is hardcoded; `mode-watcher` initializes with `defaultMode="dark"`. Light mode is "supported" but never default for new users and not persisted under SPA static build | **Low** | `src/app.html:2`, `routes/+layout.svelte:42` | Let `ModeWatcher` write the class; remove the hardcoded `dark` |
| FE-17 | `apiFetch` always sets `Content-Type: application/json`, including for `GET` and `DELETE` with no body | **Low** | `lib/api.ts:5-10` | Only set when `options.body` is a string; otherwise the browser may set it correctly for `FormData` (relevant for future CV upload — see FE-12) |
| FE-18 | `apiFetch` returns `res.json()` unconditionally, including for 204 / empty bodies (would throw on parse) | **Low** | `lib/api.ts:16` | Guard `if (res.status === 204) return undefined as T;` |
| FE-19 | `send(data: any)` — no type for outbound WS messages | **Low** | `lib/stores/websocket.ts:96` | Type as `ClientMessage` (from `ws_models.py`) once codegen is in place |
| FE-20 | A11y — drop zone uses `role="region"` on a focusable interactive element; keyboard cannot upload | **Med** | `routes/cv/+page.svelte:151-165` | `role` should be removed; the hidden `<input type=file>` is tabbable but the visual hint area doesn't show focus ring |
| FE-21 | A11y — sidebar nav link "active" state is conveyed only by background color, no `aria-current="page"` | **Low** | `routes/+layout.svelte:55-67` | Add `aria-current={isActive ? 'page' : undefined}` |
| FE-22 | A11y — connection status uses red/green dot only; no text equivalent for screen readers (label is there but icon has no `aria-hidden`) | **Low** | `lib/components/StatusBar.svelte:16-22` | Add `aria-hidden="true"` to the dot, ensure text suffices |
| FE-23 | A11y — `<iframe>` PDF preview falls back to diff on error, but there's no loading state and `title="Tailored CV PDF"` is the only screen-reader cue | **Low** | `lib/components/CVReviewPanel.svelte:297-303` | Add `<noscript>` / "Loading..." cover; surface load failures explicitly |
| FE-24 | Tab order on Job Queue cards: 3 buttons per card (Auto/Manual/Skip) plus list of 10+ jobs creates a 30-button tab loop with no keyboard shortcuts | **Low** | `routes/+page.svelte:295-308` | Use radiogroup pattern (`role="radiogroup"` + `role="radio"`) for the per-job mode selector |
| FE-25 | SVG bar chart has `aria-label` but no data table / textual fallback | **Low** | `routes/analytics/+page.svelte:174-215` | Add visually-hidden table for screen readers |
| FE-26 | `apiFetch` swallows network errors as `Error('Failed to fetch')`; the inline error banner has no "retry" UX | **Low** | `lib/api.ts:3-17` + all routes | Distinguish network-down vs HTTP-non-2xx in the wrapper; banner offers a retry button |
| FE-27 | Inline arrow functions in `<button onclick={() => ...}>` re-create on every render — fine in Svelte 5 ($state granular reactivity) but the `routes/jobs/[id]/+page.svelte:273-294` "Fetch Full Description" button has a **70-line inline async handler** that should be a named function | **Low** | `routes/jobs/[id]/+page.svelte:273-294` | Extract to `async function fetchFullDescription()` |
| FE-28 | Settings page imports Google Font twice (HTML + `<style>` `@import` in `+page.svelte:418`) | **Low** | `routes/settings/+page.svelte:418`, `src/app.html:6` | Remove the `@import`; declare the Outfit font in `app.html` if needed globally, or use `tailwind.config.js` `fontFamily.heading` |
| FE-29 | `KanbanBoard.svelte` `byColumn` is `$derived(() => {...})` — `$derived` should hold a value, not a function; consumers call `byColumn()` like `byColumn()['rejected']` which defeats memoization | **Med** | `lib/components/KanbanBoard.svelte:50-59`, `61`, `136` | Use `$derived.by(() => {...})` or `$derived((() => {...})())` so it evaluates once per dep change |
| FE-30 | Same `$derived(() => ...)` anti-pattern in `JobCard.svelte:45` (`salary`) and `routes/jobs/[id]/+page.svelte:72,85` | **Med** | as above | Same fix |
| FE-31 | `jobs/[id]/+page.svelte` reads `$page.params.id` and `parseInt(... ?? '0')` — silently treats invalid URLs as match id `0` and 404s as load-error | **Med** | `routes/jobs/[id]/+page.svelte:59` | Validate `Number.isFinite(matchId) && matchId > 0` and call `error(404, ...)` from SvelteKit |
| FE-32 | `tsconfig.json` extends generated `.svelte-kit/tsconfig.json`; no path-aliases beyond `$lib`, no explicit `include` — relies on stock SK defaults (fine, but means custom dirs added later won't be checked) | **Low** | `frontend/tsconfig.json` | Leave; document via comment |
| FE-33 | `vite.config.ts` is the bare-minimum stub — no `define`, no `optimizeDeps`, no `server.proxy` for `/api` & `/ws` (developers need to set `VITE_API_BASE_URL` in `.env.development`) | **Low** | `frontend/vite.config.ts` | Add `server.proxy` for `/api` and `/ws` → `localhost:8000` for cleaner dev UX |
| FE-34 | `svelte.config.js` adapter-static with `fallback: 'index.html'` — no `prerender` directive anywhere; every route ships JS for an SPA only. OK for an internal tool, suboptimal for SEO (n/a here) | **Low** | `frontend/svelte.config.js` | Leave; document the SPA choice |
| FE-35 | No tests anywhere — `check` script is the only verification (`svelte-check` typecheck only) | **Med** | `package.json:11`, no `*.test.ts`/`*.spec.ts` files | Add Vitest + Testing Library for the diff utility (`wordDiff.ts` is pure and easy to test), API client wrapper, and at least one route smoke test |
| FE-36 | `package.json` lacks `lint` / `format` scripts; no ESLint, no Prettier config; relies on editor defaults | **Low** | `package.json` | Add `eslint-plugin-svelte` + Prettier `prettier-plugin-svelte`; wire a `lint` script |

---

## Per-Finding Detail

### FE-01 — WebSocket message-type triple drift (**High**)

Three different vocabularies are in play for "the same" event taxonomy:

| What it does | Pydantic class (`ws_models.py`) | Actual `type` string sent by backend | Frontend listener |
|---|---|---|---|
| General progress | `ScrapingStatus`/`MatchingStatus`/`TailoringStatus` (`scraping_status`, etc.) | `"status"` (`ws.py:198`, `morning_batch.py:218`) | `"status"` ✓ matches backend; `"scraping_progress"`, `"matching_progress"`, `"tailoring_progress"` ✗ never sent (`StatusBar.svelte:25-29`) |
| Login | `LoginRequired { type: "login_required" }` | (presumably matches; not verified end-to-end) | `"login_required"` ✓ (`websocket.ts:64`, `StatusBar.svelte:31`) |
| Apply review | `ApplyReview { type: "apply_review" }` | (matches) | `"apply_review"` ✓ (`routes/+page.svelte:58`) |
| Job assessment | (no Pydantic class) | `"job_progress"` (`ws.py:218`) | Not consumed in frontend |

Snippet — `StatusBar.svelte:25-30`:

```svelte
{#if $lastMessage.type === 'scraping_progress'}        <!-- DEAD -->
    <span>🔍 Scraping: {$lastMessage.data?.source ?? ''} — {$lastMessage.data?.found ?? 0} jobs found</span>
{:else if $lastMessage.type === 'matching_progress'}    <!-- DEAD -->
    <span>Matching: {$lastMessage.data?.matched ?? 0} matched</span>
{:else if $lastMessage.type === 'tailoring_progress'}   <!-- DEAD -->
    <span>Tailoring CV for {$lastMessage.data?.company ?? 'job'}…</span>
```

These three branches will never fire — yet they look real and reference a `.data` sub-object that nothing else uses. The user will only ever see the generic `lastMessage.message` branch (`StatusBar.svelte:35-52`).

**Migration path:**
1. Decide canonical taxonomy. The Pydantic models in `ws_models.py` already exist and are the most descriptive — adopt them.
2. Replace `broadcast_status(...)` in `ws.py` with typed helpers (`broadcast_scraping_status`, etc.) that emit the model's `type` literal.
3. Generate TS types from Pydantic (`datamodel-code-generator` or `pydantic2ts`) into `frontend/src/lib/api/ws-schema.ts`.
4. Type the `messages` store as `WSMessage[]` (see FE-06) — the discriminated-union `type` field gives narrowing for free.

### FE-02 — No API typing layer (**High**)

`lib/api.ts` is 17 lines and asks the caller to pass `T` as a type argument. There is no contract.

```ts
// lib/api.ts:3-17
export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}
```

Same shapes are redeclared with drift:

- `interface Job` appears in `routes/+page.svelte:11`, `routes/jobs/[id]/+page.svelte:22`, `lib/components/CVReviewPanel.svelte:15`, `lib/components/JobCard.svelte:9` (nested) — `location` is `string` in 2 places and `string?` in 2 others; `apply_url` is required in 2, optional in 2.
- `interface QueueMatch` is in 3 files with the same drift.
- `interface DiffEntry` and `DiffResponse` in 2 files.

**Migration path:**
```bash
npm install -D openapi-typescript openapi-fetch
npx openapi-typescript http://localhost:8000/openapi.json -o src/lib/api/schema.ts
```
Then replace `apiFetch<T>` with `openapi-fetch`'s typed client (`client.GET("/api/queue", {...})` knows the response shape from the schema). Estimated impact: deletes ~10 duplicated interfaces, hits all 38 call-sites.

### FE-03 — Error handling story is `e: any` everywhere (**High**)

18 `catch (e: any)` / `catch (err: any)` sites. They all do:
```ts
} catch (e: any) {
  error = e.message ?? 'Failed';
}
```

`routes/+page.svelte:118-122` shows the consequence:
```ts
} catch (e: any) {
  // 409 = already running — keep refreshing state
  if (e.message?.includes('409')) return;
  error = e.message ?? 'Refresh failed';
}
```
Status code is being parsed out of a string template. If the backend changes the error format, this silently breaks.

**Fix:**
```ts
export class ApiError extends Error {
  constructor(public status: number, public body: string, public code?: string) {
    super(`API ${status}: ${body}`);
  }
}
```
Then `catch (e) { if (e instanceof ApiError && e.status === 409) return; }`. Pair with a `toastStore` (`writable<Toast[]>`) so error banner UI isn't copy-pasted across every route.

### FE-04 — `settings/+page.svelte` god-component (**High**)

1,115 lines. ~20 `$state` declarations. 7 loaders + 7 savers + the entire markup for 6 tabs in one file. Diagram of state ownership:

- Profile tab — 8 state vars
- Search tab — 11 state vars (keywords, excludedKeywords, locations, excludedCompanies — each a `Input` string + chip array pair)
- Sites tab — 3 state vars + `Record` toggle map
- Credentials tab — 4 state vars including `credFormMap`, `credSavingMap`, `sessionClearingMap`
- Sources tab — `sources` + `sourcesLoading`
- System tab — `setupStatus` + `systemLoading`

The "Add chip" helper is generalized through 4 callback functions (`addChip(input, chips, setInput, setChips)`) — a sign that a `<ChipInput>` component is begging to exist.

**Decomposition:**
```
lib/components/settings/
  ProfileTab.svelte
  SearchTab.svelte
  SitesTab.svelte
  CredentialsTab.svelte
  SourcesTab.svelte
  SystemTab.svelte
  ChipInput.svelte         <- the reusable primitive
  ToggleSwitch.svelte      <- there are 3 hand-rolled inline switches
```
Routing tab via `?tab=profile` query param so URL-shareability survives.

### FE-05 — Svelte 4/5 hybrid event handling (**High**)

Svelte 5 prefers callback props (`onsubmit`, `onclose`). Old `createEventDispatcher` pattern still works but requires `on:event` consumption — which is **deprecated** in Svelte 5 and breaks the rune-style ergonomics.

Status today:
- 6 components use callback props (`CVReviewPanel`, `LoginRequiredModal`, `EasterEggToast`, etc.) ✓
- 3 components still use `createEventDispatcher`:
  - `KanbanBoard.svelte:40` — `dispatch<{update, addEvent}>`, consumed at `routes/tracker/+page.svelte:145-146` as `on:update / on:addEvent`
  - `JobCard.svelte:30` — `dispatch<{skip, apply}>` (but `JobCard.svelte` is itself dead, FE-10)
  - `SetupWizard.svelte:16` — `dispatch<{close, complete}>`, consumed at `routes/analytics/+page.svelte:112-113` as `on:close / on:complete`

**Fix:** convert all three to:
```ts
let { onupdate, onaddEvent } = $props<{...}>();
// instead of dispatch('update', detail) →  onupdate(detail)
```

### FE-06 — `messages` store is `any[]`

```ts
// lib/stores/websocket.ts:7-8
const _messages = writable<any[]>([]);
export const messages: Readable<any[]> = { subscribe: _messages.subscribe };
```

Every consumer (`BatchPipelineTracker.svelte:25-26`, `StatusBar.svelte:8-10`, `routes/+page.svelte:56-74`) then does ad-hoc type-narrowing on a string field with no help from the compiler. Fix: type as `WSMessage[]` from `ws_models.py` (see FE-01).

### FE-07/08 — WebSocket reconnect & history

`lib/stores/websocket.ts:88-94`:
```ts
function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectWs(); }, 3000);
}
```
Three-second fixed timer, no exponential backoff, no max attempts, no jitter. If the backend is down for 5 minutes, every JobPilot tab is hammering it every 3s. Add backoff.

`_messages.update(msgs => [...msgs.slice(-199), data])` (line 63) — keeps last 200 messages in memory and broadcasts the entire array on every push. Every subscriber re-runs. Replace history with a `lastStatusMessage` derived store; only `routes/+page.svelte` actually walks the history (and only the *latest* item).

### FE-12 — CV upload is fake (**High**)

`routes/cv/+page.svelte:62-72`:
```ts
async function handleFileUpload(file: File) {
  if (!file.name.endsWith('.tex')) { error = 'Please upload a .tex file.'; return; }
  uploading = true;
  try {
    const fileName = file.name;
    // Save profile with a placeholder path (real upload path is server-side)
    await apiFetch('/api/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({ base_cv_path: `uploads/${fileName}` })
    });
    currentCvPath = `uploads/${fileName}`;
    successMsg = `CV template "${fileName}" registered.`;
```

The file bytes are **never sent**. Only a string `uploads/<filename>` is stored. The user thinks they uploaded a CV; they registered a filename. This is a real bug, not just a code-quality issue.

Either:
1. Add a `POST /api/cv/upload` multipart endpoint and use `FormData` (note `apiFetch` would break — see FE-17).
2. Remove the drop-zone entirely if the design intent is "user copies file into the project dir manually."

### FE-13 — Random text re-rolled on every reactive recompute (**Med**)

```ts
// routes/+page.svelte:160
const queueEmptyMessage = $derived(matches.length === 0 ? getEmptyState('queue') : '');
```
`getEmptyState()` calls `Math.random()`. Every time `matches` changes (e.g. user toggles a mode), the empty-state copy gets re-randomized. The UX intent is "show one playful message until the user leaves" — needs `$state(getEmptyState('queue'))` once on mount, not `$derived`.

Same issue at `routes/cv/+page.svelte:20`, `tracker/+page.svelte:15`, `CVReviewPanel.svelte:71` (applyQuote).

### FE-14 — Hardcoded zinc colors (**Med**)

7 occurrences across `DiffBlock`, `JobCard`, `CVReviewPanel`. These bypass the design tokens defined in `app.css` (which already have `--muted-foreground`, `--border`, etc.). In light mode (toggleable via `mode-watcher`) `text-zinc-300` is unreadably light.

### FE-15 — `app.css` token system is half HSL, half hex

```css
:root {
  /* lines 7-34 use the HSL-without-the-hsl() pattern (Tailwind/shadcn) */
  --accent: 210 40% 96.1%;            /* line 21 — HSL */
  ...
  --accent: #6366f1;                   /* line 35 — HEX. Overrides the above. */
  --border: #e5e7eb;                   /* line 36 — HEX. Overrides line 15. */
```
The `tailwind.config.js:41-42` says `accent: { DEFAULT: "var(--accent)", ... }` (raw `var(--accent)` — works for hex), but at the same time `border: "var(--border)"` and other tokens expect HSL via `hsl(var(--input) / <alpha-value>)` (line 19). Inconsistent. The `<alpha-value>` mechanism is broken for `accent` and `border` because they're hex.

### FE-29/30 — `$derived(() => ...)` anti-pattern (**Med**)

Svelte 5's `$derived(expr)` takes an expression, not a thunk. `$derived(() => { ... })` returns a *function*, and consumers then call it (`byColumn()['applied']`). This means:
1. The derived value is "stable" (it's a function reference) so Svelte's reactivity doesn't re-fire downstream the way the author probably expects.
2. Every call re-executes the body — defeating memoization.

Sites:
- `lib/components/KanbanBoard.svelte:50-59` (`byColumn`), called 6 times in the template
- `lib/components/JobCard.svelte:45-52` (`salary`), called twice
- `routes/jobs/[id]/+page.svelte:72-79` (`salary`), called twice
- `routes/jobs/[id]/+page.svelte:81-89` (`timeAgo` — but this is a regular `const`, harmless)

Use `$derived.by(() => {...})` (Svelte 5's official "block-form" derived) or just `$derived((() => {...})())`.

### FE-31 — `jobs/[id]` route swallows invalid ids

```ts
// routes/jobs/[id]/+page.svelte:59
const matchId = $derived(parseInt($page.params.id ?? '0'));
```
URL `/jobs/abc` → `NaN`, then `parseInt('abc')` is `NaN`, `apiFetch(/api/queue/NaN)` 404s, user sees inline red banner instead of SvelteKit's 404 page. Use SvelteKit `+page.ts` `load` with `error(404)`.

---

## Recommendations by Theme

### Type safety
- Wire up `openapi-typescript` against FastAPI's `/openapi.json` (FE-02). Same generation step should also output WS message types from `ws_models.py` (FE-01, FE-06).
- Stop using `catch (e: any)`. Introduce `class ApiError` (FE-03).
- Type the outbound WS `send()` (FE-19) and the `messages` store (FE-06).

### Accessibility
- Replace icon-only "active state" indicators with `aria-current` / `role="radio"` (FE-21, FE-24).
- Run an actual a11y audit pass with `@axe-core/playwright` once tests exist (FE-35). The current score is "no obvious violations on a glance" — buttons are real `<button>` (0 `<div onclick>`), labels mostly have `for`, `<img>` has `alt`. But there's no keyboard testing.
- The drop zone (FE-20) is the worst offender.

### Performance
- Lazy-load `lucide-svelte` icons via per-icon imports — currently every page imports 5-10 named icons which is fine since lucide-svelte does ship per-component, but verify in bundle analyzer.
- Split the settings page (FE-04) to enable per-tab code-splitting if it ever gets larger.
- WebSocket history is bounded at 200 but emits the whole array on every push (FE-08).
- Add `vite.config.ts` `server.proxy` for `/api` + `/ws` so dev mode doesn't depend on CORS (FE-33).

### State
- Finish the Svelte 5 migration (FE-05). One pattern: callback props everywhere.
- Move the websocket store from `writable<any>` to a `$state`-rune-based class — would unlock proper TS narrowing on the discriminated union.
- Stop sticking random-text generators inside `$derived` (FE-13).

### UX
- Toast/error-boundary primitive (FE-03) — currently every route copy-pastes a red banner.
- CV upload (FE-12) — either implement or remove.
- Sidebar nav misses an `aria-current` (FE-21) — minor but cheap.
- Reconnect with backoff (FE-07).

### Dev experience
- Add `eslint` + `prettier` + `vitest` (FE-35, FE-36). The `wordDiff.ts` LCS utility is a perfect first test target — pure function, easy to verify.
- Document the SPA-static choice in a `README.md` block (currently only 700 B `README.md`).

---

## Already Good

- **No raw `<div onclick>`** anywhere — all interactive surfaces are real `<button>` / `<a>`. The grep returned 0 hits.
- **Svelte 5 rune adoption is consistent within components** — 147 rune usages, 0 legacy `$:` reactive statements. The hybrid is at the *component-interface* boundary (FE-05), not inside components.
- **Design token system exists** (`app.css` + `tailwind.config.js`) with proper light/dark variants, even if it's internally inconsistent (FE-15). The shadcn-style `hsl(var(--x) / <alpha-value>)` setup is right.
- **`lib/utils/wordDiff.ts`** is a proper LCS implementation, pure, tokenized — easy to test, easy to maintain (68 lines).
- **`BatchPipelineTracker.svelte`** is one of the strongest components — typed `PipelineStep`, well-modeled state machine (`waiting/active/done/error`), tasteful visuals.
- **Error page** (`routes/+error.svelte`) exists and uses `$page.status` correctly.
- **WebSocket store has a `(re)connect callbacks` mechanism** (`onWsConnect`) used by `routes/+page.svelte:197` to re-sync batch status after a reconnect — this is exactly the right pattern; it just needs better types around it.
- **Forms use semantic HTML** (`<form onsubmit={...}>`, native `<input type="email"/url/number">`, `<button type="submit">`) — not roll-your-own. Good for autofill + a11y.
- **Optimistic updates** are properly implemented in `routes/tracker/+page.svelte:33-53` (mutate UI, then revert on failure).
- **Skeleton-loader pattern is consistent** across routes (`.animate-pulse` placeholders match the eventual content shape).
- **`tsconfig.json` has `strict: true`** — the `any` issues (FE-02, FE-03, FE-06) are author choice, not config.
- **Path alias `$lib`** is used consistently; no `../../../lib/...` imports.
- **`adapter-static` + SPA fallback** is the right deployment choice for a desktop-class internal tool, even if it forecloses SSR.
