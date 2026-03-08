<script lang="ts">
	import { messages, wsStatus } from '$lib/stores/websocket';
	import { derived } from 'svelte/store';

	// Show the most recent meaningful status message
	const lastMessage = derived(messages, ($msgs) => {
		if ($msgs.length === 0) return null;
		return $msgs[$msgs.length - 1];
	});
</script>

<div
	class="border-t border-border px-4 py-1.5 flex items-center gap-3 text-xs text-muted-foreground bg-background/80 backdrop-blur-sm"
>
	{#if $wsStatus === 'connected'}
		<span class="inline-block w-1.5 h-1.5 rounded-full bg-green-500"></span>
	{:else if $wsStatus === 'reconnecting'}
		<span class="inline-block w-1.5 h-1.5 rounded-full bg-yellow-500 animate-pulse"></span>
	{:else}
		<span class="inline-block w-1.5 h-1.5 rounded-full bg-red-500"></span>
	{/if}

	{#if $lastMessage}
		{#if $lastMessage.type === 'scraping_progress'}
			<span>Scraping: {$lastMessage.data?.source ?? ''} — {$lastMessage.data?.found ?? 0} jobs found</span>
		{:else if $lastMessage.type === 'matching_progress'}
			<span>Matching: {$lastMessage.data?.matched ?? 0} matched</span>
		{:else if $lastMessage.type === 'tailoring_progress'}
			<span>Tailoring CV for {$lastMessage.data?.company ?? 'job'}…</span>
		{:else if $lastMessage.type === 'login_required'}
			<span class="text-yellow-400">Waiting for manual login: {$lastMessage.site}</span>
		{:else if $lastMessage.type === 'error'}
			<span class="text-red-400">Error: {$lastMessage.data?.message ?? 'Unknown error'}</span>
		{:else if $lastMessage.type === 'status'}
			<span class="flex-1 truncate">{$lastMessage.message}</span>
			{#if $lastMessage.progress > 0 && $lastMessage.progress < 1}
				<div class="w-32 h-1 bg-muted rounded-full overflow-hidden">
					<div
						class="h-full bg-primary transition-all duration-300"
						style="width: {$lastMessage.progress * 100}%"
					></div>
				</div>
				<span class="tabular-nums">{Math.round($lastMessage.progress * 100)}%</span>
			{/if}
		{:else}
			<span>{$wsStatus === 'connected' ? 'Ready' : 'Offline'}</span>
		{/if}
	{:else}
		<span>{$wsStatus === 'connected' ? 'Ready' : 'Offline'}</span>
	{/if}
</div>
