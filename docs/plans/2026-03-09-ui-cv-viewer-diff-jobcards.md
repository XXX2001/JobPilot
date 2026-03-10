# UI Enhancement Plan: CV Viewer, Diff Logic & Job Cards

**Date:** 2026-03-09
**Scope:** PDF viewer in review panel, word-level diff, enriched job cards

---

## Summary

Three streams of UI improvements:
1. **PDF Viewer** — Diff/PDF tab toggle in CVReviewPanel using native `<iframe>` embed
2. **Word-Level Diff** — Client-side word diff algorithm replacing the current section-level strikethrough display
3. **Job Card Enrichment** — Full job description (expandable) and apply URL on cards

No new dependencies. One backend change (add `description` to queue API response).

---

## Stream 1: PDF Viewer in CVReviewPanel

### Task 1.1 — Add Diff/PDF tab toggle to CVReviewPanel

**File:** `frontend/src/lib/components/CVReviewPanel.svelte`

Currently the right pane shows text diffs only. Add a tab bar at the top of the right pane:

```
[ Diff View ] [ PDF Preview ]
```

- **Diff View** (default): Shows the existing word-level diff (see Stream 2)
- **PDF Preview**: Renders an `<iframe>` pointing to the existing backend endpoint

**Implementation:**

```svelte
<script>
  let activeTab = $state<'diff' | 'pdf'>('diff');
</script>

<!-- Tab bar -->
<div class="flex border-b border-zinc-700 mb-3">
  <button
    class="px-4 py-2 text-sm font-medium {activeTab === 'diff' ? 'border-b-2 border-blue-500 text-blue-400' : 'text-zinc-400'}"
    onclick={() => activeTab = 'diff'}>
    Diff View
  </button>
  <button
    class="px-4 py-2 text-sm font-medium {activeTab === 'pdf' ? 'border-b-2 border-blue-500 text-blue-400' : 'text-zinc-400'}"
    onclick={() => activeTab = 'pdf'}>
    PDF Preview
  </button>
</div>

<!-- Content -->
{#if activeTab === 'diff'}
  <!-- existing diff rendering (enhanced with word-level diff from Stream 2) -->
{:else}
  <iframe
    src="/api/documents/{currentMatch.id}/cv/pdf"
    class="w-full h-full rounded border border-zinc-700"
    title="Tailored CV PDF"
  />
{/if}
```

**Edge cases:**
- If no tailored CV exists yet (no diffs), show a message: "No tailored CV generated for this job"
- If PDF endpoint returns 404, show fallback message instead of broken iframe
- The iframe should fill the available height: `style="height: calc(100% - 48px)"`

**No backend changes needed** — the `/api/documents/{matchId}/cv/pdf` endpoint already exists.

---

### Task 1.2 — Add PDF tab for motivation letter

Same pattern as 1.1 but for the letter tab (if it exists in the review flow).
Check if there's a letter PDF endpoint: `/api/documents/{matchId}/letter/pdf`.
If so, add the same Diff/PDF toggle. If not, skip this task.

---

## Stream 2: Word-Level Diff

### Task 2.1 — Create `wordDiff` utility function

**New file:** `frontend/src/lib/utils/wordDiff.ts`

Implement a lightweight word-level diff using longest common subsequence (LCS):

```typescript
export interface DiffSpan {
  type: 'same' | 'added' | 'removed';
  text: string;
}

export function wordDiff(original: string, edited: string): DiffSpan[] {
  // 1. Tokenize both strings into words (split on whitespace, preserve whitespace in output)
  // 2. Run LCS on the word arrays
  // 3. Walk the LCS result to produce DiffSpan[] with:
  //    - 'same' for words present in both
  //    - 'removed' for words only in original
  //    - 'added' for words only in edited
  // 4. Merge consecutive spans of the same type for cleaner output
}
```

**Algorithm:** Standard O(n*m) LCS with backtracking. For CV replacement strings
(typically <100 words), this is instant.

**Tests:** Create `frontend/src/lib/utils/wordDiff.test.ts` (if test runner is configured):
- Identical strings → all 'same' spans
- Single word change → 'same' + 'removed' + 'added' + 'same'
- Complete rewrite → all 'removed' then all 'added'
- Empty strings → handle gracefully

---

### Task 2.2 — Create `DiffBlock` display component

**New file:** `frontend/src/lib/components/DiffBlock.svelte`

Renders a single CV replacement as an inline word-level diff:

```svelte
<script lang="ts">
  import { wordDiff, type DiffSpan } from '$lib/utils/wordDiff';

  interface Props {
    section: string;
    originalText: string;
    editedText: string;
    reason: string;
  }

  let { section, originalText, editedText, reason }: Props = $props();

  let spans = $derived(wordDiff(originalText, editedText));
</script>

<!-- Section header -->
<div class="flex items-center gap-2 mb-2">
  <span class="text-xs font-semibold uppercase tracking-wide text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded">
    {section}
  </span>
  <span class="text-xs text-zinc-500 italic">{reason}</span>
</div>

<!-- Inline diff -->
<div class="font-mono text-sm leading-relaxed bg-zinc-900/50 rounded p-3 border border-zinc-800">
  {#each spans as span}
    {#if span.type === 'removed'}
      <span class="bg-red-500/20 text-red-400 line-through">{span.text}</span>
    {:else if span.type === 'added'}
      <span class="bg-green-500/20 text-green-300">{span.text}</span>
    {:else}
      <span class="text-zinc-300">{span.text}</span>
    {/if}
  {/each}
</div>
```

---

### Task 2.3 — Replace current diff rendering in CVReviewPanel

**File:** `frontend/src/lib/components/CVReviewPanel.svelte`

Replace the existing strikethrough/green block rendering with the new `DiffBlock` component:

**Current** (approximate):
```svelte
{#each diffs as d}
  <div class="bg-zinc-800/50 rounded p-2 mb-1 text-xs">
    <span class="text-zinc-500">{d.section}</span>
  </div>
  <div class="line-through text-red-400/70">{d.original_text}</div>
  <div class="bg-green-500/10 text-green-300 rounded p-2">{d.edited_text}</div>
{/each}
```

**New:**
```svelte
{#each diffs as d}
  <DiffBlock
    section={d.section}
    originalText={d.original_text}
    editedText={d.edited_text}
    reason={d.change_description}
  />
{/each}
```

---

## Stream 3: Job Card Enrichment

### Task 3.1 — Add `description` field to queue API response

**File:** `backend/api/queue.py`

Add `description` to the `JobOut` Pydantic model:

```python
class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location: Optional[str]
    country: Optional[str]
    salary_min: Optional[float]
    salary_max: Optional[float]
    description: Optional[str] = None  # ADD THIS
    url: str
    apply_url: str
    apply_method: str
    posted_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)
```

The `Job` ORM model already has a `description` field in the DB —
`from_attributes=True` will pick it up automatically. No query changes needed.

---

### Task 3.2 — Update frontend `Job` interface

**Files:**
- `frontend/src/routes/+page.svelte` (Job interface definition)
- `frontend/src/lib/components/CVReviewPanel.svelte` (Job interface definition)

Add `description` to both:

```typescript
interface Job {
  id: number;
  title: string;
  company: string;
  location?: string;
  country?: string;
  salary_min?: number;
  salary_max?: number;
  description?: string;   // ADD
  url: string;
  apply_url: string;
  apply_method: string;
  posted_at?: string;
}
```

---

### Task 3.3 — Add expandable description and apply URL to JobCard

**File:** `frontend/src/lib/components/JobCard.svelte`

Add below the existing metadata row:

**Apply URL** — shown as a subtle external link:
```svelte
{#if match.job.apply_url}
  <a
    href={match.job.apply_url}
    target="_blank"
    rel="noopener noreferrer"
    class="text-xs text-blue-400/70 hover:text-blue-400 truncate max-w-[200px] inline-flex items-center gap-1"
  >
    <ExternalLink size={12} />
    {new URL(match.job.apply_url).hostname}
  </a>
{/if}
```

**Full description** — expandable accordion:
```svelte
<script>
  let expanded = $state(false);
</script>

{#if match.job.description}
  <button
    class="text-xs text-zinc-500 hover:text-zinc-300 flex items-center gap-1 mt-1"
    onclick={() => expanded = !expanded}
  >
    {#if expanded}
      <ChevronUp size={14} /> Hide description
    {:else}
      <ChevronDown size={14} /> Show description
    {/if}
  </button>

  {#if expanded}
    <div class="mt-2 text-xs text-zinc-400 leading-relaxed bg-zinc-800/30 rounded p-3 max-h-[300px] overflow-y-auto">
      {match.job.description}
    </div>
  {/if}
{/if}
```

**Layout change:** The description accordion sits below the existing card content,
inside the flex-1 content area. It does NOT expand the card width — only height.

---

### Task 3.4 — Add description to CVReviewPanel left pane (job details)

**File:** `frontend/src/lib/components/CVReviewPanel.svelte`

The left pane shows job details for the current match being reviewed.
Add the full description below the existing job info:

```svelte
{#if currentMatch.job.description}
  <div class="mt-4">
    <h4 class="text-xs font-semibold uppercase text-zinc-500 mb-2">Description</h4>
    <div class="text-sm text-zinc-300 leading-relaxed max-h-[400px] overflow-y-auto">
      {currentMatch.job.description}
    </div>
  </div>
{/if}
```

This gives context while reviewing the CV diff — you can see what the job asks for
right next to the changes the LLM made.

---

## Execution Order

| # | Task | Stream | Dependencies |
|---|------|--------|-------------|
| 1 | 2.1 Create `wordDiff.ts` utility | Diff | None |
| 2 | 2.2 Create `DiffBlock.svelte` component | Diff | 2.1 |
| 3 | 2.3 Replace diff rendering in CVReviewPanel | Diff | 2.2 |
| 4 | 1.1 Add Diff/PDF tab toggle | PDF | 2.3 (so diff tab uses new rendering) |
| 5 | 1.2 Add letter PDF tab (if endpoint exists) | PDF | 1.1 |
| 6 | 3.1 Add `description` to queue API | Cards | None |
| 7 | 3.2 Update frontend Job interface | Cards | 3.1 |
| 8 | 3.3 Add description + URL to JobCard | Cards | 3.2 |
| 9 | 3.4 Add description to CVReviewPanel left pane | Cards | 3.2 |

Tasks 1-5 (diff + PDF) and 6-9 (cards) are independent streams — can be done in parallel.

---

## Files Touched

| File | Tasks | Change Type |
|------|-------|-------------|
| `frontend/src/lib/utils/wordDiff.ts` (NEW) | 2.1 | New utility |
| `frontend/src/lib/components/DiffBlock.svelte` (NEW) | 2.2 | New component |
| `frontend/src/lib/components/CVReviewPanel.svelte` | 1.1, 2.3, 3.4 | Tab toggle + diff + description |
| `frontend/src/lib/components/JobCard.svelte` | 3.3 | Description + URL |
| `frontend/src/routes/+page.svelte` | 3.2 | Job interface update |
| `backend/api/queue.py` | 3.1 | Add description to JobOut |

---

## Verification Checklist

- [ ] Word diff correctly highlights changed words in green, removed in red strikethrough
- [ ] Identical text produces no diff highlighting
- [ ] PDF tab loads CV in iframe; shows fallback on 404
- [ ] Tab state resets when navigating between matches
- [ ] Job card description expands/collapses without layout shift
- [ ] Apply URL shows domain with external link icon, opens in new tab
- [ ] CVReviewPanel left pane shows full job description with scroll
- [ ] Invalid/missing URLs handled gracefully (no crash on `new URL()`)
