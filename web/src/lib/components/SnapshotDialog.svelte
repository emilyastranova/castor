<script lang="ts">
	import type { Attachment, UUID } from '$lib/api';
	import { Dialog } from '$lib/m3';
	import SnapshotEditor from './SnapshotEditor.svelte';

	/** SnapshotEditor in a dialog: from a card's console or attachments, or a code block anywhere. */
	interface Props {
		open?: boolean;
		jobId?: UUID | null;
		text?: string | null;
		language?: string | null;
		taskId?: UUID | null;
		title?: string;
		range?: { from: number; to: number } | null;
		attachment?: Attachment | null;
		canAttach?: boolean;
		onsaved?: (a: Attachment) => void;
	}
	let { open = $bindable(false), ...rest }: Props = $props();
</script>

<Dialog bind:open title={rest.attachment ? 'Change the snapshot' : 'Snapshot'} icon="photo_camera" size="lg" initialFocus="dialog">
	{#if open}
		<SnapshotEditor {...rest} onclose={() => (open = false)} />
	{/if}
</Dialog>
