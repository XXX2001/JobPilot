<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { AlertCircle, CheckCircle2, User, Code, Cpu } from 'lucide-svelte';
	import type { SetupStatus } from '$lib/types/api';

	let setupStatus = $state<SetupStatus | null>(null);
	let systemLoading = $state(true);

	async function loadSystem() {
		systemLoading = true;
		try {
			setupStatus = await apiFetch<SetupStatus>('/api/settings/status');
		} catch {
			//
		} finally {
			systemLoading = false;
		}
	}

	onMount(() => {
		loadSystem();
	});
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

{#if systemLoading}
	<div class="space-y-4 animate-pulse">
		<div class="h-64 bg-card/50 border border-border/30 rounded-xl"></div>
	</div>
{:else if setupStatus}
	<div class="space-y-6">
		<div class="mb-4">
			<h2 class="text-xl font-semibold font-heading mb-1">System Status</h2>
			<p class="text-sm text-muted-foreground">Checklist of required components to ensure JobPilot runs correctly.</p>
		</div>

		<div class="bg-card/30 border border-border/50 rounded-2xl overflow-hidden shadow-sm">
			<div class="divide-y divide-border/40">
				{#each [
					['Gemini API Key', setupStatus.gemini_key_set, 'GOOGLE_API_KEY in .env file required for CV tailoring', Cpu],
					['Adzuna API Keys', setupStatus.adzuna_key_set, 'ADZUNA_APP_ID & ADZUNA_APP_KEY in .env for job search', Code],
					['Tectonic Engine', setupStatus.tectonic_found, 'Local LaTeX compiler (download via script or cargo install)', Cpu],
					['Base CV Uploaded', setupStatus.base_cv_uploaded, 'Master .tex file uploaded in CV Manager', User]
				] as [label, ok, hint, Icon]}
					<div class="flex items-start gap-4 p-5 hover:bg-muted/10 transition-colors">
						<div class="mt-0.5 w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 {ok ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'}">
							{#if ok}
								<CheckCircle2 size={16} />
							{:else}
								<AlertCircle size={16} />
							{/if}
						</div>
						<div class="flex-1">
							<p class="text-sm font-medium text-foreground">{label}</p>
							<p class="text-xs text-muted-foreground mt-1 leading-relaxed max-w-lg">{hint}</p>
						</div>
						<span class="text-xs font-bold uppercase tracking-wider px-2.5 py-1 rounded-md {ok ? 'bg-green-500/10 text-green-500 border border-green-500/20' : 'bg-yellow-500/10 text-yellow-500 border border-yellow-500/20'}">
							{ok ? 'Ready' : 'Action Required'}
						</span>
					</div>
				{/each}
			</div>
			
			<div class="p-5 bg-background border-t border-border/50">
				{#if setupStatus.setup_complete}
					<div class="flex items-center gap-3 p-4 bg-green-500/10 border border-green-500/20 rounded-xl shadow-sm">
						<div class="w-10 h-10 bg-green-500/20 rounded-full flex items-center justify-center flex-shrink-0">
							<CheckCircle2 size={20} class="text-green-500" />
						</div>
						<div>
							<h4 class="text-sm font-semibold text-green-400">All systems operational</h4>
							<p class="text-xs text-green-400/80 mt-0.5">JobPilot is fully configured and ready to process applications.</p>
						</div>
					</div>
				{:else}
					<div class="flex items-center gap-3 p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-xl shadow-sm">
						<div class="w-10 h-10 bg-yellow-500/20 rounded-full flex items-center justify-center flex-shrink-0">
							<AlertCircle size={20} class="text-yellow-500" />
						</div>
						<div>
							<h4 class="text-sm font-semibold text-yellow-500">Setup incomplete</h4>
							<p class="text-xs text-yellow-500/80 mt-0.5">Please resolve the warnings above to enable application processing.</p>
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
