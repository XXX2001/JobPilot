<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { CheckCircle2, AlertCircle, Upload, ArrowRight, X } from 'lucide-svelte';

	interface SetupStatus {
		gemini_key_set: boolean;
		adzuna_key_set: boolean;
		tectonic_found: boolean;
		base_cv_uploaded: boolean;
		setup_complete: boolean;
	}

	let { status }: { status: SetupStatus } = $props();

	const dispatch = createEventDispatcher<{ close: void; complete: void }>();

	let step = $state(1);
	let keywords = $state<string[]>([]);
	let keywordInput = $state('');
	let cvUploading = $state(false);
	let cvDone = $derived(status.base_cv_uploaded);
	let error = $state('');

	const envSnippet = `# .env file (project root)
GOOGLE_API_KEY=your_gemini_api_key_here
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key`;

	async function handleCvUpload(e: Event) {
		const file = (e.target as HTMLInputElement).files?.[0];
		if (!file) return;
		if (!file.name.endsWith('.tex')) {
			error = 'Please select a .tex file.';
			return;
		}
		cvUploading = true;
		error = '';
		try {
			await apiFetch('/api/settings/profile', {
				method: 'PUT',
				body: JSON.stringify({ base_cv_path: `uploads/${file.name}` })
			});
			cvDone = true;
		} catch (e: any) {
			error = e.message ?? 'Upload failed';
		} finally {
			cvUploading = false;
		}
	}

	async function saveKeywords() {
		if (keywords.length > 0) {
			try {
				await apiFetch('/api/settings/search', {
					method: 'PUT',
					body: JSON.stringify({ keywords: { include: keywords } })
				});
			} catch {
				// non-critical
			}
		}
		dispatch('complete');
	}

	function addKeyword() {
		const kw = keywordInput.trim();
		if (kw && !keywords.includes(kw)) {
			keywords = [...keywords, kw];
		}
		keywordInput = '';
	}

	const steps = [
		{ n: 1, label: 'API Keys' },
		{ n: 2, label: 'CV Template' },
		{ n: 3, label: 'Keywords' }
	];
</script>

<!-- Modal overlay -->
<div class="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
	<div class="bg-card border border-border rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
		<!-- Header -->
		<div class="p-5 border-b border-border flex items-center gap-3">
			<div class="flex-1">
				<h2 class="font-semibold">Welcome to JobPilot</h2>
				<p class="text-xs text-muted-foreground mt-0.5">Complete setup to start applying</p>
			</div>
			<button onclick={() => dispatch('close')} class="text-muted-foreground hover:text-foreground transition-colors">
				<X size={16} />
			</button>
		</div>

		<!-- Progress -->
		<div class="px-5 pt-4 pb-2">
			<div class="flex items-center gap-2">
				{#each steps as s, i}
					<div class="flex items-center gap-2 flex-1">
						<div class="flex items-center gap-1.5">
							<div class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium {step === s.n
								? 'bg-primary text-primary-foreground'
								: step > s.n
								? 'bg-green-500/20 text-green-400'
								: 'bg-muted text-muted-foreground'}">
								{#if step > s.n}
									<CheckCircle2 size={12} />
								{:else}
									{s.n}
								{/if}
							</div>
							<span class="text-xs {step === s.n ? 'text-foreground font-medium' : 'text-muted-foreground'}">{s.label}</span>
						</div>
						{#if i < steps.length - 1}
							<div class="flex-1 h-px bg-border mx-1"></div>
						{/if}
					</div>
				{/each}
			</div>
		</div>

		<!-- Step content -->
		<div class="p-5">
			{#if error}
				<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
					<AlertCircle size={12} />{error}
				</div>
			{/if}

			{#if step === 1}
				<!-- Step 1: API Keys -->
				<div class="space-y-4">
					<p class="text-sm text-muted-foreground">
						JobPilot needs API keys to scrape jobs and tailor your CV. Set them in a <code class="bg-muted px-1 rounded">.env</code> file at the project root.
					</p>

					<div class="space-y-2">
						<div class="flex items-center gap-2 text-xs">
							{#if status.gemini_key_set}
								<CheckCircle2 size={13} class="text-green-500" />
								<span>Gemini API key set</span>
							{:else}
								<AlertCircle size={13} class="text-yellow-500" />
								<span class="text-muted-foreground">Gemini API key missing</span>
							{/if}
						</div>
						<div class="flex items-center gap-2 text-xs">
							{#if status.adzuna_key_set}
								<CheckCircle2 size={13} class="text-green-500" />
								<span>Adzuna keys set</span>
							{:else}
								<AlertCircle size={13} class="text-yellow-500" />
								<span class="text-muted-foreground">Adzuna keys missing</span>
							{/if}
						</div>
					</div>

					{#if !status.gemini_key_set || !status.adzuna_key_set}
						<div>
							<p class="text-xs text-muted-foreground mb-2">Copy this snippet to your <code class="bg-muted px-1 rounded">.env</code> file:</p>
							<pre class="text-xs font-mono bg-muted p-3 rounded-lg overflow-x-auto text-muted-foreground leading-relaxed">{envSnippet}</pre>
						</div>
					{/if}
				</div>

			{:else if step === 2}
				<!-- Step 2: CV Upload -->
				<div class="space-y-4">
					<p class="text-sm text-muted-foreground">
						Upload your base LaTeX CV. JobPilot will surgically tailor it for each job using <code class="bg-muted px-1 rounded">%%JOBPILOT:marker%%</code> zones.
					</p>

					{#if cvDone}
						<div class="flex items-center gap-2 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-xs text-green-400">
							<CheckCircle2 size={13} />
							CV template uploaded.
						</div>
					{:else}
						<label class="block border-2 border-dashed border-border rounded-lg p-6 text-center cursor-pointer hover:border-primary/50 transition-colors relative">
							<input type="file" accept=".tex" onchange={handleCvUpload} class="sr-only" />
							<div class="flex flex-col items-center gap-2">
								<Upload size={24} class="text-muted-foreground" />
								{#if cvUploading}
									<p class="text-sm text-muted-foreground">Uploading…</p>
								{:else}
									<p class="text-sm font-medium">Click to upload .tex file</p>
								{/if}
							</div>
						</label>
					{/if}
				</div>

			{:else if step === 3}
				<!-- Step 3: Keywords -->
				<div class="space-y-4">
					<p class="text-sm text-muted-foreground">
						Enter your target job titles or keywords. You can change these anytime in Settings.
					</p>
					<div class="space-y-2">
						<div class="flex flex-wrap gap-1.5 p-2 bg-background border border-border rounded-md min-h-[38px]">
							{#each keywords as kw}
								<span class="flex items-center gap-1 text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
									{kw}
									<button onclick={() => (keywords = keywords.filter((k) => k !== kw))} class="hover:text-red-400">×</button>
								</span>
							{/each}
							<input
								type="text"
								placeholder="ex. Ingénieur logiciel, Python…"
								bind:value={keywordInput}
								onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addKeyword(); } }}
								class="flex-1 min-w-32 bg-transparent text-xs focus:outline-none placeholder:text-muted-foreground/60"
							/>
						</div>
						<p class="text-xs text-muted-foreground">Press Enter to add each keyword.</p>
					</div>
				</div>
			{/if}
		</div>

		<!-- Footer -->
		<div class="flex items-center justify-between px-5 pb-5">
			<button
				onclick={() => dispatch('close')}
				class="text-xs text-muted-foreground hover:text-foreground transition-colors"
			>
				Skip for now
			</button>
			<div class="flex items-center gap-2">
				{#if step > 1}
					<button
						onclick={() => (step -= 1)}
						class="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors"
					>
						Back
					</button>
				{/if}
				{#if step < 3}
					<button
						onclick={() => (step += 1)}
						class="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
					>
						Next <ArrowRight size={12} />
					</button>
				{:else}
					<button
						onclick={saveKeywords}
						class="flex items-center gap-1.5 text-xs px-4 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
					>
						<CheckCircle2 size={12} />
						Finish Setup
					</button>
				{/if}
			</div>
		</div>
	</div>
</div>
