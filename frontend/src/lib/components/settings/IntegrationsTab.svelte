<script lang="ts">
	import GmailConnectCard from '$lib/components/GmailConnectCard.svelte';
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

<div class="space-y-6">
	<div class="mb-4">
		<h2 class="text-xl font-semibold font-heading mb-1">Integrations</h2>
		<p class="text-sm text-muted-foreground">Connect external accounts to enrich JobPilot with your existing data.</p>
	</div>
	<GmailConnectCard />
</div>
