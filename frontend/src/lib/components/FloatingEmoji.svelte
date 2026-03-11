<script lang="ts">
	let {
		emoji,
		size = 'md'
	}: {
		emoji: string;
		size?: 'sm' | 'md' | 'lg';
	} = $props();

	const sizes = { sm: 'text-2xl', md: 'text-4xl', lg: 'text-5xl' };
</script>

<div class="animate-float inline-block {sizes[size]}">
	{emoji}
</div>
