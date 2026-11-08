<script lang="ts">
	import type { CommandDefinition } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { needsCredentials, signature, usageCount } from '$lib/library';

	/** One command in the rack: its name, what it takes and gives, and the flags worth a glance. */
	interface Props {
		command: CommandDefinition;
		selected?: boolean;
		/** Inside a folder: indented under the tool. */
		nested?: boolean;
		id?: string;
		/** `option` in a listbox, `treeitem` in the Library's tree. */
		role?: 'option' | 'treeitem';
		onselect?: () => void;
	}
	let { command: c, selected = false, nested = true, id, role = 'option', onselect }: Props = $props();
	let sig = $derived(signature(c));
	let uses = $derived(usageCount(c));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_interactive_supports_focus (the list owns focus and the keyboard; rows aren't focusable, so they can be dragged and a click focuses the list) -->
<div
	{id}
	{role}
	aria-selected={selected}
	class="state-layer flex min-h-12 cursor-pointer items-center gap-3 rounded-md py-1.5 pr-3 {nested ? 'pl-10' : 'pl-3'} {selected
		? 'bg-secondary-container text-on-secondary-container'
		: 'text-on-surface'}"
	onclick={onselect}
>
	<div class="min-w-0 flex-1">
		<p class="truncate title-small">{c.name}</p>
		{#if sig.inputs.length || sig.outputs.length}
			<p class="flex min-w-0 items-center gap-1 truncate font-mono text-[11.5px] {selected ? 'text-on-secondary-container/80' : 'text-on-surface-variant'}">
				<span class="truncate">{sig.inputs.join(', ') || '—'}</span>
				<Icon name="arrow_forward" size={12} class="shrink-0 opacity-70" />
				<span class="truncate">{sig.outputs.join(', ') || 'text'}</span>
			</p>
		{/if}
	</div>
	<span class="flex shrink-0 items-center gap-2 label-small {selected ? 'text-on-secondary-container/80' : 'text-on-surface-variant'}">
		{#if c.spec.tty}<span class="flex" title="Interactive: runs in a terminal"><Icon name="terminal" size={16} /></span>{/if}
		{#if needsCredentials(c)}<span class="flex" title="Uses credentials (variable sets)"><Icon name="key" size={16} /></span>{/if}
		{#if c.origin === 'user'}<span class="rounded-full bg-tertiary-container px-1.5 text-on-tertiary-container">yours</span>{/if}
		{#if uses}
			<span class="flex items-center gap-0.5 tabular-nums" title="{c.card_count} card{c.card_count === 1 ? '' : 's'}, {c.playbooks.length} playbook{c.playbooks.length === 1 ? '' : 's'}">
				<Icon name="link" size={14} />{uses}
			</span>
		{/if}
	</span>
</div>
