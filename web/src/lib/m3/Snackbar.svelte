<script lang="ts">
	import { snackbar } from '$lib/snackbar.svelte';
	import IconButton from './IconButton.svelte';

	let current = $derived(snackbar.queue[0]);

	$effect(() => {
		if (!current) return;
		const id = current.id;
		const t = setTimeout(() => snackbar.dismiss(id), current.timeout ?? 4000);
		return () => clearTimeout(t);
	});
</script>

{#if current}
	<div
		class="fixed bottom-4 left-1/2 z-50 flex min-h-12 w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 items-center gap-2 rounded-xs py-1 pl-4 pr-1 shadow-e3 body-medium {current.tone === 'error'
			? 'bg-error-container text-on-error-container'
			: 'bg-inverse-surface text-inverse-on-surface'}"
		role="status"
	>
		<span class="flex-1 py-2">{current.text}</span>
		{#if current.action}
			<button
				type="button"
				class="state-layer rounded-full px-3 py-2 label-large {current.tone === 'error' ? 'text-on-error-container' : 'text-inverse-primary'}"
				onclick={() => {
					current?.action?.run();
					snackbar.dismiss(current!.id);
				}}>{current.action.label}</button
			>
		{/if}
		<IconButton
			icon="close"
			label="Dismiss"
			class={current.tone === 'error' ? 'text-on-error-container' : 'text-inverse-on-surface'}
			onclick={() => snackbar.dismiss(current!.id)}
		/>
	</div>
{/if}
