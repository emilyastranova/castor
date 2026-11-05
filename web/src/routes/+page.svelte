<script lang="ts">
	import Dashboard from '$lib/components/dashboard/Dashboard.svelte';

	let { data } = $props();
</script>

<svelte:head><title>Home · Castor</title></svelte:head>

<div class="flex-1 overflow-auto">
	<Dashboard projects={data.projects} />
</div>
