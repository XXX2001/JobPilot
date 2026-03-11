<script lang="ts">
	import { onMount } from 'svelte';

	let {
		message,
		emoji = '🎉',
		type = 'info',
		duration = 5000,
		onclose
	}: {
		message: string;
		emoji?: string;
		type?: 'milestone' | 'info' | 'celebration';
		duration?: number;
		onclose?: () => void;
	} = $props();

	let visible = $state(true);
	let exiting = $state(false);

	function dismiss() {
		exiting = true;
		setTimeout(() => {
			visible = false;
			onclose?.();
		}, 300);
	}

	onMount(() => {
		const timer = setTimeout(dismiss, duration);
		return () => clearTimeout(timer);
	});
</script>

{#if visible}
	<div
		class="fixed top-4 right-4 z-[100] max-w-sm rounded-lg border px-4 py-3 shadow-lg backdrop-blur-sm
			{exiting ? 'animate-fade-out-down' : 'animate-fade-in-up'}
			{type === 'celebration'
			? 'border-yellow-500/30 bg-yellow-500/10 animate-glow-pulse'
			: type === 'milestone'
				? 'border-amber-500/20 bg-amber-500/5'
				: 'border-border bg-card/95'}"
		role="status"
	>
		<div class="flex items-start gap-3">
			<span
				class="text-xl flex-shrink-0
					{type === 'celebration' ? 'animate-confetti-pop' : ''}"
			>
				{emoji}
			</span>
			<div class="flex-1 min-w-0">
				<p
					class="text-sm font-medium leading-snug
						{type === 'celebration'
						? 'bg-gradient-to-r from-yellow-300 via-amber-200 to-yellow-300 bg-clip-text text-transparent bg-[length:200%_auto] animate-shimmer'
						: type === 'milestone'
							? 'text-amber-200'
							: 'text-foreground'}"
				>
					{message}
				</p>
			</div>
			<button
				onclick={dismiss}
				class="text-muted-foreground hover:text-foreground text-xs flex-shrink-0 mt-0.5 transition-colors"
			>
				✕
			</button>
		</div>
	</div>
{/if}
