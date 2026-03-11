<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { Upload, FileText, CheckCircle2, AlertCircle, Eye, GitCompare, Clock } from 'lucide-svelte';
	import FloatingEmoji from '$lib/components/FloatingEmoji.svelte';
	import { getEmptyState } from '$lib/utils/easterEggs';

	interface Document {
		id: number;
		job_match_id?: number;
		doc_type: string;
		tex_path?: string;
		pdf_path?: string;
		diff_json?: unknown;
		created_at: string;
	}

	let documents = $state<Document[]>([]);
	let docsLoading = $state(true);
	const cvEmptyMessage = $derived(documents.length === 0 ? getEmptyState('cv') : '');
	let uploading = $state(false);
	let error = $state('');
	let successMsg = $state('');
	let dragOver = $state(false);
	let currentCvPath = $state('');
	let profileLoading = $state(true);

	async function loadProfile() {
		profileLoading = true;
		try {
			const p = await apiFetch<{ base_cv_path?: string }>('/api/settings/profile');
			currentCvPath = p.base_cv_path ?? '';
		} catch {
			//
		} finally {
			profileLoading = false;
		}
	}

	async function loadDocs() {
		docsLoading = true;
		try {
			const docs = await apiFetch<Document[]>('/api/documents');
			documents = docs.filter((d) => d.doc_type === 'cv');
		} catch {
			//
		} finally {
			docsLoading = false;
		}
	}

	async function handleFileUpload(file: File) {
		if (!file.name.endsWith('.tex')) {
			error = 'Please upload a .tex file.';
			return;
		}
		uploading = true;
		error = '';
		successMsg = '';

		try {
			const fileName = file.name;

			// Save profile with a placeholder path (real upload path is server-side)
			await apiFetch('/api/settings/profile', {
				method: 'PUT',
				body: JSON.stringify({ base_cv_path: `uploads/${fileName}` })
			});

			currentCvPath = `uploads/${fileName}`;
			successMsg = `CV template "${fileName}" registered.`;
		} catch (e: any) {
			error = e.message ?? 'Upload failed';
		} finally {
			uploading = false;
		}
	}

	function onDragOver(e: DragEvent) {
		e.preventDefault();
		dragOver = true;
	}

	function onDragLeave() {
		dragOver = false;
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragOver = false;
		const file = e.dataTransfer?.files[0];
		if (file) handleFileUpload(file);
	}

	function onFileInput(e: Event) {
		const file = (e.target as HTMLInputElement).files?.[0];
		if (file) handleFileUpload(file);
	}

	const timeAgo = (dateStr: string) => {
		const d = new Date(dateStr);
		const now = new Date();
		const days = Math.floor((now.getTime() - d.getTime()) / 86400000);
		if (days === 0) return 'today';
		if (days === 1) return 'yesterday';
		return `${days}d ago`;
	};

	onMount(() => {
		loadProfile();
		loadDocs();
	});
</script>

<!-- Header -->
<div class="mb-6">
	<h1 class="text-xl font-semibold tracking-tight">CV Manager</h1>
	<p class="text-xs text-muted-foreground mt-0.5">Upload your base LaTeX CV template and view tailored versions.</p>
</div>

<!-- Messages -->
{#if error}
	<div class="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mb-4">
		<AlertCircle size={13} />{error}
		<button onclick={() => (error = '')} class="ml-auto">✕</button>
	</div>
{/if}
{#if successMsg}
	<div class="flex items-center gap-2 text-xs text-green-400 bg-green-500/10 border border-green-500/20 rounded-md px-3 py-2 mb-4">
		<CheckCircle2 size={13} />{successMsg}
		<button onclick={() => (successMsg = '')} class="ml-auto">✕</button>
	</div>
{/if}

<div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
	<!-- Left: Upload -->
	<div class="space-y-4">
		<h2 class="text-sm font-medium">Base CV Template</h2>

		<!-- Current CV info -->
		{#if !profileLoading && currentCvPath}
			<div class="flex items-center gap-2 p-3 bg-card border border-border rounded-lg text-xs">
				<FileText size={14} class="text-primary flex-shrink-0" />
				<span class="flex-1 truncate text-muted-foreground">{currentCvPath}</span>
				<CheckCircle2 size={13} class="text-green-500 flex-shrink-0" />
			</div>
		{/if}

		<!-- Drop zone -->
		<div
			class="relative border-2 border-dashed rounded-lg p-8 text-center transition-colors {dragOver
				? 'border-primary bg-primary/5'
				: 'border-border hover:border-border/60 bg-card/50'}"
			ondragover={onDragOver}
			ondragleave={onDragLeave}
			ondrop={onDrop}
			role="region"
			aria-label="Drop zone for .tex file"
		>
			<input
				type="file"
				accept=".tex"
				onchange={onFileInput}
				class="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
				aria-label="Upload .tex CV template"
			/>
			<div class="flex flex-col items-center gap-3 pointer-events-none">
				<div class="w-12 h-12 rounded-full bg-muted flex items-center justify-center">
					<Upload size={20} class="text-muted-foreground" />
				</div>
				{#if uploading}
					<p class="text-sm text-muted-foreground">Uploading…</p>
				{:else}
					<div>
						<p class="text-sm font-medium">Drop your .tex file here</p>
						<p class="text-xs text-muted-foreground mt-1">or click to browse</p>
					</div>
				{/if}
			</div>
		</div>
	</div>

	<!-- Right: Edit history -->
	<div class="space-y-4">
		<h2 class="text-sm font-medium">Tailored CV History</h2>

		{#if docsLoading}
			<div class="space-y-2 animate-pulse">
				{#each Array(4) as _}<div class="h-14 bg-muted rounded-lg"></div>{/each}
			</div>
		{:else if documents.length === 0}
			<div class="flex flex-col items-center justify-center py-12 gap-3 bg-card border border-border rounded-lg">
				<FloatingEmoji emoji="📄" size="sm" />
				<p class="text-sm text-muted-foreground font-medium">{cvEmptyMessage}</p>
				<p class="text-xs text-muted-foreground">CVs are generated during the job scan when jobs are matched.</p>
			</div>
		{:else}
			<div class="space-y-2">
				{#each documents as doc (doc.id)}
					<div class="flex items-center gap-3 p-3 bg-card border border-border rounded-lg">
						<FileText size={14} class="text-muted-foreground flex-shrink-0" />
						<div class="flex-1 min-w-0">
							<p class="text-xs font-medium">Match #{doc.job_match_id ?? '—'}</p>
							<div class="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
								<Clock size={10} />
								{timeAgo(doc.created_at)}
							</div>
						</div>
						<div class="flex items-center gap-2">
							{#if doc.pdf_path}
								<a
									href="/api/documents/{doc.job_match_id}/cv/pdf"
									target="_blank"
									rel="noopener noreferrer"
									class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
									title="View PDF"
								>
									<Eye size={13} />
								</a>
							{/if}
							{#if doc.diff_json}
								<a
									href="/jobs/{doc.job_match_id}?tab=diff"
									class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
									title="View diff"
								>
									<GitCompare size={13} />
								</a>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
