<script lang="ts">
	import type { Snippet } from 'svelte';

	/** A library definition's YAML: unwrapped, so indentation reads as written. Ctrl/Cmd+S saves. */
	interface Props {
		value: string;
		readonly?: boolean;
		error?: string | null;
		label: string;
		onsave?: () => void;
		/** Notes or a preview beside the text on wide panes. */
		aside?: Snippet;
	}
	let { value = $bindable(), readonly = false, error = null, label, onsave, aside }: Props = $props();

	function keydown(e: KeyboardEvent) {
		if ((e.ctrlKey || e.metaKey) && e.key === 's') {
			e.preventDefault();
			if (!readonly) onsave?.();
		}
		if (e.key === 'Tab' && !readonly && !e.shiftKey) {
			// Two spaces, as YAML wants, instead of leaving the field.
			e.preventDefault();
			const el = e.currentTarget as HTMLTextAreaElement;
			const { selectionStart: a, selectionEnd: b } = el;
			value = value.slice(0, a) + '  ' + value.slice(b);
			requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
		}
	}
</script>

<div class="flex flex-col gap-3 {aside ? '2xl:grid 2xl:grid-cols-[minmax(0,1fr)_18rem]' : ''}">
	<div class="flex min-w-0 flex-col gap-2">
		<textarea
			aria-label={label}
			class="min-h-[28rem] resize-y overflow-auto rounded-md border bg-surface-container-lowest p-3 font-mono text-[12.5px] leading-5 whitespace-pre text-on-surface outline-none focus:border-primary
				{error ? 'border-error' : 'border-outline-variant'} {readonly ? 'text-on-surface-variant' : ''}"
			wrap="off"
			spellcheck="false"
			autocomplete="off"
			{readonly}
			bind:value
			onkeydown={keydown}
		></textarea>
		{#if error}<p class="rounded-md bg-error-container px-3 py-2 body-small break-words whitespace-pre-line text-on-error-container" role="alert">{error}</p>{/if}
	</div>
	{#if aside}<div class="flex min-w-0 flex-col gap-2">{@render aside()}</div>{/if}
</div>
