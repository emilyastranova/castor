<script lang="ts">
	import { confirmStore } from '$lib/confirm.svelte';
	import ConfirmPrompt from './ConfirmPrompt.svelte';
</script>

{#if confirmStore.current}
	{#key confirmStore.current}
		<ConfirmPrompt options={confirmStore.current} onsettle={(ok) => confirmStore.settle(ok)} />
	{/key}
{/if}
