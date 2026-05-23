<script lang="ts">
	import type { WeekStatsSection } from '$lib/types/today';
	import { Send, BarChart2, MessageSquare } from 'lucide-svelte';

	interface Props {
		stats: WeekStatsSection;
	}

	let { stats }: Props = $props();

	function limitColor(used: number, total: number): string {
		const ratio = total > 0 ? used / total : 0;
		if (ratio >= 0.9) return 'text-red-500';
		if (ratio >= 0.7) return 'text-amber-500';
		return 'text-green-400';
	}
</script>

<section>
	<div class="mb-3">
		<h2 class="text-sm font-semibold tracking-tight">This week</h2>
	</div>

	<div class="grid grid-cols-3 gap-3">
		<!-- Applications submitted -->
		<div class="rounded-lg border border-border bg-card p-3 flex flex-col gap-1">
			<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<Send size={12} />
				<span>Submitted</span>
			</div>
			<p class="text-2xl font-bold tabular-nums">{stats.applications_submitted}</p>
			<p class="text-xs text-muted-foreground">last 7 days</p>
		</div>

		<!-- Daily limit -->
		<div class="rounded-lg border border-border bg-card p-3 flex flex-col gap-1">
			<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<BarChart2 size={12} />
				<span>Today's quota</span>
			</div>
			<p class="text-2xl font-bold tabular-nums {limitColor(stats.daily_limit_used, stats.daily_limit_total)}">
				{stats.daily_limit_used}<span class="text-base font-normal text-muted-foreground">/{stats.daily_limit_total}</span>
			</p>
			<p class="text-xs text-muted-foreground">resets at midnight UTC</p>
		</div>

		<!-- Response rate -->
		<div class="rounded-lg border border-border bg-card p-3 flex flex-col gap-1">
			<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<MessageSquare size={12} />
				<span>Response rate</span>
			</div>
			<p class="text-lg font-medium text-muted-foreground leading-tight">{stats.response_rate}</p>
		</div>
	</div>
</section>
