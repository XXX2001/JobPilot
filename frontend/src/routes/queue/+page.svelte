<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { messages, send, onWsConnect } from '$lib/stores/websocket';
	import { RefreshCw, AlertCircle, Zap, MousePointer, X, Eye } from 'lucide-svelte';
	import CVReviewPanel from '$lib/components/CVReviewPanel.svelte';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
	import BatchPipelineTracker from '$lib/components/BatchPipelineTracker.svelte';
	import { getEmptyState } from '$lib/utils/easterEggs';
	import { register, deregister } from '$lib/utils/hotkeys';
	import type { BindingHandle } from '$lib/utils/hotkeys';
	import type { Job, QueueMatch } from '$lib/types/api';
	import { focusTrap } from '$lib/utils/focusTrap';
	import {
		addPendingReviewId,
		loadPendingReviewIds,
		removePendingReviewId,
		reviewStateToModal,
		savePendingReviewIds
	} from '$lib/utils/pendingReview';

	type ApplyMode = 'auto' | 'manual' | 'skip';
	type Phase = 'select' | 'review';

	let matches = $state<QueueMatch[]>([]);
	let modes = $state<Map<number, ApplyMode>>(new Map());
	let phase = $state<Phase>('select');
	let loading = $state(true);
	let error = $state('');
	let refreshing = $state(false);
	let refreshTimeout: ReturnType<typeof setTimeout> | null = null;

	type PreviewMatch = { title: string; company: string; score: number; location: string };
	let previewing = $state(false);
	let preview = $state<PreviewMatch[] | null>(null);
	let previewError = $state('');
	let confirmModal = $state<{
		jobId: number;
		method: string;
		fields?: Record<string, string>;
		screenshot?: string;
	} | null>(null);

	/** Index of the keyboard-focused card in the `matches` array. -1 = none. */
	let focusedIndex = $state(-1);
	let hotkeyHandle: BindingHandle | null = null;

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
			// Mirror the awaiting job id so the review survives a WS reconnect/reload.
			savePendingReviewIds(addPendingReviewId(loadPendingReviewIds(), lastMsg.job_id));
		}
		if (lastMsg.type === 'status' && (lastMsg.progress >= 1.0 || lastMsg.progress < 0)) {
			refreshing = false;
			if (refreshTimeout) {
				clearTimeout(refreshTimeout);
				refreshTimeout = null;
			}
			if (lastMsg.progress >= 1.0) loadQueue();
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

	/** Check backend batch status and sync local refreshing state. */
	async function syncBatchStatus() {
		try {
			const status = await apiFetch<{ running: boolean; last_status: any }>('/api/queue/status');
			if (status.running) {
				refreshing = true;
				if (refreshTimeout) clearTimeout(refreshTimeout);
				refreshTimeout = setTimeout(() => { refreshing = false; }, 5 * 60 * 1000);
			} else {
				refreshing = false;
				if (refreshTimeout) { clearTimeout(refreshTimeout); refreshTimeout = null; }
			}
		} catch {
			// Ignore — status endpoint may not be available
		}
	}

	async function refreshQueue() {
		if (refreshing) return; // Prevent double-click
		refreshing = true;
		if (refreshTimeout) clearTimeout(refreshTimeout);
		refreshTimeout = setTimeout(() => {
			refreshing = false;
		}, 5 * 60 * 1000);
		try {
			await apiFetch('/api/queue/refresh', { method: 'POST' });
		} catch (e: any) {
			// 409 = already running — keep refreshing state
			if (e.message?.includes('409')) return;
			error = e.message ?? 'Refresh failed';
			refreshing = false;
			if (refreshTimeout) {
				clearTimeout(refreshTimeout);
				refreshTimeout = null;
			}
		}
	}

	/** Dry-run: preview what today's batch WOULD match without committing rows. */
	async function previewMatches() {
		if (previewing || refreshing) return;
		previewing = true;
		previewError = '';
		try {
			const data = await apiFetch<{
				status: string;
				matches: PreviewMatch[];
				total: number;
			}>('/api/queue/refresh?dry_run=true', { method: 'POST' });
			preview = data.matches ?? [];
		} catch (e: any) {
			// 409 = a batch is already running — can't preview right now.
			if (e.message?.includes('409')) {
				previewError = 'A search is already running — try the preview again once it finishes.';
			} else {
				previewError = e.message ?? 'Preview failed';
			}
		} finally {
			previewing = false;
		}
	}

	function dismissPreview() {
		preview = null;
		previewError = '';
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
			} catch (e: any) {
				// revert on failure and surface the reason
				m.set(matchId, prev ?? 'manual');
				modes = new Map(m);
				error = e.message ?? 'Failed to skip this match';
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
			const jobId = confirmModal.jobId;
			if (confirmModal.fields) {
				send({ type: 'patch_fields', job_id: jobId, fields: confirmModal.fields });
			}
			send({ type: 'confirm_submit', job_id: jobId });
			confirmModal = null;
			savePendingReviewIds(removePendingReviewId(loadPendingReviewIds(), jobId));
		}
	}

	function cancelApply() {
		if (confirmModal) {
			const jobId = confirmModal.jobId;
			send({ type: 'cancel_apply', job_id: jobId });
			confirmModal = null;
			savePendingReviewIds(removePendingReviewId(loadPendingReviewIds(), jobId));
		}
	}

	/**
	 * Recover an in-flight apply-review after a WS reconnect or page reload.
	 *
	 * The single-modal UI shows at most one review at a time, so bail early if a
	 * modal is already open. For each persisted job id we re-fetch the engine
	 * snapshot; a 404/error means the review is gone, so we drop that id.
	 */
	async function recoverPendingReviews() {
		if (confirmModal) return;
		for (const id of loadPendingReviewIds()) {
			try {
				const payload = await apiFetch(`/api/applications/${id}/review-state`);
				const modal = reviewStateToModal(payload);
				// Re-check after the await: a live `apply_review` for another job may
				// have opened a modal while we were fetching — don't clobber it.
				if (modal && !confirmModal) {
					confirmModal = modal;
					return;
				}
			} catch (e: any) {
				// apiFetch throws `API error <status>: ...` for non-2xx and on network
				// errors. Only a confirmed 404 means the review is truly gone; drop
				// that id. Any other (possibly transient) error keeps the id so a
				// later reconnect retries.
				if (typeof e?.message === 'string' && e.message.includes('404')) {
					savePendingReviewIds(removePendingReviewId(loadPendingReviewIds(), id));
				}
			}
		}
	}

	/** Move keyboard focus to the previous visible card. */
	function focusPrev() {
		if (phase !== 'select' || matches.length === 0) return;
		focusedIndex = focusedIndex <= 0 ? matches.length - 1 : focusedIndex - 1;
	}

	/** Move keyboard focus to the next visible card. */
	function focusNext() {
		if (phase !== 'select' || matches.length === 0) return;
		focusedIndex = focusedIndex >= matches.length - 1 ? 0 : focusedIndex + 1;
	}

	/** Return the match.id of the currently focused card, or null. */
	function focusedMatchId(): number | null {
		if (focusedIndex < 0 || focusedIndex >= matches.length) return null;
		return matches[focusedIndex].id;
	}

	/** Set mode on focused card. */
	function setFocusedMode(mode: ApplyMode) {
		const id = focusedMatchId();
		if (id !== null) setMode(id, mode);
	}

	let unsubWsConnect: (() => void) | null = null;
	let unsubWsRecover: (() => void) | null = null;

	onMount(() => {
		loadQueue();
		syncBatchStatus();
		// Recover a paused review if the page was reloaded while the WS is open.
		recoverPendingReviews();
		// On WS reconnect (e.g. after page refresh), re-sync batch status
		unsubWsConnect = onWsConnect(syncBatchStatus);
		// ...and recover any in-flight apply-review that was awaiting confirmation.
		unsubWsRecover = onWsConnect(recoverPendingReviews);

		// NOTE: Queue page + CVReviewPanel both register at route '/'.
		// Each binding's action MUST self-guard on phase/panelPhase to avoid cross-firing.
		hotkeyHandle = register('/', {
			j:      { label: 'Move focus down',      action: focusNext },
			k:      { label: 'Move focus up',        action: focusPrev },
			a:      { label: 'Set focused card → Auto',   action: () => setFocusedMode('auto') },
			m:      { label: 'Set focused card → Manual', action: () => setFocusedMode('manual') },
			s:      { label: 'Set focused card → Skip',   action: () => setFocusedMode('skip') },
			Enter:  { label: 'Review & apply',        action: () => { if (activeMatches.length > 0 && phase === 'select') proceedToReview(); } },
			Escape: { label: 'Close modal / clear focus', action: () => {
				// Confirm-apply modal takes precedence when open.
				if (confirmModal) { cancelApply(); return; }
				if (phase === 'select') focusedIndex = -1;
			} }
		}, { group: 'Job Queue' });
	});

	onDestroy(() => {
		unsubWsConnect?.();
		unsubWsRecover?.();
		if (refreshTimeout) clearTimeout(refreshTimeout);
		if (hotkeyHandle) deregister(hotkeyHandle);
	});
</script>

<!-- Header -->
<div class="mb-5 flex items-center justify-between">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Job Queue</h1>
		<p class="text-muted-foreground mt-0.5 text-xs">
			{matches.length} pending · Scan to discover new opportunities
		</p>
	</div>
	<div class="flex items-center gap-2">
		<button
			onclick={previewMatches}
			disabled={previewing || refreshing}
			class="border-border hover:bg-accent flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
			title="Preview today's matches without saving anything"
		>
			<Eye size={13} class={previewing ? 'animate-pulse' : ''} />
			{previewing ? 'Previewing…' : "Preview today's matches"}
		</button>
		<button
			onclick={refreshQueue}
			disabled={refreshing}
			class="border-border hover:bg-accent flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition-colors disabled:opacity-50"
		>
			<RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
			{refreshing ? 'Scanning…' : 'Scan for Jobs'}
		</button>
	</div>
</div>

{#if error}
	<div
		class="mb-4 flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
	>
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{/if}

{#if previewError}
	<div
		class="mb-4 flex items-center gap-2 rounded-md border border-yellow-500/20 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-400"
	>
		<AlertCircle size={13} />{previewError}
		<button onclick={() => (previewError = '')} class="ml-auto hover:text-yellow-300">✕</button>
	</div>
{/if}

{#if preview !== null}
	<div class="border-border bg-card mb-4 rounded-lg border">
		<div class="border-border flex items-center justify-between border-b px-4 py-2.5">
			<div class="flex items-center gap-2">
				<Eye size={14} class="text-muted-foreground" />
				<span class="text-sm font-medium">Preview · {preview.length} match{preview.length === 1 ? '' : 'es'}</span>
				<span class="text-muted-foreground text-xs">— nothing saved</span>
			</div>
			<button
				onclick={dismissPreview}
				class="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
				aria-label="Dismiss preview"
			>
				<X size={14} />
			</button>
		</div>
		{#if preview.length === 0}
			<p class="text-muted-foreground px-4 py-6 text-center text-xs">
				No jobs would match today's settings.
			</p>
		{:else}
			<ul class="divide-border divide-y">
				{#each preview as item, idx (idx)}
					<li class="flex items-center gap-3 px-4 py-2.5">
						<span
							class="w-10 flex-shrink-0 text-center text-sm font-bold {item.score >= 80
								? 'text-green-400'
								: item.score >= 60
									? 'text-yellow-400'
									: 'text-red-400'}"
						>
							{Math.round(item.score)}%
						</span>
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium">{item.title}</p>
							<p class="text-muted-foreground truncate text-xs">
								{item.company}{item.location ? ` · ${item.location}` : ''}
							</p>
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
{/if}

{#if phase === 'review'}
	<CVReviewPanel matches={activeMatches} {modes} onback={backToSelect} oncomplete={onRunComplete} />
{:else if refreshing}
	<BatchPipelineTracker />
{:else if loading}
	<div class="flex flex-col items-center justify-center gap-4 py-20">
		<div class="relative">
			<div class="h-10 w-10 rounded-full border-2 border-primary/20 border-t-primary animate-spin"></div>
		</div>
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
		{#each matches as match, idx (match.id)}
			{@const mode = modes.get(match.id) ?? 'manual'}
			{@const isFocused = idx === focusedIndex}
			<div
				class="bg-card border-border flex items-center gap-4 rounded-lg border px-4 py-3 {mode ===
				'skip'
					? 'opacity-40'
					: ''} {isFocused ? 'ring-2 ring-primary ring-offset-1 ring-offset-background' : ''}"
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
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
		onclick={(e) => { if (e.target === e.currentTarget) cancelApply(); }}
	>
		<div
			class="bg-card border-border mx-4 w-full max-w-lg overflow-hidden rounded-xl border shadow-2xl"
			role="dialog"
			tabindex="-1"
			aria-modal="true"
			aria-labelledby="confirm-apply-title"
			aria-describedby="confirm-apply-desc"
			use:focusTrap
		>
			<div class="border-border border-b p-5">
				<h2 id="confirm-apply-title" class="font-semibold">Confirm Auto Apply</h2>
				<p id="confirm-apply-desc" class="text-muted-foreground mt-1 text-xs">Review the filled fields before submitting.</p>
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
						{#each Object.keys(confirmModal.fields) as k}
							<div class="flex items-center gap-2 text-xs">
								<dt class="text-muted-foreground w-28 flex-shrink-0">{k}</dt>
								<dd class="flex-1">
									<input
										bind:value={confirmModal.fields[k]}
										class="border-border bg-background focus:ring-ring w-full rounded border px-2 py-1 text-xs focus:outline-none focus:ring-1"
										aria-label={k}
									/>
								</dd>
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
