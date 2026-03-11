<script lang="ts">
	import { onMount } from 'svelte';

	let {
		messages,
		typingSpeed = 35,
		pauseDuration = 2500,
		class: className = ''
	}: {
		messages: string[];
		typingSpeed?: number;
		pauseDuration?: number;
		class?: string;
	} = $props();

	let displayed = $state('');
	let msgIndex = $state(0);
	let charIndex = $state(0);
	let phase = $state<'typing' | 'pausing' | 'erasing'>('typing');
	let timer: ReturnType<typeof setTimeout>;

	function tick() {
		const current = messages[msgIndex];

		if (phase === 'typing') {
			if (charIndex < current.length) {
				displayed = current.slice(0, charIndex + 1);
				charIndex++;
				timer = setTimeout(tick, typingSpeed);
			} else {
				phase = 'pausing';
				timer = setTimeout(tick, pauseDuration);
			}
		} else if (phase === 'pausing') {
			phase = 'erasing';
			timer = setTimeout(tick, typingSpeed / 2);
		} else if (phase === 'erasing') {
			if (charIndex > 0) {
				charIndex--;
				displayed = current.slice(0, charIndex);
				timer = setTimeout(tick, typingSpeed / 2);
			} else {
				msgIndex = (msgIndex + 1) % messages.length;
				phase = 'typing';
				timer = setTimeout(tick, typingSpeed * 3);
			}
		}
	}

	onMount(() => {
		tick();
		return () => clearTimeout(timer);
	});
</script>

<span class="inline-flex items-center {className}">
	<span>{displayed}</span>
	<span class="ml-0.5 inline-block w-[2px] h-[1em] bg-current animate-caret-blink"></span>
</span>
