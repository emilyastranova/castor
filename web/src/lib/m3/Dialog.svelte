<script lang="ts">
	import type { Snippet } from 'svelte';
	import IconButton from './IconButton.svelte';

	interface Props {
		open?: boolean;
		title?: string;
		icon?: string;
		size?: 'sm' | 'md' | 'lg' | 'full';
		onclose?: () => void;
		children?: Snippet;
		actions?: Snippet;
		header?: Snippet;
		/** 'first' lets the browser focus the first control (default); 'dialog' focuses the
		 *  dialog itself so no text field gets a caret until the user clicks into one. */
		initialFocus?: 'first' | 'dialog';
	}
	let {
		open = $bindable(false),
		title,
		icon,
		size = 'sm',
		onclose,
		children,
		actions,
		header,
		initialFocus = 'first'
	}: Props = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	// Escape, the backdrop, the close button and the native `close` event can all reach
	// close() for the same dismissal; onclose must fire once per opening.
	let notified = false;

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) {
			notified = false;
			dialog.showModal();
			if (initialFocus === 'dialog') dialog.focus();
		} else if (!open && dialog.open) dialog.close();
	});

	function close() {
		open = false;
		if (notified) return;
		notified = true;
		onclose?.();
	}

	const sizes = {
		sm: 'w-[min(560px,calc(100vw-2rem))]',
		md: 'w-[min(760px,calc(100vw-2rem))]',
		lg: 'w-[min(1040px,calc(100vw-2rem))] h-[min(88vh,900px)]',
		full: 'h-full w-full max-h-none max-w-none rounded-none'
	};
</script>

<dialog
	bind:this={dialog}
	tabindex="-1"
	class="m-auto outline-none max-h-[calc(100vh-2rem)] overflow-hidden rounded-xl bg-surface-container-high p-0 text-on-surface shadow-e3 backdrop:bg-scrim/40 open:flex open:flex-col {sizes[size]}"
	onclose={close}
	onkeydown={(e) => {
		// Handle Escape here and cancel the key, rather than relying on the browser's close
		// request: Chrome groups a dialog opened without a fresh click/keypress (e.g. a prompt
		// that follows another prompt) with the one beneath it, and Escape would close both.
		if (e.key === 'Escape' && !e.defaultPrevented) {
			e.preventDefault();
			e.stopPropagation();
			close();
		}
	}}
	oncancel={(e) => {
		e.preventDefault();
		close();
	}}
	onclick={(e) => {
		if (e.target === dialog) close();
	}}
>
	{#if header}
		{@render header()}
	{:else if title}
		<div class="flex items-start gap-4 px-6 pt-6 pb-4">
			{#if icon}<span class="material-symbols-rounded text-secondary">{icon}</span>{/if}
			<h2 class="flex-1 headline-small">{title}</h2>
			{#if size === 'lg' || size === 'full'}
				<IconButton icon="close" label="Close" onclick={close} />
			{/if}
		</div>
	{/if}
	<div class="min-h-0 flex-1 overflow-auto px-6 text-on-surface-variant body-medium">
		{@render children?.()}
	</div>
	{#if actions}
		<div class="flex justify-end gap-2 px-6 pt-4 pb-6">
			{@render actions()}
		</div>
	{/if}
</dialog>
