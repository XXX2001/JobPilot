<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { getApplyConfirmation } from '$lib/utils/easterEggs';
	import ScoreIndicator from '$lib/components/ScoreIndicator.svelte';
	import {
		ArrowLeft,
		Briefcase,
		MapPin,
		DollarSign,
		Clock,
		ExternalLink,
		Zap,
		MousePointer,
		Globe,
		FileText,
		AlertCircle,
		CheckCircle2
	} from 'lucide-svelte';

	interface Job {
		id: number;
		title: string;
		company: string;
		location?: string;
		salary_min?: number;
		salary_max?: number;
		description?: string;
		url: string;
		apply_url?: string;
		posted_at?: string;
		apply_method?: string;
	}

	interface QueueMatch {
		id: number;
		job_id: number;
		score: number;
		status: string;
		batch_date?: string;
		matched_at: string;
		job: Job;
	}

	interface DiffEntry {
		section: string;
		original_text: string;
		edited_text: string;
		change_description: string;
	}

	interface DiffResponse {
		match_id: number;
		diff: DiffEntry[];
		generated_at?: string;
	}

	const matchId = $derived(parseInt($page.params.id ?? '0'));

	let matchData = $state<QueueMatch | null>(null);
	const job = $derived(matchData?.job ?? null);
	const score = $derived(matchData?.score ?? 0);
	let diff = $state<DiffEntry[]>([]);
	let loading = $state(true);
	let applyLoading = $state('');
	let error = $state('');
	let successMsg = $state('');
	let activeTab = $state<'description' | 'diff'>('description');
	let enriching = $state(false);

	const salary = $derived(() => {
		if (!job) return null;
		if (!job.salary_min && !job.salary_max) return null;
		if (job.salary_min && job.salary_max)
			return `£${Math.round(job.salary_min / 1000)}k – £${Math.round(job.salary_max / 1000)}k`;
		if (job.salary_min) return `£${Math.round(job.salary_min / 1000)}k+`;
		return `up to £${Math.round((job.salary_max ?? 0) / 1000)}k`;
	});

	const timeAgo = (dateStr?: string) => {
		if (!dateStr) return '';
		const d = new Date(dateStr);
		const now = new Date();
		const hrs = Math.round((now.getTime() - d.getTime()) / 3600000);
		if (hrs < 1) return 'just now';
		if (hrs < 24) return `${hrs}h ago`;
		return `${Math.floor(hrs / 24)}d ago`;
	};

	async function load() {
		loading = true;
		error = '';
		try {
			matchData = await apiFetch<QueueMatch>(`/api/queue/${matchId}`);
			try {
				const diffData = await apiFetch<DiffResponse>(`/api/documents/${matchId}/diff`);
				diff = diffData.diff ?? [];
			} catch {
				diff = [];
			}
		} catch (e: any) {
			error = e.message ?? 'Failed to load job';
		} finally {
			loading = false;
		}
	}

	async function applyWith(method: string) {
		applyLoading = method;
		error = '';
		successMsg = '';
		try {
			const res = await apiFetch<{ status: string; method: string; message: string }>(`/api/applications/${matchId}/apply`, {
				method: 'POST',
				body: JSON.stringify({ method })
			});
			successMsg = method === 'manual'
				? (res.message || 'Job opened — apply manually. Tailored CV copied to ~/Downloads.')
				: method === 'assisted'
				? 'Assisted apply started — follow the browser instructions.'
				: 'Auto-apply queued — confirm in the pop-up when ready.';
		} catch (e: any) {
			error = e.message ?? 'Apply failed';
		} finally {
			applyLoading = '';
		}
	}

	const isEasyApply = $derived(job?.apply_method === 'easy_apply' || job?.apply_method === 'auto');
	let applyQuote = $state(getApplyConfirmation());

	onMount(load);
</script>

<!-- Breadcrumb -->
<div class="flex items-center gap-2 text-xs text-muted-foreground mb-5">
	<a href="/" class="flex items-center gap-1 hover:text-foreground transition-colors">
		<ArrowLeft size={13} />
		Job Queue
	</a>
	{#if job}
		<span>/</span>
		<span class="text-foreground truncate max-w-xs">{job.title} @ {job.company}</span>
	{/if}
</div>

{#if loading}
	<div class="space-y-4 animate-pulse">
		<div class="h-7 bg-muted rounded w-1/2"></div>
		<div class="h-4 bg-muted rounded w-1/3"></div>
		<div class="h-64 bg-muted rounded-lg mt-6"></div>
	</div>
{:else if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />
		{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{:else if job}
	<!-- Header -->
	<div class="flex items-start gap-4 mb-6">
		<ScoreIndicator score={Math.round(score)} />
		<div class="flex-1 min-w-0">
			<h1 class="text-xl font-semibold tracking-tight">{job.title}</h1>
			<div class="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
				<span class="flex items-center gap-1"><Briefcase size={12} />{job.company}</span>
				{#if job.location}
					<span class="flex items-center gap-1"><MapPin size={12} />{job.location}</span>
				{/if}
				{#if salary()}
					<span class="flex items-center gap-1"><DollarSign size={12} />{salary()}</span>
				{/if}
				{#if job.posted_at}
					<span class="flex items-center gap-1"><Clock size={12} />{timeAgo(job.posted_at)}</span>
				{/if}
				{#if job.apply_method}
					<span class="px-2 py-0.5 rounded-full text-xs bg-accent text-accent-foreground capitalize">{job.apply_method.replace('_', ' ')}</span>
				{/if}
			</div>
		</div>

		<!-- Action buttons -->
		<div class="flex items-center gap-2 flex-shrink-0">
			<a
				href={job.url}
				target="_blank"
				rel="noopener noreferrer"
				class="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors"
			>
				<ExternalLink size={13} />
				View Listing
			</a>
		</div>
	</div>

	<!-- Success message -->
	{#if successMsg}
		<div class="flex items-center gap-2 text-xs text-green-400 bg-green-500/10 border border-green-500/20 rounded-md px-3 py-2 mb-4">
			<CheckCircle2 size={13} />
			{successMsg}
			<button onclick={() => (successMsg = '')} class="ml-auto hover:text-green-300">✕</button>
		</div>
	{/if}

	<!-- Apply section -->
	<p class="text-xs italic text-muted-foreground/60 mb-2 animate-fade-in-up">{applyQuote}</p>
	<div class="flex items-center gap-2 mb-6 p-4 bg-card border border-border rounded-lg">
		<span class="text-xs text-muted-foreground mr-2">Apply via:</span>

		<button
			onclick={() => applyWith('auto')}
			disabled={!!applyLoading}
			class="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md bg-green-600 text-white hover:bg-green-700 transition-colors disabled:opacity-50"
		>
			<Zap size={12} />
			{applyLoading === 'auto' ? 'Starting…' : 'Auto Apply'}
		</button>

		<button
			onclick={() => applyWith('assisted')}
			disabled={!!applyLoading}
			class="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
		>
			<MousePointer size={12} />
			{applyLoading === 'assisted' ? 'Starting…' : 'Assisted Apply'}
		</button>

		<button
			onclick={() => applyWith('manual')}
			disabled={!!applyLoading}
			class="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
		>
			<Globe size={12} />
			{applyLoading === 'manual' ? 'Opening…' : 'Open & Apply'}
		</button>
	</div>

	<!-- Tabs: Description | CV Diff -->
	<div class="flex items-center gap-1 border-b border-border mb-4">
		<button
			onclick={() => (activeTab = 'description')}
			class="text-xs px-4 py-2 border-b-2 transition-colors {activeTab === 'description'
				? 'border-primary text-foreground font-medium'
				: 'border-transparent text-muted-foreground hover:text-foreground'}"
		>
			Description
		</button>
		<button
			onclick={() => (activeTab = 'diff')}
			class="flex items-center gap-1.5 text-xs px-4 py-2 border-b-2 transition-colors {activeTab === 'diff'
				? 'border-primary text-foreground font-medium'
				: 'border-transparent text-muted-foreground hover:text-foreground'}"
		>
			<FileText size={12} />
			CV Diff
			{#if diff.length > 0}
				<span class="ml-1 px-1.5 py-0.5 rounded-full bg-primary/20 text-primary text-xs">{diff.length}</span>
			{/if}
		</button>
	</div>

	{#if activeTab === 'description'}
		<div class="bg-card border border-border rounded-lg p-5 max-w-3xl">
			{#if job.description}
				<div class="prose prose-sm max-w-none text-sm text-foreground leading-relaxed whitespace-pre-wrap">
					{job.description}
				</div>
			{:else}
				<p class="text-muted-foreground text-sm">No description available for this listing.</p>
			{/if}
			{#if !job.description || job.description.length < 300}
				<button
					onclick={async () => {
						enriching = true;
						error = '';
						try {
							const res = await apiFetch<{ status: string; description: string }>(`/api/queue/${matchId}/enrich-description`, { method: 'POST' });
							if (res.description && matchData?.job) {
								matchData = { ...matchData, job: { ...matchData.job, description: res.description } };
							}
						} catch (e: any) {
							error = e.message ?? 'Failed to fetch full description';
						} finally {
							enriching = false;
						}
					}}
					disabled={enriching}
					class="mt-3 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
				>
					<Globe size={12} />
					{enriching ? 'Fetching full description…' : 'Fetch Full Description'}
				</button>
			{/if}
		</div>
	{:else}
		<!-- CV Diff view -->
		<div class="max-w-3xl">
			{#if diff.length === 0}
				<div class="flex flex-col items-center justify-center py-16 gap-3 bg-card border border-border rounded-lg">
					<FileText size={32} class="text-muted-foreground/40" />
					<p class="text-sm text-muted-foreground font-medium">No CV changes yet</p>
					<p class="text-xs text-muted-foreground">CV tailoring runs during the job scan. Check back after it completes.</p>
				</div>
			{:else}
				<div class="space-y-3">
					{#each diff as entry, i (i)}
						<div class="bg-card border border-border rounded-lg overflow-hidden">
							<div class="px-4 py-2 bg-muted/50 border-b border-border flex items-center gap-2">
								<span class="text-xs font-medium text-muted-foreground">{entry.section}</span>
								{#if entry.change_description}
									<span class="text-xs text-muted-foreground/70">· {entry.change_description}</span>
								{/if}
							</div>
							<div class="p-4 space-y-2">
								{#if entry.original_text}
									<div class="flex gap-2">
										<span class="text-red-500 text-xs font-mono mt-0.5 flex-shrink-0">−</span>
										<p class="text-xs line-through text-muted-foreground leading-relaxed">{entry.original_text}</p>
									</div>
								{/if}
								{#if entry.edited_text}
									<div class="flex gap-2">
										<span class="text-green-500 text-xs font-mono mt-0.5 flex-shrink-0">+</span>
										<p class="text-xs text-green-400 leading-relaxed">{entry.edited_text}</p>
									</div>
								{/if}
							</div>
						</div>
					{/each}
				</div>

				<!-- PDF preview link -->
				<div class="mt-4 flex items-center gap-2">
					<a
						href="/api/documents/{matchId}/cv/pdf"
						target="_blank"
						rel="noopener noreferrer"
						class="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors"
					>
						<FileText size={12} />
						View Tailored CV (PDF)
					</a>
				</div>
			{/if}
		</div>
	{/if}
{/if}
