<script lang="ts" module>
	/** Whose name gets the easter egg (compared on the display name, case-insensitively). */
	export function isAstra(name: string | null | undefined): boolean {
		return (name ?? '').trim().replace(/\s+/g, ' ').toLowerCase() === 'emily astranova';
	}
</script>

<script lang="ts">
	// A person's display name. For one person it shimmers on hover.
	let { name }: { name: string | null | undefined } = $props();
</script>

{#if isAstra(name)}<span class="astra-name">{name}</span>{:else}{name}{/if}
