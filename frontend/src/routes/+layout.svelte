<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import { ModeWatcher, toggleMode } from 'mode-watcher';
	import '../app.css';
	import { wsStatus, connectWs } from '$lib/stores/websocket';
	import { dailyLimit, limitColour } from '$lib/stores/dailyLimit';
	import { toasts, dismissToast } from '$lib/stores/toast';
	import { onMount } from 'svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import LoginRequiredModal from '$lib/components/LoginRequiredModal.svelte';
	import HotkeyHelp from '$lib/components/HotkeyHelp.svelte';
	import { page } from '$app/stores';
	import { handle as hotkeyHandle, setCurrentRoute } from '$lib/utils/hotkeys';
	import {
		LayoutDashboard,
		KanbanSquare,
		Route,
		FileText,
		Mail,
		Settings,
		BarChart2,
		Inbox,
		Compass,
		Sun,
		Moon,
		Wifi,
		WifiOff,
		Loader2
	} from 'lucide-svelte';

	let { children } = $props();

	onMount(() => {
		connectWs();
	});

	// Keep the dispatcher in sync with the current route.
	$effect(() => {
		setCurrentRoute($page.route.id ?? '/');
	});

	const navLinks = [
		{ href: '/', label: 'Today', icon: LayoutDashboard },
		{ href: '/queue', label: 'Queue', icon: KanbanSquare },
		{ href: '/tracker', label: 'Tracker', icon: Route },
		{ href: '/inbox', label: 'Inbox', icon: Inbox },
		{ href: '/cv', label: 'CV Manager', icon: FileText },
		{ href: '/letters', label: 'Letters', icon: Mail },
		{ href: '/settings', label: 'Settings', icon: Settings },
		{ href: '/analytics', label: 'Analytics', icon: BarChart2 }
	];
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<svelte:window onkeydown={hotkeyHandle} />

<ModeWatcher defaultMode="dark" />

<LoginRequiredModal />
<HotkeyHelp />

<!-- Global toast stack (top-right). Populated via pushToast() in $lib/stores/toast. -->
{#if $toasts.length > 0}
	<div class="fixed top-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
		{#each $toasts as t (t.id)}
			<div
				role="status"
				class="rounded-lg border px-4 py-3 shadow-lg backdrop-blur-sm bg-card/95 animate-fade-in-up
					{t.kind === 'success'
						? 'border-emerald-500/30'
						: t.kind === 'warning'
							? 'border-amber-500/30'
							: t.kind === 'error'
								? 'border-red-500/30'
								: 'border-border'}"
			>
				<div class="flex items-start gap-3">
					<div class="flex-1 min-w-0">
						<p class="text-sm font-medium leading-snug text-foreground break-words">{t.message}</p>
						{#if t.href}
							<a
								href={t.href}
								class="mt-1 inline-block text-xs text-primary hover:underline"
								onclick={() => dismissToast(t.id)}
							>
								{t.hrefLabel ?? 'Open'}
							</a>
						{/if}
					</div>
					<button
						onclick={() => dismissToast(t.id)}
						class="text-muted-foreground hover:text-foreground text-xs flex-shrink-0 mt-0.5 transition-colors"
						aria-label="Dismiss notification"
					>
						✕
					</button>
				</div>
			</div>
		{/each}
	</div>
{/if}

<div class="flex h-screen text-foreground overflow-hidden">
	<!-- Sidebar -->
	<aside class="glass w-[224px] flex-shrink-0 border-y-0 border-l-0 border-r flex flex-col py-5 px-3 gap-0.5">
		<!-- Wordmark -->
		<div class="px-3 py-2 mb-4 flex items-center gap-2.5">
			<span
				class="grid h-8 w-8 place-items-center rounded-xl aurora-ring aurora-glow text-primary"
			>
				<Compass size={17} strokeWidth={2.25} />
			</span>
			<span class="font-display text-[1.35rem] font-semibold leading-none text-aurora">JobPilot</span>
		</div>

		<!-- Nav links -->
		{#each navLinks as link}
			{@const isActive = $page.url.pathname === link.href}
			<a
				href={link.href}
				class="group relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-200
					{isActive
					? 'glass aurora-ring text-foreground font-medium shadow-aurora'
					: 'text-muted-foreground hover:text-foreground hover:bg-accent/60'}"
			>
				{#if isActive}
					<span class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-full"
						style="background:linear-gradient(hsl(var(--aurora-from)),hsl(var(--aurora-to)))"></span>
				{/if}
				<link.icon size={16} class={isActive ? 'text-primary' : 'transition-transform group-hover:scale-110'} />
				{link.label}
			</a>
		{/each}

		<!-- Spacer -->
		<div class="flex-1"></div>

		<!-- WS status indicator -->
		<div class="px-3 py-2 flex items-center gap-2.5 text-xs text-muted-foreground">
			{#if $wsStatus === 'connected'}
				<span class="relative inline-flex h-2 w-2 items-center justify-center">
					<span class="animate-ping-soft text-emerald-400"></span>
					<span class="relative h-2 w-2 rounded-full bg-emerald-400"></span>
				</span>
				<span>Live</span>
			{:else if $wsStatus === 'reconnecting'}
				<Loader2 size={14} class="animate-spin text-amber-400" />
				<span>Reconnecting…</span>
			{:else}
				<WifiOff size={14} class="text-red-400" />
				<span>Offline</span>
			{/if}
		</div>

		<!-- Daily limit pill -->
		{#if $dailyLimit !== null}
			<div class="px-3 py-1 flex items-center gap-2 text-xs">
				<span class="font-medium {limitColour($dailyLimit.used)}">
					{$dailyLimit.used} / {$dailyLimit.limit} today
				</span>
			</div>
		{/if}

		<!-- Dark mode toggle -->
		<button
			onclick={toggleMode}
			class="mx-3 flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground
				hover:text-foreground hover:bg-accent/50 transition-colors"
		>
			<Sun size={16} class="dark:hidden" />
			<Moon size={16} class="hidden dark:block" />
			<span class="dark:hidden">Light mode</span>
			<span class="hidden dark:block">Dark mode</span>
		</button>
	</aside>

	<!-- Main area -->
	<div class="flex flex-col flex-1 overflow-hidden">
		<!-- Page content -->
		<main class="flex-1 overflow-y-auto p-6">
			{@render children()}
		</main>

		<!-- Status bar -->
		<StatusBar />
	</div>
</div>

