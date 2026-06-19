<script lang="ts">
	import { onMount } from 'svelte';
	import { Mail, RefreshCw, Unplug, AlertCircle, CheckCircle2 } from 'lucide-svelte';
	import {
		disconnect,
		fetchGmailStatus,
		forceSync,
		type GmailStatus
	} from '$lib/api/gmail';

	let status = $state<GmailStatus | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let loading = $state(true);

	async function refresh() {
		error = null;
		try {
			status = await fetchGmailStatus();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	onMount(refresh);

	function connect() {
		window.location.href = '/api/gmail/oauth/start';
	}

	async function doDisconnect() {
		if (!status?.email_address) return;
		if (!confirm(`Disconnect ${status.email_address}?`)) return;
		busy = true;
		error = null;
		try {
			await disconnect(status.email_address);
			await refresh();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	async function doSync() {
		busy = true;
		error = null;
		try {
			const { synced } = await forceSync();
			alert(`Synced ${synced} new message(s)`);
			await refresh();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<div class="bg-card/40 border border-border/50 rounded-2xl p-6 md:p-8 shadow-sm space-y-5">
	<div class="flex items-start justify-between gap-4">
		<div class="flex items-center gap-3">
			<div class="w-10 h-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
				<Mail size={20} />
			</div>
			<div>
				<h2 class="text-xl font-semibold font-heading">Gmail</h2>
				<p class="text-xs text-muted-foreground mt-0.5">
					Surface recruiter emails alongside your applications.
				</p>
			</div>
		</div>
		{#if !loading}
			{#if status?.connected}
				<span class="text-xs font-bold uppercase tracking-wider px-2.5 py-1 rounded-md bg-green-500/10 text-green-500 border border-green-500/20 inline-flex items-center gap-1.5 flex-shrink-0">
					<CheckCircle2 size={12} />
					Connected
				</span>
			{:else}
				<span class="text-xs font-bold uppercase tracking-wider px-2.5 py-1 rounded-md bg-muted/40 text-muted-foreground border border-border/50 inline-flex items-center gap-1.5 flex-shrink-0">
					Not connected
				</span>
			{/if}
		{/if}
	</div>

	{#if error}
		<div class="flex items-center gap-3 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">
			<AlertCircle size={16} class="flex-shrink-0" />
			<span class="flex-1 font-medium">{error}</span>
		</div>
	{/if}

	{#if loading}
		<div class="h-20 bg-card/50 border border-border/30 rounded-xl animate-pulse"></div>
	{:else if status?.connected}
		<div class="bg-background/40 border border-border/40 rounded-xl p-4 space-y-2">
			<div class="flex items-baseline gap-2">
				<span class="text-xs text-muted-foreground uppercase tracking-wider">Account</span>
				<strong class="text-sm font-medium text-foreground">{status.email_address}</strong>
			</div>
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
				<span>{status.message_count} message{status.message_count === 1 ? '' : 's'} cached</span>
				{#if status.last_synced_at}
					<span>
						Last sync: {new Date(status.last_synced_at).toLocaleString()}
					</span>
				{/if}
			</div>
		</div>

		<div class="flex flex-wrap gap-2 pt-2 border-t border-border/30">
			<button
				type="button"
				disabled={busy}
				onclick={doSync}
				class="flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm hover:shadow disabled:opacity-50 active:scale-[0.98]"
			>
				<RefreshCw size={14} class={busy ? 'animate-spin' : ''} />
				{busy ? 'Syncing...' : 'Sync now'}
			</button>
			<button
				type="button"
				disabled={busy}
				onclick={doDisconnect}
				class="flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-all disabled:opacity-50 active:scale-[0.98]"
			>
				<Unplug size={14} />
				Disconnect
			</button>
		</div>
	{:else}
		<div class="bg-background/40 border border-border/40 rounded-xl p-4 space-y-3">
			<p class="text-sm text-muted-foreground leading-relaxed">
				Connect your Gmail to surface recruiter emails alongside your applications.
				Read-only access (<code class="text-xs bg-muted/40 px-1.5 py-0.5 rounded">gmail.readonly</code>
				scope). Refresh tokens are encrypted at rest.
			</p>
		</div>
		<button
			type="button"
			onclick={connect}
			class="flex items-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm hover:shadow active:scale-[0.98]"
		>
			<Mail size={16} />
			Connect Gmail
		</button>
	{/if}
</div>
