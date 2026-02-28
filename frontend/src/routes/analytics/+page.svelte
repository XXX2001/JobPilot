<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import SetupWizard from '$lib/components/SetupWizard.svelte';
	import { TrendingUp, Users, BarChart, Calendar, AlertCircle } from 'lucide-svelte';

	interface Summary {
		total_apps: number;
		apps_this_week: number;
		response_rate: number;
		avg_match_score?: number;
	}

	interface DailyTrend {
		date: string;
		count: number;
	}

	interface TrendsResponse {
		trends: DailyTrend[];
		days: number;
	}

	interface SetupStatus {
		gemini_key_set: boolean;
		adzuna_key_set: boolean;
		tectonic_found: boolean;
		base_cv_uploaded: boolean;
		setup_complete: boolean;
	}

	let summary = $state<Summary | null>(null);
	let trends = $state<DailyTrend[]>([]);
	let setupStatus = $state<SetupStatus | null>(null);
	let showWizard = $state(false);
	let loading = $state(true);
	let error = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			const [s, t, status] = await Promise.all([
				apiFetch<Summary>('/api/analytics/summary'),
				apiFetch<TrendsResponse>('/api/analytics/trends?days=30'),
				apiFetch<SetupStatus>('/api/settings/status')
			]);
			summary = s;
			trends = t.trends ?? [];
			setupStatus = status;
			showWizard = !status.setup_complete;
		} catch (e: any) {
			error = e.message ?? 'Failed to load analytics';
		} finally {
			loading = false;
		}
	}

	// Bar chart helpers
	const maxCount = $derived(Math.max(...trends.map((t) => t.count), 1));

	const BAR_H = 100; // SVG chart height in px

	const chartWidth = $derived(trends.length * 12);

	function barHeight(count: number) {
		return Math.max(2, (count / maxCount) * BAR_H);
	}

	// Source breakdown (computed from summary; actual sources TBD)
	const statCards = $derived(
		summary
			? [
					{
						label: 'Total Applications',
						value: summary.total_apps,
						icon: Users,
						color: 'text-blue-400',
						bg: 'bg-blue-500/10'
					},
					{
						label: 'Response Rate',
						value: `${summary.response_rate}%`,
						icon: TrendingUp,
						color: 'text-green-400',
						bg: 'bg-green-500/10'
					},
					{
						label: 'Avg Match Score',
						value: summary.avg_match_score != null ? `${summary.avg_match_score}%` : '—',
						icon: BarChart,
						color: 'text-purple-400',
						bg: 'bg-purple-500/10'
					},
					{
						label: 'This Week',
						value: summary.apps_this_week,
						icon: Calendar,
						color: 'text-yellow-400',
						bg: 'bg-yellow-500/10'
					}
			  ]
			: []
	);

	onMount(load);
</script>

{#if showWizard && setupStatus}
	<SetupWizard
		status={setupStatus}
		on:close={() => (showWizard = false)}
		on:complete={() => { showWizard = false; load(); }}
	/>
{/if}

<!-- Header -->
<div class="flex items-center justify-between mb-6">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Analytics</h1>
		<p class="text-xs text-muted-foreground mt-0.5">Your application activity at a glance.</p>
	</div>
	{#if setupStatus && !setupStatus.setup_complete}
		<button
			onclick={() => (showWizard = true)}
			class="text-xs px-3 py-1.5 rounded-md border border-yellow-500/30 text-yellow-400 bg-yellow-500/10 hover:bg-yellow-500/20 transition-colors"
		>
			Complete Setup
		</button>
	{/if}
</div>

{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto">✕</button>
	</div>
{/if}

{#if loading}
	<div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
		{#each Array(4) as _}
			<div class="h-24 bg-muted rounded-xl animate-pulse"></div>
		{/each}
	</div>
	<div class="h-40 bg-muted rounded-xl animate-pulse"></div>
{:else}
	<!-- Stat cards -->
	<div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
		{#each statCards as card}
			<div class="bg-card border border-border rounded-xl p-4 space-y-2">
				<div class="flex items-center justify-between">
					<p class="text-xs text-muted-foreground">{card.label}</p>
					<div class="w-8 h-8 rounded-lg {card.bg} flex items-center justify-center">
					<card.icon size={15} class={card.color} />
					</div>
				</div>
				<p class="text-2xl font-semibold tracking-tight">{card.value}</p>
			</div>
		{/each}
	</div>

	<!-- Bar chart: applications per day -->
	<div class="bg-card border border-border rounded-xl p-5 mb-6">
		<h2 class="text-sm font-medium mb-4">Applications per day (last 30 days)</h2>

		{#if trends.every((t) => t.count === 0)}
			<div class="flex flex-col items-center justify-center py-10 text-muted-foreground/50">
				<BarChart size={32} />
				<p class="text-sm mt-2">No data yet</p>
			</div>
		{:else}
			<div class="overflow-x-auto">
				<svg
					width={Math.max(chartWidth, 600)}
					height={BAR_H + 30}
					class="block"
					aria-label="Applications per day bar chart"
				>
					{#each trends as day, i}
						{@const x = i * 12}
						{@const h = barHeight(day.count)}
						{@const y = BAR_H - h}
						<g>
							<rect
								{x}
								{y}
								width="9"
								height={h}
								rx="2"
								class="fill-primary/70 hover:fill-primary transition-colors"
							/>
							{#if day.count > 0}
								<text
									x={x + 4.5}
									y={y - 3}
									text-anchor="middle"
									class="fill-muted-foreground text-[9px]"
									font-size="9"
								>{day.count}</text>
							{/if}
							<!-- X-axis label: show every 5th day -->
							{#if i % 5 === 0}
								<text
									x={x + 4.5}
									y={BAR_H + 20}
									text-anchor="middle"
									class="fill-muted-foreground text-[9px]"
									font-size="9"
								>{day.date.slice(5)}</text>
							{/if}
						</g>
					{/each}
				</svg>
			</div>
		{/if}
	</div>

	<!-- Summary footer -->
	{#if summary && summary.total_apps > 0}
		<div class="bg-card border border-border rounded-xl p-5">
			<h2 class="text-sm font-medium mb-3">Insights</h2>
			<div class="space-y-2 text-xs text-muted-foreground">
				<p>
					You have submitted <span class="text-foreground font-medium">{summary.total_apps}</span> application{summary.total_apps !== 1 ? 's' : ''} total,
					with a <span class="text-foreground font-medium">{summary.response_rate}%</span> response rate.
				</p>
				{#if summary.apps_this_week > 0}
					<p>This week: <span class="text-foreground font-medium">{summary.apps_this_week}</span> application{summary.apps_this_week !== 1 ? 's' : ''} sent.</p>
				{/if}
				{#if summary.avg_match_score != null}
					<p>Average match score: <span class="text-foreground font-medium">{summary.avg_match_score}%</span>. {summary.avg_match_score >= 70 ? 'Great targeting!' : summary.avg_match_score >= 50 ? 'Consider refining your keywords.' : 'Try broader search terms.'}</p>
				{/if}
			</div>
		</div>
	{/if}
{/if}
