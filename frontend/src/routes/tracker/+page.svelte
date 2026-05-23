<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import KanbanBoard from '$lib/components/KanbanBoard.svelte';
	import { AlertCircle, RefreshCw } from 'lucide-svelte';
	import type { Application } from '$lib/components/KanbanBoard.svelte';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
	import EasterEggToast from '$lib/components/EasterEggToast.svelte';
	import { getEmptyState, getRejectionMilestone } from '$lib/utils/easterEggs';

	let applications = $state<Application[]>([]);
	let followUpApplications = $state<Application[]>([]);
	let loading = $state(true);
	let error = $state('');
	let activeTab = $state<'all' | 'follow_up'>('all');

	const appEmptyMessage = $derived(applications.length === 0 ? getEmptyState('applications') : '');
	let milestoneToast = $state<{ message: string; emoji: string; isSpecial: boolean } | null>(null);

	async function load() {
		loading = true;
		error = '';
		try {
			const [allData, fuData] = await Promise.all([
				apiFetch<{ applications: Application[]; total: number }>('/api/applications'),
				apiFetch<{ applications: Application[]; total: number }>(
					'/api/applications?needs_follow_up=true'
				)
			]);
			applications = allData.applications ?? [];
			followUpApplications = fuData.applications ?? [];
		} catch (e: any) {
			error = e.message ?? 'Failed to load applications';
		} finally {
			loading = false;
		}
	}

	async function handleUpdate(e: CustomEvent<{ id: number; status: string }>) {
		const { id, status } = e.detail;
		// Optimistic update
		applications = applications.map((a) =>
			a.id === id ? { ...a, status } : a
		);
		try {
			await apiFetch(`/api/applications/${id}`, {
				method: 'PATCH',
				body: JSON.stringify({ status })
			});
			// Check rejection milestone
			if (status === 'rejected') {
				const rejectedCount = applications.filter((a) => a.status === 'rejected').length;
				const milestone = getRejectionMilestone(rejectedCount);
				if (milestone) {
					milestoneToast = milestone;
				}
			}
		} catch (err: any) {
			error = err.message ?? 'Failed to update status';
			load(); // revert by reloading
		}
	}

	async function handleAddEvent(e: CustomEvent<{ id: number; event_type: string; details?: string }>) {
		const { id, event_type, details } = e.detail;
		try {
			const newEvent = await apiFetch<Application['events'][number]>(
				`/api/applications/${id}/events`,
				{
					method: 'POST',
					body: JSON.stringify({ event_type, details })
				}
			);
			applications = applications.map((a) => {
				if (a.id !== id) return a;
				return { ...a, events: [...(a.events ?? []), newEvent] };
			});
			// Also move to matching status column if event implies status change
			const statusMap: Record<string, string> = {
				heard_back: 'heard_back',
				interview: 'interview',
				offer: 'offer',
				rejected: 'rejected'
			};
			if (statusMap[event_type]) {
				handleUpdate(new CustomEvent('update', { detail: { id, status: statusMap[event_type] } }));
			}
			// If a follow_up event was logged, refresh follow-up list so the count badge updates.
			if (event_type === 'follow_up') {
				load();
			}
		} catch (err: any) {
			error = err.message ?? 'Failed to add event';
		}
	}

	onMount(load);
</script>

{#if milestoneToast}
	<EasterEggToast
		message={milestoneToast.message}
		emoji={milestoneToast.emoji}
		type={milestoneToast.isSpecial ? 'celebration' : 'milestone'}
		onclose={() => (milestoneToast = null)}
	/>
{/if}

<!-- Header -->
<div class="flex items-center justify-between mb-5">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Application Tracker</h1>
		<p class="text-xs text-muted-foreground mt-0.5">
			{applications.length} application{applications.length !== 1 ? 's' : ''} tracked
		</p>
	</div>
	<button
		onclick={load}
		disabled={loading}
		class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
	>
		<RefreshCw size={13} class={loading ? 'animate-spin' : ''} />
		Refresh
	</button>
</div>

<!-- Tabs -->
<div class="flex gap-1 mb-4 border-b border-border">
	<button
		onclick={() => (activeTab = 'all')}
		class="px-3 py-1.5 text-xs font-medium rounded-t-md transition-colors {activeTab === 'all'
			? 'bg-background border border-b-background border-border -mb-px text-foreground'
			: 'text-muted-foreground hover:text-foreground'}"
	>
		All Applications
	</button>
	<button
		onclick={() => (activeTab = 'follow_up')}
		class="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-t-md transition-colors {activeTab === 'follow_up'
			? 'bg-background border border-b-background border-border -mb-px text-foreground'
			: 'text-muted-foreground hover:text-foreground'}"
	>
		Needs follow-up
		{#if followUpApplications.length > 0}
			<span
				class="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-semibold bg-orange-500 text-white"
			>
				{followUpApplications.length}
			</span>
		{/if}
	</button>
</div>

{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />
		{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{/if}

{#if activeTab === 'all'}
	{#if loading}
		<div class="flex gap-3 h-80">
			{#each Array(5) as _}
				<div class="w-60 flex-shrink-0 bg-muted rounded-lg animate-pulse"></div>
			{/each}
		</div>
	{:else if applications.length === 0}
		<div class="flex flex-col items-center justify-center py-20 gap-3 text-center">
			<FloatingEmoji emoji="📋" />
			<p class="text-muted-foreground text-sm font-medium">{appEmptyMessage}</p>
			<p class="text-muted-foreground text-xs">Apply to jobs from the Job Queue to see them here.</p>
			<a
				href="/"
				class="mt-2 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
			>
				Go to Job Queue
			</a>
		</div>
	{:else}
		<div class="h-[calc(100vh-220px)]">
			<KanbanBoard
				{applications}
				on:update={handleUpdate}
				on:addEvent={handleAddEvent}
			/>
		</div>
	{/if}
{:else}
	<!-- Needs follow-up tab -->
	{#if loading}
		<div class="flex gap-3 h-80">
			{#each Array(3) as _}
				<div class="w-60 flex-shrink-0 bg-muted rounded-lg animate-pulse"></div>
			{/each}
		</div>
	{:else if followUpApplications.length === 0}
		<div class="flex flex-col items-center justify-center py-20 gap-3 text-center">
			<FloatingEmoji emoji="✅" />
			<p class="text-muted-foreground text-sm font-medium">All caught up!</p>
			<p class="text-muted-foreground text-xs">
				No applications are waiting for a follow-up right now.
			</p>
		</div>
	{:else}
		<div class="h-[calc(100vh-220px)]">
			<KanbanBoard
				applications={followUpApplications}
				on:update={handleUpdate}
				on:addEvent={handleAddEvent}
			/>
		</div>
	{/if}
{/if}
