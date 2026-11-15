<script lang="ts">
	import { apiFetch } from '$lib/api';

	// The dedicated JobPilot Chrome: opened here, logged into by hand, once per site.
	let browser = $state<{ running: boolean; logged_in: Record<string, boolean> } | null>(null);
	let busy = $state(false);
	let error = $state('');

	async function call(method: 'GET' | 'POST') {
		busy = true;
		error = '';
		try {
			browser = await apiFetch(method === 'POST' ? '/api/browser/open' : '/api/browser/status', {
				method
			});
		} catch (e: any) {
			error = e.message ?? 'Could not reach the JobPilot browser';
		} finally {
			busy = false;
		}
	}
</script>

<div class="space-y-4">
	<p class="text-sm text-muted-foreground">
		JobPilot uses its own Chrome window, separate from your everyday browser: your applications
		browser. Open it and sign in to Google (Gmail) and LinkedIn there, plus any job site you use.
		You do this once; the logins are kept. JobPilot uses the Google login for "Sign in with Google"
		on job sites and to read their verification emails.
	</p>
	<div class="flex items-center gap-2">
		<button
			onclick={() => call('POST')}
			disabled={busy}
			class="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
		>
			Open the JobPilot browser
		</button>
		<button
			onclick={() => call('GET')}
			disabled={busy}
			class="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
		>
			Check logins
		</button>
	</div>
	{#if error}
		<p class="text-xs text-red-400">{error}</p>
	{/if}
	{#if browser}
		<div class="p-3 bg-muted/40 border border-border rounded-lg text-xs space-y-1">
			<p>Browser: {browser.running ? 'running' : 'not running'}</p>
			{#each Object.entries(browser.logged_in) as [site, ok]}
				<p class={ok ? 'text-green-400' : 'text-muted-foreground'}>
					{site}: {ok ? 'logged in' : 'not logged in yet'}
				</p>
			{/each}
		</div>
	{/if}
</div>
