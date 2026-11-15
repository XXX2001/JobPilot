<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import type { SetupStatus } from '$lib/types/api';

	// "Am I ready to apply?" at a glance: each item is done, or names the one thing to do.
	type Item = { label: string; ok: boolean; hint: string; action?: () => void; href?: string };
	let items = $state<Item[]>([]);
	let busy = $state(false);

	async function load() {
		const [browser, monitor, setup] = await Promise.all([
			apiFetch<{ running: boolean; logged_in: Record<string, boolean> }>('/api/browser/status'),
			apiFetch<{ enabled: boolean; sources: unknown[] }>('/api/monitor/status'),
			apiFetch<SetupStatus>('/api/settings/status')
		]);
		items = [
			{
				label: 'Job-site logins',
				ok: browser.running && Object.values(browser.logged_in).some(Boolean),
				hint: browser.running ? 'Log into LinkedIn in this window' : 'Open the JobPilot browser',
				action: openBrowser
			},
			{
				label: `Watching ${monitor.sources.length} sources`,
				ok: monitor.enabled && monitor.sources.length > 0,
				hint: monitor.enabled ? 'Add a source' : 'Monitor is off',
				href: '/settings'
			},
			{ label: 'AI provider', ok: setup.llm_key_set, hint: 'Log in to Claude Code or Codex', href: '/settings' },
			{ label: 'CV', ok: setup.base_cv_uploaded, hint: 'Upload your CV', href: '/cv' }
		];
	}

	async function openBrowser() {
		busy = true;
		try {
			await apiFetch('/api/browser/open', { method: 'POST' });
			await load();
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		load().catch(() => (items = []));
	});
</script>

{#if items.length}
	<div class="mb-6 flex flex-wrap gap-2" aria-label="Setup status">
		{#each items as item}
			{#if item.ok}
				<span class="rounded-full border border-green-500/30 bg-green-500/10 px-3 py-1 text-xs text-green-400">
					✓ {item.label}
				</span>
			{:else if item.action}
				<button
					onclick={item.action}
					disabled={busy}
					class="rounded-full border border-yellow-500/40 bg-yellow-500/10 px-3 py-1 text-xs text-yellow-400 hover:bg-yellow-500/20 disabled:opacity-50"
				>
					{item.hint} →
				</button>
			{:else}
				<a
					href={item.href}
					class="rounded-full border border-yellow-500/40 bg-yellow-500/10 px-3 py-1 text-xs text-yellow-400 hover:bg-yellow-500/20"
				>
					{item.hint} →
				</a>
			{/if}
		{/each}
	</div>
{/if}
