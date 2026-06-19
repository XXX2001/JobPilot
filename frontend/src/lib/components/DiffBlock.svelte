<script lang="ts">
	import { wordDiff, type DiffSpan } from '$lib/utils/wordDiff';

	interface Props {
		section: string;
		originalText: string;
		editedText: string;
		reason: string;
	}

	let { section, originalText, editedText, reason }: Props = $props();

	let spans: DiffSpan[] = $derived(wordDiff(originalText, editedText));
</script>

<div class="border-border overflow-hidden rounded-lg border">
	<!-- Section header -->
	<div class="border-border bg-muted/30 border-b px-3 py-1.5">
		<span class="text-xs font-medium">{section}</span>
		{#if reason}
			<span class="text-muted-foreground ml-2 text-xs">— {reason}</span>
		{/if}
	</div>

	<!-- Inline word-level diff -->
	<div class="p-3 font-mono text-xs leading-relaxed">
		{#each spans as span}
			{#if span.type === 'removed'}
				<span class="bg-red-500/20 text-red-400 line-through">{span.text}</span>
			{:else if span.type === 'added'}
				<span class="bg-green-500/20 text-green-300">{span.text}</span>
			{:else}
				<span class="text-zinc-300">{span.text}</span>
			{/if}
			{' '}
		{/each}
	</div>
</div>
