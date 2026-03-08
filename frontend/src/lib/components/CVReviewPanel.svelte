<script lang="ts">
	import { apiFetch } from '$lib/api';
	import {
		ArrowLeft,
		Zap,
		MousePointer,
		CheckCircle2,
		SkipForward,
		ExternalLink
	} from 'lucide-svelte';

	interface Job {
		id: number;
		title: string;
		company: string;
		location: string;
		url: string;
		apply_url: string;
		apply_method: string;
		salary_min?: number;
		salary_max?: number;
	}
	interface QueueMatch {
		id: number;
		job_id: number;
		score: number;
		status: string;
		batch_date: string;
		matched_at: string;
		job: Job;
	}
	interface DiffEntry {
		section: string;
		original_text: string;
		edited_text: string;
		change_description: string;
	}

	type ApplyMode = 'auto' | 'manual' | 'skip';
	type Decision = 'approved' | 'base_cv' | 'skip';
	type PanelPhase = 'review' | 'confirm' | 'done';

	let {
		matches,
		modes,
		onback,
		oncomplete
	}: {
		matches: QueueMatch[];
		modes: Map<number, ApplyMode>;
		onback: () => void;
		oncomplete: () => void;
	} = $props();

	let cursor = $state(0);
	let diffs = $state<Map<number, DiffEntry[]>>(new Map());
	let diffLoading = $state(false);
	let decisions = $state<Map<number, Decision>>(new Map());
	let running = $state(false);
	let runResults = $state<{ matchId: number; ok: boolean; msg: string }[]>([]);
	let panelPhase = $state<PanelPhase>('review');

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

	function decide(decision: Decision) {
		const d = new Map(decisions);
		d.set(current.id, decision);
		decisions = d;
		if (cursor < matches.length - 1) {
			cursor++;
		} else {
			panelPhase = 'confirm';
		}
	}

	function goBack() {
		if (cursor > 0) cursor--;
	}

	const approvedMatches = $derived(
		matches.filter((m) => {
			const dec = decisions.get(m.id);
			return dec === 'approved' || dec === 'base_cv';
		})
	);

	async function runApplications() {
		running = true;
		panelPhase = 'done';
		const results: { matchId: number; ok: boolean; msg: string }[] = [];

		for (const match of approvedMatches) {
			const method = modes.get(match.id) ?? 'manual';
			try {
				await apiFetch(`/api/applications/${match.id}/apply`, {
					method: 'POST',
					body: JSON.stringify({ method })
				});
				results.push({
					matchId: match.id,
					ok: true,
					msg: `${match.job.title} — queued (${method})`
				});
			} catch (e: any) {
				results.push({ matchId: match.id, ok: false, msg: e.message ?? 'Failed' });
		}
		}

		runResults = results;
		running = false;
	}
</script>

{#if panelPhase === 'review'}
	<!-- Navigation header -->
	<div class="mb-4 flex items-center gap-3">
		<button
			onclick={onback}
			class="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs transition-colors"
		>
			<ArrowLeft size={13} /> Back
		</button>
		<span class="text-muted-foreground text-xs">
			Reviewing {cursor + 1} of {matches.length}
		</span>
		<div class="ml-2 flex gap-1">
			{#each matches as m, i}
				{@const dec = decisions.get(m.id)}
				<div
					class="h-2 w-2 rounded-full {i === cursor
						? 'bg-primary'
						: dec === 'approved'
							? 'bg-green-500'
							: dec === 'base_cv'
								? 'bg-yellow-500'
								: dec === 'skip'
									? 'bg-muted'
									: 'bg-border'}"
				></div>
			{/each}
		</div>
	</div>

	<!-- Split pane -->
	<div class="grid min-h-[500px] grid-cols-2 gap-4" style="height: calc(100vh - 220px)">
		<!-- LEFT: Job details -->
		<div class="border-border flex flex-col gap-3 overflow-y-auto rounded-lg border p-4">
			<div class="flex items-start justify-between gap-2">
				<div>
					<p class="text-sm font-semibold leading-tight">{current.job.title}</p>
					<p class="text-muted-foreground mt-0.5 text-xs">{current.job.company}</p>
					{#if current.job.location}
						<p class="text-muted-foreground text-xs">{current.job.location}</p>
					{/if}
				</div>
				<span
					class="flex-shrink-0 text-lg font-bold {current.score >= 80
						? 'text-green-400'
						: current.score >= 60
							? 'text-yellow-400'
							: 'text-red-400'}"
				>
					{Math.round(current.score)}%
				</span>
			</div>

			<div class="text-muted-foreground flex items-center gap-2 text-xs">
				<span class="border-border rounded-full border px-2 py-0.5">
					{modes.get(current.id) === 'auto' ? '⚡ Auto apply' : '🖱 Manual apply'}
				</span>
				<a
					href={current.job.url}
					target="_blank"
					class="hover:text-foreground flex items-center gap-1 transition-colors"
				>
					<ExternalLink size={11} /> View job
				</a>
			</div>

			{#if current.job.salary_min || current.job.salary_max}
				<p class="text-muted-foreground text-xs">
					💰 {current.job.salary_min ? `£${current.job.salary_min.toLocaleString()}` : '?'} –
					{current.job.salary_max ? `£${current.job.salary_max.toLocaleString()}` : '?'}
				</p>
			{/if}
		</div>

		<!-- RIGHT: CV diff -->
		<div class="border-border overflow-y-auto rounded-lg border p-4">
			<p class="text-muted-foreground mb-3 text-xs font-medium uppercase tracking-wide">
				Proposed CV changes
			</p>

			{#if diffLoading}
				<div class="space-y-3">
					{#each Array(3) as _}
						<div class="bg-muted/30 h-20 animate-pulse rounded-lg"></div>
					{/each}
				</div>
			{:else}
				{@const currentDiffs = diffs.get(current.id) ?? []}
				{#if currentDiffs.length === 0}
					<div class="flex flex-col items-center justify-center gap-2 py-12 text-center">
						<p class="text-muted-foreground text-sm">No changes — base CV will be used as-is.</p>
					</div>
				{:else}
					<div class="space-y-4">
						{#each currentDiffs as entry}
							<div class="border-border overflow-hidden rounded-lg border">
								<div class="border-border bg-muted/30 border-b px-3 py-1.5">
									<span class="text-xs font-medium">{entry.section}</span>
									{#if entry.change_description}
										<span class="text-muted-foreground ml-2 text-xs">— {entry.change_description}</span>
									{/if}
								</div>
								<div class="space-y-2 p-3">
									<p class="text-muted-foreground text-xs leading-relaxed line-through">
										{entry.original_text}
									</p>
									<p
										class="rounded border border-green-500/20 bg-green-500/10 px-2 py-1 text-xs leading-relaxed text-green-300"
									>
										{entry.edited_text}
									</p>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
	</div>

	<!-- Action bar -->
	<div class="border-border mt-4 flex items-center gap-2 border-t pt-4">
		<button
			onclick={goBack}
			disabled={cursor === 0}
			class="border-border hover:bg-accent flex items-center gap-1 rounded-md border px-3 py-2 text-xs transition-colors disabled:opacity-30"
		>
			<ArrowLeft size={12} /> Back
		</button>
		<div class="flex-1"></div>
		<button
			onclick={() => decide('skip')}
			class="border-border text-muted-foreground hover:text-foreground hover:bg-accent flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs transition-colors"
		>
			<SkipForward size={13} /> Skip job
		</button>
		<button
			onclick={() => decide('base_cv')}
			class="border-border hover:bg-accent flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs transition-colors"
		>
			Use base CV
		</button>
		<button
			onclick={() => decide('approved')}
			class="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-colors"
		>
			<CheckCircle2 size={13} /> Approve changes →
		</button>
	</div>

{:else if panelPhase === 'confirm'}
	<div class="mx-auto max-w-lg py-8">
		<h2 class="mb-1 text-lg font-semibold">Ready to apply</h2>
		<p class="text-muted-foreground mb-5 text-sm">
			{approvedMatches.length} job{approvedMatches.length !== 1 ? 's' : ''} approved · {matches.length -
				approvedMatches.length} skipped
		</p>

		<div class="mb-6 space-y-2">
			{#each approvedMatches as match}
				{@const dec = decisions.get(match.id)}
				<div class="border-border flex items-center gap-3 rounded-lg border px-3 py-2 text-sm">
					<span class={dec === 'approved' ? 'text-green-400' : 'text-yellow-400'}>
						{dec === 'approved' ? '✓ Tailored CV' : '○ Base CV'}
					</span>
					<span class="flex-1 truncate">{match.job.title} · {match.job.company}</span>
					<span class="text-muted-foreground flex items-center gap-1 text-xs">
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
			<button
				onclick={() => {
					cursor = 0;
					panelPhase = 'review';
				}}
				class="border-border hover:bg-accent flex-1 rounded-md border px-4 py-2 text-sm transition-colors"
			>
				← Back to review
			</button>
			<button
				onclick={runApplications}
				class="bg-primary text-primary-foreground hover:bg-primary/90 flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors"
			>
				Run {approvedMatches.length} applications →
			</button>
		</div>
	</div>

{:else if panelPhase === 'done'}
	<div class="mx-auto max-w-lg py-8">
		<h2 class="mb-1 text-lg font-semibold">{running ? 'Applying…' : 'Done'}</h2>
		<p class="text-muted-foreground mb-5 text-sm">
			{running
				? 'Applications are being processed.'
				: `${runResults.filter((r) => r.ok).length} started successfully.`}
		</p>

		<div class="mb-6 space-y-2">
			{#each runResults as r}
				<div
					class="flex items-center gap-2 rounded-lg border px-3 py-2 text-xs {r.ok
						? 'border-green-500/20 bg-green-500/5 text-green-400'
						: 'border-red-500/20 bg-red-500/5 text-red-400'}"
				>
					{r.ok ? '✓' : '✕'}
					{r.msg}
				</div>
			{/each}
		</div>

		{#if !running}
			<button
				onclick={oncomplete}
				class="bg-primary text-primary-foreground hover:bg-primary/90 w-full rounded-md px-4 py-2 text-sm transition-colors"
			>
				Back to queue
			</button>
		{/if}
	</div>
{/if}
