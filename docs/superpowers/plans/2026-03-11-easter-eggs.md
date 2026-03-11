# Easter Eggs & Mood Boosters Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add warm/witty easter eggs and playful animations across the UI to boost user morale during job searching.

**Architecture:** Centralized `easterEggs.ts` utility with all messages + helpers, 3 new animation components (`EasterEggToast`, `FloatingEmoji`, `TypewriterText`), custom Tailwind keyframes, and integration into 8 existing pages/components.

**Tech Stack:** Svelte 5 (runes), Tailwind CSS, TypeScript, lucide-svelte icons

---

## Chunk 1: Foundation (Easter Egg Store + Tailwind Animations)

### Task 1: Create Easter Egg Store

**Files:**
- Create: `frontend/src/lib/utils/easterEggs.ts`

- [ ] **Step 1: Create the easter eggs utility file**

```typescript
// frontend/src/lib/utils/easterEggs.ts

// ── Rejection Milestones (warm/motivational) ──────────────────────
const rejectionMilestones = new Map<number, { message: string; emoji: string }>([
	[10, { message: "You're just warming up.", emoji: '🔥' }],
	[25, { message: 'Thomas Edison failed 1,000 times before the lightbulb. You\'re ahead of schedule.', emoji: '💡' }],
	[50, { message: 'Halfway to 100 — and after 100 rejections, the yes hits different.', emoji: '🎯' }],
	[75, { message: "At this point, you're basically rejection-proof. Armor level: mythic.", emoji: '🛡️' }],
	[100, { message: '100 rejections. You are now statistically unstoppable.', emoji: '🚀' }],
	[150, { message: 'Most people quit at 50. You\'re built different.', emoji: '💪' }],
	[200, { message: '200 rejections. At this point, companies are rejecting a legend.', emoji: '👑' }]
]);

// ── Empty States (playful) ────────────────────────────────────────
const emptyStates: Record<string, string[]> = {
	queue: [
		'The right job is out there, probably also refreshing its inbox.',
		'No jobs yet. The market is playing hard to get.',
		"Queue's empty. Time to grab coffee while we hunt."
	],
	applications: [
		'Every expert was once a beginner with an empty applications page.',
		"No applications yet. Your future employer is still writing the job post.",
		"Clean slate. The world is your oyster (that hasn't been applied to yet)."
	],
	cv: [
		'No tailored CVs yet. Your base CV is doing its best.',
		"No CVs here yet. We're warming up the LaTeX compiler.",
		'The CV forge awaits its first commission.'
	]
};

// ── Loading Messages (absurd/playful) ─────────────────────────────
const loadingMessages: string[] = [
	'Scanning the internet for your dream job...',
	'Negotiating with job boards on your behalf...',
	'Teaching robots to read job descriptions...',
	'Convincing LinkedIn you\'re not a bot...',
	'Translating recruiter-speak into English...',
	'Asking the job market to take you seriously...',
	'Bribing the algorithm with good vibes...',
	'Performing dark arts on job listings...',
	'Whispering sweet nothings to APIs...',
	'Pretending to be 47 browser tabs at once...'
];

// ── Batch Completion Messages ─────────────────────────────────────
const batchMessages: Record<string, string[]> = {
	zero_jobs: [
		'The job market took a coffee break. We\'ll try again tomorrow.',
		'Zero new jobs. Even the bots need a day off.',
		'Nothing new today. Mercury might be in retrograde.'
	],
	success: [
		'Fresh opportunities, served hot.',
		"Your future employer doesn't know it yet, but today might be the day.",
		"New jobs locked and loaded. Let's get picky."
	]
};

// ── CV Generation Toasts ──────────────────────────────────────────
const cvToasts: string[] = [
	"CV sharpened. You're now 3% more hireable (scientifically unverified).",
	"New CV forged. It's dangerous to go alone — take this.",
	'CV tailored. Looking sharp. Literally.',
	'Another CV crafted. Your LaTeX compiler sends its regards.'
];

// ── Profile Completion ────────────────────────────────────────────
const profileMessages: Record<string, { message: string; emoji: string }> = {
	complete: { message: 'Profile complete. You look great on paper.', emoji: '✨' },
	empty: { message: 'A blank profile is like showing up to an interview in pajamas — comfortable, but not ideal.', emoji: '👔' },
	partial: { message: "Getting there! A few more fields and you'll be unstoppable.", emoji: '📝' }
};

// ── Apply Confirmation ────────────────────────────────────────────
const applyConfirmation: string[] = [
	'One small click for you, one giant leap for your career.',
	"This application is about to make someone's hiring pipeline very happy.",
	"Ready to make this recruiter's day? Hit the button."
];

// ── Error / 404 Messages ──────────────────────────────────────────
const errorMessages: string[] = [
	"This page is like that perfect job listing — it doesn't exist (yet).",
	'Error 404: Job satisfaction not found. Keep looking.',
	"You've wandered off the career path. Let's get you back.",
	'This page took a personal day. Try another route.'
];

// ── Helpers ───────────────────────────────────────────────────────

let lastLoadingIndex = -1;

export function getRandomMessage(messages: string[]): string {
	return messages[Math.floor(Math.random() * messages.length)];
}

export function getLoadingMessage(): string {
	let index: number;
	do {
		index = Math.floor(Math.random() * loadingMessages.length);
	} while (index === lastLoadingIndex && loadingMessages.length > 1);
	lastLoadingIndex = index;
	return loadingMessages[index];
}

export function getRejectionMilestone(count: number): { message: string; emoji: string; isSpecial: boolean } | null {
	const milestone = rejectionMilestones.get(count);
	if (!milestone) return null;
	return { ...milestone, isSpecial: count >= 100 };
}

export function getEmptyState(context: string): string {
	const messages = emptyStates[context];
	if (!messages) return '';
	return getRandomMessage(messages);
}

export function getBatchMessage(outcome: 'zero_jobs' | 'success'): string {
	return getRandomMessage(batchMessages[outcome]);
}

export function getCvToast(): string {
	return getRandomMessage(cvToasts);
}

export function getProfileStatus(fields: Record<string, unknown>): { message: string; emoji: string } {
	const values = Object.values(fields);
	const filled = values.filter((v) => v !== null && v !== undefined && v !== '').length;
	const ratio = filled / values.length;
	if (ratio === 0) return profileMessages.empty;
	if (ratio >= 1) return profileMessages.complete;
	return profileMessages.partial;
}

export function getApplyConfirmation(): string {
	return getRandomMessage(applyConfirmation);
}

export function getErrorMessage(): string {
	return getRandomMessage(errorMessages);
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/utils/easterEggs.ts
git commit -m "feat: add centralized easter egg store with all messages and helpers"
```

---

### Task 2: Add Custom Tailwind Keyframes

**Files:**
- Modify: `frontend/tailwind.config.js:72-90`

- [ ] **Step 1: Add new keyframes and animations to tailwind config**

Inside the `keyframes` object (after the `"caret-blink"` entry at line ~84), add:

```javascript
"fade-in-up": {
    "0%": { opacity: "0", transform: "translateY(16px) scale(0.98)" },
    "100%": { opacity: "1", transform: "translateY(0) scale(1)" }
},
"fade-out-down": {
    "0%": { opacity: "1", transform: "translateY(0) scale(1)" },
    "100%": { opacity: "0", transform: "translateY(16px) scale(0.98)" }
},
"float": {
    "0%, 100%": { transform: "translateY(0px)" },
    "50%": { transform: "translateY(-8px)" }
},
"shimmer": {
    "0%": { backgroundPosition: "-200% center" },
    "100%": { backgroundPosition: "200% center" }
},
"confetti-pop": {
    "0%": { transform: "scale(0.8) rotate(-3deg)", opacity: "0" },
    "50%": { transform: "scale(1.05) rotate(1deg)", opacity: "1" },
    "100%": { transform: "scale(1) rotate(0deg)", opacity: "1" }
},
"gentle-bounce": {
    "0%, 100%": { transform: "translateY(0)" },
    "50%": { transform: "translateY(-4px)" }
},
"glow-pulse": {
    "0%, 100%": { boxShadow: "0 0 8px 0 rgba(234, 179, 8, 0)" },
    "50%": { boxShadow: "0 0 20px 4px rgba(234, 179, 8, 0.3)" }
},
"progress-shimmer": {
    "0%": { transform: "translateX(-100%)" },
    "100%": { transform: "translateX(100%)" }
},
```

Inside the `animation` object (after `"caret-blink"` entry at line ~90), add:

```javascript
"fade-in-up": "fade-in-up 0.4s ease-out",
"fade-out-down": "fade-out-down 0.3s ease-in forwards",
"float": "float 3s ease-in-out infinite",
"shimmer": "shimmer 2s linear infinite",
"confetti-pop": "confetti-pop 0.5s ease-out",
"gentle-bounce": "gentle-bounce 2s ease-in-out infinite",
"glow-pulse": "glow-pulse 2s ease-in-out infinite",
"progress-shimmer": "progress-shimmer 1.5s ease-in-out infinite",
```

- [ ] **Step 2: Commit**

```bash
git add frontend/tailwind.config.js
git commit -m "feat: add custom keyframes for easter egg animations"
```

---

### Task 3: Create EasterEggToast Component

**Files:**
- Create: `frontend/src/lib/components/EasterEggToast.svelte`

- [ ] **Step 1: Create the toast component**

```svelte
<script lang="ts">
	import { onMount } from 'svelte';

	let {
		message,
		emoji = '🎉',
		type = 'info',
		duration = 5000,
		onclose
	}: {
		message: string;
		emoji?: string;
		type?: 'milestone' | 'info' | 'celebration';
		duration?: number;
		onclose?: () => void;
	} = $props();

	let visible = $state(true);
	let exiting = $state(false);

	function dismiss() {
		exiting = true;
		setTimeout(() => {
			visible = false;
			onclose?.();
		}, 300);
	}

	onMount(() => {
		const timer = setTimeout(dismiss, duration);
		return () => clearTimeout(timer);
	});
</script>

{#if visible}
	<div
		class="fixed top-4 right-4 z-[100] max-w-sm rounded-lg border px-4 py-3 shadow-lg backdrop-blur-sm
			{exiting ? 'animate-fade-out-down' : 'animate-fade-in-up'}
			{type === 'celebration'
			? 'border-yellow-500/30 bg-yellow-500/10 animate-glow-pulse'
			: type === 'milestone'
				? 'border-amber-500/20 bg-amber-500/5'
				: 'border-border bg-card/95'}"
		role="status"
	>
		<div class="flex items-start gap-3">
			<span
				class="text-xl flex-shrink-0
					{type === 'celebration' ? 'animate-confetti-pop' : ''}"
			>
				{emoji}
			</span>
			<div class="flex-1 min-w-0">
				<p
					class="text-sm font-medium leading-snug
						{type === 'celebration'
						? 'bg-gradient-to-r from-yellow-300 via-amber-200 to-yellow-300 bg-clip-text text-transparent bg-[length:200%_auto] animate-shimmer'
						: type === 'milestone'
							? 'text-amber-200'
							: 'text-foreground'}"
				>
					{message}
				</p>
			</div>
			<button
				onclick={dismiss}
				class="text-muted-foreground hover:text-foreground text-xs flex-shrink-0 mt-0.5 transition-colors"
			>
				✕
			</button>
		</div>
	</div>
{/if}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/components/EasterEggToast.svelte
git commit -m "feat: add EasterEggToast component with milestone/celebration variants"
```

---

### Task 4: Create FloatingEmoji Component

**Files:**
- Create: `frontend/src/lib/components/FloatingEmoji.svelte`

- [ ] **Step 1: Create the floating emoji component**

```svelte
<script lang="ts">
	let {
		emoji,
		size = 'md'
	}: {
		emoji: string;
		size?: 'sm' | 'md' | 'lg';
	} = $props();

	const sizes = { sm: 'text-2xl', md: 'text-4xl', lg: 'text-5xl' };
</script>

<div class="animate-float inline-block {sizes[size]}">
	{emoji}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/components/FloatingEmoji.svelte
git commit -m "feat: add FloatingEmoji component with float animation"
```

---

### Task 5: Create TypewriterText Component

**Files:**
- Create: `frontend/src/lib/components/TypewriterText.svelte`

- [ ] **Step 1: Create the typewriter text component**

```svelte
<script lang="ts">
	import { onMount } from 'svelte';

	let {
		messages,
		typingSpeed = 35,
		pauseDuration = 2500,
		class: className = ''
	}: {
		messages: string[];
		typingSpeed?: number;
		pauseDuration?: number;
		class?: string;
	} = $props();

	let displayed = $state('');
	let msgIndex = $state(0);
	let charIndex = $state(0);
	let phase = $state<'typing' | 'pausing' | 'erasing'>('typing');
	let timer: ReturnType<typeof setTimeout>;

	function tick() {
		const current = messages[msgIndex];

		if (phase === 'typing') {
			if (charIndex < current.length) {
				displayed = current.slice(0, charIndex + 1);
				charIndex++;
				timer = setTimeout(tick, typingSpeed);
			} else {
				phase = 'pausing';
				timer = setTimeout(tick, pauseDuration);
			}
		} else if (phase === 'pausing') {
			phase = 'erasing';
			timer = setTimeout(tick, typingSpeed / 2);
		} else if (phase === 'erasing') {
			if (charIndex > 0) {
				charIndex--;
				displayed = current.slice(0, charIndex);
				timer = setTimeout(tick, typingSpeed / 2);
			} else {
				msgIndex = (msgIndex + 1) % messages.length;
				phase = 'typing';
				timer = setTimeout(tick, typingSpeed * 3);
			}
		}
	}

	onMount(() => {
		tick();
		return () => clearTimeout(timer);
	});
</script>

<span class="inline-flex items-center {className}">
	<span>{displayed}</span>
	<span class="ml-0.5 inline-block w-[2px] h-[1em] bg-current animate-caret-blink"></span>
</span>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/components/TypewriterText.svelte
git commit -m "feat: add TypewriterText component with typing/erasing cycle"
```

---

## Chunk 2: Integration into Existing Pages

### Task 6: Job Queue Empty State + Loading Messages

**Files:**
- Modify: `frontend/src/routes/+page.svelte:198-217`

- [ ] **Step 1: Add imports at top of script block**

Add to imports:
```typescript
import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
import TypewriterText from '$lib/components/TypewriterText.svelte';
import { getEmptyState, getLoadingMessage } from '$lib/utils/easterEggs';
```

Add state variable (with other state declarations):
```typescript
const queueEmptyMessage = $derived(matches.length === 0 ? getEmptyState('queue') : '');
```

Note: We use `$derived` so message stays stable while empty, and re-rolls when matches change.

- [ ] **Step 2: Replace empty state (lines ~204-217)**

Replace:
```svelte
{:else if matches.length === 0}
	<div class="flex flex-col items-center justify-center gap-3 py-20 text-center">
		<div class="text-4xl">📭</div>
		<p class="text-muted-foreground text-sm font-medium">No pending jobs.</p>
		<p class="text-muted-foreground text-xs">Click "Scan for Jobs" to search for new opportunities.</p>
```

With:
```svelte
{:else if matches.length === 0}
	<div class="flex flex-col items-center justify-center gap-3 py-20 text-center">
		<FloatingEmoji emoji="📭" />
		<p class="text-muted-foreground text-sm font-medium">{queueEmptyMessage}</p>
		<p class="text-muted-foreground text-xs">Click "Scan for Jobs" to search for new opportunities.</p>
```

- [ ] **Step 3: Replace loading skeleton (lines ~198-203)**

Replace:
```svelte
{:else if loading}
	<div class="space-y-2">
		{#each Array(3) as _}
			<div class="border-border bg-muted/30 h-16 animate-pulse rounded-lg border p-4"></div>
		{/each}
	</div>
```

With:
```svelte
{:else if loading}
	<div class="flex flex-col items-center justify-center gap-4 py-20">
		<div class="relative">
			<div class="h-10 w-10 rounded-full border-2 border-primary/20 border-t-primary animate-spin"></div>
		</div>
		<TypewriterText
			messages={[
				'Scanning the internet for your dream job...',
				'Negotiating with job boards on your behalf...',
				'Teaching robots to read job descriptions...',
				'Convincing LinkedIn you\'re not a bot...',
				'Translating recruiter-speak into English...',
				'Bribing the algorithm with good vibes...'
			]}
			class="text-muted-foreground text-sm"
		/>
	</div>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/+page.svelte
git commit -m "feat: add easter egg empty state and typewriter loading to job queue"
```

---

### Task 7: Tracker Page Empty State + Rejection Milestones

**Files:**
- Modify: `frontend/src/routes/tracker/+page.svelte:106-125`
- Modify: `frontend/src/lib/components/KanbanBoard.svelte:29-35,132-136,220-224`

- [ ] **Step 1: Update tracker empty state (lines ~106-117)**

Add imports:
```typescript
import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
import EasterEggToast from '$lib/components/EasterEggToast.svelte';
import { getEmptyState, getRejectionMilestone } from '$lib/utils/easterEggs';
```

Add state:
```typescript
const appEmptyMessage = $derived(applications.length === 0 ? getEmptyState('applications') : '');
let milestoneToast = $state<{ message: string; emoji: string; isSpecial: boolean } | null>(null);
```

Replace the empty state block:
```svelte
{:else if applications.length === 0}
	<div class="flex flex-col items-center justify-center py-20 gap-3 text-center">
		<FloatingEmoji emoji="📋" />
		<p class="text-muted-foreground text-sm font-medium">{appEmptyMessage}</p>
		<p class="text-muted-foreground text-xs">Apply to jobs from the Job Queue to see them here.</p>
```

- [ ] **Step 2: Add milestone toast above KanbanBoard (after the kanban wrapper div)**

After the `</div>` closing the kanban section, add:
```svelte
{#if milestoneToast}
	<EasterEggToast
		message={milestoneToast.message}
		emoji={milestoneToast.emoji}
		type={milestoneToast.isSpecial ? 'celebration' : 'milestone'}
		onclose={() => (milestoneToast = null)}
	/>
{/if}
```

- [ ] **Step 3: Add rejection milestone check**

In the `handleAddEvent` function (or `handleUpdate`), after the status update succeeds, add:
```typescript
// Check rejection milestone
if (event_type === 'rejected' || newStatus === 'rejected') {
	const rejectedCount = applications.filter((a) => a.status === 'rejected').length;
	const milestone = getRejectionMilestone(rejectedCount);
	if (milestone) {
		milestoneToast = milestone;
	}
}
```

- [ ] **Step 4: Update KanbanBoard rejected column header (lines ~132-136)**

In `KanbanBoard.svelte`, find the column header section. After the count badge, add a milestone message for the rejected column:

Add import:
```typescript
import { getRejectionMilestone } from '$lib/utils/easterEggs';
```

Add derived state:
```typescript
let rejectedCount = $derived(
	(grouped['rejected'] ?? []).length
);
let rejectionMessage = $derived(
	(() => {
		// Find the highest milestone at or below current count
		const thresholds = [200, 150, 100, 75, 50, 25, 10];
		for (const t of thresholds) {
			if (rejectedCount >= t) {
				return getRejectionMilestone(t);
			}
		}
		return null;
	})()
);
```

Below the column header div (after the closing `</div>` of the header, before the items), for the rejected column only:
```svelte
{#if col.id === 'rejected' && rejectionMessage}
	<div class="px-3 py-1.5 text-xs italic text-amber-400/70 border-b border-border bg-amber-500/5">
		{rejectionMessage.emoji} {rejectionMessage.message}
	</div>
{/if}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/tracker/+page.svelte frontend/src/lib/components/KanbanBoard.svelte
git commit -m "feat: add rejection milestones and easter egg empty states to tracker"
```

---

### Task 8: CV Page Empty State + CV Toast

**Files:**
- Modify: `frontend/src/routes/cv/+page.svelte:188-193`
- Modify: `frontend/src/lib/components/CVReviewPanel.svelte:268-271,369`

- [ ] **Step 1: Update CV page empty state (lines ~188-193)**

Add imports:
```typescript
import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
import { getEmptyState } from '$lib/utils/easterEggs';
```

Add state:
```typescript
const cvEmptyMessage = $derived(documents.length === 0 ? getEmptyState('cv') : '');
```

Replace:
```svelte
{:else if documents.length === 0}
	<div class="flex flex-col items-center justify-center py-12 gap-3 bg-card border border-border rounded-lg">
		<FileText size={28} class="text-muted-foreground/40" />
		<p class="text-sm text-muted-foreground font-medium">No tailored CVs yet</p>
		<p class="text-xs text-muted-foreground">CVs are generated during the job scan when jobs are matched.</p>
	</div>
```

With:
```svelte
{:else if documents.length === 0}
	<div class="flex flex-col items-center justify-center py-12 gap-3 bg-card border border-border rounded-lg">
		<FloatingEmoji emoji="📄" size="sm" />
		<p class="text-sm text-muted-foreground font-medium">{cvEmptyMessage}</p>
		<p class="text-xs text-muted-foreground">CVs are generated during the job scan when jobs are matched.</p>
	</div>
```

- [ ] **Step 2: Add apply confirmation message to CVReviewPanel confirm phase (line ~369)**

Add imports to `CVReviewPanel.svelte`:
```typescript
import EasterEggToast from './EasterEggToast.svelte';
import { getApplyConfirmation, getCvToast } from '$lib/utils/easterEggs';
```

Add state:
```typescript
let cvToastMessage = $state<string | null>(null);
const applyQuote = $derived(panelPhase === 'confirm' ? getApplyConfirmation() : '');
```

Before the "Run X applications" button (line ~369), add:
```svelte
<p class="text-xs text-muted-foreground italic text-center mb-4 animate-fade-in-up">
	{applyQuote}
</p>
```

- [ ] **Step 3: Show CV toast when diff loads successfully**

In the diff-loading logic, after diffs are loaded for a match, trigger toast:
```typescript
// After successful diff load
cvToastMessage = getCvToast();
setTimeout(() => (cvToastMessage = null), 4000);
```

Add toast at top of component template:
```svelte
{#if cvToastMessage}
	<EasterEggToast message={cvToastMessage} emoji="📜" type="info" duration={4000} onclose={() => (cvToastMessage = null)} />
{/if}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/cv/+page.svelte frontend/src/lib/components/CVReviewPanel.svelte
git commit -m "feat: add CV easter egg empty state, apply confirmation, and CV toast"
```

---

### Task 9: Settings Profile Completion Message

**Files:**
- Modify: `frontend/src/routes/settings/+page.svelte:459-462`

- [ ] **Step 1: Add import and derived state**

Add import:
```typescript
import { getProfileStatus } from '$lib/utils/easterEggs';
```

Add derived (using the profile form fields):
```typescript
const profileEasterEgg = $derived(getProfileStatus(profileForm));
```

- [ ] **Step 2: Add message below profile section header (lines ~459-462)**

Replace:
```svelte
<div class="space-y-1 mb-2">
	<h2 class="text-xl font-semibold font-heading">Personal Information</h2>
	<p class="text-xs text-muted-foreground">This information is used to automatically fill job application forms.</p>
</div>
```

With:
```svelte
<div class="space-y-1 mb-2">
	<h2 class="text-xl font-semibold font-heading">Personal Information</h2>
	<p class="text-xs text-muted-foreground">This information is used to automatically fill job application forms.</p>
	<p class="text-xs italic text-muted-foreground/70 mt-1">
		{profileEasterEgg.emoji} {profileEasterEgg.message}
	</p>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/settings/+page.svelte
git commit -m "feat: add profile completion easter egg to settings page"
```

---

### Task 10: StatusBar Loading Messages + Batch Messages

**Files:**
- Modify: `frontend/src/lib/components/StatusBar.svelte:24-44`

- [ ] **Step 1: Add imports**

```typescript
import TypewriterText from './TypewriterText.svelte';
import { getBatchMessage } from '$lib/utils/easterEggs';
```

- [ ] **Step 2: Enhance scraping progress display (line ~24-25)**

Replace the scraping progress text with a more playful version. Keep the data, but when progress is between 0 and 0.3 (early scraping), show the typewriter:

```svelte
{#if $lastMessage.type === 'scraping_progress'}
	<span>🔍 {$lastMessage.data?.source ?? 'Searching'} — {$lastMessage.data?.found ?? 0} jobs found</span>
```

- [ ] **Step 3: Enhance status message for batch completion (line ~34-44)**

After the status message display logic, when progress === 1.0, show the easter egg:

```svelte
{:else if $lastMessage.type === 'status'}
	{#if $lastMessage.progress >= 1}
		<span class="flex-1 truncate text-green-400">✨ {getBatchMessage('success')}</span>
	{:else}
		<span class="flex-1 truncate">{$lastMessage.message}</span>
	{/if}
	{#if $lastMessage.progress > 0 && $lastMessage.progress < 1}
		<div class="w-32 h-1 bg-muted rounded-full overflow-hidden relative">
			<div
				class="h-full bg-primary transition-all duration-300"
				style="width: {$lastMessage.progress * 100}%"
			></div>
			<div class="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent animate-progress-shimmer"></div>
		</div>
		<span class="tabular-nums">{Math.round($lastMessage.progress * 100)}%</span>
	{/if}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/StatusBar.svelte
git commit -m "feat: add easter egg messages to status bar for scraping and batch"
```

---

### Task 11: Job Detail Apply Confirmation

**Files:**
- Modify: `frontend/src/routes/jobs/[id]/+page.svelte:205-234`

- [ ] **Step 1: Add import and state**

```typescript
import { getApplyConfirmation } from '$lib/utils/easterEggs';
```

```typescript
let applyQuote = $state(getApplyConfirmation());
```

- [ ] **Step 2: Add confirmation message above apply buttons (line ~205)**

Before the apply section div, add:
```svelte
<p class="text-xs italic text-muted-foreground/60 mb-2 animate-fade-in-up">{applyQuote}</p>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/jobs/[id]/+page.svelte
git commit -m "feat: add apply confirmation easter egg to job detail page"
```

---

### Task 12: Error Page

**Files:**
- Create: `frontend/src/routes/+error.svelte`

- [ ] **Step 1: Create error page**

```svelte
<script lang="ts">
	import { page } from '$app/stores';
	import { getErrorMessage } from '$lib/utils/easterEggs';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';

	const errorMsg = getErrorMessage();
</script>

<div class="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center px-4">
	<FloatingEmoji emoji="🗺️" size="lg" />

	<div class="space-y-2">
		<h1 class="text-6xl font-bold text-foreground/20 animate-gentle-bounce">
			{$page.status}
		</h1>
		<p class="text-sm text-muted-foreground font-medium max-w-xs">
			{errorMsg}
		</p>
	</div>

	<a
		href="/"
		class="mt-4 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
	>
		Back to Job Queue
	</a>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/routes/+error.svelte
git commit -m "feat: add playful error page with floating emoji and easter egg messages"
```

---

## Chunk 3: Final Polish

### Task 13: Verify All Integrations Build

- [ ] **Step 1: Run the dev build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 2: Fix any build errors**

If any import paths are wrong or types mismatch, fix them.

- [ ] **Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve build issues from easter egg integration"
```

---

### Task 14: Manual Smoke Test Checklist

- [ ] **Step 1: Verify each easter egg works**

Run `cd frontend && npm run dev` and check:

1. `/` with empty queue → floating emoji + random message
2. `/` while scanning → typewriter loading animation
3. `/tracker` with empty → floating emoji + random message
4. `/tracker` Kanban rejected column with 10+ items → milestone message under header
5. `/cv` with no documents → floating emoji + random message
6. `/settings` → profile completion message below header
7. `/jobs/[id]` → apply confirmation quote above buttons
8. Navigate to non-existent route → error page with floating emoji
9. StatusBar during batch completion → green success message with sparkle
10. CV review panel confirm phase → italic quote above "Run N applications" button

- [ ] **Step 2: Fix any visual issues found during testing**

- [ ] **Step 3: Final commit**

```bash
git add -u
git commit -m "polish: final adjustments to easter egg animations and placement"
```
