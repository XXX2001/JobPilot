# Easter Eggs & Mood Boosters — Design Spec

## Goal

Add warm, witty easter eggs throughout the UI to boost user morale during the job search grind. Motivational milestones for rejection counts, playful loading/empty states, and celebratory animations.

## Approach

**Centralized Easter Egg Store** — All copy lives in `frontend/src/lib/utils/easterEggs.ts`. Components import helpers. Animations defined via Tailwind keyframes + a reusable `EasterEggToast.svelte` component.

## Architecture

### 1. Easter Egg Store (`frontend/src/lib/utils/easterEggs.ts`)

Single source of truth for all messages. Organized by category:

| Category | Type | Trigger |
|----------|------|---------|
| `rejectionMilestones` | `Map<number, string>` | Rejection count hits threshold |
| `emptyStates` | `Record<context, string[]>` | Empty list in queue/applications/cv |
| `loadingMessages` | `string[]` | During scraping/batch operations |
| `batchMessages` | `Record<outcome, string[]>` | Batch completes with 0 or N jobs |
| `cvToasts` | `string[]` | After CV generation succeeds |
| `profileCompletion` | `Record<status, string>` | Settings page profile fill level |
| `applyConfirmation` | `string[]` | Before submitting application |
| `errorMessages` | `string[]` | 404/error states |

**Helpers exported:**
- `getRandomMessage(messages: string[]): string`
- `getRejectionMilestone(count: number): { message: string, isSpecial: boolean } | null`
- `getEmptyState(context: string): string`
- `getLoadingMessage(): string` (cycles without repeating consecutively)

### 2. Animations

**New Tailwind keyframes** (added to `tailwind.config.js`):

| Animation | Used For | Effect |
|-----------|----------|--------|
| `fade-in-up` | Toast appearance | Slide up + fade in (0.4s ease-out) |
| `fade-out-down` | Toast dismissal | Slide down + fade out (0.3s ease-in) |
| `shimmer` | Milestone celebration | Gold shimmer sweep across text |
| `float` | Empty state emoji | Gentle up/down float (3s infinite) |
| `typewriter` | Loading messages | Characters appear one by one |
| `confetti-pop` | 100+ rejection milestone | Scale bounce with rotate (0.5s) |
| `gentle-bounce` | Empty state icons | Subtle bounce (2s infinite) |
| `glow-pulse` | Special milestones | Soft golden glow pulse |

### 3. New Components

#### `EasterEggToast.svelte`
Animated toast notification for milestone celebrations and CV generation messages.

**Props:**
- `message: string`
- `type: 'milestone' | 'info' | 'celebration'`
- `duration: number` (ms, default 5000)
- `emoji?: string`

**Behavior:**
- Slides in from top-right with `fade-in-up`
- Auto-dismisses after duration with `fade-out-down`
- Milestone type: gold border + shimmer effect
- Celebration type (100+ rejections): confetti-pop + glow-pulse
- Info type: standard border with subtle entrance

#### `FloatingEmoji.svelte`
Wrapper for empty-state emojis with float animation.

**Props:**
- `emoji: string`
- `size?: 'sm' | 'md' | 'lg'` (default 'md')

#### `TypewriterText.svelte`
Cycles through loading messages with typewriter effect.

**Props:**
- `messages: string[]`
- `typingSpeed?: number` (ms per char, default 40)
- `pauseDuration?: number` (ms between messages, default 2000)

## Integration Points

### A. Rejection Counter + Milestone Toast — `KanbanBoard.svelte`
- Count cards in the "Rejected" column
- Display count badge: "Rejected (N)"
- When count crosses a milestone threshold, show `EasterEggToast` with type `milestone` or `celebration` (for 100+)
- Milestone message persists below the Rejected column header as a small italic text

### B. Empty States — `+page.svelte`, `KanbanBoard.svelte`, `CVReviewPanel.svelte`
- Replace current emoji + text with `FloatingEmoji` + randomized message from `getEmptyState(context)`
- Contexts: `queue`, `applications`, `cv`

### C. Loading/Scraping States — `+page.svelte`, `StatusBar.svelte`
- During scraping: show `TypewriterText` cycling through `loadingMessages`
- Replace static "Scanning..." text with the animated component

### D. Batch Completion — `StatusBar.svelte`
- When batch broadcast has progress=1.0: show random `batchMessages.success` message
- When 0 jobs found: show random `batchMessages.zero_jobs` message

### E. CV Generation Toast — `CVReviewPanel.svelte`
- After CV diff loads successfully: brief `EasterEggToast` with random `cvToasts` message

### F. Profile Completion — `settings/+page.svelte`
- Calculate fill percentage from profile fields
- Show appropriate `profileCompletion` message below profile section header
- 0% = 'empty', 1-99% = 'partial', 100% = 'complete'

### G. Apply Confirmation — `jobs/[id]/+page.svelte` or `CVReviewPanel.svelte` (confirm phase)
- Show random `applyConfirmation` message above the "Run X applications" button
- Subtle fade-in animation

### H. Error/404 Page — New `+error.svelte`
- Create SvelteKit error page with random `errorMessages`
- Playful layout with floating emoji and gentle-bounce animation

## Messages Content

### Rejection Milestones (warm/motivational)
```
10  → "You're just warming up."
25  → "Thomas Edison failed 1,000 times before the lightbulb. You're ahead of schedule."
50  → "Halfway to 100 — and after 100 rejections, the yes hits different."
75  → "At this point, you're basically rejection-proof. Armor level: mythic."
100 → "100 rejections. You are now statistically unstoppable."
150 → "Most people quit at 50. You're built different."
200 → "200 rejections. At this point, companies are rejecting a legend."
```

### Empty States (playful)
```
queue:
- "The right job is out there, probably also refreshing its inbox."
- "No jobs yet. The market is playing hard to get."
- "Queue's empty. Time to grab coffee while we hunt."

applications:
- "Every expert was once a beginner with an empty applications page."
- "No applications yet. Your future employer is still writing the job post."
- "Clean slate. The world is your oyster (that hasn't been applied to yet)."

cv:
- "No tailored CVs yet. Your base CV is doing its best."
- "No CVs here yet. We're warming up the LaTeX compiler."
- "The CV forge awaits its first commission."
```

### Loading Messages (absurd/playful)
```
"Scanning the internet for your dream job..."
"Negotiating with job boards on your behalf..."
"Teaching robots to read job descriptions..."
"Convincing LinkedIn you're not a bot..."
"Translating recruiter-speak into English..."
"Asking the job market to take you seriously..."
"Bribing the algorithm with good vibes..."
"Performing dark arts on job listings..."
"Whispering sweet nothings to APIs..."
"Pretending to be 47 browser tabs at once..."
```

### Batch Messages
```
zero_jobs:
- "The job market took a coffee break. We'll try again tomorrow."
- "Zero new jobs. Even the bots need a day off."
- "Nothing new today. Mercury might be in retrograde."

success:
- "Fresh opportunities, served hot."
- "Your future employer doesn't know it yet, but today might be the day."
- "New jobs locked and loaded. Let's get picky."
```

### CV Toasts
```
"CV sharpened. You're now 3% more hireable (scientifically unverified)."
"New CV forged. It's dangerous to go alone — take this."
"CV tailored. Looking sharp. Literally."
"Another CV crafted. Your LaTeX compiler sends its regards."
```

### Profile Completion
```
complete → "Profile complete. You look great on paper."
empty    → "A blank profile is like showing up to an interview in pajamas — comfortable, but not ideal."
partial  → "Getting there! A few more fields and you'll be unstoppable."
```

### Apply Confirmation
```
"One small click for you, one giant leap for your career."
"This application is about to make someone's hiring pipeline very happy."
"Ready to make this recruiter's day? Hit the button."
```

### Error/404
```
"This page is like that perfect job listing — it doesn't exist (yet)."
"Error 404: Job satisfaction not found. Keep looking."
"You've wandered off the career path. Let's get you back."
"This page took a personal day. Try another route."
```

## Files Modified

| File | Change |
|------|--------|
| `frontend/src/lib/utils/easterEggs.ts` | **NEW** — All messages + helpers |
| `frontend/src/lib/components/EasterEggToast.svelte` | **NEW** — Animated toast |
| `frontend/src/lib/components/FloatingEmoji.svelte` | **NEW** — Float animation wrapper |
| `frontend/src/lib/components/TypewriterText.svelte` | **NEW** — Typewriter cycling text |
| `frontend/tailwind.config.js` | Add custom keyframes + animations |
| `frontend/src/routes/+error.svelte` | **NEW** — Error page with easter egg |
| `frontend/src/routes/+page.svelte` | Empty state, loading state, apply confirmation |
| `frontend/src/routes/tracker/+page.svelte` | Rejection milestone integration |
| `frontend/src/lib/components/KanbanBoard.svelte` | Rejection count + milestone display |
| `frontend/src/lib/components/CVReviewPanel.svelte` | CV toast, empty state |
| `frontend/src/lib/components/StatusBar.svelte` | Loading messages, batch messages |
| `frontend/src/routes/settings/+page.svelte` | Profile completion message |
| `frontend/src/routes/cv/+page.svelte` | Empty state |
| `frontend/src/routes/jobs/[id]/+page.svelte` | Apply confirmation message |

## Non-Goals
- No backend changes (all frontend-only)
- No persistent storage of which messages were shown
- No user preference to disable easter eggs (keep it simple for now)
