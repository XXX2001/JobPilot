<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { apiFetch } from '$lib/api';
	import { RefreshCw, AlertCircle } from 'lucide-svelte';
	import NewMatchesFeed from '$lib/components/NewMatchesFeed.svelte';
	import BlockedActionsStrip from '$lib/components/BlockedActionsStrip.svelte';
	import WeekStats from '$lib/components/WeekStats.svelte';
	import SourceHealthPills from '$lib/components/SourceHealthPills.svelte';
	import type { TodayResponse } from '$lib/types/today';
	import type { SetupStatus } from '$lib/types/api';
	import { ONBOARDING_DISMISSED_KEY, shouldAutoRedirect } from '$lib/utils/onboarding';

	let data = $state<TodayResponse | null>(null);
	let loading = $state(true);
	let error = $state('');
	let refreshing = $state(false);

	/**
	 * First-run gate: if setup is incomplete, send the user to `/onboarding`.
	 *
	 * To avoid a redirect loop (the onboarding page never redirects back) and to
	 * respect "do this later", we only auto-redirect once per session — guarded by
	 * a `sessionStorage` flag set the moment we redirect. Returns true when a
	 * redirect was triggered so the caller can skip loading the dashboard.
	 */
	async function maybeRedirectToOnboarding(): Promise<boolean> {
		let dismissed = false;
		try {
			dismissed = sessionStorage.getItem(ONBOARDING_DISMISSED_KEY) === 'true';
		} catch {
			// sessionStorage unavailable (private mode) — treat as not dismissed.
		}
		if (dismissed) return false;

		try {
			const status = await apiFetch<SetupStatus>('/api/settings/status');
			if (shouldAutoRedirect(status, dismissed)) {
				try {
					sessionStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
				} catch {
					// best-effort; the onboarding page itself does not redirect, so no loop.
				}
				await goto('/onboarding');
				return true;
			}
		} catch {
			// Status check is best-effort: never block the dashboard on it.
		}
		return false;
	}

	async function load() {
		try {
			if (await maybeRedirectToOnboarding()) return;
			data = await apiFetch<TodayResponse>('/api/today');
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Failed to load today dashboard';
		} finally {
			loading = false;
		}
	}

	async function refresh() {
		if (refreshing) return;
		refreshing = true;
		error = '';
		try {
			data = await apiFetch<TodayResponse>('/api/today');
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Refresh failed';
		} finally {
			refreshing = false;
		}
	}

	onMount(() => {
		load();
	});
</script>

<!-- Header -->
<div class="mb-6 flex items-end justify-between">
	<div class="animate-fade-in-up">
		<h1 class="font-display text-3xl font-semibold tracking-tight">Today</h1>
		<p class="text-muted-foreground mt-1 text-sm">Your job search, at a glance.</p>
	</div>
	<div class="flex items-center gap-2">
		<a
			href="/queue"
			class="hover:bg-accent/60 flex items-center gap-2 rounded-lg border border-border/70 px-3 py-1.5 text-xs transition-colors"
		>
			Classic queue →
		</a>
		<button
			onclick={refresh}
			disabled={refreshing || loading}
			class="glass hover:shadow-aurora flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs transition-all disabled:opacity-50"
		>
			<RefreshCw size={13} class={refreshing ? 'animate-spin' : ''} />
			{refreshing ? 'Refreshing…' : 'Refresh'}
		</button>
	</div>
</div>

<SourceHealthPills />

{#if error}
	<div
		class="mb-4 flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
	>
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{/if}

{#if loading}
	<div class="flex flex-col items-center justify-center gap-4 py-24">
		<div class="aurora-spinner"></div>
		<p class="text-muted-foreground text-xs">Loading your flight deck…</p>
	</div>
{:else if data}
	<div class="max-w-3xl space-y-8">
		<!-- What needs attention -->
		{#if data.blocked_actions.actions.length > 0}
			<div class="animate-fade-in-up" style="animation-delay:60ms">
				<BlockedActionsStrip actions={data.blocked_actions.actions} />
			</div>
			<hr class="border-border/60" />
		{/if}

		<!-- What's new since last visit -->
		<div class="animate-fade-in-up" style="animation-delay:120ms">
			<NewMatchesFeed
				highConfidence={data.new_matches.high_confidence}
				worthReviewing={data.new_matches.worth_reviewing}
				skipped={data.new_matches.skipped}
				total={data.new_matches.total}
				since={data.new_matches.since}
			/>
		</div>

		<hr class="border-border/60" />

		<!-- How am I doing this week -->
		<div class="animate-fade-in-up" style="animation-delay:180ms">
			<WeekStats stats={data.week_stats} />
		</div>
	</div>
{/if}

<style>
	/* Aurora conic spinner — duotone ring masked into a thin stroke. */
	.aurora-spinner {
		width: 2.75rem;
		height: 2.75rem;
		border-radius: 9999px;
		background: conic-gradient(
			from 0deg,
			hsl(var(--aurora-from)),
			hsl(var(--aurora-to)),
			hsl(var(--aurora-from))
		);
		-webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px));
		mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px));
		animation: spin 0.9s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(1turn);
		}
	}
</style>
