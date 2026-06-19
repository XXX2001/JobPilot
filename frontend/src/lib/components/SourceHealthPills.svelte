<!--
  SourceHealthPills — tiny per-source status indicator for the queue page.

  Reads GET /api/queue/source-health (in-memory tracker on the orchestrator).
  Renders one pill per source the user has scraped at least once since the
  process started.

  Polled lazily: refresh on mount + when `refreshKey` changes (e.g. when a
  scan completes). Stays silent when the tracker is empty (no scrapes yet).
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { CheckCircle2, AlertTriangle, XCircle, HelpCircle } from 'lucide-svelte';

	interface SourceHealth {
		source: string;
		status: 'healthy' | 'degraded' | 'down' | 'unknown';
		last_outcome: 'ok' | 'empty' | 'error' | null;
		last_attempt_at: string | null;
		last_success_at: string | null;
		consecutive_failures: number;
		last_error: string | null;
		last_job_count: number;
		total_attempts: number;
		total_jobs: number;
		history: ('ok' | 'empty' | 'error')[];
	}

	let { refreshKey = 0 }: { refreshKey?: number } = $props();

	let sources = $state<SourceHealth[]>([]);

	async function load() {
		try {
			const data = await apiFetch<{ sources: SourceHealth[] }>(
				'/api/queue/source-health'
			);
			sources = data.sources ?? [];
		} catch {
			// Endpoint may be 503 in dev — silently ignore.
		}
	}

	onMount(load);

	// Refresh whenever the parent bumps refreshKey (e.g. after a scan).
	$effect(() => {
		// touch refreshKey
		refreshKey;
		load();
	});

	function pillClass(status: SourceHealth['status']): string {
		switch (status) {
			case 'healthy':
				return 'border-green-500/30 bg-green-500/10 text-green-400';
			case 'degraded':
				return 'border-yellow-500/30 bg-yellow-500/10 text-yellow-400';
			case 'down':
				return 'border-red-500/30 bg-red-500/10 text-red-400';
			default:
				return 'border-border bg-card text-muted-foreground';
		}
	}

	function statusIcon(status: SourceHealth['status']) {
		switch (status) {
			case 'healthy':
				return CheckCircle2;
			case 'degraded':
				return AlertTriangle;
			case 'down':
				return XCircle;
			default:
				return HelpCircle;
		}
	}

	function shortName(s: string): string {
		// "welcome_to_the_jungle" → "WTTJ"; "google_jobs" → "Google Jobs"; otherwise titlecase.
		if (s === 'welcome_to_the_jungle') return 'WTTJ';
		if (s === 'google_jobs') return 'Google Jobs';
		return s.charAt(0).toUpperCase() + s.slice(1);
	}

	function tooltipFor(s: SourceHealth): string {
		const parts: string[] = [];
		parts.push(`Last: ${s.last_outcome ?? 'never'} (${s.last_job_count} jobs)`);
		if (s.consecutive_failures > 0) {
			parts.push(`${s.consecutive_failures} consecutive failure${s.consecutive_failures > 1 ? 's' : ''}`);
		}
		if (s.last_error) {
			parts.push(`Error: ${s.last_error}`);
		}
		if (s.total_attempts > 0) {
			parts.push(`${s.total_jobs} jobs across ${s.total_attempts} attempts`);
		}
		return parts.join(' · ');
	}
</script>

{#if sources.length > 0}
	<div class="mb-3 flex flex-wrap items-center gap-1.5">
		<span class="text-muted-foreground text-[10px] uppercase tracking-wide">Sources</span>
		{#each sources as src (src.source)}
			{@const Icon = statusIcon(src.status)}
			<span
				class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium {pillClass(src.status)}"
				title={tooltipFor(src)}
			>
				<Icon size={10} />
				{shortName(src.source)}
				{#if src.last_job_count > 0}
					<span class="opacity-70">·&nbsp;{src.last_job_count}</span>
				{/if}
			</span>
		{/each}
	</div>
{/if}
