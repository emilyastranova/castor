<script lang="ts">
	import type { HTMLInputAttributes, HTMLTextareaAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';
	import { markdownKeydown } from './markdownKeys';

	interface Props {
		label: string;
		value?: string | number | null;
		variant?: 'outlined' | 'filled';
		multiline?: boolean;
		rows?: number;
		mono?: boolean;
		/** A markdown box: ` over selected text makes it code (markdownKeys.ts). */
		markdown?: boolean;
		supporting?: string;
		error?: string | null;
		leadingIcon?: string;
		trailingIcon?: string;
		ontrailing?: () => void;
		/** Accessible name and tooltip for the trailing button (defaults to the label). */
		trailingLabel?: string;
		type?: HTMLInputAttributes['type'];
		placeholder?: string;
		required?: boolean;
		disabled?: boolean;
		readonly?: boolean;
		autofocus?: boolean;
		name?: string;
		autocomplete?: HTMLInputAttributes['autocomplete'];
		class?: string;
		id?: string;
		oninput?: HTMLInputAttributes['oninput'] & HTMLTextareaAttributes['oninput'];
		onkeydown?: HTMLInputAttributes['onkeydown'] & HTMLTextareaAttributes['onkeydown'];
		onblur?: HTMLInputAttributes['onblur'] & HTMLTextareaAttributes['onblur'];
		onpaste?: HTMLTextareaAttributes['onpaste'];
	}
	let {
		label,
		value = $bindable(''),
		variant = 'outlined',
		multiline = false,
		rows = 3,
		mono = false,
		markdown = false,
		supporting,
		error = null,
		leadingIcon,
		trailingIcon,
		ontrailing,
		trailingLabel,
		type = 'text',
		placeholder,
		required,
		disabled,
		readonly,
		autofocus,
		name,
		// Off by default: browser autofill and reload form-restore must never put text into
		// app fields behind the app's back.
		autocomplete = 'off',
		class: klass = '',
		id = `tf-${Math.random().toString(36).slice(2, 8)}`,
		oninput,
		onkeydown,
		onblur,
		onpaste
	}: Props = $props();

	let focused = $state(false);
	let populated = $derived(value !== '' && value !== null && value !== undefined);
	let float = $derived(focused || populated || !!placeholder);
	let colour = $derived(error ? 'text-error' : focused ? 'text-primary' : 'text-on-surface-variant');
	let border = $derived(
		error
			? (focused ? 'border-2 border-error' : 'border-error')
			: focused
				? 'border-2 border-primary'
				: 'border-outline hover:border-on-surface'
	);
	const fieldBase = 'peer w-full bg-transparent text-on-surface outline-none placeholder:text-on-surface-variant/70 disabled:opacity-40';
</script>

<div class="flex flex-col {variant === 'outlined' ? 'pt-2' : ''} {klass}">
	<div
		class="relative flex items-start gap-3 transition-colors {variant === 'outlined'
			? 'rounded-xs bg-transparent'
			: `rounded-t-xs border-b bg-surface-container-highest ${error ? 'border-error' : focused ? 'border-primary' : 'border-on-surface-variant'}`} {multiline ? 'py-3' : 'h-14 items-center'} {leadingIcon ? 'pl-3' : 'pl-4'} {trailingIcon ? 'pr-2' : 'pr-4'}"
	>
		{#if variant === 'outlined'}
			<fieldset
				aria-hidden="true"
				class="pointer-events-none absolute inset-x-0 -top-[5px] bottom-0 m-0 rounded-xs border px-3 transition-colors {border}"
			>
				<legend
					class="invisible h-[11px] overflow-hidden whitespace-nowrap text-[12px] leading-[11px] transition-[max-width,padding] duration-150 {float ? 'max-w-full px-1' : 'max-w-0 p-0'} {leadingIcon ? 'ml-8' : ''}"
					>{label}{required ? ' *' : ''}</legend
				>
			</fieldset>
		{/if}
		<label
			for={id}
			class="pointer-events-none absolute z-10 origin-left transition-all duration-150 {colour} {leadingIcon ? 'left-12' : 'left-4'} {float
				? variant === 'outlined'
					? '-top-2 body-small'
					: 'top-1.5 body-small'
				: multiline
					? 'top-3 body-large'
					: 'top-1/2 -translate-y-1/2 body-large'}"
			>{label}{required ? ' *' : ''}</label
		>
		{#if leadingIcon}
			<Icon name={leadingIcon} class="{multiline ? 'mt-0.5' : ''} text-on-surface-variant" />
		{/if}
		<div class="flex min-w-0 flex-1 flex-col self-stretch justify-center">
			{#if multiline}
				<textarea
					{id}
					{name}
					{rows}
					{autocomplete}
					class="{fieldBase} resize-y {mono ? 'font-mono text-sm' : 'body-large'} {variant === 'filled' ? 'pt-3' : ''}"
					bind:value
					{placeholder}
					{required}
					{disabled}
					{readonly}
					onfocus={() => (focused = true)}
					onblur={(e) => {
						focused = false;
						onblur?.(e);
					}}
					{oninput}
					onkeydown={(e) => {
						onkeydown?.(e);
						if (markdown) markdownKeydown(e);
					}}
					{onpaste}
				></textarea>
			{:else}
				<!-- svelte-ignore a11y_autofocus -->
				<input
					{id}
					{name}
					{type}
					class="{fieldBase} h-full {mono ? 'font-mono text-sm' : 'body-large'} {variant === 'filled' ? 'pt-4' : ''}"
					bind:value
					{placeholder}
					{required}
					{disabled}
					{readonly}
					{autocomplete}
					{autofocus}
					onfocus={() => (focused = true)}
					onblur={(e) => {
						focused = false;
						onblur?.(e);
					}}
					{oninput}
					{onkeydown}
				/>
			{/if}
		</div>
		{#if trailingIcon}
			<button
				type="button"
				class="state-layer -my-1 inline-flex size-10 shrink-0 items-center justify-center rounded-full text-on-surface-variant"
				onclick={ontrailing}
				tabindex={ontrailing ? 0 : -1}
				aria-label={trailingLabel ?? label}
				title={trailingLabel}
			>
				<Icon name={trailingIcon} />
			</button>
		{/if}
	</div>
	{#if error || supporting}
		<p class="px-4 pt-1 body-small {error ? 'text-error' : 'text-on-surface-variant'}">{error ?? supporting}</p>
	{/if}
</div>
