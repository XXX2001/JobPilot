<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import KanbanBoard from '$lib/components/KanbanBoard.svelte';
	import { AlertCircle, RefreshCw } from 'lucide-svelte';
	import type { Application } from '$lib/components/KanbanBoard.svelte';

	let applications = $state<Application[]>([]);
	let loading = $state(true);
	let error = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			const data = await apiFetch<{ applications: Application[]; total: number }>('/api/applications');
			applications = data.applications ?? [];
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
		} catch (err: any) {
			error = err.message ?? 'Failed to add event';
		}
	}

	onMount(load);
</script>

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

{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />
		{error}
		<button onclick={() => (error = '')} class="ml-auto hover:text-red-300">✕</button>
	</div>
{/if}

{#if loading}
	<div class="flex gap-3 h-80">
		{#each Array(5) as _}
			<div class="w-60 flex-shrink-0 bg-muted rounded-lg animate-pulse"></div>
		{/each}
	</div>
{:else if applications.length === 0}
	<div class="flex flex-col items-center justify-center py-20 gap-3 text-center">
		<div class="text-4xl">📋</div>
		<p class="text-muted-foreground text-sm font-medium">No applications yet.</p>
		<p class="text-muted-foreground text-xs">Apply to jobs from the Morning Queue to see them here.</p>
		<a
			href="/"
			class="mt-2 text-xs px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
		>
			Go to Morning Queue
		</a>
	</div>
{:else}
	<div class="h-[calc(100vh-180px)]">
		<KanbanBoard
			{applications}
			on:update={handleUpdate}
			on:addEvent={handleAddEvent}
		/>
	</div>
{/if}
