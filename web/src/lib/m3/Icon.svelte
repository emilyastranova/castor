<script lang="ts">
	interface Props {
		name: string;
		filled?: boolean;
		size?: number;
		class?: string;
	}
	let { name, filled = false, size = 24, class: klass = '' }: Props = $props();
</script>

<span
	class="material-symbols-rounded {klass}"
	class:filled
	style:font-size="{size}px"
	style:width="{size}px"
	style:height="{size}px"
	aria-hidden="true">{name}</span
>
