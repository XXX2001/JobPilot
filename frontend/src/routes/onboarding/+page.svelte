<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { apiFetch } from '$lib/api';
	import {
		CheckCircle2,
		AlertCircle,
		XCircle,
		Upload,
		ArrowRight,
		ArrowLeft,
		Rocket,
		PartyPopper
	} from 'lucide-svelte';
	import type { SetupStatus } from '$lib/types/api';
	import {
		ONBOARDING_DISMISSED_KEY,
		ONBOARDING_TOTAL_STEPS,
		firstIncompleteStep
	} from '$lib/utils/onboarding';

	interface SiteItem {
		name: string;
		display_name: string;
		type: string;
		requires_login: boolean;
		base_url: string;
		enabled: boolean;
	}

	// ─── State ─────────────────────────────────────────────────────────────────
	let status = $state<SetupStatus | null>(null);
	let loading = $state(true);
	let error = $state('');

	let step = $state(1);

	// Step 2 — CV upload
	let cvUploading = $state(false);
	let cvDone = $state(false);

	// Step 3 — keywords
	let keywords = $state<string[]>([]);
	let keywordInput = $state('');
	let keywordsSaving = $state(false);
	let keywordsSaved = $state(false);

	// Step 4 — source + first batch
	let sites = $state<SiteItem[]>([]);
	let selectedSite = $state('');
	let launching = $state(false);
	let batchLaunched = $state(false);

	const envSnippet = `# .env file (project root)
GOOGLE_API_KEY=your_gemini_api_key_here
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key`;

	const steps = [
		{ n: 1, label: 'API Keys' },
		{ n: 2, label: 'CV Template' },
		{ n: 3, label: 'Keywords' },
		{ n: 4, label: 'First Batch' }
	];

	// ─── Loaders ─────────────────────────────────────────────────────────────────
	async function load() {
		loading = true;
		error = '';
		try {
			status = await apiFetch<SetupStatus>('/api/settings/status');
			cvDone = status.base_cv_uploaded;
			// Resume on the first step that still needs attention.
			step = firstIncompleteStep(status);
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Failed to load setup status';
		} finally {
			loading = false;
		}
	}

	async function loadSites() {
		try {
			sites = await apiFetch<SiteItem[]>('/api/settings/sites');
			// Default the selector to the first already-enabled site, else the first one.
			const enabled = sites.find((s) => s.enabled);
			selectedSite = enabled?.name ?? sites[0]?.name ?? '';
		} catch {
			// Non-critical: the user can still finish onboarding without picking a source.
		}
	}

	// ─── Step 2: CV upload (mirrors SetupWizard.handleCvUpload) ────────────────────
	async function handleCvUpload(e: Event) {
		const file = (e.target as HTMLInputElement).files?.[0];
		if (!file) return;
		cvUploading = true;
		error = '';
		try {
			const fd = new FormData();
			fd.append('file', file, file.name);
			await apiFetch<{ path: string; filename: string; size_bytes: number }>(
				'/api/settings/profile/cv-upload',
				{ method: 'POST', body: fd }
			);
			cvDone = true;
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Upload failed';
		} finally {
			cvUploading = false;
		}
	}

	// ─── Step 3: keywords (mirrors SetupWizard.saveKeywords) ───────────────────────
	function addKeyword() {
		const kw = keywordInput.trim();
		if (kw && !keywords.includes(kw)) {
			keywords = [...keywords, kw];
		}
		keywordInput = '';
	}

	async function saveKeywords() {
		if (keywords.length === 0) return;
		keywordsSaving = true;
		error = '';
		try {
			await apiFetch('/api/settings/search', {
				method: 'PUT',
				body: JSON.stringify({ keywords: { include: keywords } })
			});
			keywordsSaved = true;
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Failed to save keywords';
		} finally {
			keywordsSaving = false;
		}
	}

	// ─── Step 4: enable a source then kick off the first batch ─────────────────────
	async function enableAndRun() {
		if (!selectedSite) return;
		launching = true;
		error = '';
		try {
			await apiFetch(`/api/settings/sites/${selectedSite}`, {
				method: 'PUT',
				body: JSON.stringify({ enabled: true })
			});
			await apiFetch('/api/queue/refresh', { method: 'POST' });
			batchLaunched = true;
		} catch (e: unknown) {
			// 409 = a batch is already running — treat as success (it's kicked off).
			if (e instanceof Error && e.message.includes('409')) {
				batchLaunched = true;
			} else {
				error = e instanceof Error ? e.message : 'Failed to launch first batch';
			}
		} finally {
			launching = false;
		}
	}

	// ─── Navigation ────────────────────────────────────────────────────────────────
	function next() {
		if (step < ONBOARDING_TOTAL_STEPS) step += 1;
	}

	function back() {
		if (step > 1) step -= 1;
	}

	/**
	 * Leave onboarding. Records the dismissal so the root-page gate does not
	 * bounce the user straight back here (single auto-redirect per session).
	 */
	function finish() {
		try {
			sessionStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
		} catch {
			// sessionStorage may be unavailable (private mode); navigating is enough.
		}
		goto('/');
	}

	onMount(() => {
		load();
		loadSites();
	});
</script>

<div class="mx-auto max-w-2xl py-6">
	<!-- Header -->
	<div class="mb-6 flex items-start justify-between gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">Welcome to JobPilot</h1>
			<p class="text-muted-foreground mt-1 text-sm">
				Let's get you set up to start applying — four quick steps.
			</p>
		</div>
		<button
			onclick={finish}
			class="text-muted-foreground hover:text-foreground flex-shrink-0 text-xs transition-colors"
		>
			Do this later →
		</button>
	</div>

	<!-- Progress indicator -->
	<div class="mb-8">
		<div class="flex items-center gap-2">
			{#each steps as s, i}
				<div class="flex flex-1 items-center gap-2">
					<div class="flex items-center gap-2">
						<div
							class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium {step ===
							s.n
								? 'bg-primary text-primary-foreground'
								: step > s.n
									? 'bg-green-500/20 text-green-400'
									: 'bg-muted text-muted-foreground'}"
						>
							{#if step > s.n}
								<CheckCircle2 size={14} />
							{:else}
								{s.n}
							{/if}
						</div>
						<span
							class="hidden text-xs sm:inline {step === s.n
								? 'text-foreground font-medium'
								: 'text-muted-foreground'}">{s.label}</span
						>
					</div>
					{#if i < steps.length - 1}
						<div class="bg-border mx-1 h-px flex-1"></div>
					{/if}
				</div>
			{/each}
		</div>
	</div>

	{#if error}
		<div
			class="mb-4 flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
		>
			<AlertCircle size={13} />{error}
			<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
		</div>
	{/if}

	{#if loading}
		<div class="flex justify-center py-20">
			<div
				class="border-primary/20 border-t-primary h-10 w-10 animate-spin rounded-full border-2"
			></div>
		</div>
	{:else}
		<!-- Step content -->
		<div class="border-border bg-card rounded-xl border p-6">
			{#if step === 1}
				<!-- Step 1: API keys (instructional) -->
				<div class="space-y-4">
					<h2 class="text-lg font-medium">1. API keys</h2>
					<p class="text-muted-foreground text-sm">
						JobPilot reads its API keys from a <code class="bg-muted rounded px-1">.env</code> file at
						the project root — they cannot be set from this UI. Add them, then restart the backend.
					</p>

					<div class="space-y-2">
						<div class="flex items-center gap-2 text-sm">
							{#if status?.gemini_key_set}
								<CheckCircle2 size={15} class="text-green-500" />
								<span>Gemini API key set</span>
							{:else}
								<XCircle size={15} class="text-red-400" />
								<span class="text-muted-foreground">Gemini API key missing</span>
							{/if}
						</div>
						<div class="flex items-center gap-2 text-sm">
							{#if status?.adzuna_key_set}
								<CheckCircle2 size={15} class="text-green-500" />
								<span>Adzuna keys set</span>
							{:else}
								<XCircle size={15} class="text-red-400" />
								<span class="text-muted-foreground">Adzuna keys missing</span>
							{/if}
						</div>
						<div class="flex items-center gap-2 text-sm">
							{#if status?.tectonic_found}
								<CheckCircle2 size={15} class="text-green-500" />
								<span>Tectonic (LaTeX) found</span>
							{:else}
								<XCircle size={15} class="text-red-400" />
								<span class="text-muted-foreground">Tectonic not found on PATH</span>
							{/if}
						</div>
					</div>

					<div>
						<p class="text-muted-foreground mb-2 text-xs">
							Copy this snippet into your <code class="bg-muted rounded px-1">.env</code> file:
						</p>
						<pre
							class="bg-muted text-muted-foreground overflow-x-auto rounded-lg p-3 font-mono text-xs leading-relaxed">{envSnippet}</pre>
					</div>
				</div>
			{:else if step === 2}
				<!-- Step 2: CV upload -->
				<div class="space-y-4">
					<h2 class="text-lg font-medium">2. Upload your CV template</h2>
					<p class="text-muted-foreground text-sm">
						Upload your base LaTeX CV. JobPilot surgically tailors it for each job using
						<code class="bg-muted rounded px-1">%%JOBPILOT:marker%%</code> zones.
					</p>

					{#if cvDone}
						<div
							class="flex items-center gap-2 rounded-lg border border-green-500/20 bg-green-500/10 p-3 text-sm text-green-400"
						>
							<CheckCircle2 size={15} />
							CV template uploaded.
						</div>
					{:else}
						<label
							class="border-border hover:border-primary/50 relative block cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors"
						>
							<input type="file" accept=".tex,.cls" onchange={handleCvUpload} class="sr-only" />
							<div class="flex flex-col items-center gap-2">
								<Upload size={28} class="text-muted-foreground" />
								{#if cvUploading}
									<p class="text-muted-foreground text-sm">Uploading…</p>
								{:else}
									<p class="text-sm font-medium">Click to upload a .tex file</p>
								{/if}
							</div>
						</label>
					{/if}
				</div>
			{:else if step === 3}
				<!-- Step 3: keywords -->
				<div class="space-y-4">
					<h2 class="text-lg font-medium">3. Target keywords</h2>
					<p class="text-muted-foreground text-sm">
						Enter your target job titles or keywords. You can change these anytime in Settings.
					</p>
					<div class="space-y-2">
						<div
							class="bg-background border-border flex min-h-[42px] flex-wrap gap-1.5 rounded-md border p-2"
						>
							{#each keywords as kw}
								<span
									class="bg-primary/10 text-primary flex items-center gap-1 rounded-full px-2 py-0.5 text-xs"
								>
									{kw}
									<button
										onclick={() => (keywords = keywords.filter((k) => k !== kw))}
										class="hover:text-red-400">×</button
									>
								</span>
							{/each}
							<input
								type="text"
								placeholder="e.g. Software Engineer, Python…"
								bind:value={keywordInput}
								onkeydown={(e) => {
									if (e.key === 'Enter') {
										e.preventDefault();
										addKeyword();
									}
								}}
								class="placeholder:text-muted-foreground/60 min-w-32 flex-1 bg-transparent text-sm focus:outline-none"
							/>
						</div>
						<p class="text-muted-foreground text-xs">Press Enter to add each keyword.</p>
					</div>

					{#if keywordsSaved}
						<div
							class="flex items-center gap-2 rounded-lg border border-green-500/20 bg-green-500/10 p-3 text-sm text-green-400"
						>
							<CheckCircle2 size={15} />
							Keywords saved.
						</div>
					{:else}
						<button
							onclick={saveKeywords}
							disabled={keywords.length === 0 || keywordsSaving}
							class="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-1.5 text-xs transition-colors disabled:opacity-50"
						>
							{keywordsSaving ? 'Saving…' : 'Save keywords'}
						</button>
					{/if}
				</div>
			{:else if step === 4}
				<!-- Step 4: enable a source + run first batch -->
				<div class="space-y-4">
					<h2 class="text-lg font-medium">4. Enable a source &amp; run your first batch</h2>
					<p class="text-muted-foreground text-sm">
						Pick a job source to enable, then kick off your first scraping batch. You can manage all
						sources later in Settings.
					</p>

					{#if batchLaunched}
						<div
							class="flex items-center gap-2 rounded-lg border border-green-500/20 bg-green-500/10 p-3 text-sm text-green-400"
						>
							<Rocket size={15} />
							First batch kicked off!
							<a href="/queue" class="ml-auto font-medium underline hover:text-green-300"
								>View the queue →</a
							>
						</div>
					{:else}
						<div class="space-y-3">
							<select
								bind:value={selectedSite}
								class="bg-background border-border w-full rounded-md border px-3 py-2 text-sm focus:outline-none"
							>
								{#if sites.length === 0}
									<option value="">No sources available</option>
								{:else}
									{#each sites as s}
										<option value={s.name}>{s.display_name}{s.enabled ? ' (enabled)' : ''}</option>
									{/each}
								{/if}
							</select>
							<button
								onclick={enableAndRun}
								disabled={!selectedSite || launching}
								class="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-md px-4 py-1.5 text-xs transition-colors disabled:opacity-50"
							>
								<Rocket size={13} />
								{launching ? 'Launching…' : 'Enable & run first batch'}
							</button>
						</div>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Footer navigation -->
		<div class="mt-6 flex items-center justify-between">
			<div>
				{#if step > 1}
					<button
						onclick={back}
						class="border-border hover:bg-accent flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors"
					>
						<ArrowLeft size={13} /> Back
					</button>
				{/if}
			</div>
			<div class="flex items-center gap-2">
				<button
					onclick={finish}
					class="text-muted-foreground hover:text-foreground text-xs transition-colors"
				>
					Skip for now
				</button>
				{#if step < ONBOARDING_TOTAL_STEPS}
					<button
						onclick={next}
						class="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-md px-4 py-1.5 text-xs transition-colors"
					>
						Next <ArrowRight size={13} />
					</button>
				{:else}
					<button
						onclick={finish}
						class="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-md px-4 py-1.5 text-xs transition-colors"
					>
						<PartyPopper size={13} /> Finish
					</button>
				{/if}
			</div>
		</div>
	{/if}
</div>
