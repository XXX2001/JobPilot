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
		<h2 class="font-display text-base font-semibold tracking-tight">This week</h2>
	</div>

	<div class="grid grid-cols-3 gap-3">
		<!-- Applications submitted -->
		<div class="glass group rounded-xl p-4 flex flex-col gap-1 transition-shadow hover:shadow-aurora">
			<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<Send size={12} />
				<span>Submitted</span>
			</div>
			<p class="font-mono-num text-aurora text-3xl font-bold leading-none">{stats.applications_submitted}</p>
			<p class="text-xs text-muted-foreground">last 7 days</p>
		</div>

		<!-- Daily limit -->
		<div class="glass rounded-xl p-4 flex flex-col gap-1 transition-shadow hover:shadow-aurora">
			<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<BarChart2 size={12} />
				<span>Today's quota</span>
			</div>
			<p class="font-mono-num text-3xl font-bold leading-none {limitColor(stats.daily_limit_used, stats.daily_limit_total)}">
				{stats.daily_limit_used}<span class="text-base font-normal text-muted-foreground">/{stats.daily_limit_total}</span>
			</p>
			<p class="text-xs text-muted-foreground">resets at midnight UTC</p>
		</div>

		<!-- Response rate -->
		<div class="glass rounded-xl p-4 flex flex-col gap-1 transition-shadow hover:shadow-aurora">
			<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<MessageSquare size={12} />
				<span>Response rate</span>
			</div>
			<p class="font-mono-num text-foreground text-2xl font-semibold leading-tight">{stats.response_rate}</p>
		</div>
	</div>
</section>
