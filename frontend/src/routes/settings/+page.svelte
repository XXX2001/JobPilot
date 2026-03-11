<script lang="ts">
import { onMount } from 'svelte';
import { apiFetch } from '$lib/api';
import { getProfileStatus } from '$lib/utils/easterEggs';
	import { AlertCircle, CheckCircle2, Key, Info, Globe, Trash2, User, Search, Code, Cpu, X, Plus, Save } from 'lucide-svelte';

// ─── Types ───────────────────────────────────────────────────────────────────

	interface Profile {
		id: number;
		full_name: string;
		email: string;
		phone?: string;
		location?: string;
		linkedin_url?: string;
		driver_license?: string;
		mobility?: string;
		base_cv_path?: string;
		additional_info?: Record<string, unknown>;
	}

	interface SearchSettings {
		id: number;
		keywords: { include?: string[] };
		excluded_keywords?: { items?: string[] };
		locations?: { items?: string[] };
		salary_min?: number;
		remote_only: boolean;
		excluded_companies?: { items?: string[] };
		daily_limit: number;
		min_match_score: number;
		cv_modification_sensitivity?: string;
	}

	interface Sources {
		adzuna: { configured: boolean; app_id_hint?: string };
		gemini: { configured: boolean };
	}

	interface SetupStatus {
		gemini_key_set: boolean;
		adzuna_key_set: boolean;
		tectonic_found: boolean;
		base_cv_uploaded: boolean;
		setup_complete: boolean;
	}

	interface SiteItem {
		name: string;
		display_name: string;
		type: string;
		requires_login: boolean;
		base_url: string;
		enabled: boolean;
		has_session: boolean;
	}

	interface CredentialItem {
		site_name: string;
		display_name: string;
		masked_email: string | null;
		has_session: boolean;
	}

	interface CustomSiteItem {
		id: number;
		name: string;
		display_name: string | null;
		url: string | null;
		enabled: boolean;
	}

	// ─── State ───────────────────────────────────────────────────────────────────

	type TabId = 'profile' | 'search' | 'sites' | 'credentials' | 'sources' | 'system';
	const tabs: [TabId, string, typeof User][] = [
		['profile', 'Profile', User],
		['search', 'Search', Search],
		['sites', 'Sites', Globe],
		['credentials', 'Credentials', Key],
		['sources', 'Sources', Code],
		['system', 'System', Cpu]
	];

	let activeTab = $state<TabId>('profile');
	let saving = $state(false);
	let error = $state('');
	let successMsg = $state('');

	// Profile form
	let profileForm = $state({ full_name: '', email: '', phone: '', location: '', linkedin_url: '', driver_license: '', mobility: '', additional_info_json: '' });
	const profileEasterEgg = $derived(getProfileStatus(profileForm));
	let profileLoading = $state(true);

	// Search settings form
	let keywordsInput = $state('');
	let keywords = $state<string[]>([]);
	let excludedKeywordsInput = $state('');
	let excludedKeywords = $state<string[]>([]);
	let locationsInput = $state('');
	let locations = $state<string[]>([]);
	let excludedCompaniesInput = $state('');
	let excludedCompanies = $state<string[]>([]);
	let salaryMin = $state(0);
	let remoteOnly = $state(false);
	let dailyLimit = $state(10);
	let minMatchScore = $state(30);
	let cvModificationSensitivity = $state<'conservative' | 'balanced' | 'aggressive'>('balanced');
	let searchLoading = $state(true);

	// Sources
	let sources = $state<Sources | null>(null);
	let sourcesLoading = $state(true);

	// System status
	let setupStatus = $state<SetupStatus | null>(null);
	let systemLoading = $state(true);

	// Sites tab
	let sitesList = $state<SiteItem[]>([]);
	let sitesLoading = $state(true);
	let siteTogglingMap = $state<Record<string, boolean>>({});

	// Credentials tab
	let credentialsList = $state<CredentialItem[]>([]);
	let credentialsLoading = $state(true);
	let expandedCredential = $state<string | null>(null);
	let credFormMap = $state<Record<string, { email: string; password: string }>>({});
	let credSavingMap = $state<Record<string, boolean>>({});
	let sessionClearingMap = $state<Record<string, boolean>>({});

	// Custom sites
	let customSitesList = $state<CustomSiteItem[]>([]);
	let customSitesLoading = $state(true);
	let newCustomSite = $state({ name: '', url: '', display_name: '' });
	let addingCustomSite = $state(false);

	// ─── Loaders ─────────────────────────────────────────────────────────────────

	async function loadProfile() {
		profileLoading = true;
		try {
			const p = await apiFetch<Profile>('/api/settings/profile');
			profileForm = {
				full_name: p.full_name ?? '',
				email: p.email ?? '',
				phone: p.phone ?? '',
				location: p.location ?? '',
				linkedin_url: p.linkedin_url ?? '',
				driver_license: p.driver_license ?? '',
				mobility: p.mobility ?? '',
				additional_info_json: p.additional_info ? JSON.stringify(p.additional_info, null, 2) : ''
			};
		} catch {
			// profile may not exist yet
		} finally {
			profileLoading = false;
		}
	}

	async function loadSearch() {
		searchLoading = true;
		try {
			const s = await apiFetch<SearchSettings>('/api/settings/search');
			keywords = s.keywords?.include ?? [];
			excludedKeywords = s.excluded_keywords?.items ?? [];
			locations = s.locations?.items ?? [];
			excludedCompanies = s.excluded_companies?.items ?? [];
			salaryMin = s.salary_min ?? 0;
			remoteOnly = s.remote_only ?? false;
			dailyLimit = s.daily_limit ?? 10;
			minMatchScore = s.min_match_score ?? 30;
			cvModificationSensitivity = (s.cv_modification_sensitivity as 'conservative' | 'balanced' | 'aggressive') ?? 'balanced';
		} catch {
			// not yet configured
		} finally {
			searchLoading = false;
		}
	}

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

	async function loadSites() {
		sitesLoading = true;
		try {
			sitesList = await apiFetch<SiteItem[]>('/api/settings/sites');
		} catch {
			//
		} finally {
			sitesLoading = false;
		}
	}

	async function loadCredentials() {
		credentialsLoading = true;
		try {
			credentialsList = await apiFetch<CredentialItem[]>('/api/settings/credentials');
		} catch {
			//
		} finally {
			credentialsLoading = false;
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

	// ─── Savers ──────────────────────────────────────────────────────────────────

	async function saveProfile() {
		saving = true;
		error = '';
		successMsg = '';
		let additional_info: Record<string, unknown> | undefined;
		if (profileForm.additional_info_json.trim()) {
			try {
				additional_info = JSON.parse(profileForm.additional_info_json);
			} catch {
				error = 'Additional info is not valid JSON';
				saving = false;
				return;
			}
		}
		try {
			await apiFetch('/api/settings/profile', {
				method: 'PUT',
				body: JSON.stringify({
					full_name: profileForm.full_name,
					email: profileForm.email,
					phone: profileForm.phone || null,
					location: profileForm.location || null,
					linkedin_url: profileForm.linkedin_url || null,
					driver_license: profileForm.driver_license || null,
					mobility: profileForm.mobility || null,
					additional_info: additional_info ?? null
				})
			});
			successMsg = 'Profile saved.';
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			saving = false;
		}
	}

	async function saveSearch() {
		saving = true;
		error = '';
		successMsg = '';
		try {
			await apiFetch('/api/settings/search', {
				method: 'PUT',
				body: JSON.stringify({
					keywords: { include: keywords },
					excluded_keywords: { items: excludedKeywords },
					locations: { items: locations },
					excluded_companies: { items: excludedCompanies },
					salary_min: salaryMin || null,
					remote_only: remoteOnly,
					daily_limit: dailyLimit,
					batch_time: batchTime,
					min_match_score: minMatchScore,
					cv_modification_sensitivity: cvModificationSensitivity
				})
			});
			successMsg = 'Search settings saved.';
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			saving = false;
		}
	}

	async function toggleSite(site: SiteItem) {
		siteTogglingMap = { ...siteTogglingMap, [site.name]: true };
		try {
			await apiFetch(`/api/settings/sites/${site.name}`, {
				method: 'PUT',
				body: JSON.stringify({ enabled: !site.enabled })
			});
			sitesList = sitesList.map((s) => s.name === site.name ? { ...s, enabled: !s.enabled } : s);
		} catch (e: any) {
			error = e.message ?? 'Toggle failed';
		} finally {
			siteTogglingMap = { ...siteTogglingMap, [site.name]: false };
		}
	}

	async function saveCredential(siteName: string) {
		const form = credFormMap[siteName];
		if (!form?.email || !form?.password) return;
		credSavingMap = { ...credSavingMap, [siteName]: true };
		try {
			await apiFetch(`/api/settings/credentials/${siteName}`, {
				method: 'PUT',
				body: JSON.stringify({ email: form.email, password: form.password })
			});
			successMsg = `Credentials saved for ${siteName}.`;
			expandedCredential = null;
			await loadCredentials();
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			credSavingMap = { ...credSavingMap, [siteName]: false };
		}
	}

	async function clearSession(siteName: string) {
		sessionClearingMap = { ...sessionClearingMap, [siteName]: true };
		try {
			await apiFetch(`/api/settings/credentials/${siteName}/session`, { method: 'DELETE' });
			successMsg = `Session cleared for ${siteName}.`;
			await loadCredentials();
			await loadSites();
		} catch (e: any) {
			error = e.message ?? 'Clear failed';
		} finally {
			sessionClearingMap = { ...sessionClearingMap, [siteName]: false };
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

	// ─── Chip helpers ────────────────────────────────────────────────────────────

	function addChip(
		input: string,
		chips: string[],
		setInput: (v: string) => void,
		setChips: (v: string[]) => void
	) {
		const trimmed = input.trim();
		if (trimmed && !chips.includes(trimmed)) {
			setChips([...chips, trimmed]);
		}
		setInput('');
	}

	function removeChip(chip: string, chips: string[], setChips: (v: string[]) => void) {
		setChips(chips.filter((c) => c !== chip));
	}

	onMount(() => {
		loadProfile();
		loadSearch();
		loadSources();
		loadSystem();
		loadSites();
		loadCredentials();
		loadCustomSites();
	});
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

<div class="max-w-3xl pb-16">
<!-- ── PROFILE TAB ─────────────────────────────────────────────────────────── -->
{#if activeTab === 'profile'}
	{#if profileLoading}
		<div class="space-y-4 animate-pulse">
			<div class="h-64 bg-card/50 border border-border/30 rounded-2xl"></div>
		</div>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); saveProfile(); }} class="bg-card/40 border border-border/50 rounded-2xl p-6 md:p-8 shadow-sm space-y-6">
			<div class="space-y-1 mb-2">
				<h2 class="text-xl font-semibold font-heading">Personal Information</h2>
				<p class="text-xs text-muted-foreground">This information is used to automatically fill job application forms.</p>
				<p class="text-xs italic text-muted-foreground/70 mt-1">
					{profileEasterEgg.emoji} {profileEasterEgg.message}
				</p>
			</div>
			
			<div class="grid grid-cols-1 md:grid-cols-2 gap-5">
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="full_name">Full name</label>
					<input id="full_name" type="text" bind:value={profileForm.full_name} placeholder="Jean Dupont"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="email">Email address</label>
					<input id="email" type="email" bind:value={profileForm.email} placeholder="jean@exemple.fr"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="phone">Phone number</label>
					<input id="phone" type="tel" bind:value={profileForm.phone} placeholder="+33 6 00 00 00 00"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="location">Location</label>
					<input id="location" type="text" bind:value={profileForm.location} placeholder="Paris, France"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="linkedin_url">LinkedIn URL</label>
					<input id="linkedin_url" type="url" bind:value={profileForm.linkedin_url} placeholder="https://www.linkedin.com/in/yourprofile"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="driver_license">Driver license</label>
					<input id="driver_license" type="text" bind:value={profileForm.driver_license} placeholder="ex. B (voiture), A (moto)"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="mobility">Mobility / Relocation</label>
					<input id="mobility" type="text" bind:value={profileForm.mobility} placeholder="ex. Île-de-France, ouvert à la mobilité"
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
				</div>
			</div>
			
			<div class="space-y-1.5 pt-2">
				<div class="flex items-center gap-2">
					<label class="text-sm font-medium text-foreground/90" for="additional_info">Additional answers</label>
					<span class="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground bg-muted px-2 py-0.5 rounded-full">JSON</span>
				</div>
				<p class="text-xs text-muted-foreground mb-2">Pre-defined answers to common application questions.</p>
				<textarea id="additional_info" rows={5} bind:value={profileForm.additional_info_json} placeholder={'{ "visa_required": "no" }'}
					class="w-full text-sm font-mono px-3.5 py-3 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all resize-y shadow-sm placeholder:text-muted-foreground/30"
				></textarea>
			</div>
			
			<div class="pt-4 border-t border-border/30 flex justify-end">
				<button type="submit" disabled={saving}
					class="flex items-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm hover:shadow disabled:opacity-50 active:scale-[0.98]">
					<Save size={16} />
					{saving ? 'Saving Profile...' : 'Save Profile'}
				</button>
			</div>
		</form>
	{/if}

<!-- ── SEARCH TAB ──────────────────────────────────────────────────────────── -->
{:else if activeTab === 'search'}
	{#if searchLoading}
		<div class="space-y-4 animate-pulse">
			<div class="h-96 bg-card/50 border border-border/30 rounded-2xl"></div>
		</div>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); saveSearch(); }} class="space-y-6">
			<!-- Top Section -->
			<div class="bg-card/40 border border-border/50 rounded-2xl p-6 shadow-sm space-y-6">
				<div class="space-y-1 mb-4">
					<h2 class="text-xl font-semibold font-heading">Job Search Preferences</h2>
					<p class="text-xs text-muted-foreground">Configure the automated matching engine criteria.</p>
				</div>
				
				<div class="grid grid-cols-1 md:grid-cols-2 gap-6">
					<!-- Keywords -->
					<div class="space-y-2">
						<label class="text-sm font-medium text-foreground/90" for="keywords-input">Keywords to include</label>
						<div class="flex flex-wrap gap-2 p-2.5 bg-background/50 border border-border/60 rounded-lg min-h-[46px] focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50 transition-all shadow-sm">
							{#each keywords as kw}
								<span class="flex items-center gap-1.5 text-xs font-medium bg-primary/15 text-primary border border-primary/20 px-2.5 py-1 rounded-md transition-all hover:bg-primary/25">
									{kw}
									<button type="button" onclick={() => removeChip(kw, keywords, (v) => (keywords = v))} class="hover:text-primary/70 transition-colors focus:outline-none">
										<X size={12} />
									</button>
								</span>
							{/each}
							<input id="keywords-input" type="text" bind:value={keywordsInput} placeholder={keywords.length === 0 ? "Add keywords..." : ""}
								onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addChip(keywordsInput, keywords, (v) => (keywordsInput = v), (v) => (keywords = v)); } }}
								class="flex-1 min-w-[120px] bg-transparent text-sm px-1 focus:outline-none placeholder:text-muted-foreground/40" />
						</div>
					</div>

					<!-- Locations -->
					<div class="space-y-2">
						<label class="text-sm font-medium text-foreground/90" for="locations-input">Locations</label>
						<div class="flex flex-wrap gap-2 p-2.5 bg-background/50 border border-border/60 rounded-lg min-h-[46px] focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50 transition-all shadow-sm">
							{#each locations as loc}
								<span class="flex items-center gap-1.5 text-xs font-medium bg-accent/15 text-accent-foreground border border-accent/20 px-2.5 py-1 rounded-md transition-all hover:bg-accent/25">
									{loc}
									<button type="button" onclick={() => removeChip(loc, locations, (v) => (locations = v))} class="hover:text-accent-foreground/70 transition-colors focus:outline-none">
										<X size={12} />
									</button>
								</span>
							{/each}
							<input id="locations-input" type="text" bind:value={locationsInput} placeholder={locations.length === 0 ? "Add locations..." : ""}
								onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addChip(locationsInput, locations, (v) => (locationsInput = v), (v) => (locations = v)); } }}
								class="flex-1 min-w-[120px] bg-transparent text-sm px-1 focus:outline-none placeholder:text-muted-foreground/40" />
						</div>
					</div>

					<!-- Excluded keywords -->
					<div class="space-y-2">
						<label class="text-sm font-medium text-foreground/90" for="excl-keywords-input">Excluded keywords</label>
						<div class="flex flex-wrap gap-2 p-2.5 bg-background/50 border border-border/60 rounded-lg min-h-[46px] focus-within:ring-2 focus-within:ring-red-500/20 focus-within:border-red-500/50 transition-all shadow-sm">
							{#each excludedKeywords as kw}
								<span class="flex items-center gap-1.5 text-xs font-medium bg-red-500/15 text-red-400 border border-red-500/20 px-2.5 py-1 rounded-md transition-all hover:bg-red-500/25">
									{kw}
									<button type="button" onclick={() => removeChip(kw, excludedKeywords, (v) => (excludedKeywords = v))} class="hover:text-red-300 transition-colors focus:outline-none">
										<X size={12} />
									</button>
								</span>
							{/each}
							<input id="excl-keywords-input" type="text" bind:value={excludedKeywordsInput} placeholder={excludedKeywords.length === 0 ? "Exclude keywords..." : ""}
								onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addChip(excludedKeywordsInput, excludedKeywords, (v) => (excludedKeywordsInput = v), (v) => (excludedKeywords = v)); } }}
								class="flex-1 min-w-[120px] bg-transparent text-sm px-1 focus:outline-none placeholder:text-muted-foreground/40" />
						</div>
					</div>

					<!-- Excluded companies -->
					<div class="space-y-2">
						<label class="text-sm font-medium text-foreground/90" for="excl-companies-input">Excluded companies</label>
						<div class="flex flex-wrap gap-2 p-2.5 bg-background/50 border border-border/60 rounded-lg min-h-[46px] focus-within:ring-2 focus-within:ring-red-500/20 focus-within:border-red-500/50 transition-all shadow-sm">
							{#each excludedCompanies as co}
								<span class="flex items-center gap-1.5 text-xs font-medium bg-red-500/15 text-red-400 border border-red-500/20 px-2.5 py-1 rounded-md transition-all hover:bg-red-500/25">
									{co}
									<button type="button" onclick={() => removeChip(co, excludedCompanies, (v) => (excludedCompanies = v))} class="hover:text-red-300 transition-colors focus:outline-none">
										<X size={12} />
									</button>
								</span>
							{/each}
							<input id="excl-companies-input" type="text" bind:value={excludedCompaniesInput} placeholder={excludedCompanies.length === 0 ? "Exclude companies..." : ""}
								onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addChip(excludedCompaniesInput, excludedCompanies, (v) => (excludedCompaniesInput = v), (v) => (excludedCompanies = v)); } }}
								class="flex-1 min-w-[120px] bg-transparent text-sm px-1 focus:outline-none placeholder:text-muted-foreground/40" />
						</div>
					</div>
				</div>
			</div>

			<!-- Bottom Section -->
			<div class="bg-card/40 border border-border/50 rounded-2xl p-6 shadow-sm space-y-6">
				<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-6">
					<!-- Salary min -->
					<div class="space-y-1.5">
						<label class="text-sm font-medium text-foreground/90" for="salary_min">Salaire minimum (€)</label>
						<div class="relative">
							<span class="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground">€</span>
							<input id="salary_min" type="number" min="0" step="5000" bind:value={salaryMin}
								class="w-full text-sm pl-8 pr-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
						</div>
					</div>

					<!-- Daily limit -->
					<div class="space-y-1.5">
						<label class="text-sm font-medium text-foreground/90" for="daily_limit">Daily apply limit</label>
						<input id="daily_limit" type="number" min="1" max="50" bind:value={dailyLimit}
							class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
					</div>
				</div>

				<hr class="border-border/30" />

				<div class="flex flex-col sm:flex-row sm:items-center gap-6 justify-between">
					<!-- Min match score -->
					<div class="space-y-3 flex-1 max-w-sm">
						<div class="flex justify-between items-center">
							<label class="text-sm font-medium text-foreground/90" for="min_score">Match confidence threshold</label>
							<span class="text-sm font-bold bg-primary/10 text-primary px-2 py-0.5 rounded-md">{minMatchScore}%</span>
						</div>
						<input id="min_score" type="range" min="0" max="100" step="5" bind:value={minMatchScore}
							class="w-full accent-primary h-2 bg-muted rounded-lg appearance-none cursor-pointer" />
					</div>

					<!-- Remote only -->
					<div class="flex items-center gap-3 p-3 bg-background/50 border border-border/60 rounded-xl shadow-sm">
						<div class="flex flex-col">
							<label class="text-sm font-medium text-foreground/90 cursor-pointer" for="remote_only">Remote only</label>
							<span class="text-[10px] text-muted-foreground">Filter out on-site roles</span>
						</div>
						<button
							type="button" role="switch" id="remote_only" aria-checked={remoteOnly} aria-label="Toggle remote only"
							onclick={() => (remoteOnly = !remoteOnly)}
							class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background ml-4 {remoteOnly ? 'bg-primary' : 'bg-muted-foreground/30'}"
						>
							<span class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-lg ring-0 transition duration-200 ease-in-out {remoteOnly ? 'translate-x-5' : 'translate-x-0'}"></span>
						</button>
					</div>
				</div>

				<hr class="border-border/30" />

				<!-- CV Modification Sensitivity -->
				<div class="space-y-2">
					<label class="text-sm font-medium text-foreground/90" for="cv-sensitivity">CV Modification Sensitivity</label>
					<select
						id="cv-sensitivity"
						bind:value={cvModificationSensitivity}
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm"
					>
						<option value="conservative">Conservative — Modify CV for most jobs</option>
						<option value="balanced">Balanced — Only modify when meaningful gaps exist</option>
						<option value="aggressive">Aggressive — Trust my base CV, rarely modify</option>
					</select>
					<p class="text-xs text-muted-foreground">
						Controls how aggressively the system tailors your CV for each job.
					</p>
				</div>
			</div>

			<div class="flex justify-end">
				<button type="submit" disabled={saving}
					class="flex items-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm hover:shadow disabled:opacity-50 active:scale-[0.98]">
					<Save size={16} />
					{saving ? 'Saving...' : 'Save Preferences'}
				</button>
			</div>
		</form>
	{/if}

<!-- ── SITES TAB ──────────────────────────────────────────────────────────────── -->
{:else if activeTab === 'sites'}
	{#if sitesLoading}
		<div class="space-y-3 animate-pulse">
			{#each Array(4) as _}<div class="h-20 bg-card/50 border border-border/30 rounded-xl"></div>{/each}
		</div>
	{:else}
		<div class="space-y-4">
			<div class="mb-6">
				<h2 class="text-xl font-semibold font-heading mb-1">Job Sources</h2>
				<p class="text-sm text-muted-foreground">Enable or disable job source sites. Disabled sites will be skipped during the scraping batch.</p>
			</div>
			
			<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
				{#each sitesList as site}
					<div class="group flex items-center justify-between p-4 bg-card/40 border border-border/50 rounded-xl hover:bg-card/80 hover:border-border transition-all shadow-sm hover:shadow-md">
						<div class="flex items-center gap-4 overflow-hidden">
							<div class="w-10 h-10 rounded-full bg-background flex items-center justify-center border border-border/60 shadow-inner flex-shrink-0">
								<Globe size={18} class="text-muted-foreground group-hover:text-primary transition-colors duration-300" />
							</div>
							<div class="flex flex-col min-w-0">
								<div class="flex items-center gap-2">
									<p class="text-sm font-semibold text-foreground truncate">{site.display_name}</p>
									{#if site.has_session}
										<span class="flex items-center gap-1 text-[9px] uppercase tracking-wider font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-1.5 py-0.5 rounded-md flex-shrink-0">
							<CheckCircle2 size={10} />Session</span>
									{/if}
								</div>
								<p class="text-xs text-muted-foreground truncate">{site.base_url || site.type}</p>
							</div>
						</div>
						<button
							type="button" role="switch" aria-checked={site.enabled} aria-label="Toggle {site.display_name}"
							onclick={() => toggleSite(site)}
							disabled={siteTogglingMap[site.name]}
							class="relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ml-4 disabled:opacity-50 {site.enabled ? 'bg-primary' : 'bg-muted-foreground/30'}"
						>
							<span class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-lg ring-0 transition duration-200 ease-in-out {site.enabled ? 'translate-x-5' : 'translate-x-0'}"></span>
						</button>
					</div>
				{/each}
			</div>
		</div>
	{/if}

<!-- ── CREDENTIALS TAB ───────────────────────────────────────────────────── -->
{:else if activeTab === 'credentials'}
	{#if credentialsLoading}
		<div class="space-y-4 animate-pulse">
			{#each Array(2) as _}<div class="h-24 bg-card/50 border border-border/30 rounded-xl"></div>{/each}
		</div>
	{:else if credentialsList.length === 0}
		<div class="flex flex-col items-center justify-center p-12 text-center bg-card/30 border border-border/40 rounded-2xl border-dashed">
			<div class="w-12 h-12 bg-muted rounded-full flex items-center justify-center mb-4">
				<Key size={24} class="text-muted-foreground" />
			</div>
			<p class="text-base font-medium">No credentials required</p>
			<p class="text-sm text-muted-foreground mt-1 max-w-sm">None of your enabled sites currently require stored login credentials.</p>
		</div>
	{:else}
		<div class="space-y-4">
			<div class="mb-6">
				<h2 class="text-xl font-semibold font-heading mb-1">Encrypted Credentials</h2>
				<p class="text-sm text-muted-foreground">Store login credentials for sites that require authentication. Securely encrypted using Fernet keys.</p>
			</div>

			<div class="space-y-4">
				{#each credentialsList as cred}
					<div class="group rounded-xl border transition-all duration-300 overflow-hidden {expandedCredential === cred.site_name ? 'border-primary/40 bg-primary/[0.02] shadow-md' : 'border-border/50 bg-card/40 hover:border-border/80 shadow-sm'}">
						<div class="flex items-center gap-4 p-4 md:p-5">
							<div class="w-10 h-10 rounded-full bg-background flex items-center justify-center border border-border/60 shadow-inner flex-shrink-0 {expandedCredential === cred.site_name ? 'border-primary/30' : ''}">
								<Key size={18} class="{expandedCredential === cred.site_name ? 'text-primary' : 'text-muted-foreground'} transition-colors" />
							</div>
							<div class="flex-1 min-w-0">
								<div class="flex items-center gap-2">
									<p class="text-sm font-semibold text-foreground">{cred.display_name}</p>
									{#if cred.has_session}
										<span class="flex items-center gap-1 text-[9px] uppercase tracking-wider font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-1.5 py-0.5 rounded-md">
											<CheckCircle2 size={10} /> Active Session
										</span>
									{/if}
								</div>
								{#if cred.masked_email}
									<p class="text-xs text-muted-foreground mt-0.5 font-mono bg-muted/50 w-fit px-1.5 py-0.5 rounded">{cred.masked_email}</p>
								{:else}
									<p class="text-xs text-yellow-500/80 mt-0.5">No credentials stored</p>
								{/if}
							</div>
							<div class="flex items-center gap-2 flex-shrink-0">
								{#if cred.has_session}
									<button type="button" onclick={() => clearSession(cred.site_name)} disabled={sessionClearingMap[cred.site_name]}
										class="text-xs font-medium px-3 py-1.5 rounded-lg text-red-400 bg-red-500/10 hover:bg-red-500/20 transition-colors disabled:opacity-50">
										{sessionClearingMap[cred.site_name] ? 'Clearing...' : 'Clear Session'}
									</button>
								{/if}
								<button type="button" onclick={() => {
									if (expandedCredential === cred.site_name) { expandedCredential = null; } 
									else { expandedCredential = cred.site_name; if (!credFormMap[cred.site_name]) { credFormMap = { ...credFormMap, [cred.site_name]: { email: '', password: '' } }; } }
								}} class="text-xs font-medium px-3 py-1.5 rounded-lg transition-colors {expandedCredential === cred.site_name ? 'bg-muted text-foreground' : 'bg-primary/10 text-primary hover:bg-primary/20'}">
									{expandedCredential === cred.site_name ? 'Cancel' : (cred.masked_email ? 'Update' : 'Add')}
								</button>
							</div>
						</div>
						
						{#if expandedCredential === cred.site_name}
							<div class="border-t border-primary/10 bg-background/40 p-4 md:p-5 animate-in slide-in-from-top-2 duration-200">
								<form onsubmit={(e) => { e.preventDefault(); saveCredential(cred.site_name); }} class="flex flex-col md:flex-row gap-3">
									<div class="flex-1 space-y-1">
										<label class="text-xs font-medium text-foreground/80 pl-1" for="email-{cred.site_name}">Email</label>
										<input id="email-{cred.site_name}" type="email" placeholder="account@example.com" bind:value={credFormMap[cred.site_name].email} required
											class="w-full text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
									</div>
									<div class="flex-1 space-y-1">
										<label class="text-xs font-medium text-foreground/80 pl-1" for="password-{cred.site_name}">Password</label>
										<input id="password-{cred.site_name}" type="password" placeholder="••••••••" bind:value={credFormMap[cred.site_name].password} required
											class="w-full text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
									</div>
									<div class="flex items-end pb-0.5">
										<button type="submit" disabled={credSavingMap[cred.site_name]}
											class="w-full md:w-auto flex items-center justify-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm disabled:opacity-50">
											<Save size={16} />
											{credSavingMap[cred.site_name] ? 'Saving...' : 'Save'}
										</button>
									</div>
								</form>
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	{/if}

<!-- ── SOURCES TAB ───────────────────────────────────────────────────────────── -->
{:else if activeTab === 'sources'}
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

<!-- ── SYSTEM TAB ──────────────────────────────────────────────────────────── -->
{:else if activeTab === 'system'}
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
{/if}
</div>
