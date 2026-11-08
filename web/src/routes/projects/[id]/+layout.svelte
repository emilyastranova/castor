<script lang="ts">
	import { page } from '$app/state';
	import { currentProject } from '$lib/currentProject.svelte';
	import { snackbar } from '$lib/snackbar.svelte';

	let { data, children } = $props();

	// Opening a project page makes it the current project. Reaching another project through
	// a link (a notification, search, a bookmark) says so, so nobody acts in the wrong one;
	// the switcher sets it first, so a switch made on purpose isn't announced.
	$effect(() => {
		const { id, codename } = data.project;
		if (page.data.user) currentProject.init(page.data.user.id);
		if (currentProject.id === id) return;
		const switched = currentProject.id !== null;
		currentProject.set(id);
		if (switched) snackbar.show(`Switched to ${codename}`);
	});
</script>

{@render children()}
