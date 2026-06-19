<script lang="ts">
	import { onMount } from 'svelte';
	import { Inbox as InboxIcon, RefreshCw, Link2, Mail, AlertCircle } from 'lucide-svelte';
	import { fetchUnlinked, linkMessage, type UnlinkedItem } from '$lib/api/gmail';
	import LinkApplicationModal from '$lib/components/LinkApplicationModal.svelte';

	let items = $state<UnlinkedItem[]>([]);
	let loading = $state(true);
	let refreshing = $state(false);
	let error = $state<string | null>(null);
	let modalFor = $state<UnlinkedItem | null>(null);
	let modalOpen = $state(false);

	async function refresh() {
		if (items.length === 0) {
			loading = true;
		} else {
			refreshing = true;
		}
		error = null;
		try {
			items = await fetchUnlinked();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	onMount(refresh);

	function openLink(msg: UnlinkedItem) {
		modalFor = msg;
		modalOpen = true;
	}

	async function handleLink(applicationId: number) {
		if (!modalFor) return;
		try {
			await linkMessage(applicationId, modalFor.id);
			modalFor = null;
			await refresh();
		} catch (e) {
			error = (e as Error).message;
			throw e;
		}
	}

	function categoryClass(cat: string | null): string {
		switch (cat) {
			case 'rejection':
				return 'bg-red-500/10 text-red-400 border-red-500/20';
			case 'interview_invite':
				return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
			case 'offer':
				return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
			case 'ats_ack':
				return 'bg-muted/40 text-muted-foreground border-border/50';
			case 'recruiter_outreach':
				return 'bg-primary/10 text-primary border-primary/20';
			default:
				return 'bg-muted/20 text-muted-foreground border-border/50';
		}
	}

	function categoryLabel(cat: string | null): string {
		if (!cat) return 'other';
		return cat.replace(/_/g, ' ');
	}
</script>

<div class="max-w-4xl mx-auto">
	<header class="flex items-center justify-between gap-3 mb-6">
		<div class="flex items-center gap-3">
			<div class="w-10 h-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
				<InboxIcon size={20} />
			</div>
			<div>
				<h1 class="text-2xl font-semibold font-heading text-foreground tracking-tight">Inbox</h1>
				<p class="text-xs text-muted-foreground mt-0.5">
					Unlinked, job-related Gmail messages. Attach them to applications to enrich your tracker.
				</p>
			</div>
		</div>
		<button
			type="button"
			disabled={loading || refreshing}
			onclick={refresh}
			class="flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-lg bg-muted/40 hover:bg-muted/60 text-foreground transition-colors disabled:opacity-50 active:scale-[0.98]"
		>
			<RefreshCw size={14} class={refreshing ? 'animate-spin' : ''} />
			{refreshing ? 'Refreshing…' : 'Refresh'}
		</button>
	</header>

	{#if error}
		<div class="mb-4 flex items-center gap-3 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">
			<AlertCircle size={16} class="flex-shrink-0" />
			<span class="flex-1 font-medium">{error}</span>
		</div>
	{/if}

	{#if loading}
		<div class="space-y-2">
			{#each Array(3) as _, i (i)}
				<div class="h-20 bg-card/40 border border-border/30 rounded-2xl animate-pulse"></div>
			{/each}
		</div>
	{:else if items.length === 0}
		<div class="rounded-2xl border border-border/50 bg-card/40 p-10 text-center">
			<div class="w-12 h-12 rounded-xl bg-primary/10 text-primary flex items-center justify-center mx-auto mb-3">
				<Mail size={22} />
			</div>
			<p class="text-sm font-medium text-foreground">No unlinked messages</p>
			<p class="text-xs text-muted-foreground mt-1">
				Job-related Gmail messages that aren't yet attached to an application will show up here.
			</p>
		</div>
	{:else}
		<ul class="space-y-2">
			{#each items as msg (msg.id)}
				<li class="rounded-2xl border border-border/50 bg-card/40 p-4 hover:bg-card/60 transition-colors">
					<div class="flex items-start justify-between gap-4">
						<div class="min-w-0 flex-1 space-y-1.5">
							<div class="flex items-center gap-2 text-xs flex-wrap">
								<span
									class="px-2 py-0.5 rounded-md border font-medium {categoryClass(msg.category)}"
								>
									{categoryLabel(msg.category)}
								</span>
								<span class="text-muted-foreground truncate" title={msg.from_address}>
									{msg.from_address}
								</span>
								<span class="text-muted-foreground/60">·</span>
								<span class="text-muted-foreground">
									{new Date(msg.received_at).toLocaleString()}
								</span>
							</div>
							<p class="text-sm font-medium text-foreground truncate">
								{msg.subject ?? '(no subject)'}
							</p>
							{#if msg.snippet}
								<p class="text-xs text-muted-foreground line-clamp-2">{msg.snippet}</p>
							{/if}
						</div>
						<button
							type="button"
							onclick={() => openLink(msg)}
							class="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm hover:shadow active:scale-[0.98] whitespace-nowrap flex-shrink-0"
						>
							<Link2 size={12} />
							Link to app…
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<LinkApplicationModal bind:open={modalOpen} onLink={handleLink} />
