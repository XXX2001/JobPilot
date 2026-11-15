<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import type { BrowserStatus, SetupStatus } from '$lib/types/api';

	// "Am I ready to apply?" at a glance: each item is done, or names the one thing to do.
	type Item = { label: string; ok: boolean; hint: string; href?: string };
	let items = $state<Item[]>([]);
	let busy = $state(true);
	let error = $state('');
	let signedIn = $state(false);

	async function load() {
		const [browser, monitor, setup] = await Promise.all([
			apiFetch<BrowserStatus>('/api/browser/status'),
			apiFetch<{ enabled: boolean; sources: unknown[] }>('/api/monitor/status'),
			apiFetch<SetupStatus>('/api/settings/status')
		]);
		signedIn = browser.running && !!browser.logged_in.google && !!browser.logged_in.linkedin;
		items = [
			...(browser.running
				? [
						{
							label: 'Google (Gmail)',
							ok: !!browser.logged_in.google,
							hint: 'Google (Gmail): not signed in'
						},
						{
							label: 'LinkedIn',
							ok: !!browser.logged_in.linkedin,
							hint: 'LinkedIn: not signed in'
						}
					]
				: [{ label: 'JobPilot Chrome', ok: false, hint: "JobPilot's Chrome is not running" }]),
			{
				label: `Watching ${monitor.sources.length} sources`,
				ok: monitor.enabled && monitor.sources.length > 0,
				hint: monitor.enabled ? 'Add a source' : 'Monitor is off',
				href: '/settings'
			},
			{
				label: 'AI provider',
				ok: setup.llm_key_set,
				hint: 'Log in to Claude Code or Codex',
				href: '/settings'
			},
			{ label: 'CV', ok: setup.base_cv_uploaded, hint: 'Upload your CV', href: '/cv' }
		];
	}

	async function refresh(open = false) {
		busy = true;
		error = '';
		try {
			if (open) await apiFetch('/api/browser/open', { method: 'POST' });
			await load();
		} catch {
			error = 'Could not refresh setup status. Try again.';
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		refresh();
	});
</script>

{#if busy && !items.length}
	<p role="status" class="text-muted-foreground mb-6 text-xs">Checking…</p>
{:else if items.length || error}
	<div class="mb-6 flex flex-wrap gap-2" aria-label="Setup status">
		{#each items as item}
			{#if item.ok}
				<span
					class="rounded-full border border-green-500/30 bg-green-500/10 px-3 py-1 text-xs text-green-400"
				>
					✓ {item.label}
				</span>
			{:else if !item.href}
				<span
					class="rounded-full border border-yellow-500/40 bg-yellow-500/10 px-3 py-1 text-xs text-yellow-400"
				>
					{item.hint}
				</span>
			{:else}
				<a
					href={item.href}
					class="rounded-full border border-yellow-500/40 bg-yellow-500/10 px-3 py-1 text-xs text-yellow-400 hover:bg-yellow-500/20"
				>
					{item.hint} →
				</a>
			{/if}
		{/each}
		{#if !signedIn}
			<button
				onclick={() => refresh(true)}
				disabled={busy}
				class="border-border hover:bg-accent rounded-full border px-3 py-1 text-xs disabled:opacity-50"
			>
				Open sign-in pages
			</button>
			<button
				onclick={() => refresh()}
				disabled={busy}
				class="border-border hover:bg-accent rounded-full border px-3 py-1 text-xs disabled:opacity-50"
			>
				Refresh
			</button>
		{/if}
		{#if error}
			<p role="alert" class="w-full text-xs text-red-400">{error}</p>
		{/if}
	</div>
{/if}
