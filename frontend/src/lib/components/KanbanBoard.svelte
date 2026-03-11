<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { Briefcase, Clock, GripVertical, MessageSquare, ChevronDown } from 'lucide-svelte';
	import { getRejectionMilestone } from '$lib/utils/easterEggs';

	export interface Application {
		id: number;
		job_match_id?: number;
		method: string;
		status: string;
		applied_at?: string;
		notes?: string;
		error_log?: string;
		created_at: string;
		events: ApplicationEvent[];
		// denormalized fields for display (injected by parent)
		job_title?: string;
		company?: string;
	}

	export interface ApplicationEvent {
		id: number;
		application_id: number;
		event_type: string;
		details?: string;
		event_date: string;
	}

	const COLUMNS: { id: string; label: string; color: string }[] = [
		{ id: 'applied', label: 'Applied', color: 'border-blue-500/40' },
		{ id: 'heard_back', label: 'Heard Back', color: 'border-yellow-500/40' },
		{ id: 'interview', label: 'Interview', color: 'border-purple-500/40' },
		{ id: 'offer', label: 'Offer', color: 'border-green-500/40' },
		{ id: 'rejected', label: 'Rejected', color: 'border-red-500/40' }
	];

	let { applications = [] }: { applications: Application[] } = $props();

	const dispatch = createEventDispatcher<{
		update: { id: number; status: string };
		addEvent: { id: number; event_type: string; details?: string };
	}>();

	let draggedId = $state<number | null>(null);
	let dragOverCol = $state<string | null>(null);
	let noteInputs = $state<Record<number, string>>({});
	let showEventMenu = $state<number | null>(null);

	const byColumn = $derived(() => {
		const map: Record<string, Application[]> = {};
		for (const col of COLUMNS) map[col.id] = [];
		for (const app of applications) {
			const col = app.status.toLowerCase().replace(' ', '_');
			if (map[col]) map[col].push(app);
			else map['applied'].push(app);
		}
		return map;
	});

	let rejectedCount = $derived((byColumn()['rejected'] ?? []).length);
	let rejectionMessage = $derived(
		(() => {
			const thresholds = [200, 150, 100, 75, 50, 25, 10];
			for (const t of thresholds) {
				if (rejectedCount >= t) {
					return getRejectionMilestone(t);
				}
			}
			return null;
		})()
	);

	function onDragStart(e: DragEvent, id: number) {
		draggedId = id;
		if (e.dataTransfer) {
			e.dataTransfer.effectAllowed = 'move';
		}
	}

	function onDragOver(e: DragEvent, col: string) {
		e.preventDefault();
		dragOverCol = col;
	}

	function onDrop(e: DragEvent, col: string) {
		e.preventDefault();
		if (draggedId !== null && col !== getColForApp(draggedId)) {
			dispatch('update', { id: draggedId, status: col });
		}
		draggedId = null;
		dragOverCol = null;
	}

	function onDragEnd() {
		draggedId = null;
		dragOverCol = null;
	}

	function getColForApp(id: number): string {
		const app = applications.find((a) => a.id === id);
		if (!app) return 'applied';
		return app.status.toLowerCase().replace(' ', '_');
	}

	function addNote(appId: number) {
		const note = (noteInputs[appId] ?? '').trim();
		if (!note) return;
		dispatch('addEvent', { id: appId, event_type: 'note', details: note });
		noteInputs = { ...noteInputs, [appId]: '' };
	}

	function addEvent(appId: number, eventType: string) {
		dispatch('addEvent', { id: appId, event_type: eventType });
		showEventMenu = null;
	}

	const timeAgo = (dateStr: string) => {
		const d = new Date(dateStr);
		const now = new Date();
		const days = Math.floor((now.getTime() - d.getTime()) / 86400000);
		if (days === 0) return 'today';
		if (days === 1) return 'yesterday';
		return `${days}d ago`;
	};

	const methodBadge: Record<string, string> = {
		auto: 'bg-green-500/10 text-green-400',
		assisted: 'bg-blue-500/10 text-blue-400',
		manual: 'bg-muted text-muted-foreground'
	};
</script>

<div class="flex gap-3 h-full overflow-x-auto pb-2">
	{#each COLUMNS as col}
		{@const items = byColumn()[col.id] ?? []}
		<div
			class="flex flex-col flex-shrink-0 w-60 rounded-lg border {dragOverCol === col.id
				? 'border-primary/50 bg-primary/5'
				: 'border-border bg-card/50'} transition-colors"
			ondragover={(e) => onDragOver(e, col.id)}
			ondrop={(e) => onDrop(e, col.id)}
			role="region"
			aria-label="{col.label} column"
		>
			<!-- Column header -->
			<div class="flex items-center gap-2 px-3 py-2.5 border-b border-border {col.color} border-l-4">
				<span class="text-xs font-medium flex-1">{col.label}</span>
				<span class="text-xs px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">{items.length}</span>
			</div>

			{#if col.id === 'rejected' && rejectionMessage}
				<div class="px-3 py-1.5 text-xs italic text-amber-400/70 border-b border-border bg-amber-500/5">
					{rejectionMessage.emoji} {rejectionMessage.message}
				</div>
			{/if}

			<!-- Cards -->
			<div class="flex-1 overflow-y-auto p-2 space-y-2">
				{#each items as app (app.id)}
					<div
						draggable="true"
						ondragstart={(e) => onDragStart(e, app.id)}
						ondragend={onDragEnd}
						class="group bg-card border border-border rounded-md p-3 cursor-grab active:cursor-grabbing hover:border-border/80 transition-colors {draggedId === app.id ? 'opacity-40' : ''}"
						role="article"
					>
						<!-- Drag handle + title -->
						<div class="flex items-start gap-1.5">
							<GripVertical size={12} class="text-muted-foreground/40 mt-0.5 flex-shrink-0 group-hover:text-muted-foreground transition-colors" />
							<div class="flex-1 min-w-0">
								{#if app.job_match_id}
									<a href="/jobs/{app.job_match_id}" class="text-xs font-medium line-clamp-1 hover:text-primary hover:underline transition-colors">{app.job_title ?? 'Unknown Position'}</a>
								{:else}
									<p class="text-xs font-medium line-clamp-1">{app.job_title ?? 'Unknown Position'}</p>
								{/if}
								<div class="flex items-center gap-1.5 mt-0.5 text-xs text-muted-foreground">
									<Briefcase size={10} />
									<span class="truncate">{app.company ?? '—'}</span>
								</div>
							</div>
						</div>

						<!-- Meta row -->
						<div class="flex items-center gap-2 mt-2">
							<span class="text-xs px-1.5 py-0.5 rounded capitalize {methodBadge[app.method] ?? methodBadge.manual}">{app.method}</span>
							<span class="flex items-center gap-1 text-xs text-muted-foreground ml-auto">
								<Clock size={9} />
								{timeAgo(app.applied_at ?? app.created_at)}
							</span>
						</div>

						<!-- Events -->
						{#if app.events.length > 0}
							<div class="mt-2 pt-2 border-t border-border/50 space-y-1">
								{#each app.events.slice(-2) as evt}
									<p class="text-xs text-muted-foreground truncate">
										<span class="capitalize">{evt.event_type.replace('_', ' ')}</span>
										{#if evt.details} · {evt.details}{/if}
									</p>
								{/each}
							</div>
						{/if}

						<!-- Note input -->
						<div class="mt-2 flex gap-1">
							<input
								type="text"
								placeholder="Add note…"
								value={noteInputs[app.id] ?? ''}
								oninput={(e) => {
									noteInputs = { ...noteInputs, [app.id]: (e.target as HTMLInputElement).value };
								}}
								onkeydown={(e) => e.key === 'Enter' && addNote(app.id)}
								class="flex-1 text-xs px-2 py-1 bg-muted/50 border border-border rounded text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-primary/50"
							/>
							<div class="relative">
								<button
									onclick={() => (showEventMenu = showEventMenu === app.id ? null : app.id)}
									class="text-xs px-1.5 py-1 border border-border rounded hover:bg-accent transition-colors"
									title="Add event"
								>
									<ChevronDown size={11} />
								</button>
								{#if showEventMenu === app.id}
									<div class="absolute right-0 bottom-full mb-1 w-36 bg-popover border border-border rounded-md shadow-lg py-1 z-20">
										{#each [['heard_back', 'Heard Back'], ['interview', 'Interview'], ['offer', 'Offer'], ['rejected', 'Rejection']] as [et, label]}
											<button
												onclick={() => addEvent(app.id, et)}
												class="w-full text-left px-3 py-1.5 text-xs hover:bg-accent transition-colors"
											>{label}</button>
										{/each}
									</div>
								{/if}
							</div>
						</div>
					</div>
				{/each}

				{#if items.length === 0}
					<div class="flex items-center justify-center py-8 text-muted-foreground/40 text-xs">
						Drop here
					</div>
				{/if}
			</div>
		</div>
	{/each}
</div>
