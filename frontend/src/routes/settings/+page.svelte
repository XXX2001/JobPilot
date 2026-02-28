<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { AlertCircle, CheckCircle2, Key, Info } from 'lucide-svelte';

	// ─── Types ───────────────────────────────────────────────────────────────────

	interface Profile {
		id: number;
		full_name: string;
		email: string;
		phone?: string;
		location?: string;
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
		batch_time: string;
		min_match_score: number;
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

	// ─── State ───────────────────────────────────────────────────────────────────

	let activeTab = $state<'profile' | 'search' | 'sources' | 'system'>('profile');
	let saving = $state(false);
	let error = $state('');
	let successMsg = $state('');

	// Profile form
	let profileForm = $state({ full_name: '', email: '', phone: '', location: '', additional_info_json: '' });
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
	let batchTime = $state('08:00');
	let minMatchScore = $state(30);
	let searchLoading = $state(true);

	// Sources
	let sources = $state<Sources | null>(null);
	let sourcesLoading = $state(true);

	// System status
	let setupStatus = $state<SetupStatus | null>(null);
	let systemLoading = $state(true);

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
			batchTime = s.batch_time ?? '08:00';
			minMatchScore = s.min_match_score ?? 30;
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
					min_match_score: minMatchScore
				})
			});
			successMsg = 'Search settings saved.';
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			saving = false;
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
	});
</script>

<!-- Header -->
<div class="mb-6">
	<h1 class="text-xl font-semibold tracking-tight">Settings</h1>
	<p class="text-xs text-muted-foreground mt-0.5">Configure your profile, job search preferences, and integrations.</p>
</div>

<!-- Tabs -->
<div class="flex items-center gap-1 border-b border-border mb-6">
	{#each [['profile', 'Profile'], ['search', 'Search'], ['sources', 'Sources'], ['system', 'System']] as [tab, label]}
		<button
			onclick={() => { activeTab = tab as typeof activeTab; error = ''; successMsg = ''; }}
			class="text-xs px-4 py-2 border-b-2 transition-colors {activeTab === tab
				? 'border-primary text-foreground font-medium'
				: 'border-transparent text-muted-foreground hover:text-foreground'}"
		>{label}</button>
	{/each}
</div>

<!-- Messages -->
{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto">✕</button>
	</div>
{/if}
{#if successMsg}
	<div class="flex items-center gap-2 text-xs text-green-400 bg-green-500/10 border border-green-500/20 rounded-md px-3 py-2 mb-4">
		<CheckCircle2 size={13} />{successMsg}
		<button onclick={() => (successMsg = '')} class="ml-auto">✕</button>
	</div>
{/if}

<!-- ── PROFILE TAB ─────────────────────────────────────────────────────────── -->
{#if activeTab === 'profile'}
	{#if profileLoading}
		<div class="space-y-3 animate-pulse max-w-lg">
			{#each Array(5) as _}<div class="h-9 bg-muted rounded"></div>{/each}
		</div>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); saveProfile(); }} class="space-y-4 max-w-lg">
			<div class="space-y-1">
				<label class="text-xs font-medium" for="full_name">Full name</label>
				<input id="full_name" type="text" bind:value={profileForm.full_name}
					class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
			</div>
			<div class="space-y-1">
				<label class="text-xs font-medium" for="email">Email</label>
				<input id="email" type="email" bind:value={profileForm.email}
					class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
			</div>
			<div class="space-y-1">
				<label class="text-xs font-medium" for="phone">Phone</label>
				<input id="phone" type="tel" bind:value={profileForm.phone}
					class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
			</div>
			<div class="space-y-1">
				<label class="text-xs font-medium" for="location">Location</label>
				<input id="location" type="text" bind:value={profileForm.location}
					class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
			</div>
			<div class="space-y-1">
				<label class="text-xs font-medium" for="additional_info">Additional answers (JSON)</label>
				<p class="text-xs text-muted-foreground">Answers to common application questions, e.g. <code class="bg-muted px-1 rounded">{"{ \"visa\": \"yes\" }"}</code></p>
				<textarea id="additional_info" rows={4} bind:value={profileForm.additional_info_json}
					class="w-full text-xs font-mono px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors resize-none"
				></textarea>
			</div>
			<button type="submit" disabled={saving}
				class="text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50">
				{saving ? 'Saving…' : 'Save Profile'}
			</button>
		</form>
	{/if}

<!-- ── SEARCH TAB ──────────────────────────────────────────────────────────── -->
{:else if activeTab === 'search'}
	{#if searchLoading}
		<div class="space-y-3 animate-pulse max-w-lg">
			{#each Array(6) as _}<div class="h-9 bg-muted rounded"></div>{/each}
		</div>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); saveSearch(); }} class="space-y-5 max-w-lg">

			<!-- Keywords -->
			<div class="space-y-2">
				<label class="text-xs font-medium" for="keywords-input">Keywords to include</label>
				<div class="flex flex-wrap gap-1.5 p-2 bg-background border border-border rounded-md min-h-[38px]">
					{#each keywords as kw}
						<span class="flex items-center gap-1 text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
							{kw}
							<button type="button" onclick={() => removeChip(kw, keywords, (v) => (keywords = v))} class="hover:text-red-400">×</button>
						</span>
					{/each}
					<input
						id="keywords-input"
						aria-label="Add keyword"
						type="text"
						bind:value={keywordsInput}
						placeholder="Add keyword…"
						onkeydown={(e) => {
							if (e.key === 'Enter') { e.preventDefault(); addChip(keywordsInput, keywords, (v) => (keywordsInput = v), (v) => (keywords = v)); }
						}}
						class="flex-1 min-w-24 bg-transparent text-xs focus:outline-none placeholder:text-muted-foreground/60"
					/>
				</div>
			</div>

			<!-- Locations -->
			<div class="space-y-2">
				<label class="text-xs font-medium" for="locations-input">Locations</label>
				<div class="flex flex-wrap gap-1.5 p-2 bg-background border border-border rounded-md min-h-[38px]">
					{#each locations as loc}
						<span class="flex items-center gap-1 text-xs bg-accent text-accent-foreground px-2 py-0.5 rounded-full">
							{loc}
							<button type="button" onclick={() => removeChip(loc, locations, (v) => (locations = v))} class="hover:text-red-400">×</button>
						</span>
					{/each}
					<input
						id="locations-input"
						aria-label="Add location"
						type="text"
						bind:value={locationsInput}
						placeholder="Add location…"
						onkeydown={(e) => {
							if (e.key === 'Enter') { e.preventDefault(); addChip(locationsInput, locations, (v) => (locationsInput = v), (v) => (locations = v)); }
						}}
						class="flex-1 min-w-24 bg-transparent text-xs focus:outline-none placeholder:text-muted-foreground/60"
					/>
				</div>
			</div>

			<!-- Salary min -->
			<div class="space-y-1">
				<label class="text-xs font-medium" for="salary_min">Minimum salary (£)</label>
				<input id="salary_min" type="number" min="0" step="5000" bind:value={salaryMin}
					class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
			</div>

			<!-- Remote only -->
			<div class="flex items-center gap-3">
				<label class="text-xs font-medium" for="remote_only">Remote only</label>
				<button
					aria-label="Toggle remote only"
					aria-pressed={remoteOnly}
					type="button"
					id="remote_only"
					onclick={() => (remoteOnly = !remoteOnly)}
					class="w-10 h-5 rounded-full transition-colors relative {remoteOnly ? 'bg-primary' : 'bg-muted'}"
				>
					<span class="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform {remoteOnly ? 'translate-x-5' : 'translate-x-0.5'}"></span>
				</button>
			</div>

			<!-- Excluded keywords -->
			<div class="space-y-2">
				<label class="text-xs font-medium" for="excl-keywords-input">Excluded keywords</label>
				<div class="flex flex-wrap gap-1.5 p-2 bg-background border border-border rounded-md min-h-[38px]">
					{#each excludedKeywords as kw}
						<span class="flex items-center gap-1 text-xs bg-red-500/10 text-red-400 px-2 py-0.5 rounded-full">
							{kw}
							<button type="button" onclick={() => removeChip(kw, excludedKeywords, (v) => (excludedKeywords = v))} class="hover:text-red-600">×</button>
						</span>
					{/each}
					<input
						id="excl-keywords-input"
						aria-label="Add excluded keyword"
						type="text"
						bind:value={excludedKeywordsInput}
						placeholder="Add excluded keyword…"
						onkeydown={(e) => {
							if (e.key === 'Enter') { e.preventDefault(); addChip(excludedKeywordsInput, excludedKeywords, (v) => (excludedKeywordsInput = v), (v) => (excludedKeywords = v)); }
						}}
						class="flex-1 min-w-24 bg-transparent text-xs focus:outline-none placeholder:text-muted-foreground/60"
					/>
				</div>
			</div>

			<!-- Excluded companies -->
			<div class="space-y-2">
				<label class="text-xs font-medium" for="excl-companies-input">Excluded companies</label>
				<div class="flex flex-wrap gap-1.5 p-2 bg-background border border-border rounded-md min-h-[38px]">
					{#each excludedCompanies as co}
						<span class="flex items-center gap-1 text-xs bg-red-500/10 text-red-400 px-2 py-0.5 rounded-full">
							{co}
							<button type="button" onclick={() => removeChip(co, excludedCompanies, (v) => (excludedCompanies = v))} class="hover:text-red-600">×</button>
						</span>
					{/each}
					<input
						id="excl-companies-input"
						aria-label="Add company to exclude"
						type="text"
						bind:value={excludedCompaniesInput}
						placeholder="Add company to exclude…"
						onkeydown={(e) => {
							if (e.key === 'Enter') { e.preventDefault(); addChip(excludedCompaniesInput, excludedCompanies, (v) => (excludedCompaniesInput = v), (v) => (excludedCompanies = v)); }
						}}
						class="flex-1 min-w-24 bg-transparent text-xs focus:outline-none placeholder:text-muted-foreground/60"
					/>
				</div>
			</div>

			<!-- Daily limit & batch time -->
			<div class="grid grid-cols-2 gap-4">
				<div class="space-y-1">
					<label class="text-xs font-medium" for="daily_limit">Daily apply limit</label>
					<input id="daily_limit" type="number" min="1" max="50" bind:value={dailyLimit}
						class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
				</div>
				<div class="space-y-1">
					<label class="text-xs font-medium" for="batch_time">Batch time</label>
					<input id="batch_time" type="time" bind:value={batchTime}
						class="w-full text-sm px-3 py-2 bg-background border border-border rounded-md focus:outline-none focus:border-primary/50 transition-colors" />
				</div>
			</div>

			<!-- Min match score -->
			<div class="space-y-1">
				<label class="text-xs font-medium" for="min_score">
					Minimum match score: <span class="text-foreground font-semibold">{minMatchScore}%</span>
				</label>
				<input id="min_score" type="range" min="0" max="100" step="5" bind:value={minMatchScore}
					class="w-full accent-primary" />
			</div>

			<button type="submit" disabled={saving}
				class="text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50">
				{saving ? 'Saving…' : 'Save Search Settings'}
			</button>
		</form>
	{/if}

<!-- ── SOURCES TAB ─────────────────────────────────────────────────────────── -->
{:else if activeTab === 'sources'}
	{#if sourcesLoading}
		<div class="space-y-3 animate-pulse max-w-lg">
			{#each Array(2) as _}<div class="h-16 bg-muted rounded-lg"></div>{/each}
		</div>
	{:else}
		<div class="space-y-3 max-w-lg">
			<!-- Adzuna -->
			<div class="p-4 border border-border rounded-lg space-y-2">
				<div class="flex items-center gap-3">
					<div class="text-sm font-medium flex-1">Adzuna</div>
					{#if sources?.adzuna?.configured}
						<span class="flex items-center gap-1 text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full">
							<CheckCircle2 size={11} />Configured
						</span>
					{:else}
						<span class="flex items-center gap-1 text-xs text-yellow-400 bg-yellow-500/10 px-2 py-0.5 rounded-full">
							<AlertCircle size={11} />Not configured
						</span>
					{/if}
				</div>
				{#if sources?.adzuna?.app_id_hint}
					<p class="text-xs text-muted-foreground">App ID: <code class="bg-muted px-1 rounded">{sources.adzuna.app_id_hint}</code></p>
				{/if}
				<p class="text-xs text-muted-foreground">
					Set <code class="bg-muted px-1 rounded">ADZUNA_APP_ID</code> and <code class="bg-muted px-1 rounded">ADZUNA_APP_KEY</code> in your <code class="bg-muted px-1 rounded">.env</code> file.
				</p>
			</div>

			<!-- Gemini -->
			<div class="p-4 border border-border rounded-lg space-y-2">
				<div class="flex items-center gap-3">
					<div class="text-sm font-medium flex-1">Google Gemini</div>
					{#if sources?.gemini?.configured}
						<span class="flex items-center gap-1 text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full">
							<CheckCircle2 size={11} />Configured
						</span>
					{:else}
						<span class="flex items-center gap-1 text-xs text-yellow-400 bg-yellow-500/10 px-2 py-0.5 rounded-full">
							<AlertCircle size={11} />Not configured
						</span>
					{/if}
				</div>
				<p class="text-xs text-muted-foreground">
					Set <code class="bg-muted px-1 rounded">GOOGLE_API_KEY</code> in your <code class="bg-muted px-1 rounded">.env</code> file.
				</p>
			</div>

			<div class="flex items-start gap-2 p-3 bg-muted/50 rounded-lg text-xs text-muted-foreground">
				<Info size={13} class="mt-0.5 flex-shrink-0" />
				<span>API keys are never stored in the database. Edit your <code class="bg-muted px-1 rounded">.env</code> file at the project root and restart the server to apply changes.</span>
			</div>
		</div>
	{/if}

<!-- ── SYSTEM TAB ──────────────────────────────────────────────────────────── -->
{:else if activeTab === 'system'}
	{#if systemLoading}
		<div class="space-y-3 animate-pulse max-w-lg">
			{#each Array(4) as _}<div class="h-12 bg-muted rounded-lg"></div>{/each}
		</div>
	{:else if setupStatus}
		<div class="space-y-3 max-w-lg">
			{#each [
				['Gemini API Key', setupStatus.gemini_key_set, 'GOOGLE_API_KEY in .env'],
				['Adzuna API Keys', setupStatus.adzuna_key_set, 'ADZUNA_APP_ID + ADZUNA_APP_KEY in .env'],
				['Tectonic (LaTeX compiler)', setupStatus.tectonic_found, 'Download via installer script or `cargo install tectonic`'],
				['Base CV uploaded', setupStatus.base_cv_uploaded, 'Upload a .tex file in CV Manager']
			] as [label, ok, hint]}
				<div class="flex items-start gap-3 p-3 border border-border rounded-lg">
					{#if ok}
						<CheckCircle2 size={15} class="text-green-500 mt-0.5 flex-shrink-0" />
					{:else}
						<AlertCircle size={15} class="text-yellow-500 mt-0.5 flex-shrink-0" />
					{/if}
					<div>
						<p class="text-xs font-medium">{label}</p>
						{#if !ok}
							<p class="text-xs text-muted-foreground mt-0.5">{hint}</p>
						{/if}
					</div>
					<span class="ml-auto text-xs {ok ? 'text-green-400' : 'text-yellow-400'}">{ok ? 'OK' : 'Missing'}</span>
				</div>
			{/each}

			{#if setupStatus.setup_complete}
				<div class="flex items-center gap-2 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-xs text-green-400">
					<CheckCircle2 size={13} />
					Setup complete! JobPilot is ready to use.
				</div>
			{:else}
				<div class="flex items-center gap-2 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg text-xs text-yellow-400">
					<AlertCircle size={13} />
					Complete the missing items above to finish setup.
				</div>
			{/if}
		</div>
	{/if}
{/if}
