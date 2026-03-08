<script lang="ts">
	import { apiFetch } from '$lib/api';
	import ScoreIndicator from './ScoreIndicator.svelte';
	import { send } from '$lib/stores/websocket';
	import { Briefcase, MapPin, DollarSign, Clock, ExternalLink, Eye, FileText, ChevronDown, ChevronUp } from 'lucide-svelte';
	import { createEventDispatcher } from 'svelte';

	interface JobMatch {
		id: number;
		job_id: number;
		score: number;
		status: string;
		batch_date: string;
		job: {
			id: number;
			title: string;
			company: string;
			location: string;
			salary_min?: number;
			salary_max?: number;
			description?: string;
			url: string;
			apply_url: string;
			apply_method: string;
			posted_at?: string;
		};
	}

	let { match }: { match: JobMatch } = $props();
	const dispatch = createEventDispatcher<{ skip: number; apply: { id: number; method: string } }>();

	let showMethodMenu = $state(false);
	let applying = $state(false);
	let expanded = $state(false);

	const timeAgo = (dateStr?: string) => {
		if (!dateStr) return '';
		const d = new Date(dateStr);
		const now = new Date();
		const hrs = Math.round((now.getTime() - d.getTime()) / 3600000);
		if (hrs < 24) return `${hrs}h ago`;
		return `${Math.floor(hrs / 24)}d ago`;
	};

	const salary = $derived(() => {
		const j = match.job;
		if (!j.salary_min && !j.salary_max) return null;
		if (j.salary_min && j.salary_max)
			return `£${Math.round(j.salary_min / 1000)}k–£${Math.round(j.salary_max / 1000)}k`;
		if (j.salary_min) return `£${Math.round(j.salary_min / 1000)}k+`;
		return `up to £${Math.round((j.salary_max ?? 0) / 1000)}k`;
	});

	async function skip() {
		await apiFetch(`/api/queue/${match.id}/skip`, { method: 'PATCH' });
		dispatch('skip', match.id);
	}

	async function apply(method: string) {
		showMethodMenu = false;
		applying = true;
		dispatch('apply', { id: match.id, method });
		applying = false;
	}
</script>

<div class="job-card group border border-border rounded-lg p-4 bg-card hover:border-border/80 hover:bg-card/90 transition-all">
	<div class="flex items-start gap-4">
		<!-- Score -->
		<div class="flex-shrink-0">
			<ScoreIndicator score={Math.round(match.score)} />
		</div>

		<!-- Content -->
		<div class="flex-1 min-w-0">
			<div class="flex items-start justify-between gap-2">
				<div>
					<a href="/jobs/{match.id}" class="text-sm font-medium hover:underline line-clamp-1">
						{match.job.title}
					</a>
					<div class="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground flex-wrap">
						<span class="flex items-center gap-1"><Briefcase size={11} />{match.job.company}</span>
						{#if match.job.location}
							<span class="flex items-center gap-1"><MapPin size={11} />{match.job.location}</span>
						{/if}
						{#if salary()}
							<span class="flex items-center gap-1"><DollarSign size={11} />{salary()}</span>
						{/if}
						{#if match.job.posted_at}
							<span class="flex items-center gap-1"><Clock size={11} />{timeAgo(match.job.posted_at)}</span>
						{/if}
					</div>
				</div>

				<!-- Score badge -->
				<span class="score-badge flex-shrink-0 text-xs font-medium px-2 py-0.5 rounded-full {match.score >= 80 ? 'bg-green-500/10 text-green-400' : match.score >= 60 ? 'bg-yellow-500/10 text-yellow-400' : 'bg-red-500/10 text-red-400'}">
					{Math.round(match.score)}%
				</span>
			</div>
		</div>
	</div>

	<!-- Apply URL + expandable description -->
	<div class="mt-2 ml-[52px]">
		{#if match.job.apply_url}
			{@const applyHost = (() => { try { return new URL(match.job.apply_url).hostname; } catch { return match.job.apply_url; } })()}
			<a
				href={match.job.apply_url}
				target="_blank"
				rel="noopener noreferrer"
				class="inline-flex items-center gap-1 text-xs text-blue-400/70 hover:text-blue-400 truncate max-w-[200px]"
			>
				<ExternalLink size={12} />{applyHost}
			</a>
		{/if}

		{#if match.job.description}
			<button
				class="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 mt-1"
				onclick={() => (expanded = !expanded)}
			>
				{#if expanded}
					<ChevronUp size={14} /> Hide description
				{:else}
					<ChevronDown size={14} /> Show description
				{/if}
			</button>

			{#if expanded}
				<div class="mt-2 text-xs text-zinc-400 leading-relaxed bg-zinc-800/30 rounded p-3 max-h-[300px] overflow-y-auto">
					{match.job.description}
				</div>
			{/if}
		{/if}
	</div>

	<!-- Actions -->
	<div class="flex items-center gap-2 mt-3 pt-3 border-t border-border/50">
		<a href="/jobs/{match.id}" class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
			<Eye size={13} />Preview CV
		</a>
		<a href="/jobs/{match.id}?tab=letter" class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
			<FileText size={13} />Letter
		</a>

		<div class="flex-1"></div>

		<button
			onclick={skip}
			class="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded hover:bg-accent transition-colors"
		>
			Skip
		</button>

		<!-- Apply button with method selector -->
		<div class="relative">
			<div class="flex rounded-md overflow-hidden border border-border">
				<button
					onclick={() => apply('manual')}
					disabled={applying}
					class="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
				>
					{#if applying}Applying…{:else}Apply{/if}
				</button>
				<button
					onclick={() => (showMethodMenu = !showMethodMenu)}
					class="flex items-center px-1.5 bg-primary text-primary-foreground border-l border-primary-foreground/20 hover:bg-primary/90 transition-colors"
				>
					<ChevronDown size={12} />
				</button>
			</div>

			{#if showMethodMenu}
				<div class="absolute right-0 bottom-full mb-1 w-36 bg-popover border border-border rounded-md shadow-lg py-1 z-10">
					{#each [['manual', 'Open & Apply'], ['assisted', 'Assisted'], ['auto', 'Auto Apply']] as [m, label]}
						<button
							onclick={() => apply(m)}
							class="w-full text-left px-3 py-1.5 text-xs hover:bg-accent transition-colors"
						>{label}</button>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</div>
