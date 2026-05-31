<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { Save, X } from 'lucide-svelte';

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
		cv_tailoring_enabled?: boolean;
		max_results_per_source?: number;
		max_job_age_days?: number | null;
	}

	let { error = $bindable(''), successMsg = $bindable('') }: { error: string; successMsg: string } =
		$props();

	let saving = $state(false);

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
	let cvTailoringEnabled = $state(true);
	let maxResultsPerSource = $state(20);
	let maxJobAgeDays = $state<number | null>(null);
	let searchLoading = $state(true);

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
			cvTailoringEnabled = s.cv_tailoring_enabled ?? true;
			maxResultsPerSource = s.max_results_per_source ?? 20;
			maxJobAgeDays = s.max_job_age_days ?? null;
		} catch {
			// not yet configured
		} finally {
			searchLoading = false;
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
					min_match_score: minMatchScore,
					cv_modification_sensitivity: cvModificationSensitivity,
					cv_tailoring_enabled: cvTailoringEnabled,
					max_results_per_source: maxResultsPerSource,
					max_job_age_days: maxJobAgeDays || null
				})
			});
			successMsg = 'Search settings saved.';
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			saving = false;
		}
	}

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
		loadSearch();
	});
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

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

				<!-- Max results per source -->
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="max_results">Max jobs per source</label>
					<input id="max_results" type="number" min="5" max="100" step="5" bind:value={maxResultsPerSource}
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
					<p class="text-[10px] text-muted-foreground">Limits results fetched from each source. Lower = fewer AI calls.</p>
				</div>

				<!-- Max job age -->
				<div class="space-y-1.5">
					<label class="text-sm font-medium text-foreground/90" for="max_age">Only recent jobs (days)</label>
					<select id="max_age"
						value={maxJobAgeDays ?? ''}
						onchange={(e) => {
							const v = (e.target as HTMLSelectElement).value;
							maxJobAgeDays = v ? Number(v) : null;
						}}
						class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm">
						<option value="">No limit</option>
						<option value="1">Past 24 hours</option>
						<option value="3">Past 3 days</option>
						<option value="7">Past week</option>
						<option value="14">Past 2 weeks</option>
						<option value="30">Past month</option>
					</select>
					<p class="text-[10px] text-muted-foreground">Filters search results to recent postings only.</p>
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

			<!-- AI CV Tailoring Toggle -->
			<div class="flex items-center justify-between py-1">
				<div class="space-y-0.5">
					<label class="text-sm font-medium text-foreground/90" for="cv-tailoring-toggle">AI CV Tailoring</label>
					<p class="text-xs text-muted-foreground">
						When enabled, the system uses AI to adapt your CV for each job. Disable this to save API calls and always use your base CV as-is.
					</p>
				</div>
				<button
					id="cv-tailoring-toggle"
					type="button"
					role="switch"
					aria-checked={cvTailoringEnabled}
					onclick={() => cvTailoringEnabled = !cvTailoringEnabled}
					class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary/20 {cvTailoringEnabled ? 'bg-primary' : 'bg-muted-foreground/30'}"
				>
					<span class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out {cvTailoringEnabled ? 'translate-x-5' : 'translate-x-0'}"></span>
				</button>
			</div>

			<!-- CV Modification Sensitivity (only visible when tailoring is enabled) -->
			{#if cvTailoringEnabled}
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
			{/if}
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
