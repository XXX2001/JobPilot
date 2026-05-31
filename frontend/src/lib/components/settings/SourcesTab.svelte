<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { AlertCircle, CheckCircle2, Info, Globe, Trash2, Code, Cpu, Plus } from 'lucide-svelte';

	interface Sources {
		adzuna: { configured: boolean; app_id_hint?: string };
		gemini: { configured: boolean };
	}

	interface CustomSiteItem {
		id: number;
		name: string;
		display_name: string | null;
		url: string | null;
		enabled: boolean;
	}

	let { error = $bindable('') }: { error: string } = $props();

	let sources = $state<Sources | null>(null);
	let sourcesLoading = $state(true);

	let customSitesList = $state<CustomSiteItem[]>([]);
	let customSitesLoading = $state(true);
	let newCustomSite = $state({ name: '', url: '', display_name: '' });
	let addingCustomSite = $state(false);

	async function loadSources() {
		sourcesLoading = true;
		try {
			sources = await apiFetch<Sources>('/api/settings/sources');
		} catch {
			//
		} finally {
			sourcesLoading = false;
		}
	}

	async function loadCustomSites() {
		customSitesLoading = true;
		try {
			customSitesList = await apiFetch<CustomSiteItem[]>('/api/settings/custom-sites');
		} catch {
			//
		} finally {
			customSitesLoading = false;
		}
	}

	async function addCustomSite() {
		if (!newCustomSite.name.trim() || !newCustomSite.url.trim()) return;
		addingCustomSite = true;
		try {
			await apiFetch('/api/settings/custom-sites', {
				method: 'POST',
				body: JSON.stringify(newCustomSite)
			});
			newCustomSite = { name: '', url: '', display_name: '' };
			await loadCustomSites();
		} catch (e: any) {
			error = e.message ?? 'Add failed';
		} finally {
			addingCustomSite = false;
		}
	}

	async function deleteCustomSite(id: number) {
		try {
			await apiFetch(`/api/settings/custom-sites/${id}`, { method: 'DELETE' });
			await loadCustomSites();
		} catch (e: any) {
			error = e.message ?? 'Delete failed';
		}
	}

	onMount(() => {
		loadSources();
		loadCustomSites();
	});
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

{#if sourcesLoading}
	<div class="space-y-4 animate-pulse">
		{#each Array(2) as _}<div class="h-24 bg-card/50 border border-border/30 rounded-xl"></div>{/each}
	</div>
{:else}
	<div class="space-y-8">
		<!-- API Configs -->
		<section>
			<div class="mb-4">
				<h2 class="text-xl font-semibold font-heading mb-1">API Integrations</h2>
				<p class="text-sm text-muted-foreground">Manage your third-party API configurations via the <code class="text-xs bg-muted px-1 py-0.5 rounded text-foreground">.env</code> file.</p>
			</div>
			
			<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
				<!-- Adzuna -->
				<div class="p-5 bg-card/40 border border-border/50 rounded-xl shadow-sm relative overflow-hidden group hover:border-border transition-colors">
					<div class="absolute top-0 right-0 w-32 h-32 bg-primary/5 rounded-full blur-2xl -mr-10 -mt-10 group-hover:bg-primary/10 transition-colors"></div>
					<div class="relative flex flex-col h-full">
						<div class="flex items-start justify-between mb-4">
							<div class="flex items-center gap-3">
								<div class="w-10 h-10 rounded-lg bg-background border border-border/60 flex items-center justify-center shadow-sm">
									<Code size={18} class="text-primary" />
								</div>
								<div>
									<h3 class="text-base font-semibold">Adzuna API</h3>
									<p class="text-xs text-muted-foreground">Job Search Engine</p>
								</div>
							</div>
							{#if sources?.adzuna?.configured}
								<span class="flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-2 py-1 rounded-md">
									<CheckCircle2 size={12} /> Configured
								</span>
							{:else}
								<span class="flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-yellow-500 bg-yellow-500/10 border border-yellow-500/20 px-2 py-1 rounded-md">
									<AlertCircle size={12} /> Missing
								</span>
							{/if}
						</div>
						
						<div class="mt-auto pt-2 space-y-2">
							{#if sources?.adzuna?.app_id_hint}
								<div class="flex items-center justify-between text-xs p-2 bg-background/50 rounded border border-border/40">
									<span class="text-muted-foreground">App ID</span>
									<code class="font-mono text-foreground font-medium">{sources.adzuna.app_id_hint}</code>
								</div>
							{/if}
							<p class="text-xs text-muted-foreground/80 leading-relaxed">
								Configure <code class="text-[10px] bg-muted px-1 py-0.5 rounded text-foreground">ADZUNA_APP_ID</code> and <code class="text-[10px] bg-muted px-1 py-0.5 rounded text-foreground">ADZUNA_APP_KEY</code> in your environment.
							</p>
						</div>
					</div>
				</div>

				<!-- Gemini -->
				<div class="p-5 bg-card/40 border border-border/50 rounded-xl shadow-sm relative overflow-hidden group hover:border-border transition-colors">
					<div class="absolute top-0 right-0 w-32 h-32 bg-accent/5 rounded-full blur-2xl -mr-10 -mt-10 group-hover:bg-accent/10 transition-colors"></div>
					<div class="relative flex flex-col h-full">
						<div class="flex items-start justify-between mb-4">
							<div class="flex items-center gap-3">
								<div class="w-10 h-10 rounded-lg bg-background border border-border/60 flex items-center justify-center shadow-sm">
									<Cpu size={18} class="text-accent-foreground" />
								</div>
								<div>
									<h3 class="text-base font-semibold">Google Gemini</h3>
									<p class="text-xs text-muted-foreground">LLM Engine</p>
								</div>
							</div>
							{#if sources?.gemini?.configured}
								<span class="flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-2 py-1 rounded-md">
									<CheckCircle2 size={12} /> Configured
								</span>
							{:else}
								<span class="flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-yellow-500 bg-yellow-500/10 border border-yellow-500/20 px-2 py-1 rounded-md">
									<AlertCircle size={12} /> Missing
								</span>
							{/if}
						</div>
						
						<div class="mt-auto pt-2">
							<p class="text-xs text-muted-foreground/80 leading-relaxed mt-2">
								Configure <code class="text-[10px] bg-muted px-1 py-0.5 rounded text-foreground">GOOGLE_API_KEY</code> in your environment. Used for CV tailoring.
							</p>
						</div>
					</div>
				</div>
			</div>

			<div class="flex items-start gap-3 p-4 mt-4 bg-muted/30 border border-border/40 rounded-xl text-sm text-muted-foreground">
				<Info size={16} class="mt-0.5 flex-shrink-0 text-primary" />
				<p>API keys are never stored in the database. Edit your <code class="bg-background border border-border/50 px-1.5 py-0.5 rounded text-foreground text-xs shadow-sm">.env</code> file at the project root and restart the server to apply changes.</p>
			</div>
		</section>

		<!-- Custom Sites -->
		<section>
			<div class="mb-4 flex items-center justify-between">
				<div>
					<h3 class="text-xl font-semibold font-heading mb-1">Custom Websites</h3>
					<p class="text-sm text-muted-foreground">Add any job board, careers page, or company website URL to scrape for job listings.</p>
				</div>
			</div>

			{#if customSitesLoading}
				<div class="h-16 bg-card/50 rounded-xl animate-pulse border border-border/30"></div>
			{:else}
				<div class="bg-card/30 border border-border/50 rounded-xl overflow-hidden shadow-sm">
					{#if customSitesList.length > 0}
						<div class="divide-y divide-border/40">
							{#each customSitesList as site}
								<div class="flex items-center gap-4 p-4 hover:bg-muted/20 transition-colors">
									<div class="w-8 h-8 rounded-full bg-background flex items-center justify-center border border-border/60 flex-shrink-0">
										<Globe size={14} class="text-muted-foreground" />
									</div>
									<div class="flex-1 min-w-0 grid grid-cols-1 md:grid-cols-2 gap-2">
										<p class="text-sm font-medium text-foreground truncate">{site.display_name ?? site.name}</p>
										<p class="text-xs text-muted-foreground truncate font-mono bg-background/50 w-fit px-2 py-0.5 rounded border border-border/30">{site.url ?? 'No URL'}</p>
									</div>
									<button type="button" onclick={() => deleteCustomSite(site.id)}
										class="text-muted-foreground hover:text-red-400 hover:bg-red-400/10 p-2 rounded-lg transition-colors"
										aria-label="Delete {site.display_name ?? site.name}">
										<Trash2 size={16} />
									</button>
								</div>
							{/each}
						</div>
					{:else}
						<div class="p-8 text-center border-b border-border/40">
							<p class="text-sm text-muted-foreground">No custom URLs configured yet.</p>
						</div>
					{/if}
					
					<form onsubmit={(e) => { e.preventDefault(); addCustomSite(); }} class="p-4 bg-muted/10">
						<p class="text-xs font-medium text-foreground/80 mb-3 uppercase tracking-wider">Add New Source</p>
						<div class="flex flex-col md:flex-row gap-3">
							<input type="text" placeholder="Short id (e.g. company_careers)" bind:value={newCustomSite.name} required
								class="flex-1 min-w-[140px] text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
							<input type="text" placeholder="Display name" bind:value={newCustomSite.display_name}
								class="flex-1 min-w-[140px] text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
							<input type="url" placeholder="https://url..." bind:value={newCustomSite.url} required
								class="flex-[2] min-w-[200px] text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
							<button type="submit" disabled={addingCustomSite || !newCustomSite.name.trim() || !newCustomSite.url.trim()}
								class="flex items-center justify-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 border border-border transition-all shadow-sm disabled:opacity-50 whitespace-nowrap">
								<Plus size={16} />
								{addingCustomSite ? 'Adding...' : 'Add'}
							</button>
						</div>
					</form>
				</div>
			{/if}
		</section>
	</div>
{/if}
