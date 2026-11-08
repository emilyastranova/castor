<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	// Tools live in the Library now. Old links name the tool in the hash (/tools#nmap), which
	// only the browser sees, so this redirect runs here rather than in a load function.
	onMount(() => {
		const id = decodeURIComponent(location.hash.slice(1));
		void goto(id ? `/library?t=${encodeURIComponent(id)}` : '/library', { replaceState: true });
	});
</script>
