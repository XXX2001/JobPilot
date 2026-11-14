<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import BrowserLogins from '$lib/components/BrowserLogins.svelte';
	import { Pause, Play, RefreshCw } from 'lucide-svelte';

	interface MonitorSource {
		id: number;
		name: string;
		adapter: string;
		enabled: boolean;
		interval_s: number;
		last_scraped_at: string | null;
		next_poll_at: string | null;
		fail_count: number;
		last_error: string | null;
		jobs_seen: number;
		median_lag_s: number | null;
	}

	interface MonitorStatus {
		enabled: boolean;
		sources: MonitorSource[];
	}

	let { error = $bindable('') }: { error: string } = $props();

	let status = $state<MonitorStatus | null>(null);
	let loading = $state(true);
	let busy = $state<Record<number, boolean>>({});

	async function load() {
		try {
			status = await apiFetch<MonitorStatus>('/api/monitor/status');
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load monitor status';
		} finally {
			loading = false;
		}
	}

	async function toggle(source: MonitorSource) {
		busy[source.id] = true;
		try {
			const action = source.enabled ? 'pause' : 'resume';
			const updated = await apiFetch<MonitorSource>(
				`/api/monitor/sources/${source.id}/${action}`,
				{ method: 'POST' }
			);
			if (status) {
				status.sources = status.sources.map((s) => (s.id === updated.id ? updated : s));
			}
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to update the source';
		} finally {
			busy[source.id] = false;
		}
	}

	function duration(seconds: number | null): string {
		if (seconds === null) return '—';
		if (seconds < 90) return `${Math.round(seconds)} s`;
		if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
		if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
		return `${Math.round(seconds / 86400)} d`;
	}

	function time(iso: string | null): string {
		return iso ? new Date(iso).toLocaleString() : '—';
	}

	onMount(load);
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

<div class="mb-6 p-4 bg-card/50 border border-border/30 rounded-xl">
	<h2 class="text-base font-semibold font-heading mb-2">JobPilot browser</h2>
	<BrowserLogins />
</div>

{#if loading}
	<div class="space-y-3 animate-pulse">
		<div class="h-64 bg-card/50 border border-border/30 rounded-xl"></div>
	</div>
{:else if status}
	<div class="space-y-4">
		<div class="flex items-start justify-between gap-4">
			<div>
				<h2 class="text-xl font-semibold font-heading mb-1">Monitor</h2>
				<p class="text-sm text-muted-foreground">
					Sources polled on their own interval. Polling is
					<span class="font-semibold {status.enabled ? 'text-green-500' : 'text-yellow-500'}">
						{status.enabled ? 'on' : 'off (set MONITOR_ENABLED=true in .env)'}
					</span>.
				</p>
			</div>
			<button
				type="button"
				onclick={load}
				aria-label="Refresh monitor status"
				class="p-2 rounded-lg border border-border/50 text-muted-foreground hover:text-foreground hover:bg-muted/20 transition-colors"
			>
				<RefreshCw size={16} />
			</button>
		</div>

		{#if status.sources.length === 0}
			<p class="text-sm text-muted-foreground p-5 bg-card/30 border border-border/50 rounded-2xl">
				No monitor source yet. Import a watchlist with <code>scripts/import_watchlist.py</code>.
			</p>
		{:else}
			<div class="bg-card/30 border border-border/50 rounded-2xl overflow-x-auto shadow-sm">
				<table class="w-full text-sm">
					<thead class="text-xs uppercase tracking-wider text-muted-foreground text-left">
						<tr class="border-b border-border/40">
							<th class="p-3 font-medium">Source</th>
							<th class="p-3 font-medium">Every</th>
							<th class="p-3 font-medium">Last poll</th>
							<th class="p-3 font-medium">Next poll</th>
							<th class="p-3 font-medium">Jobs</th>
							<th class="p-3 font-medium">Median lag</th>
							<th class="p-3 font-medium">Health</th>
							<th class="p-3 font-medium"><span class="sr-only">Pause or resume</span></th>
						</tr>
					</thead>
					<tbody class="divide-y divide-border/40">
						{#each status.sources as source (source.id)}
							<tr class="hover:bg-muted/10 transition-colors {source.enabled ? '' : 'opacity-60'}">
								<td class="p-3">
									<p class="font-medium text-foreground">{source.name}</p>
									<p class="text-xs text-muted-foreground">{source.adapter}</p>
								</td>
								<td class="p-3 whitespace-nowrap">{duration(source.interval_s)}</td>
								<td class="p-3 whitespace-nowrap">{time(source.last_scraped_at)}</td>
								<td class="p-3 whitespace-nowrap">{source.enabled ? time(source.next_poll_at) : 'paused'}</td>
								<td class="p-3">{source.jobs_seen}</td>
								<td class="p-3 whitespace-nowrap">{duration(source.median_lag_s)}</td>
								<td class="p-3 max-w-xs">
									{#if source.fail_count > 0 || source.last_error}
										<p class="text-xs text-yellow-500 break-words">
											{source.fail_count} failed · {source.last_error ?? 'unknown error'}
										</p>
									{:else}
										<span class="text-xs text-green-500">ok</span>
									{/if}
								</td>
								<td class="p-3 text-right">
									<button
										type="button"
										onclick={() => toggle(source)}
										disabled={busy[source.id]}
										aria-label="{source.enabled ? 'Pause' : 'Resume'} {source.name}"
										class="p-2 rounded-lg border border-border/50 text-muted-foreground hover:text-foreground hover:bg-muted/20 transition-colors disabled:opacity-50"
									>
										{#if source.enabled}<Pause size={14} />{:else}<Play size={14} />{/if}
									</button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
{/if}
