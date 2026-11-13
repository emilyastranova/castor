<script lang="ts">
	import { TopAppBar } from '$lib/m3';
	import SnapshotEditor from '$lib/components/SnapshotEditor.svelte';
</script>

<svelte:head><title>Snapshot · Castor</title></svelte:head>

<!-- A picture of any code or terminal output, for a report: paste it, style it, copy or download it.
     Snapshots of a job's output start from its card's console instead. -->
<TopAppBar title="Snapshot" subtitle="A picture of code or terminal output, for a report" />
<div class="flex min-h-0 flex-1 flex-col overflow-auto px-4 pb-4 sm:px-6 lg:overflow-hidden">
	<SnapshotEditor page text="" />
</div>
