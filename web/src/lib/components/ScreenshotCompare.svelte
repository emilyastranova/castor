<script lang="ts">
	import type { AssetEvidence } from '$lib/api';
	import { api } from '$lib/api';
	import { Dialog, Icon, Select } from '$lib/m3';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { lightbox } from '$lib/lightbox.svelte';
	import Spoilers from './Spoilers.svelte';

	/** Two screenshots of one asset side by side: by default the chosen one (the newest) and the one
	 *  before it. Pick either side from its history. */
	interface Props {
		open: boolean;
		/** The asset's screenshots, newest first. */
		shots: AssetEvidence[];
		label: string;
		/** The one to put on the right (newer side) at first. */
		focus?: string | null;
	}
	let { open = $bindable(false), shots, label, focus = null }: Props = $props();

	let right = $state('');
	let left = $state('');
	$effect(() => {
		if (!open) return;
		const i = Math.max(0, shots.findIndex((s) => s.id === focus));
		const r = shots[i];
		const l = shots[i + 1] ?? r; // the one just before
		right = r?.id ?? '';
		left = l?.id ?? '';
	});
	const options = $derived(
		shots.map((s) => ({
			value: s.id,
			label: new Date(s.at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }),
			description: [s.look_changed ? 'looked different' : '', s.source?.task?.title ?? '', s.source?.agent ? `from ${s.source.agent}` : ''].filter(Boolean).join(' · ')
		}))
	);
	const pick = (id: string) => shots.find((s) => s.id === id);
</script>

<Dialog bind:open title="Screenshots of {label}" icon="compare" size="lg">
	<div class="grid gap-4 pt-1 pb-2 md:grid-cols-2">
		{#each [{ side: 'Earlier', id: left, set: (v: string) => (left = v) }, { side: 'Later', id: right, set: (v: string) => (right = v) }] as pane (pane.side)}
			{@const s = pick(pane.id)}
			<figure class="flex min-w-0 flex-col gap-2">
				<Select label={pane.side} value={pane.id} onchange={pane.set} {options} />
				{#if s?.attachment}
					<button
						type="button"
						class="focus-ring overflow-hidden rounded-md border border-outline-variant bg-surface-container-low"
						title="Open full size"
						onclick={() => lightbox.open([{ src: api().attachments.url(s.attachment!.id, true), name: s.attachment!.filename }])}
					>
						<img src={api().attachments.thumbnail(s.attachment.id, 720)} alt="{pane.side} screenshot of {label}" class="block h-auto w-full" />
					</button>
					<figcaption class="flex flex-wrap items-center gap-x-2 body-small text-on-surface-variant">
						<span title={absoluteTime(s.at)}>{relativeTime(s.at)}</span>
						{#if s.source?.task}· <a href={s.source.task.link} class="text-primary hover:underline"><Spoilers text={s.source.task.title} /></a>{/if}
						{#if s.source?.agent}<span class="inline-flex items-center gap-0.5"><Icon name="dns" size={12} />{s.source.agent}</span>{/if}
					</figcaption>
				{/if}
			</figure>
		{/each}
	</div>
	{#if pick(right)?.look_changed}
		<p class="pb-2 body-small text-on-surface-variant">The later one looked different from the screenshot before it.</p>
	{/if}
</Dialog>
