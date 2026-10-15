<script lang="ts">
	import { Button, Dialog } from '$lib/m3';
	import type { ConfirmOptions } from '$lib/confirm.svelte';

	interface Props {
		options: ConfirmOptions;
		onsettle: (confirmed: boolean) => void;
	}
	let { options, onsettle }: Props = $props();

	// One instance per request (the host keys it), so it settles exactly once.
	let open = $state(true);
	let done = false;

	function settle(confirmed: boolean) {
		if (done) return;
		done = true;
		open = false;
		onsettle(confirmed);
	}
</script>

<!-- Cancel comes first so it gets initial focus: Enter or Escape never deletes anything. -->
<Dialog bind:open title={options.title} icon={options.icon ?? (options.danger ? 'delete' : 'help')} onclose={() => settle(false)}>
	<p class="pb-2 body-medium whitespace-pre-line text-on-surface-variant">{options.message}</p>
	{#snippet actions()}
		<Button variant="text" onclick={() => settle(false)}>{options.cancelLabel ?? 'Cancel'}</Button>
		<Button variant={options.danger ? 'danger' : 'filled'} onclick={() => settle(true)}>
			{options.confirmLabel ?? 'Confirm'}
		</Button>
	{/snippet}
</Dialog>
