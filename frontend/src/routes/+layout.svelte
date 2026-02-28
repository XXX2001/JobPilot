<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import { ModeWatcher, toggleMode } from 'mode-watcher';
	import '../app.css';
	import { wsStatus, connectWs } from '$lib/stores/websocket';
	import { onMount } from 'svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import { page } from '$app/stores';
	import {
		LayoutDashboard,
		KanbanSquare,
		FileText,
		Settings,
		BarChart2,
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

	const navLinks = [
		{ href: '/', label: 'Morning Queue', icon: LayoutDashboard },
		{ href: '/tracker', label: 'Tracker', icon: KanbanSquare },
		{ href: '/cv', label: 'CV Manager', icon: FileText },
		{ href: '/settings', label: 'Settings', icon: Settings },
		{ href: '/analytics', label: 'Analytics', icon: BarChart2 }
	];
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<ModeWatcher defaultMode="dark" />

<div class="flex h-screen bg-background text-foreground overflow-hidden">
	<!-- Sidebar -->
	<aside class="w-[220px] flex-shrink-0 border-r border-border flex flex-col py-4 px-3 gap-1">
		<!-- Logo -->
		<div class="px-3 py-2 mb-3">
			<span class="text-lg font-semibold tracking-tight">JobPilot</span>
		</div>

		<!-- Nav links -->
		{#each navLinks as link}
			{@const isActive = $page.url.pathname === link.href}
			<a
				href={link.href}
				class="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors
					{isActive
					? 'bg-accent text-accent-foreground font-medium'
					: 'text-muted-foreground hover:text-foreground hover:bg-accent/50'}"
			>
				<link.icon size={16} />
				{link.label}
			</a>
		{/each}

		<!-- Spacer -->
		<div class="flex-1"></div>

		<!-- WS status indicator -->
		<div class="px-3 py-2 flex items-center gap-2 text-xs text-muted-foreground">
			{#if $wsStatus === 'connected'}
				<Wifi size={14} class="text-green-500" />
				<span>Connected</span>
			{:else if $wsStatus === 'reconnecting'}
				<Loader2 size={14} class="animate-spin text-yellow-500" />
				<span>Reconnecting…</span>
			{:else}
				<WifiOff size={14} class="text-red-500" />
				<span>Offline</span>
			{/if}
		</div>

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

