<script lang="ts">
	import type { BlockedAction } from '$lib/types/today';
	import { AlertTriangle, WifiOff, Clock, Send } from 'lucide-svelte';

	interface Props {
		actions: BlockedAction[];
	}

	let { actions }: Props = $props();

	function icon(kind: string) {
		switch (kind) {
			case 'broken_session':
				return WifiOff;
			case 'pending_application':
				return Send;
			case 'stale_manual':
				return Clock;
			default:
				return AlertTriangle;
		}
	}

	function chipColor(kind: string): string {
		switch (kind) {
			case 'broken_session':
				return 'border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20';
			case 'pending_application':
				return 'border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20';
			case 'stale_manual':
				return 'border-yellow-500/30 bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/20';
			default:
				return 'border-border bg-muted text-muted-foreground hover:bg-accent';
		}
	}
</script>

<section>
	<div class="mb-3">
		<h2 class="text-sm font-semibold tracking-tight">Needs attention</h2>
	</div>

	{#if actions.length === 0}
		<p class="text-xs text-muted-foreground py-3">
			Nothing blocking — you're all caught up.
		</p>
	{:else}
		<div class="flex flex-wrap gap-2">
			{#each actions as action (action.kind)}
				{@const Icon = icon(action.kind)}
				<a
					href={action.href}
					class="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors {chipColor(action.kind)}"
				>
					<Icon size={12} />
					{action.label}
				</a>
			{/each}
		</div>
	{/if}
</section>
