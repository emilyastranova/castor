<script lang="ts">
	import { updates } from '$lib/updates.svelte';
	import { Icon } from '$lib/m3';

	/** "Castor was updated": a newer version is running than this page (updates.svelte.ts).
	 *  Stays until people refresh or pick Later; moving to another page loads the new one anyway. */
	const label = $derived(updates.serverChanged && updates.serverNow ? `Castor ${updates.serverNow} is running` : 'A newer Castor is running');
</script>

{#if updates.show}
	<div
		class="m3-snackbar m3-update fixed right-4 z-50 flex w-[min(420px,calc(100vw-2rem))] items-center gap-3 rounded-xs bg-inverse-surface py-2 pr-1 pl-4 text-inverse-on-surface shadow-e3 max-md:top-2 md:bottom-4"
		role="status"
		aria-live="polite"
	>
		<Icon name="system_update_alt" size={20} class="shrink-0 text-inverse-primary" />
		<span class="min-w-0 flex-1 body-medium">
			<span class="block font-medium">{label}</span>
			<span class="block body-small opacity-80">Refresh to use it; this page is from the earlier version.</span>
		</span>
		<button type="button" class="state-layer shrink-0 rounded-full px-3 py-2 label-large text-inverse-on-surface/80" onclick={() => updates.later()}>Later</button>
		<button type="button" class="state-layer shrink-0 rounded-full px-3 py-2 label-large text-inverse-primary" onclick={() => location.reload()}>Refresh</button>
	</div>
{/if}
