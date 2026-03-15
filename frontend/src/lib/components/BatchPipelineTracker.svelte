<script lang="ts">
	import { messages } from '$lib/stores/websocket';
	import { Search, BarChart3, Database, Brain, FileText, CheckCircle2, AlertTriangle } from 'lucide-svelte';

	type StepStatus = 'waiting' | 'active' | 'done' | 'error';

	interface PipelineStep {
		id: string;
		label: string;
		icon: typeof Search;
		progressRange: [number, number]; // [start, end)
	}

	const steps: PipelineStep[] = [
		{ id: 'scrape', label: 'Scanning Sources', icon: Search, progressRange: [0, 0.35] },
		{ id: 'rank', label: 'Ranking & Filtering', icon: BarChart3, progressRange: [0.35, 0.55] },
		{ id: 'store', label: 'Storing Matches', icon: Database, progressRange: [0.55, 0.58] },
		{ id: 'fit', label: 'Fit Analysis', icon: Brain, progressRange: [0.58, 0.65] },
		{ id: 'cv', label: 'CV Generation', icon: FileText, progressRange: [0.65, 1.0] },
	];

	// Extract latest status message
	const latestStatus = $derived.by(() => {
		const msgs = $messages;
		for (let i = msgs.length - 1; i >= 0; i--) {
			if (msgs[i]?.type === 'status') return msgs[i];
		}
		return null;
	});

	const progress = $derived(latestStatus?.progress ?? 0);
	const statusMessage = $derived(latestStatus?.message ?? 'Initializing...');
	const isError = $derived(progress < 0);
	const isComplete = $derived(progress >= 1.0);

	function getStepStatus(step: PipelineStep): StepStatus {
		if (isError) {
			// Mark the active step as error, everything after as waiting
			const activeIdx = steps.findIndex(s => progress >= 0 ?
				(progress >= s.progressRange[0] && progress < s.progressRange[1]) : false);
			const idx = steps.indexOf(step);
			if (idx < activeIdx) return 'done';
			if (idx === activeIdx) return 'error';
			return 'waiting';
		}
		if (isComplete) return 'done';
		if (progress >= step.progressRange[1]) return 'done';
		if (progress >= step.progressRange[0]) return 'active';
		return 'waiting';
	}

	// Calculate sub-progress within the active step (0-1)
	function getStepProgress(step: PipelineStep): number {
		const status = getStepStatus(step);
		if (status === 'done') return 1;
		if (status === 'waiting' || status === 'error') return 0;
		const [start, end] = step.progressRange;
		const range = end - start;
		if (range <= 0) return 0;
		return Math.min(1, Math.max(0, (progress - start) / range));
	}

	// Time tracking
	let startTime = $state(Date.now());
	let elapsed = $state(0);
	let intervalId: ReturnType<typeof setInterval>;

	$effect(() => {
		startTime = Date.now();
		intervalId = setInterval(() => {
			elapsed = Math.floor((Date.now() - startTime) / 1000);
		}, 1000);
		return () => clearInterval(intervalId);
	});

	function formatElapsed(s: number): string {
		const m = Math.floor(s / 60);
		const sec = s % 60;
		return m > 0 ? `${m}m ${sec.toString().padStart(2, '0')}s` : `${sec}s`;
	}
</script>

<div class="pipeline-tracker mx-auto max-w-lg py-8">
	<!-- Header -->
	<div class="mb-8 text-center">
		<div class="inline-flex items-center gap-2 rounded-full border border-amber-500/20 bg-amber-500/5 px-4 py-1.5 text-xs">
			{#if isComplete}
				<span class="h-1.5 w-1.5 rounded-full bg-green-400"></span>
				<span class="font-medium text-green-400">Pipeline Complete</span>
			{:else if isError}
				<span class="h-1.5 w-1.5 rounded-full bg-red-400"></span>
				<span class="font-medium text-red-400">Pipeline Error</span>
			{:else}
				<span class="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse"></span>
				<span class="font-mono font-medium text-amber-400">BATCH IN PROGRESS</span>
			{/if}
			<span class="text-muted-foreground">·</span>
			<span class="font-mono text-muted-foreground tabular-nums">{formatElapsed(elapsed)}</span>
		</div>
	</div>

	<!-- Pipeline Steps -->
	<div class="relative pl-8">
		{#each steps as step, i (step.id)}
			{@const status = getStepStatus(step)}
			{@const stepProgress = getStepProgress(step)}
			{@const isLast = i === steps.length - 1}

			<div class="relative pb-8 {isLast ? 'pb-0' : ''}">
				<!-- Vertical connector line -->
				{#if !isLast}
					<div class="absolute left-[-20px] top-[28px] h-[calc(100%-12px)] w-px">
						<!-- Background line -->
						<div class="absolute inset-0 bg-border/50"></div>
						<!-- Progress fill -->
						{#if status === 'done'}
							<div class="absolute inset-x-0 top-0 h-full bg-green-500/60 transition-all duration-700"></div>
						{:else if status === 'active'}
							<div
								class="absolute inset-x-0 top-0 bg-amber-500/60 transition-all duration-500"
								style="height: {stepProgress * 100}%"
							></div>
						{/if}
					</div>
				{/if}

				<!-- Step node -->
				<div class="absolute left-[-28px] top-[2px]">
					{#if status === 'done'}
						<div class="flex h-[17px] w-[17px] items-center justify-center rounded-full bg-green-500/20 ring-1 ring-green-500/40">
							<CheckCircle2 size={11} class="text-green-400" />
						</div>
					{:else if status === 'error'}
						<div class="flex h-[17px] w-[17px] items-center justify-center rounded-full bg-red-500/20 ring-1 ring-red-500/40">
							<AlertTriangle size={11} class="text-red-400" />
						</div>
					{:else if status === 'active'}
						<div class="relative flex h-[17px] w-[17px] items-center justify-center">
							<div class="absolute inset-0 rounded-full bg-amber-500/20 ring-1 ring-amber-500/50 animate-glow-pulse"></div>
							<div class="h-[7px] w-[7px] rounded-full bg-amber-400 animate-pulse"></div>
						</div>
					{:else}
						<div class="flex h-[17px] w-[17px] items-center justify-center rounded-full bg-muted/30 ring-1 ring-border/50">
							<div class="h-[5px] w-[5px] rounded-full bg-muted-foreground/30"></div>
						</div>
					{/if}
				</div>

				<!-- Step content -->
				<div class="min-h-[40px]">
					<div class="flex items-center gap-2.5">
						<step.icon
							size={14}
							class={status === 'done'
								? 'text-green-400'
								: status === 'active'
									? 'text-amber-400'
									: status === 'error'
										? 'text-red-400'
										: 'text-muted-foreground/40'}
						/>
						<span
							class="text-sm font-medium {status === 'done'
								? 'text-green-400/80'
								: status === 'active'
									? 'text-foreground'
									: status === 'error'
										? 'text-red-400'
										: 'text-muted-foreground/40'}"
						>
							{step.label}
						</span>

						{#if status === 'done'}
							<span class="text-[10px] font-mono text-green-500/50 uppercase tracking-wider">done</span>
						{/if}
					</div>

					<!-- Active step detail -->
					{#if status === 'active'}
						<div class="mt-2.5 space-y-2">
							<!-- Live message -->
							<p class="font-mono text-xs text-muted-foreground leading-relaxed">
								{statusMessage}
							</p>

							<!-- Step sub-progress bar -->
							<div class="flex items-center gap-3">
								<div class="relative h-1 flex-1 overflow-hidden rounded-full bg-muted/30">
									<div
										class="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-500 ease-out"
										style="width: {stepProgress * 100}%"
									></div>
									<div class="absolute inset-0 bg-gradient-to-r from-transparent via-white/[0.07] to-transparent animate-progress-shimmer"></div>
								</div>
								<span class="font-mono text-[10px] text-amber-400/70 tabular-nums w-8 text-right">
									{Math.round(progress * 100)}%
								</span>
							</div>
						</div>
					{:else if status === 'error'}
						<p class="mt-2 font-mono text-xs text-red-400/80 leading-relaxed">
							{statusMessage}
						</p>
					{/if}
				</div>
			</div>
		{/each}
	</div>

	<!-- Overall progress -->
	{#if !isError && !isComplete}
		<div class="mt-8 border-t border-border/30 pt-4">
			<div class="flex items-center justify-between text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest">
				<span>Overall</span>
				<span class="tabular-nums">{Math.round(progress * 100)}%</span>
			</div>
			<div class="mt-1.5 h-0.5 w-full overflow-hidden rounded-full bg-muted/20">
				<div
					class="h-full rounded-full bg-amber-500/40 transition-all duration-700 ease-out"
					style="width: {progress * 100}%"
				></div>
			</div>
		</div>
	{/if}

	<!-- Completion state -->
	{#if isComplete}
		<div class="mt-6 rounded-lg border border-green-500/15 bg-green-500/5 px-4 py-3 text-center">
			<p class="text-sm font-medium text-green-400">All steps complete</p>
			<p class="mt-0.5 text-xs text-green-400/60">{statusMessage}</p>
		</div>
	{/if}
</div>
