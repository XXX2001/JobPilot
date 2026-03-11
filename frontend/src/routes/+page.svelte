<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { messages, send } from '$lib/stores/websocket';
	import { RefreshCw, AlertCircle, Zap, MousePointer, X } from 'lucide-svelte';
	import CVReviewPanel from '$lib/components/CVReviewPanel.svelte';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
	import TypewriterText from '$lib/components/TypewriterText.svelte';
	import { getEmptyState } from '$lib/utils/easterEggs';

	interface Job {
		id: number;
		title: string;
		company: string;
		location: string;
		salary_min?: number;
		salary_max?: number;
		description?: string;
		url: string;
		apply_url: string;
		apply_method: string;
		posted_at?: string;
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

	type ApplyMode = 'auto' | 'manual' | 'skip';
	type Phase = 'select' | 'review';

	let matches = $state<QueueMatch[]>([]);
	let modes = $state<Map<number, ApplyMode>>(new Map());
	let phase = $state<Phase>('select');
	let loading = $state(true);
	let error = $state('');
	let refreshing = $state(false);
	let refreshTimeout: ReturnType<typeof setTimeout> | null = null;
	let confirmModal = $state<{
		jobId: number;
		method: string;
		fields?: Record<string, string>;
		screenshot?: string;
	} | null>(null);

	function defaultMode(job: Job): ApplyMode {
		return job.apply_method === 'easy_apply' || job.apply_method === 'auto' ? 'auto' : 'manual';
	}

	$effect(() => {
		const lastMsg = $messages[$messages.length - 1];
		if (!lastMsg) return;
		if (lastMsg.type === 'apply_review') {
			confirmModal = {
				jobId: lastMsg.job_id,
				method: 'auto',
				fields: lastMsg.filled_fields,
				screenshot: lastMsg.screenshot_base64
			};
		}
		if (lastMsg.type === 'status' && lastMsg.progress >= 1.0) {
			refreshing = false;
			if (refreshTimeout) {
				clearTimeout(refreshTimeout);
				refreshTimeout = null;
			}
			loadQueue();
		}
	});

	async function loadQueue() {
		try {
			const data = await apiFetch<{ matches: QueueMatch[]; total: number }>('/api/queue');
			matches = data.matches ?? [];
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
		refreshTimeout = setTimeout(() => {
			refreshing = false;
		}, 5 * 60 * 1000);
		try {
			await apiFetch('/api/queue/refresh', { method: 'POST' });
		} catch (e: any) {
			error = e.message ?? 'Refresh failed';
			refreshing = false;
			if (refreshTimeout) {
				clearTimeout(refreshTimeout);
				refreshTimeout = null;
			}
		}
	}

	async function setMode(matchId: number, mode: ApplyMode) {
		const prev = modes.get(matchId);
		const m = new Map(modes);
		m.set(matchId, mode);
		modes = m;

		// Persist skip to backend immediately so it survives refresh
		if (mode === 'skip' && prev !== 'skip') {
			try {
				await apiFetch(`/api/queue/${matchId}/skip`, { method: 'PATCH' });
				// Remove from local list
				matches = matches.filter((mt) => mt.id !== matchId);
			} catch {
				// revert on failure
				m.set(matchId, prev ?? 'manual');
				modes = new Map(m);
			}
		} else if (prev === 'skip' && mode !== 'skip') {
			// Un-skip: restore to new
			try {
				await apiFetch(`/api/queue/${matchId}/status`, {
					method: 'PATCH',
					body: JSON.stringify({ status: 'new' })
				});
			} catch {
				// ignore
			}
		}
	}

	const queueEmptyMessage = $derived(matches.length === 0 ? getEmptyState('queue') : '');

	const activeMatches = $derived(matches.filter((m) => modes.get(m.id) !== 'skip'));

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

	onMount(loadQueue);
</script>

<!-- Header -->
<div class="mb-5 flex items-center justify-between">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Job Queue</h1>
		<p class="text-muted-foreground mt-0.5 text-xs">
			{matches.length} pending · Scan to discover new opportunities
		</p>
	</div>
	<button
		onclick={refreshQueue}
		disabled={refreshing}
		class="border-border hover:bg-accent flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
	>
		<RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
		{refreshing ? 'Scanning…' : 'Scan for Jobs'}
	</button>
</div>

{#if error}
	<div
		class="mb-4 flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
	>
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{/if}

{#if phase === 'review'}
	<CVReviewPanel matches={activeMatches} {modes} onback={backToSelect} oncomplete={onRunComplete} />
{:else if loading}
	<div class="flex flex-col items-center justify-center gap-4 py-20">
		<div class="relative">
			<div class="h-10 w-10 rounded-full border-2 border-primary/20 border-t-primary animate-spin"></div>
		</div>
		<TypewriterText
			messages={[
				"Scan de l'internet à la recherche de ton job de rêve...",
				'Négociation avec les job boards en ton nom...',
				'Apprentissage de la lecture de fiches de poste par les robots...',
				"Convaincre LinkedIn que tu n'es pas un bot...",
				'Traduction du jargon RH en français courant...',
				"Soudoyer l'algorithme avec de bonnes ondes..."
			]}
			class="text-muted-foreground text-sm"
		/>
	</div>
{:else if matches.length === 0}
	<div class="flex flex-col items-center justify-center gap-3 py-20 text-center">
		<FloatingEmoji emoji="📭" />
		<p class="text-muted-foreground text-sm font-medium">{queueEmptyMessage}</p>
		<p class="text-muted-foreground text-xs">Click "Scan for Jobs" to search for new opportunities.</p>
		<button
			onclick={refreshQueue}
			disabled={refreshing}
			class="bg-primary text-primary-foreground hover:bg-primary/90 mt-2 flex items-center gap-2 rounded-md px-4 py-2 text-xs transition-colors disabled:opacity-50"
		>
			<RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
			Scan for Jobs
		</button>
	</div>
{:else}
	{#if activeMatches.length > 0}
		<div class="mb-3 flex justify-end">
			<button
				onclick={proceedToReview}
				class="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-2 rounded-md px-4 py-2 text-xs font-medium transition-colors"
			>
				Review & Apply ({activeMatches.length}) →
			</button>
		</div>
	{/if}

	<div class="max-w-3xl space-y-2">
		{#each matches as match (match.id)}
			{@const mode = modes.get(match.id) ?? 'manual'}
			<div
				class="bg-card border-border flex items-center gap-4 rounded-lg border px-4 py-3 {mode ===
				'skip'
					? 'opacity-40'
					: ''}"
			>
				<span
					class="w-10 flex-shrink-0 text-center text-sm font-bold {match.score >= 80
						? 'text-green-400'
						: match.score >= 60
							? 'text-yellow-400'
							: 'text-red-400'}"
				>
					{Math.round(match.score)}%
				</span>

				<div class="min-w-0 flex-1">
					<p class="truncate text-sm font-medium">{match.job.title}</p>
					<p class="text-muted-foreground truncate text-xs">
						{match.job.company}{match.job.location ? ` · ${match.job.location}` : ''}
					</p>
				</div>

				<div class="flex flex-shrink-0 gap-1">
					{#each [['auto', 'Auto', Zap], ['manual', 'Manual', MousePointer], ['skip', 'Skip', X]] as const as [m, label, Icon]}
						<button
							onclick={() => setMode(match.id, m as ApplyMode)}
							class="border-border text-muted-foreground hover:text-foreground flex items-center gap-1 rounded border px-2 py-1 text-xs transition-colors {mode ===
							m
								? m === 'skip'
									? 'border-red-500/50 bg-red-500/10 text-red-400'
									: 'border-primary/50 bg-primary/10 text-primary'
								: ''}"
						>
							<Icon size={11} />{label}
						</button>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{/if}

{#if confirmModal}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
		<div
			class="bg-card border-border mx-4 w-full max-w-lg overflow-hidden rounded-xl border shadow-2xl"
		>
			<div class="border-border border-b p-5">
				<h2 class="font-semibold">Confirm Auto Apply</h2>
				<p class="text-muted-foreground mt-1 text-xs">Review the filled fields before submitting.</p>
			</div>
			{#if confirmModal.screenshot}
				<div class="px-5 pt-4">
					<img
						src="data:image/png;base64,{confirmModal.screenshot}"
						alt="Form preview"
						class="border-border w-full max-h-48 rounded border object-cover"
					/>
				</div>
			{/if}
			{#if confirmModal.fields && Object.keys(confirmModal.fields).length > 0}
				<div class="px-5 pb-2 pt-4">
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
				<button
					onclick={cancelApply}
					class="border-border hover:bg-accent rounded-md border px-4 py-2 text-xs transition-colors"
					>Cancel</button
				>
				<button
					onclick={confirmApply}
					class="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-2 text-xs transition-colors"
					>Confirm Submit</button
				>
			</div>
		</div>
	</div>
{/if}
