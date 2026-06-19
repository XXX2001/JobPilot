<script lang="ts">
	import { page } from '$app/stores';
	import { getErrorMessage } from '$lib/utils/easterEggs';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';

	const errorMsg = getErrorMessage();
</script>

<div class="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center px-4">
	<FloatingEmoji emoji="🗺️" size="lg" />

	<div class="space-y-2">
		<h1 class="text-6xl font-bold text-foreground/20 animate-gentle-bounce">
			{$page.status}
		</h1>
		<p class="text-sm text-muted-foreground font-medium max-w-xs">
			{errorMsg}
		</p>
	</div>

	<a
		href="/"
		class="mt-4 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
	>
		Back to Job Queue
	</a>
</div>
