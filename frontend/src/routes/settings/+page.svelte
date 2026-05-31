<script lang="ts">
	import { AlertCircle, CheckCircle2, Key, Globe, User, Search, Code, Cpu, X, Plug } from 'lucide-svelte';
	import ProfileTab from '$lib/components/settings/ProfileTab.svelte';
	import SearchTab from '$lib/components/settings/SearchTab.svelte';
	import SitesTab from '$lib/components/settings/SitesTab.svelte';
	import CredentialsTab from '$lib/components/settings/CredentialsTab.svelte';
	import SourcesTab from '$lib/components/settings/SourcesTab.svelte';
	import IntegrationsTab from '$lib/components/settings/IntegrationsTab.svelte';
	import SystemTab from '$lib/components/settings/SystemTab.svelte';

	type TabId = 'profile' | 'search' | 'sites' | 'credentials' | 'sources' | 'integrations' | 'system';
	const tabs: [TabId, string, typeof User][] = [
		['profile', 'Profile', User],
		['search', 'Search', Search],
		['sites', 'Sites', Globe],
		['credentials', 'Credentials', Key],
		['sources', 'Sources', Code],
		['integrations', 'Integrations', Plug],
		['system', 'System', Cpu]
	];

	let activeTab = $state<TabId>('profile');
	let error = $state('');
	let successMsg = $state('');
	// Bumped to ask the (always-mounted) Sites tab to reload after a credential
	// session is cleared, mirroring the original page's clearSession -> loadSites().
	let sitesRefreshKey = $state(0);
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

<!-- Header -->
<div class="mb-8 mt-2">
	<h1 class="text-3xl font-bold tracking-tight bg-gradient-to-r from-foreground to-foreground/60 bg-clip-text text-transparent font-heading">Settings</h1>
	<p class="text-sm text-muted-foreground mt-1.5 max-w-2xl leading-relaxed">Configure your profile, job search preferences, integrations, and system setup.</p>
</div>

<!-- Tabs -->
<div class="flex flex-wrap p-1.5 bg-muted/30 border border-border/40 rounded-xl mb-8 w-fit shadow-sm backdrop-blur-md">
	{#each tabs as [tab, label, Icon]}
		<button
			onclick={() => { activeTab = tab; error = ''; successMsg = ''; }}
			class="relative flex items-center gap-2 text-sm px-4 py-2 rounded-lg transition-all duration-300 {activeTab === tab
				? 'text-foreground font-medium bg-background shadow-sm border border-border/50'
				: 'text-muted-foreground hover:text-foreground hover:bg-muted/50 border border-transparent'}"
		>
			<Icon size={16} class="transition-colors {activeTab === tab ? 'text-primary' : 'text-muted-foreground/70'}" />
			{label}
		</button>
	{/each}
</div>

<!-- Messages -->
<div class="max-w-3xl">
	{#if error}
		<div class="flex items-center gap-3 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3 mb-6 shadow-sm animate-in fade-in zoom-in-95 duration-200">
			<AlertCircle size={18} class="flex-shrink-0" />
			<span class="flex-1 font-medium">{error}</span>
			<button onclick={() => (error = '')} class="p-1.5 hover:bg-red-500/20 rounded-md transition-colors"><X size={14} /></button>
		</div>
	{/if}
	{#if successMsg}
		<div class="flex items-center gap-3 text-sm text-green-400 bg-green-500/10 border border-green-500/20 rounded-xl px-4 py-3 mb-6 shadow-sm animate-in fade-in zoom-in-95 duration-200">
			<CheckCircle2 size={18} class="flex-shrink-0" />
			<span class="flex-1 font-medium">{successMsg}</span>
			<button onclick={() => (successMsg = '')} class="p-1.5 hover:bg-green-500/20 rounded-md transition-colors"><X size={14} /></button>
		</div>
	{/if}
</div>

<!--
	All tab components stay mounted; only the active one is visible. The `hidden`
	attribute keeps inactive tabs out of the a11y tree and tab order, while
	preserving each tab's $state (incl. unsaved edits) and running its loaders
	exactly once at page load — matching the original single-page behavior.
-->
<div class="max-w-3xl pb-16">
	<div hidden={activeTab !== 'profile'}><ProfileTab bind:error bind:successMsg /></div>
	<div hidden={activeTab !== 'search'}><SearchTab bind:error bind:successMsg /></div>
	<div hidden={activeTab !== 'sites'}><SitesTab bind:error refreshKey={sitesRefreshKey} /></div>
	<div hidden={activeTab !== 'credentials'}><CredentialsTab bind:error bind:successMsg onSessionCleared={() => sitesRefreshKey++} /></div>
	<div hidden={activeTab !== 'sources'}><SourcesTab bind:error /></div>
	<div hidden={activeTab !== 'integrations'}><IntegrationsTab /></div>
	<div hidden={activeTab !== 'system'}><SystemTab /></div>
</div>
