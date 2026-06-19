<script lang="ts">
	let { score }: { score: number } = $props();

	const color = $derived(
		score >= 80 ? '#22c55e' : score >= 60 ? '#eab308' : '#ef4444'
	);
	const bg = $derived(
		score >= 80 ? 'text-green-500' : score >= 60 ? 'text-yellow-500' : 'text-red-500'
	);
	const radius = 18;
	const circumference = 2 * Math.PI * radius;
	const offset = $derived(circumference - (score / 100) * circumference);
</script>

<div class="relative inline-flex items-center justify-center w-12 h-12">
	<svg class="absolute top-0 left-0" width="48" height="48" viewBox="0 0 48 48">
		<circle cx="24" cy="24" r={radius} fill="none" stroke="currentColor"
			class="text-muted/30" stroke-width="3" />
		<circle cx="24" cy="24" r={radius} fill="none" stroke={color}
			stroke-width="3" stroke-dasharray={circumference}
			stroke-dashoffset={offset} stroke-linecap="round"
			transform="rotate(-90 24 24)" />
	</svg>
	<span class="text-xs font-semibold {bg}">{score}</span>
</div>
