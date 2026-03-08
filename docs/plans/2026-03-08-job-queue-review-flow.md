# Job Queue Review Flow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the per-card Apply/Skip queue with a two-phase flow: (1) assign Auto/Manual/Skip per card, then (2) review a joint Job + CV-diff panel per card before applications run.

**Architecture:** Pure frontend redesign — no new backend endpoints or DB columns needed. The queue API already returns nested job objects; the diff endpoint (`GET /api/documents/{match_id}/diff`) already exists; the apply endpoint (`POST /api/applications/{match_id}/apply`) already exists. The queue page gains a "selection mode" and a new `CVReviewPanel` component handles the joint review step.

**Tech Stack:** Svelte 5 runes (`$state`, `$derived`, `$effect`), TypeScript, Tailwind CSS, existing `apiFetch` helper, lucide-svelte icons.

---

## Context & Key Facts

- **Diff data shape** (from `GET /api/documents/{match_id}/diff`):
  ```json
  {
    "match_id": 3,
    "diff": [
      { "section": "Profile", "original_text": "...", "edited_text": "...", "change_description": "..." }
    ],
    "generated_at": "2026-03-08T..."
  }
  ```
- **Apply endpoint**: `POST /api/applications/{match_id}/apply` with body `{ "method": "auto" | "manual" | "assisted" }`.
- **Queue API** currently filters `batch_date == today` — Task 1 removes this so older batches remain visible.
- **`+page.svelte`** is at `frontend/src/routes/+page.svelte`. The `JobCard` component is at `frontend/src/lib/components/JobCard.svelte` — it will be mostly replaced by the new inline card design in Task 2.
- **APScheduler auto-start already disabled** in `backend/main.py` — no backend scheduling work needed.
- **"Morning Queue" label** lives only in `frontend/src/routes/+page.svelte` line 147.

---

## Task 1: Remove date filter from queue API

The queue currently only shows matches from `batch_date == today`. If the scan ran yesterday, the user sees nothing. Fix: show all `status == 'new'` matches regardless of date, ordered by most recent batch first.

**Files:**
- Modify: `backend/api/queue.py`

**Step 1: Open the file and find the filter**

The query is around line 50–57. It has:
```python
.where(JobMatch.status == "new")
.where(JobMatch.batch_date == target_date)
.order_by(JobMatch.score.desc())
```

**Step 2: Remove the `batch_date` filter and update ordering**

Replace the stmt block with:
```python
stmt = (
    select(JobMatch, Job)
    .join(Job, Job.id == JobMatch.job_id)
    .where(JobMatch.status == "new")
    .order_by(JobMatch.batch_date.desc(), JobMatch.score.desc())
)
```

Also remove the `target_date` variable and the `batch_date` query param from the function signature:
```python
@router.get("", response_model=QueueOut)
async def get_queue(db: DBSession):
    """Return all pending matches (status='new'), newest batch first."""
```

**Step 3: Verify manually**

```bash
cd /home/mouad/Web-automation
python -c "
import asyncio
from backend.database import AsyncSessionLocal
from backend.api.queue import get_queue

async def test():
    async with AsyncSessionLocal() as db:
        result = await get_queue(db)
        print('matches:', result.total)
asyncio.run(test())
"
```

**Step 4: Commit**

```bash
git add backend/api/queue.py
git commit -m "fix(queue): show all pending matches regardless of date"
```

---

## Task 2: Redesign queue page — selection mode

Replace the current "Apply / Skip per card" UI with a compact card list where each card has **Auto / Manual / Skip** mode selectors. Add a sticky **"Review & Apply (N)"** button. Rename title to "Job Queue".

**Files:**
- Modify: `frontend/src/routes/+page.svelte`

**Step 1: Replace the `<script>` section**

The new script manages:
- `modes`: a `Map<number, 'auto'|'manual'|'skip'>` — per-match mode assignment
- `phase`: `'select' | 'review' | 'running'` — drives which UI is shown
- `selectedMatches`: derived list of non-skipped matches for the review phase

Replace the entire `<script lang="ts">` block with:

```typescript
<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch } from '$lib/api';
  import { messages } from '$lib/stores/websocket';
  import { RefreshCw, AlertCircle, Zap, MousePointer, X } from 'lucide-svelte';
  import CVReviewPanel from '$lib/components/CVReviewPanel.svelte';

  interface Job {
    id: number; title: string; company: string; location: string;
    salary_min?: number; salary_max?: number;
    url: string; apply_url: string; apply_method: string; posted_at?: string;
  }
  interface QueueMatch {
    id: number; job_id: number; score: number;
    status: string; batch_date: string; matched_at: string; job: Job;
  }

  type ApplyMode = 'auto' | 'manual' | 'skip';
  type Phase = 'select' | 'review' | 'running';

  let matches = $state<QueueMatch[]>([]);
  let modes = $state<Map<number, ApplyMode>>(new Map());
  let phase = $state<Phase>('select');
  let loading = $state(true);
  let error = $state('');
  let refreshing = $state(false);
  let refreshTimeout: ReturnType<typeof setTimeout> | null = null;

  // Infer default mode from job.apply_method
  function defaultMode(job: Job): ApplyMode {
    return job.apply_method === 'easy_apply' || job.apply_method === 'auto' ? 'auto' : 'manual';
  }

  $effect(() => {
    const lastMsg = $messages[$messages.length - 1];
    if (!lastMsg) return;
    if (lastMsg.type === 'status' && lastMsg.progress >= 1.0) {
      refreshing = false;
      if (refreshTimeout) { clearTimeout(refreshTimeout); refreshTimeout = null; }
      loadQueue();
    }
  });

  async function loadQueue() {
    try {
      const data = await apiFetch<{ matches: QueueMatch[]; total: number }>('/api/queue');
      matches = data.matches ?? [];
      // Set default modes for new matches
      const m = new Map<number, ApplyMode>(modes);
      for (const match of matches) {
        if (!m.has(match.id)) m.set(match.id, defaultMode(match.job));
      }
      modes = m;
    } catch (e: any) {
      error = e.message ?? 'Failed to load queue';
    } finally {
      loading = false;
    }
  }

  async function refreshQueue() {
    refreshing = true;
    if (refreshTimeout) clearTimeout(refreshTimeout);
    refreshTimeout = setTimeout(() => { refreshing = false; }, 5 * 60 * 1000);
    try {
      await apiFetch('/api/queue/refresh', { method: 'POST' });
    } catch (e: any) {
      error = e.message ?? 'Refresh failed';
      refreshing = false;
      if (refreshTimeout) { clearTimeout(refreshTimeout); refreshTimeout = null; }
    }
  }

  function setMode(matchId: number, mode: ApplyMode) {
    const m = new Map(modes);
    m.set(matchId, mode);
    modes = m;
  }

  const activeMatches = $derived(
    matches.filter(m => modes.get(m.id) !== 'skip')
  );

  function proceedToReview() {
    phase = 'review';
  }

  function backToSelect() {
    phase = 'select';
  }

  function onRunComplete() {
    phase = 'select';
    loadQueue();
  }

  onMount(loadQueue);
</script>
```

**Step 2: Replace the HTML template**

Replace the entire template section (everything after `</script>`) with:

```svelte
<!-- Header -->
<div class="flex items-center justify-between mb-5">
  <div>
    <h1 class="text-xl font-semibold tracking-tight">Job Queue</h1>
    <p class="text-xs text-muted-foreground mt-0.5">
      {matches.length} pending · Scan to discover new opportunities
    </p>
  </div>
  <button
    onclick={refreshQueue}
    disabled={refreshing}
    class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
  >
    <RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
    {refreshing ? 'Scanning…' : 'Scan for Jobs'}
  </button>
</div>

{#if error}
  <div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
    <AlertCircle size={13} />{error}
    <button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
  </div>
{/if}

{#if phase === 'review'}
  <!-- CV Review Panel takes over -->
  <CVReviewPanel
    matches={activeMatches}
    {modes}
    onback={backToSelect}
    oncomplete={onRunComplete}
  />

{:else if loading}
  <div class="space-y-2">
    {#each Array(3) as _}
      <div class="border border-border rounded-lg p-4 animate-pulse h-16 bg-muted/30"></div>
    {/each}
  </div>

{:else if matches.length === 0}
  <div class="flex flex-col items-center justify-center py-20 gap-3 text-center">
    <div class="text-4xl">📭</div>
    <p class="text-muted-foreground text-sm font-medium">No pending jobs.</p>
    <p class="text-muted-foreground text-xs">Click "Scan for Jobs" to search for new opportunities.</p>
    <button
      onclick={refreshQueue}
      disabled={refreshing}
      class="mt-2 flex items-center gap-2 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
    >
      <RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
      Scan for Jobs
    </button>
  </div>

{:else}
  <!-- Proceed button -->
  {#if activeMatches.length > 0}
    <div class="flex justify-end mb-3">
      <button
        onclick={proceedToReview}
        class="flex items-center gap-2 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors font-medium"
      >
        Review & Apply ({activeMatches.length}) →
      </button>
    </div>
  {/if}

  <!-- Card list -->
  <div class="space-y-2 max-w-3xl">
    {#each matches as match (match.id)}
      {@const mode = modes.get(match.id) ?? 'manual'}
      <div class="border border-border rounded-lg px-4 py-3 bg-card flex items-center gap-4 {mode === 'skip' ? 'opacity-40' : ''}">
        <!-- Score -->
        <span class="flex-shrink-0 w-10 text-center text-sm font-bold {match.score >= 80 ? 'text-green-400' : match.score >= 60 ? 'text-yellow-400' : 'text-red-400'}">
          {Math.round(match.score)}%
        </span>

        <!-- Info -->
        <div class="flex-1 min-w-0">
          <p class="text-sm font-medium truncate">{match.job.title}</p>
          <p class="text-xs text-muted-foreground truncate">{match.job.company}{match.job.location ? ` · ${match.job.location}` : ''}</p>
        </div>

        <!-- Mode selector -->
        <div class="flex gap-1 flex-shrink-0">
          {#each ([['auto', 'Auto', Zap], ['manual', 'Manual', MousePointer], ['skip', 'Skip', X]] as const)}
            {@const [m, label, Icon] = each}
            <button
              onclick={() => setMode(match.id, m)}
              class="flex items-center gap-1 text-xs px-2 py-1 rounded border transition-colors {mode === m ? (m === 'skip' ? 'border-red-500/50 bg-red-500/10 text-red-400' : 'border-primary/50 bg-primary/10 text-primary') : 'border-border text-muted-foreground hover:border-border/80 hover:text-foreground'}"
            >
              <Icon size={11} />{label}
            </button>
          {/each}
        </div>
      </div>
    {/each}
  </div>
{/if}
```

**Step 3: Build check**

```bash
cd /home/mouad/Web-automation/frontend
npm run check 2>&1 | tail -20
```

Expected: type errors only around `CVReviewPanel` (not created yet — fine).

**Step 4: Commit**

```bash
git add frontend/src/routes/+page.svelte
git commit -m "feat(queue): selection mode with Auto/Manual/Skip per card"
```

---

## Task 3: Build CVReviewPanel component

A full-width panel that steps through each selected match showing job details (left) and CV diff highlights (right). The user approves/rejects/skips per card, then runs all approved applications.

**Files:**
- Create: `frontend/src/lib/components/CVReviewPanel.svelte`

**Step 1: Create the component**

```svelte
<script lang="ts">
  import { apiFetch } from '$lib/api';
  import { ArrowLeft, ArrowRight, Zap, MousePointer, CheckCircle2, SkipForward, ExternalLink } from 'lucide-svelte';

  interface Job {
    id: number; title: string; company: string; location: string;
    url: string; apply_url: string; apply_method: string;
    salary_min?: number; salary_max?: number;
  }
  interface QueueMatch {
    id: number; job_id: number; score: number;
    status: string; batch_date: string; matched_at: string; job: Job;
  }
  interface DiffEntry {
    section: string;
    original_text: string;
    edited_text: string;
    change_description: string;
  }

  type ApplyMode = 'auto' | 'manual' | 'skip';

  let {
    matches,
    modes,
    onback,
    oncomplete,
  }: {
    matches: QueueMatch[];
    modes: Map<number, ApplyMode>;
    onback: () => void;
    oncomplete: () => void;
  } = $props();

  let cursor = $state(0);  // index into matches[]
  let diffs = $state<Map<number, DiffEntry[]>>(new Map());
  let diffLoading = $state(false);
  let error = $state('');
  // decisions: 'approved' | 'base_cv' | 'skip'
  let decisions = $state<Map<number, 'approved' | 'base_cv' | 'skip'>>(new Map());
  let running = $state(false);
  let runResults = $state<{ matchId: number; ok: boolean; msg: string }[]>([]);
  let phase = $state<'review' | 'confirm' | 'done'>('review');

  const current = $derived(matches[cursor]);

  async function loadDiff(matchId: number) {
    if (diffs.has(matchId)) return;
    diffLoading = true;
    try {
      const data = await apiFetch<{ diff: DiffEntry[] }>(`/api/documents/${matchId}/diff`);
      const d = new Map(diffs);
      d.set(matchId, data.diff ?? []);
      diffs = d;
    } catch {
      const d = new Map(diffs);
      d.set(matchId, []);
      diffs = d;
    } finally {
      diffLoading = false;
    }
  }

  $effect(() => {
    if (current) loadDiff(current.id);
  });

  function decide(decision: 'approved' | 'base_cv' | 'skip') {
    const d = new Map(decisions);
    d.set(current.id, decision);
    decisions = d;
    if (cursor < matches.length - 1) {
      cursor++;
    } else {
      phase = 'confirm';
    }
  }

  function goBack() { if (cursor > 0) cursor--; }

  const approvedMatches = $derived(
    matches.filter(m => {
      const dec = decisions.get(m.id);
      return dec === 'approved' || dec === 'base_cv';
    })
  );

  async function runApplications() {
    running = true;
    phase = 'done';
    const results: { matchId: number; ok: boolean; msg: string }[] = [];

    for (const match of approvedMatches) {
      const dec = decisions.get(match.id)!;
      const method = dec === 'base_cv' ? modes.get(match.id) ?? 'manual' : modes.get(match.id) ?? 'manual';
      try {
        await apiFetch(`/api/applications/${match.id}/apply`, {
          method: 'POST',
          body: JSON.stringify({ method })
        });
        results.push({ matchId: match.id, ok: true, msg: `${match.job.title} — queued (${method})` });
      } catch (e: any) {
        results.push({ matchId: match.id, ok: false, msg: e.message ?? 'Failed' });
      }
    }

    runResults = results;
    running = false;
  }
</script>

{#if phase === 'review'}
  <!-- Header nav -->
  <div class="flex items-center gap-3 mb-4">
    <button onclick={onback} class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
      <ArrowLeft size={13} /> Back
    </button>
    <span class="text-xs text-muted-foreground">
      Reviewing {cursor + 1} of {matches.length}
    </span>
    <div class="flex gap-1 ml-2">
      {#each matches as m, i}
        {@const dec = decisions.get(m.id)}
        <div class="w-2 h-2 rounded-full {i === cursor ? 'bg-primary' : dec === 'approved' ? 'bg-green-500' : dec === 'base_cv' ? 'bg-yellow-500' : dec === 'skip' ? 'bg-muted' : 'bg-border'}"></div>
      {/each}
    </div>
  </div>

  <!-- Split pane -->
  <div class="grid grid-cols-2 gap-4 h-[calc(100vh-200px)] min-h-[500px]">

    <!-- LEFT: Job details -->
    <div class="border border-border rounded-lg p-4 overflow-y-auto flex flex-col gap-3">
      <div class="flex items-start justify-between gap-2">
        <div>
          <p class="font-semibold text-sm leading-tight">{current.job.title}</p>
          <p class="text-xs text-muted-foreground mt-0.5">{current.job.company}</p>
          {#if current.job.location}
            <p class="text-xs text-muted-foreground">{current.job.location}</p>
          {/if}
        </div>
        <span class="text-lg font-bold flex-shrink-0 {current.score >= 80 ? 'text-green-400' : current.score >= 60 ? 'text-yellow-400' : 'text-red-400'}">
          {Math.round(current.score)}%
        </span>
      </div>

      <div class="flex items-center gap-2 text-xs text-muted-foreground">
        <span class="px-2 py-0.5 rounded-full border border-border">
          {modes.get(current.id) === 'auto' ? '⚡ Auto apply' : '🖱 Manual apply'}
        </span>
        <a href={current.job.url} target="_blank" class="flex items-center gap-1 hover:text-foreground transition-colors">
          <ExternalLink size={11} /> View job
        </a>
      </div>
    </div>

    <!-- RIGHT: CV diff -->
    <div class="border border-border rounded-lg p-4 overflow-y-auto">
      <p class="text-xs font-medium text-muted-foreground mb-3 uppercase tracking-wide">Proposed CV changes</p>

      {#if diffLoading}
        <div class="space-y-3">
          {#each Array(3) as _}
            <div class="animate-pulse h-20 bg-muted/30 rounded-lg"></div>
          {/each}
        </div>
      {:else}
        {@const currentDiffs = diffs.get(current.id) ?? []}
        {#if currentDiffs.length === 0}
          <div class="flex flex-col items-center justify-center py-12 text-center gap-2">
            <p class="text-sm text-muted-foreground">No changes — base CV will be used as-is.</p>
          </div>
        {:else}
          <div class="space-y-4">
            {#each currentDiffs as entry}
              <div class="rounded-lg border border-border overflow-hidden">
                <div class="px-3 py-1.5 bg-muted/30 border-b border-border">
                  <span class="text-xs font-medium">{entry.section}</span>
                  {#if entry.change_description}
                    <span class="text-xs text-muted-foreground ml-2">— {entry.change_description}</span>
                  {/if}
                </div>
                <div class="p-3 space-y-2">
                  <!-- Original (struck through) -->
                  <p class="text-xs text-muted-foreground line-through leading-relaxed">{entry.original_text}</p>
                  <!-- Replacement (highlighted) -->
                  <p class="text-xs leading-relaxed bg-green-500/10 text-green-300 px-2 py-1 rounded border border-green-500/20">{entry.edited_text}</p>
                </div>
              </div>
            {/each}
          </div>
        {/if}
      {/if}
    </div>
  </div>

  <!-- Action bar -->
  <div class="flex items-center gap-2 mt-4 pt-4 border-t border-border">
    <button
      onclick={goBack}
      disabled={cursor === 0}
      class="flex items-center gap-1 text-xs px-3 py-2 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-30"
    >
      <ArrowLeft size={12} /> Back
    </button>

    <div class="flex-1"></div>

    <button
      onclick={() => decide('skip')}
      class="flex items-center gap-1.5 text-xs px-3 py-2 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
    >
      <SkipForward size={13} /> Skip job
    </button>
    <button
      onclick={() => decide('base_cv')}
      class="flex items-center gap-1.5 text-xs px-3 py-2 rounded-md border border-border hover:bg-accent transition-colors"
    >
      Use base CV
    </button>
    <button
      onclick={() => decide('approved')}
      class="flex items-center gap-1.5 text-xs px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors font-medium"
    >
      <CheckCircle2 size={13} /> Approve changes →
    </button>
  </div>

{:else if phase === 'confirm'}
  <!-- Summary before running -->
  <div class="max-w-lg mx-auto py-8">
    <h2 class="text-lg font-semibold mb-1">Ready to apply</h2>
    <p class="text-sm text-muted-foreground mb-5">{approvedMatches.length} job{approvedMatches.length !== 1 ? 's' : ''} approved · {matches.length - approvedMatches.length} skipped</p>

    <div class="space-y-2 mb-6">
      {#each approvedMatches as match}
        {@const dec = decisions.get(match.id)}
        <div class="flex items-center gap-3 px-3 py-2 rounded-lg border border-border text-sm">
          <span class="{dec === 'approved' ? 'text-green-400' : 'text-yellow-400'}">
            {dec === 'approved' ? '✓ Tailored CV' : '○ Base CV'}
          </span>
          <span class="flex-1 truncate">{match.job.title} · {match.job.company}</span>
          <span class="text-xs text-muted-foreground flex items-center gap-1">
            {#if modes.get(match.id) === 'auto'}
              <Zap size={10} /> Auto
            {:else}
              <MousePointer size={10} /> Manual
            {/if}
          </span>
        </div>
      {/each}
    </div>

    <div class="flex gap-2">
      <button onclick={() => { cursor = 0; phase = 'review'; }} class="flex-1 text-sm px-4 py-2 rounded-md border border-border hover:bg-accent transition-colors">
        ← Back to review
      </button>
      <button onclick={runApplications} class="flex-1 text-sm px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors font-medium">
        Run {approvedMatches.length} applications →
      </button>
    </div>
  </div>

{:else if phase === 'done'}
  <!-- Results -->
  <div class="max-w-lg mx-auto py-8">
    <h2 class="text-lg font-semibold mb-1">{running ? 'Applying…' : 'Done'}</h2>
    <p class="text-sm text-muted-foreground mb-5">
      {running ? 'Applications are being processed.' : `${runResults.filter(r => r.ok).length} started successfully.`}
    </p>

    <div class="space-y-2 mb-6">
      {#each runResults as r}
        <div class="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border {r.ok ? 'border-green-500/20 bg-green-500/5 text-green-400' : 'border-red-500/20 bg-red-500/5 text-red-400'}">
          {r.ok ? '✓' : '✕'} {r.msg}
        </div>
      {/each}
    </div>

    {#if !running}
      <button onclick={oncomplete} class="w-full text-sm px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
        Back to queue
      </button>
    {/if}
  </div>
{/if}
```

**Step 2: Run type check**

```bash
cd /home/mouad/Web-automation/frontend
npm run check 2>&1 | grep -E "Error|error" | head -20
```

Fix any type errors before committing.

**Step 3: Commit**

```bash
git add frontend/src/lib/components/CVReviewPanel.svelte
git commit -m "feat(queue): CV review panel with job+diff split view"
```

---

## Task 4: Wire the auto-apply confirm modal back in

The existing auto-apply confirmation modal (filled fields + screenshot) must still work after switching to the new flow. The `+page.svelte` previously handled `apply_review` WS messages. Re-add that handler in the new script.

**Files:**
- Modify: `frontend/src/routes/+page.svelte`

**Step 1: Re-add confirmModal state and WS handler**

In the `<script>` block, add after `let runResults`:

```typescript
let confirmModal = $state<{ jobId: number; method: string; fields?: Record<string, string>; screenshot?: string } | null>(null);
```

In the `$effect` watching `$messages`, add before the status check:

```typescript
if (lastMsg.type === 'apply_review') {
  confirmModal = {
    jobId: lastMsg.job_id,
    method: 'auto',
    fields: lastMsg.filled_fields,
    screenshot: lastMsg.screenshot_base64
  };
}
```

Add the confirm/cancel functions (same as before):

```typescript
import { send } from '$lib/stores/websocket';

function confirmApply() {
  if (confirmModal) {
    send({ type: 'confirm_submit', job_id: confirmModal.jobId });
    confirmModal = null;
  }
}
function cancelApply() {
  if (confirmModal) {
    send({ type: 'cancel_apply', job_id: confirmModal.jobId });
    confirmModal = null;
  }
}
```

**Step 2: Add the confirm modal markup**

Add before the closing tag of the template (same modal HTML as the original):

```svelte
{#if confirmModal}
  <div class="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
    <div class="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
      <div class="p-5 border-b border-border">
        <h2 class="font-semibold">Confirm Auto Apply</h2>
        <p class="text-xs text-muted-foreground mt-1">Review the filled fields before submitting.</p>
      </div>
      {#if confirmModal.screenshot}
        <div class="px-5 pt-4">
          <img src="data:image/png;base64,{confirmModal.screenshot}" alt="Form preview"
            class="rounded border border-border w-full max-h-48 object-cover" />
        </div>
      {/if}
      {#if confirmModal.fields && Object.keys(confirmModal.fields).length > 0}
        <div class="px-5 pt-4 pb-2">
          <dl class="space-y-1">
            {#each Object.entries(confirmModal.fields) as [k, v]}
              <div class="flex gap-2 text-xs">
                <dt class="text-muted-foreground w-28 flex-shrink-0">{k}</dt>
                <dd class="truncate">{v}</dd>
              </div>
            {/each}
          </dl>
        </div>
      {/if}
      <div class="flex justify-end gap-2 p-4">
        <button onclick={cancelApply}
          class="text-xs px-4 py-2 rounded-md border border-border hover:bg-accent transition-colors">Cancel</button>
        <button onclick={confirmApply}
          class="text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">Confirm Submit</button>
      </div>
    </div>
  </div>
{/if}
```

**Step 3: Full build check**

```bash
cd /home/mouad/Web-automation/frontend
npm run check 2>&1 | tail -20
```

**Step 4: Commit**

```bash
git add frontend/src/routes/+page.svelte
git commit -m "feat(queue): re-add auto-apply confirm modal in new flow"
```

---

## Task 5: Update layout nav label

The sidebar navigation likely says "Morning Queue". Rename it.

**Files:**
- Modify: `frontend/src/routes/+layout.svelte`

**Step 1: Find and replace "Morning Queue"**

```bash
grep -n "Morning\|morning" /home/mouad/Web-automation/frontend/src/routes/+layout.svelte
grep -rn "Morning\|morning" /home/mouad/Web-automation/frontend/src --include="*.svelte" --include="*.ts"
```

**Step 2: Replace every occurrence of "Morning Queue" with "Job Queue"**

Also replace "Morning Batch" → "Job Scan" and any subtitle mentioning "morning" or time of day.

**Step 3: Commit**

```bash
git add frontend/src/routes/+layout.svelte
git commit -m "chore(ui): rename Morning Queue → Job Queue throughout"
```

---

## Final verification

```bash
# Backend: queue returns correct nested shape
cd /home/mouad/Web-automation
python -m pytest tests/ -q 2>&1 | tail -10

# Frontend: no type errors
cd frontend
npm run check 2>&1 | grep -c "Error"   # should be 0
```

Run the dev server and manually verify:
1. Queue page shows cards with Auto/Manual/Skip buttons — no Apply button
2. "Scan for Jobs" triggers the batch
3. "Review & Apply (N)" opens the split-pane panel
4. Left side shows job info; right side shows CV diffs with green/strikethrough formatting
5. Approve → moves to next card; "Run applications" fires the apply endpoint
6. Auto-apply confirm modal still appears when browser fills a form
