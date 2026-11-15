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
	import type {
		BrowserStatus,
		JobSitePasswordStatus,
		ProfileDetails,
		SetupStatus
	} from '$lib/types/api';
	import {
		ONBOARDING_DISMISSED_KEY,
		ONBOARDING_TOTAL_STEPS,
		firstIncompleteStep,
		passwordSaveError
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

	// Step 1 — profile, write-only job-site password, and browser sign-ins
	let profile = $state({ full_name: '', email: '', phone: '', location: '', linkedin_url: '' });
	let profileLoaded = $state(false);
	let profileSaving = $state(false);
	let savedProfile = $state('');
	let profileForm = $state<HTMLFormElement>();
	const profileDirty = $derived(JSON.stringify(profile) !== savedProfile);
	const youReady = $derived(profileLoaded && !!profile.full_name.trim() && !!profile.email.trim());
	let toolsRefreshing = $state(false);
	let toolsError = $state('');

	// Keep the password only in the input, never in a shared store or profile payload.
	let passwordInput = $state<HTMLInputElement>();
	let passwordStatus = $state<JobSitePasswordStatus | null>(null);
	let passwordChecking = $state(true);
	let passwordSaving = $state(false);
	let passwordError = $state('');
	let showPassword = $state(false);
	type BrowserViewStatus = BrowserStatus & {
		view_url?: string | null;
		view_password_hint?: string;
	};
	let browser = $state<BrowserViewStatus | null>(null);
	let browserBusy = $state(true);
	let browserError = $state('');
	const signIns = [
		{ key: 'google', label: 'Google (Gmail)' },
		{ key: 'linkedin', label: 'LinkedIn' }
	] as const;

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

	const envSnippet = `# .env file (project root). No AI key needed when Claude Code or Codex is installed.
# Optional: a local decision model for applying (see docs/browser.md)
DECISION_MODEL_URL=http://<your-gpu-box>:8009/v1/systemone
# Optional: Adzuna as an extra job source
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key`;

	const steps = [
		{ n: 1, label: 'You' },
		{ n: 2, label: 'CV Template' },
		{ n: 3, label: 'Keywords' },
		{ n: 4, label: 'First Batch' }
	];

	// ─── Loaders ─────────────────────────────────────────────────────────────────
	async function load() {
		loading = true;
		error = '';
		try {
			const [setup, details] = await Promise.all([
				apiFetch<SetupStatus>('/api/settings/status'),
				apiFetch<ProfileDetails>('/api/settings/profile')
			]);
			status = setup;
			setProfile(details);
			profileLoaded = true;
			cvDone = status.base_cv_uploaded;
			// Resume on the first step that still needs attention.
			step = firstIncompleteStep(status, profile);
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Failed to load setup status';
		} finally {
			loading = false;
		}
	}

	function setProfile(details: ProfileDetails) {
		profile = {
			full_name: details.full_name,
			email: details.email,
			phone: details.phone ?? '',
			location: details.location ?? '',
			linkedin_url: details.linkedin_url ?? ''
		};
		savedProfile = JSON.stringify(profile);
	}

	async function saveProfile(): Promise<boolean> {
		if (!profileLoaded || profileSaving || !profile.full_name.trim() || !profile.email.trim())
			return false;
		profileSaving = true;
		error = '';
		try {
			const details = await apiFetch<ProfileDetails>('/api/settings/profile', {
				method: 'PUT',
				body: JSON.stringify({
					full_name: profile.full_name.trim(),
					email: profile.email.trim(),
					phone: profile.phone.trim() || null,
					location: profile.location.trim() || null,
					linkedin_url: profile.linkedin_url.trim() || null
				})
			});
			setProfile(details);
			return true;
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : 'Failed to save profile';
			return false;
		} finally {
			profileSaving = false;
		}
	}

	async function loadPasswordStatus() {
		passwordChecking = true;
		passwordError = '';
		try {
			passwordStatus = await apiFetch<JobSitePasswordStatus>('/api/settings/job-site-password');
		} catch {
			passwordError = 'Could not check whether a job-site password is saved.';
		} finally {
			passwordChecking = false;
		}
	}

	async function savePassword(): Promise<boolean> {
		if (!passwordInput || passwordSaving) return false;
		const input = passwordInput;
		passwordSaving = true;
		passwordError = '';
		try {
			passwordStatus = await apiFetch<JobSitePasswordStatus>('/api/settings/job-site-password', {
				method: 'PUT',
				body: JSON.stringify({ password: input.value })
			});
			input.value = '';
			return true;
		} catch (e: unknown) {
			// Never display or log a response/exception that could echo the password.
			passwordError = passwordSaveError(e);
			return false;
		} finally {
			showPassword = false;
			passwordSaving = false;
		}
	}

	async function refreshBrowser(open = false) {
		browserBusy = true;
		browserError = '';
		try {
			browser = await apiFetch<BrowserViewStatus>(open ? '/api/browser/open' : '/api/browser/status', {
				method: open ? 'POST' : 'GET'
			});
		} catch {
			browserError = 'Could not reach the JobPilot Chrome. Try again.';
		} finally {
			browserBusy = false;
		}
	}

	async function refreshTools() {
		toolsRefreshing = true;
		toolsError = '';
		try {
			status = await apiFetch<SetupStatus>('/api/settings/status');
		} catch (e: unknown) {
			toolsError = e instanceof Error ? e.message : 'Failed to check tools';
		} finally {
			toolsRefreshing = false;
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
	async function next() {
		if (step === 1) {
			if (!youReady || profileSaving || passwordSaving || !profileForm?.reportValidity()) return;
			if (profileDirty && !(await saveProfile())) return;
			if (passwordInput?.value && !(await savePassword())) return;
		}
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
		loadPasswordStatus();
		refreshBrowser();
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
			{#if !profileLoaded}
				<button onclick={load} class="underline">Retry setup</button>
			{/if}
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
				<!-- Step 1: everything the owner provides -->
				<div class="space-y-4">
					<h2 class="text-lg font-medium">1. You</h2>
					<p class="text-muted-foreground text-sm">
						Your details for job applications. Full name and email are required.
					</p>
					<form
						bind:this={profileForm}
						onsubmit={(e) => {
							e.preventDefault();
							saveProfile();
						}}
						class="space-y-3"
					>
						<fieldset disabled={!profileLoaded || profileSaving} class="grid gap-3 sm:grid-cols-2">
							<label class="space-y-1 text-sm">
								<span>Full name</span>
								<input
									type="text"
									autocomplete="name"
									required
									bind:value={profile.full_name}
									class="bg-background border-border w-full rounded-md border px-3 py-2 text-sm"
								/>
							</label>
							<label class="space-y-1 text-sm">
								<span>Email</span>
								<input
									type="email"
									autocomplete="email"
									required
									bind:value={profile.email}
									class="bg-background border-border w-full rounded-md border px-3 py-2 text-sm"
								/>
							</label>
							<label class="space-y-1 text-sm">
								<span>Phone <span class="text-muted-foreground">(optional)</span></span>
								<input
									type="tel"
									autocomplete="tel"
									bind:value={profile.phone}
									class="bg-background border-border w-full rounded-md border px-3 py-2 text-sm"
								/>
							</label>
							<label class="space-y-1 text-sm">
								<span>Location <span class="text-muted-foreground">(optional)</span></span>
								<input
									type="text"
									autocomplete="address-level2"
									bind:value={profile.location}
									class="bg-background border-border w-full rounded-md border px-3 py-2 text-sm"
								/>
							</label>
							<label class="space-y-1 text-sm sm:col-span-2">
								<span>LinkedIn URL <span class="text-muted-foreground">(optional)</span></span>
								<input
									type="url"
									bind:value={profile.linkedin_url}
									placeholder="https://www.linkedin.com/in/…"
									class="bg-background border-border w-full rounded-md border px-3 py-2 text-sm"
								/>
							</label>
						</fieldset>
						<div class="flex items-center gap-3">
							<button
								type="submit"
								disabled={!profileLoaded ||
									profileSaving ||
									!profile.full_name.trim() ||
									!profile.email.trim()}
								class="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-1.5 text-xs disabled:opacity-50"
							>
								{profileSaving ? 'Saving…' : 'Save profile'}
							</button>
							{#if profileLoaded && !profileDirty && profile.full_name.trim() && profile.email.trim()}
								<span role="status" class="text-xs text-green-400">Profile saved</span>
							{/if}
						</div>
					</form>

					<form
						onsubmit={(e) => {
							e.preventDefault();
							savePassword();
						}}
						class="border-border space-y-3 border-t pt-4"
					>
						<div class="flex items-center justify-between gap-2">
							<label for="job-site-password" class="text-sm font-medium"
								>Job-site password <span class="text-muted-foreground font-normal"
									>(recommended)</span
								></label
							>
							<span
								role="status"
								class="text-xs {passwordStatus?.set ? 'text-green-400' : 'text-muted-foreground'}"
							>
								{passwordStatus === null
									? passwordChecking
										? 'Checking…'
										: 'Status unavailable'
									: passwordStatus.set
										? 'Saved'
										: 'Not set'}
							</span>
						</div>
						<p id="job-site-password-help" class="text-muted-foreground text-xs">
							Used to create and sign in to accounts on company job sites. Stored encrypted on this
							machine.
						</p>
						<div class="flex gap-2">
							<input
								id="job-site-password"
								type={showPassword ? 'text' : 'password'}
								bind:this={passwordInput}
								autocomplete="new-password"
								required
								minlength={8}
								maxlength={128}
								disabled={passwordSaving}
								aria-describedby="job-site-password-help"
								placeholder="8–128 characters"
								class="bg-background border-border min-w-0 flex-1 rounded-md border px-3 py-2 text-sm"
							/>
							<button
								type="button"
								onclick={() => (showPassword = !showPassword)}
								aria-pressed={showPassword}
								aria-label={showPassword ? 'Hide job-site password' : 'Show job-site password'}
								disabled={passwordSaving}
								class="border-border hover:bg-accent rounded-md border px-3 py-1.5 text-xs disabled:opacity-50"
							>
								{showPassword ? 'Hide' : 'Show'}
							</button>
						</div>
						<button
							type="submit"
							disabled={passwordSaving}
							class="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-1.5 text-xs disabled:opacity-50"
						>
							{passwordSaving ? 'Saving…' : 'Save password'}
						</button>
						{#if passwordError}
							<p role="alert" class="text-xs text-red-400">{passwordError}</p>
							{#if passwordStatus === null}
								<button type="button" onclick={loadPasswordStatus} class="text-xs underline"
									>Retry password status</button
								>
							{/if}
						{/if}
					</form>

					<div class="border-border space-y-3 border-t pt-4">
						<h3 class="text-sm font-medium">
							JobPilot Chrome sign-ins <span class="text-muted-foreground font-normal"
								>(recommended)</span
							>
						</h3>
						<p class="text-muted-foreground text-sm">
							JobPilot's Chrome is your applications browser: sign in to Google and LinkedIn there
							once.
						</p>
						<p class="text-muted-foreground text-xs">
							Enter your Google password and any two-factor code in Chrome, then refresh here.
						</p>
						<div class="space-y-2" aria-live="polite">
							{#if browser?.running === false}
								<p class="text-muted-foreground text-sm">JobPilot's Chrome is not running</p>
							{:else}
								{#each signIns as site}
									<div class="flex items-center justify-between gap-2 text-sm">
										<span>{site.label}</span>
										<span
											class={browser?.running && browser.logged_in[site.key]
												? 'text-green-400'
												: 'text-muted-foreground'}
										>
											{browser === null
												? browserBusy
													? 'Checking…'
													: 'Status unavailable'
												: browser.logged_in[site.key]
													? 'Signed in'
													: 'Not signed in'}
										</span>
									</div>
								{/each}
							{/if}
						</div>
						<div class="flex flex-wrap gap-2">
							{#if browser?.view_url}
								<a
									href={browser.view_url}
									target="_blank"
									rel="noopener"
									onclick={() => { void refreshBrowser(true); }}
									class="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-1.5 text-xs"
								>Open the Chrome view</a>
							{:else}
								<button
									onclick={() => refreshBrowser(true)}
									disabled={browserBusy}
									class="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-1.5 text-xs disabled:opacity-50"
									>Open sign-in pages</button
								>
							{/if}
							<button
								onclick={() => refreshBrowser()}
								disabled={browserBusy}
								class="border-border hover:bg-accent rounded-md border px-3 py-1.5 text-xs disabled:opacity-50"
								>Refresh</button
							>
						</div>
						{#if browserError}<p role="alert" class="text-xs text-red-400">{browserError}</p>{/if}
						{#if browser?.view_url}
							<p class="text-muted-foreground text-xs">
								The Chrome view password is in
								<code>{browser.view_password_hint ?? 'data/chrome-view.password'}</code> on the Docker host.
							</p>
						{/if}
					</div>

					<div class="border-border flex items-center justify-between gap-2 border-t pt-4">
						<h3 class="text-sm font-medium">Tools</h3>
						<button
							onclick={refreshTools}
							disabled={toolsRefreshing}
							class="border-border hover:bg-accent rounded-md border px-3 py-1.5 text-xs disabled:opacity-50"
						>
							{toolsRefreshing ? 'Checking…' : 'Check tools'}
						</button>
					</div>
					{#if toolsError}
						<p role="alert" class="text-xs text-red-400">{toolsError}</p>
					{/if}
					<p class="text-muted-foreground text-sm">
						JobPilot writes with your logged-in Claude Code or Codex CLI, so no AI key is needed.
						Optional keys go in a <code class="bg-muted rounded px-1">.env</code> file at the project
						root; restart the backend after changing it.
					</p>

					<div class="space-y-2">
						<div class="flex items-center gap-2 text-sm">
							{#if status?.llm_key_set}
								<CheckCircle2 size={15} class="text-green-500" />
								<span>Writing model found (Claude Code, Codex or an AI key)</span>
							{:else}
								<XCircle size={15} class="text-red-400" />
								<span class="text-muted-foreground"
									>No writing model: install and log in to Claude Code or Codex</span
								>
							{/if}
						</div>
						<div class="flex items-center gap-2 text-sm">
							{#if status?.adzuna_key_set}
								<CheckCircle2 size={15} class="text-green-500" />
								<span>Adzuna keys set</span>
							{:else}
								<CheckCircle2 size={15} class="text-muted-foreground" />
								<span class="text-muted-foreground">Adzuna not set (optional extra job source)</span
								>
							{/if}
						</div>
						<div class="flex items-center gap-2 text-sm">
							{#if status?.tectonic_found}
								<CheckCircle2 size={15} class="text-green-500" />
								<span>Tectonic (LaTeX) found</span>
							{:else}
								<XCircle size={15} class="text-red-400" />
								<span class="text-muted-foreground"
									>Tectonic not found: run <code class="bg-muted rounded px-1"
										>python scripts/download_tectonic.py</code
									></span
								>
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
							<input
								type="file"
								accept=".tex,.cls,.pdf,.docx"
								onchange={handleCvUpload}
								class="sr-only"
							/>
							<div class="flex flex-col items-center gap-2">
								<Upload size={28} class="text-muted-foreground" />
								{#if cvUploading}
									<p class="text-muted-foreground text-sm">Uploading / converting…</p>
								{:else}
									<p class="text-sm font-medium">Click to upload your CV (.tex, .pdf or .docx)</p>
									<p class="text-muted-foreground text-xs">
										PDF/DOCX are converted to LaTeX with your LLM (may take a moment).
									</p>
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
						disabled={step === 1 && (!youReady || profileSaving || passwordSaving)}
						class="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-md px-4 py-1.5 text-xs transition-colors disabled:opacity-50"
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
