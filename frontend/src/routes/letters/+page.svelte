<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { Mail, FileText, CheckCircle2, AlertCircle, RefreshCw, Clock } from 'lucide-svelte';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
	import type { Document, LetterRegenerateResponse } from '$lib/types/api';

	let documents = $state<Document[]>([]);
	let docsLoading = $state(true);
	let error = $state('');
	let successMsg = $state('');
	let currentLetterPath = $state('');
	let profileLoading = $state(true);

	// Currently previewed match + cache-busting timestamp for the iframe.
	let selectedMatchId = $state<number | null>(null);
	let ts = $state(Date.now());
	let regeneratingId = $state<number | null>(null);

	async function loadProfile() {
		profileLoading = true;
		try {
			const p = await apiFetch<{ base_letter_path?: string }>('/api/settings/profile');
			currentLetterPath = p.base_letter_path ?? '';
		} catch {
			//
		} finally {
			profileLoading = false;
		}
	}

	async function loadDocs() {
		docsLoading = true;
		error = '';
		try {
			const docs = await apiFetch<Document[]>('/api/documents');
			documents = docs.filter((d) => d.doc_type === 'letter');
			if (selectedMatchId === null && documents.length > 0) {
				selectedMatchId = documents[0].job_match_id ?? null;
			}
		} catch (e: any) {
			error = e.message ?? 'Failed to load cover letters';
		} finally {
			docsLoading = false;
		}
	}

	function selectMatch(matchId: number | null | undefined) {
		if (matchId == null) return;
		selectedMatchId = matchId;
		ts = Date.now();
	}

	async function regenerate(matchId: number | null | undefined) {
		if (matchId == null) return;
		regeneratingId = matchId;
		error = '';
		successMsg = '';
		try {
			const result = await apiFetch<LetterRegenerateResponse>(
				`/api/documents/${matchId}/letter/regenerate`,
				{ method: 'POST' }
			);
			successMsg = `Letter regenerated for match #${result.match_id}`;
			selectedMatchId = matchId;
			// Bump the timestamp to force the iframe to reload the fresh PDF.
			ts = Date.now();
		} catch (e: any) {
			error = e.message ?? 'Regeneration failed';
		} finally {
			regeneratingId = null;
		}
	}

	const timeAgo = (dateStr: string) => {
		const d = new Date(dateStr);
		const now = new Date();
		const days = Math.floor((now.getTime() - d.getTime()) / 86400000);
		if (days === 0) return 'today';
		if (days === 1) return 'yesterday';
		return `${days}d ago`;
	};

	onMount(() => {
		loadProfile();
		loadDocs();
	});
</script>

<!-- Header -->
<div class="mb-6">
	<h1 class="text-xl font-semibold tracking-tight">Cover Letters</h1>
	<p class="text-xs text-muted-foreground mt-0.5">Preview and regenerate tailored cover letters for your matches.</p>
</div>

<!-- Messages -->
{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto">✕</button>
	</div>
{/if}
{#if successMsg}
	<div class="flex items-center gap-2 text-xs text-green-400 bg-green-500/10 border border-green-500/20 rounded-md px-3 py-2 mb-4">
		<CheckCircle2 size={13} />{successMsg}
		<button onclick={() => (successMsg = '')} class="ml-auto">✕</button>
	</div>
{/if}

<div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
	<!-- Left: Template + match list -->
	<div class="space-y-4">
		<h2 class="text-sm font-medium">Base Letter Template</h2>

		<!-- Current letter template info (read-only) -->
		{#if profileLoading}
			<div class="h-10 bg-muted rounded-lg animate-pulse"></div>
		{:else if currentLetterPath}
			<div class="flex items-center gap-2 p-3 bg-card border border-border rounded-lg text-xs">
				<FileText size={14} class="text-primary flex-shrink-0" />
				<span class="flex-1 truncate text-muted-foreground">{currentLetterPath}</span>
				<CheckCircle2 size={13} class="text-green-500 flex-shrink-0" />
			</div>
		{:else}
			<div class="flex items-center gap-2 p-3 bg-card border border-border rounded-lg text-xs text-muted-foreground">
				<AlertCircle size={14} class="flex-shrink-0" />
				<span>No base letter template configured.</span>
			</div>
		{/if}

		<h2 class="text-sm font-medium pt-2">Tailored Letter History</h2>

		{#if docsLoading}
			<div class="space-y-2 animate-pulse">
				{#each Array(4) as _}<div class="h-14 bg-muted rounded-lg"></div>{/each}
			</div>
		{:else if documents.length === 0}
			<div class="flex flex-col items-center justify-center py-12 gap-3 bg-card border border-border rounded-lg">
				<FloatingEmoji emoji="✉️" size="sm" />
				<p class="text-sm text-muted-foreground font-medium">No cover letters yet.</p>
				<p class="text-xs text-muted-foreground">Letters are generated during the job scan when jobs are matched.</p>
			</div>
		{:else}
			<div class="space-y-2">
				{#each documents as doc (doc.id)}
					{@const isSelected = doc.job_match_id != null && doc.job_match_id === selectedMatchId}
					<div
						class="flex items-center gap-3 p-3 bg-card border rounded-lg transition-colors {isSelected
							? 'border-primary'
							: 'border-border'}"
					>
						<Mail size={14} class="text-muted-foreground flex-shrink-0" />
						<button
							type="button"
							onclick={() => selectMatch(doc.job_match_id)}
							class="flex-1 min-w-0 text-left"
						>
							<p class="text-xs font-medium">Match #{doc.job_match_id ?? '—'}</p>
							<div class="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
								<Clock size={10} />
								{timeAgo(doc.created_at)}
							</div>
						</button>
						<button
							type="button"
							onclick={() => regenerate(doc.job_match_id)}
							disabled={regeneratingId === doc.job_match_id}
							class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
							title="Regenerate letter"
						>
							<RefreshCw size={13} class={regeneratingId === doc.job_match_id ? 'animate-spin' : ''} />
						</button>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Right: Inline PDF preview -->
	<div class="space-y-4">
		<h2 class="text-sm font-medium">Preview</h2>

		{#if selectedMatchId != null}
			<div class="border border-border rounded-lg overflow-hidden bg-card">
				<iframe
					src={`/api/documents/${selectedMatchId}/letter/pdf?t=${ts}`}
					class="w-full border-0"
					style="height: calc(100vh - 220px); min-height: 480px"
					title="Cover letter PDF preview"
				></iframe>
			</div>
		{:else}
			<div class="flex flex-col items-center justify-center py-12 gap-3 bg-card border border-border rounded-lg">
				<FloatingEmoji emoji="📄" size="sm" />
				<p class="text-sm text-muted-foreground font-medium">Select a match to preview its letter.</p>
			</div>
		{/if}
	</div>
</div>
