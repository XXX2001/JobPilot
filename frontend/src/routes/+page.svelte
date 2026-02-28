<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import JobCard from '$lib/components/JobCard.svelte';
	import { messages, send } from '$lib/stores/websocket';
	import { RefreshCw, AlertCircle } from 'lucide-svelte';

	interface Job {
		id: number;
		title: string;
		company: string;
		location: string;
		salary_min?: number;
		salary_max?: number;
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
		job: Job;
	}

	interface QueueResponse {
		matches: QueueMatch[];
		total: number;
	}

	interface AnalyticsSummary {
		total_apps: number;
		apps_this_week: number;
		response_rate: number;
		avg_match_score: number;
	}

	let matches = $state<QueueMatch[]>([]);
	let summary = $state<AnalyticsSummary | null>(null);
	let loading = $state(true);
	let error = $state('');
	let refreshing = $state(false);
	let confirmModal = $state<{ jobId: number; method: string; fields?: Record<string, string>; screenshot?: string } | null>(null);

	// WebSocket: listen for apply_review and batch progress
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
	});

	async function loadQueue() {
		try {
			const data = await apiFetch<QueueResponse>('/api/queue');
			matches = data.matches ?? [];
		} catch (e: any) {
			error = e.message ?? 'Failed to load queue';
		} finally {
			loading = false;
		}
	}

	async function loadSummary() {
		try {
			summary = await apiFetch<AnalyticsSummary>('/api/analytics/summary');
		} catch {
			// non-critical
		}
	}

	async function refreshQueue() {
		refreshing = true;
		try {
			await apiFetch('/api/queue/refresh', { method: 'POST' });
			await loadQueue();
		} catch (e: any) {
			error = e.message ?? 'Refresh failed';
		} finally {
			refreshing = false;
		}
	}

	function handleSkip(matchId: number) {
		matches = matches.filter((m) => m.id !== matchId);
	}

	async function handleApply(detail: { id: number; method: string }) {
		try {
			await apiFetch(`/api/applications/${detail.id}/apply`, {
				method: 'POST',
				body: JSON.stringify({ method: detail.method })
			});
			if (detail.method === 'manual' || detail.method === 'assisted') {
				matches = matches.filter((m) => m.id !== detail.id);
			}
			// auto-apply waits for WS apply_review message
		} catch (e: any) {
			error = e.message ?? 'Apply failed';
		}
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

	const today = new Date().toLocaleDateString('en-GB', { weekday: 'long', month: 'short', day: 'numeric' });

	onMount(() => {
		loadQueue();
		loadSummary();
	});
</script>

<!-- Header bar -->
<div class="flex items-center justify-between mb-5">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Morning Queue</h1>
		<p class="text-xs text-muted-foreground mt-0.5">
			{today}
			{#if summary}
				· {summary.total_apps - (summary.apps_this_week ?? 0)} total · {summary.apps_this_week ?? 0} this week
			{/if}
			{#if matches.length > 0}
				· <span class="text-foreground font-medium">{matches.length} matches</span>
			{/if}
		</p>
	</div>

	<button
		onclick={refreshQueue}
		disabled={refreshing}
		class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
	>
		<RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
		{refreshing ? 'Refreshing…' : 'Refresh Queue'}
	</button>
</div>

<!-- Error banner -->
{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />
		{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{/if}

<!-- Content -->
{#if loading}
	<div class="space-y-3">
		{#each Array(3) as _}
			<div class="border border-border rounded-lg p-4 animate-pulse">
				<div class="flex gap-4">
					<div class="w-12 h-12 rounded-full bg-muted"></div>
					<div class="flex-1 space-y-2">
						<div class="h-4 bg-muted rounded w-2/3"></div>
						<div class="h-3 bg-muted rounded w-1/2"></div>
					</div>
				</div>
			</div>
		{/each}
	</div>
{:else if matches.length === 0}
	<div
		data-testid="empty-queue"
		class="flex flex-col items-center justify-center py-20 gap-3 text-center"
	>
		<div class="text-4xl">📭</div>
		<p class="text-muted-foreground text-sm font-medium">No matches today.</p>
		<p class="text-muted-foreground text-xs">Trigger a search to find new opportunities.</p>
		<button
			onclick={refreshQueue}
			disabled={refreshing}
			class="mt-2 flex items-center gap-2 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
		>
			<RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
			Refresh Queue
		</button>
	</div>
{:else}
	<div class="grid gap-3 max-w-3xl">
		{#each matches as match (match.id)}
			<JobCard
				{match}
				on:skip={(e) => handleSkip(e.detail)}
				on:apply={(e) => handleApply(e.detail)}
			/>
		{/each}
	</div>
{/if}

<!-- Auto-apply confirmation modal -->
{#if confirmModal}
	<div class="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
		<div class="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
			<div class="p-5 border-b border-border">
				<h2 class="font-semibold">Confirm Auto Apply</h2>
				<p class="text-xs text-muted-foreground mt-1">Review the filled fields before submitting.</p>
			</div>

			{#if confirmModal.screenshot}
				<div class="px-5 pt-4">
					<p class="text-xs text-muted-foreground mb-2">Form preview:</p>
					<img src="data:image/png;base64,{confirmModal.screenshot}"
						alt="Form screenshot" class="rounded border border-border w-full max-h-48 object-cover" />
				</div>
			{/if}

			{#if confirmModal.fields && Object.keys(confirmModal.fields).length > 0}
				<div class="px-5 pt-4 pb-2">
					<p class="text-xs text-muted-foreground mb-2">Filled fields:</p>
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
					class="text-xs px-4 py-2 rounded-md border border-border hover:bg-accent transition-colors">
					Cancel
				</button>
				<button onclick={confirmApply}
					class="text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
					Confirm Submit
				</button>
			</div>
		</div>
	</div>
{/if}
