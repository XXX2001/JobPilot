<script lang="ts">
	import type { MatchBrief } from '$lib/types/today';

	interface Props {
		highConfidence: MatchBrief[];
		worthReviewing: MatchBrief[];
		skipped: MatchBrief[];
		total: number;
		since: string;
	}

	let { highConfidence, worthReviewing, skipped, total, since }: Props = $props();

	function sinceLabel(iso: string): string {
		if (!iso) return 'last 24 h';
		const dt = new Date(iso);
		const now = new Date();
		const diffMs = now.getTime() - dt.getTime();
		const diffH = Math.floor(diffMs / 3_600_000);
		if (diffH < 1) return 'the last hour';
		if (diffH < 24) return `the last ${diffH} h`;
		const diffD = Math.floor(diffH / 24);
		return `the last ${diffD} day${diffD !== 1 ? 's' : ''}`;
	}

	function scoreColor(score: number): string {
		if (score >= 80) return 'text-green-400';
		if (score >= 60) return 'text-yellow-400';
		return 'text-red-400';
	}
</script>

<section>
	<div class="mb-3 flex items-center justify-between">
		<h2 class="text-sm font-semibold tracking-tight">What's new</h2>
		{#if total > 0}
			<span class="text-xs text-muted-foreground">{total} since {sinceLabel(since)}</span>
		{/if}
	</div>

	{#if total === 0}
		<p class="text-xs text-muted-foreground py-4 text-center">
			No new matches since {sinceLabel(since)}. Click "Scan for Jobs" to discover opportunities.
		</p>
	{:else}
		{#if highConfidence.length > 0}
			<div class="mb-3">
				<p class="text-xs font-medium text-muted-foreground mb-1.5 uppercase tracking-wide">
					High confidence ({highConfidence.length})
				</p>
				<div class="space-y-1.5">
					{#each highConfidence as m (m.id)}
						<a
							href="/queue"
							class="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm hover:bg-accent/50 transition-colors"
						>
							<span class="w-10 flex-shrink-0 text-center text-sm font-bold {scoreColor(m.score)}">
								{Math.round(m.score)}%
							</span>
							<div class="min-w-0 flex-1">
								<p class="truncate font-medium">{m.job_title ?? '—'}</p>
								<p class="truncate text-xs text-muted-foreground">{m.company ?? '—'}</p>
							</div>
						</a>
					{/each}
				</div>
			</div>
		{/if}

		{#if worthReviewing.length > 0}
			<div class="mb-3">
				<p class="text-xs font-medium text-muted-foreground mb-1.5 uppercase tracking-wide">
					Worth reviewing ({worthReviewing.length})
				</p>
				<div class="space-y-1.5">
					{#each worthReviewing as m (m.id)}
						<a
							href="/queue"
							class="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm hover:bg-accent/50 transition-colors"
						>
							<span class="w-10 flex-shrink-0 text-center text-sm font-bold {scoreColor(m.score)}">
								{Math.round(m.score)}%
							</span>
							<div class="min-w-0 flex-1">
								<p class="truncate font-medium">{m.job_title ?? '—'}</p>
								<p class="truncate text-xs text-muted-foreground">{m.company ?? '—'}</p>
							</div>
						</a>
					{/each}
				</div>
			</div>
		{/if}

		{#if skipped.length > 0}
			<div>
				<p class="text-xs font-medium text-muted-foreground mb-1.5 uppercase tracking-wide">
					Low match ({skipped.length})
				</p>
				<div class="space-y-1.5">
					{#each skipped as m (m.id)}
						<a
							href="/queue"
							class="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm hover:bg-accent/50 transition-colors opacity-60"
						>
							<span class="w-10 flex-shrink-0 text-center text-sm font-bold {scoreColor(m.score)}">
								{Math.round(m.score)}%
							</span>
							<div class="min-w-0 flex-1">
								<p class="truncate font-medium">{m.job_title ?? '—'}</p>
								<p class="truncate text-xs text-muted-foreground">{m.company ?? '—'}</p>
							</div>
						</a>
					{/each}
				</div>
			</div>
		{/if}
	{/if}
</section>
