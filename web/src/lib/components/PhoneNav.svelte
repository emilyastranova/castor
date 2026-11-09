<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import type { NavItem } from '$lib/m3/NavigationRail.svelte';
	import { Avatar, Icon } from '$lib/m3';
	import { palette } from '$lib/palette.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import NotificationBell from './NotificationBell.svelte';

	/** The phone's navigation (below md): a bottom bar with Home, Board, Search, Inbox and
	 *  More, where More is a sheet with every other destination and the account. */
	interface Props {
		items: NavItem[];
		current: string;
		user: { display_name: string; username: string; role: string; avatar_url?: string | null };
		dark: boolean;
		onaccount: () => void;
		ontheme: () => void;
		onlogout: () => void;
	}
	let { items, current, user, dark, onaccount, ontheme, onlogout }: Props = $props();

	// The first two destinations (Home and Board) are in the bar; the rest are in More.
	const active = (item: NavItem) => (item.exact || item.href === '/' ? current === item.href : current.startsWith(item.href));
	let pinned = $derived(items.slice(0, 2));
	let rest = $derived(items.slice(2));
	let elsewhere = $derived(rest.some(active) || current.startsWith('/tokens') || current === '/projects');

	let sheet: HTMLDialogElement | undefined = $state();
	let sheetOpen = $state(false);
	function openSheet() {
		sheet?.showModal();
		sheetOpen = true;
	}
	function closeSheet() {
		sheet?.close();
	}
	function then(action: () => void) {
		closeSheet();
		action();
	}
</script>

{#snippet item(icon: string, label: string, on: boolean, alert = false)}
	<span
		class="state-layer relative flex h-8 w-14 items-center justify-center rounded-full transition-colors group-focus-visible:outline-3 group-focus-visible:outline-offset-2 group-focus-visible:outline-secondary {on
			? 'bg-secondary-container text-on-secondary-container'
			: 'text-on-surface-variant'}"
	>
		<Icon name={icon} filled={on} />
		{#if alert}<span class="absolute top-0.5 right-2.5 size-2 rounded-full bg-error" aria-hidden="true"></span>{/if}
	</span>
	<span class="label-medium {on ? 'text-on-surface' : 'text-on-surface-variant'}">{label}</span>
{/snippet}

<nav
	class="flex shrink-0 items-stretch border-t border-outline-variant/60 bg-surface-container pb-[env(safe-area-inset-bottom)] md:hidden"
	aria-label="Main"
>
	{#each pinned as i (i.label)}
		<a href={i.href} class="group flex h-16 flex-1 flex-col items-center justify-center gap-1 outline-none" aria-current={active(i) ? 'page' : undefined}>
			{@render item(i.icon, i.label, active(i))}
		</a>
	{/each}
	<button type="button" class="group flex h-16 flex-1 flex-col items-center justify-center gap-1 outline-none" onclick={() => palette.show()}>
		{@render item('search', 'Search', false)}
	</button>
	<NotificationBell bar />
	<button
		type="button"
		class="group flex h-16 flex-1 flex-col items-center justify-center gap-1 outline-none"
		aria-haspopup="dialog"
		aria-expanded={sheetOpen}
		aria-current={elsewhere ? 'page' : undefined}
		aria-label={realtime.connected ? 'More' : 'More (live updates offline)'}
		onclick={openSheet}
	>
		<!-- The red dot is the phone's version of the rail's "disconnected" cloud; the sheet says it in words. -->
		{@render item('menu', 'More', elsewhere || sheetOpen, !realtime.connected)}
	</button>
</nav>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog
	bind:this={sheet}
	class="m-0 mt-auto max-h-[85dvh] w-full max-w-none overflow-y-auto rounded-t-xl bg-surface-container-low p-0 pb-[env(safe-area-inset-bottom)] text-on-surface shadow-e3 backdrop:bg-scrim/40 md:hidden"
	aria-label="More"
	onclose={() => (sheetOpen = false)}
	onclick={(e) => e.target === sheet && closeSheet()}
>
	<div class="mx-auto mt-2 h-1 w-8 rounded-full bg-outline-variant" aria-hidden="true"></div>
	<div class="flex items-center gap-3 px-5 pt-4 pb-3">
		<Avatar name={user.display_name} src={user.avatar_url} size={40} />
		<div class="min-w-0 flex-1">
			<p class="truncate title-small text-on-surface"><PersonName name={user.display_name} /></p>
			<p class="truncate body-small text-on-surface-variant">@{user.username} · {user.role}</p>
		</div>
		{#if !realtime.connected}
			<span class="flex items-center gap-1 text-error label-small"><Icon name="cloud_off" size={18} />Offline</span>
		{/if}
	</div>

	<ul class="grid grid-cols-3 gap-1 px-3">
		{#each rest as i (i.label)}
			<li>
				<a
					href={i.href}
					onclick={closeSheet}
					aria-current={active(i) ? 'page' : undefined}
					class="state-layer focus-ring flex h-18 flex-col items-center justify-center gap-1 rounded-lg {active(i)
						? 'bg-secondary-container text-on-secondary-container'
						: 'text-on-surface'}"
				>
					<Icon name={i.icon} filled={active(i)} />
					<span class="label-medium">{i.label}</span>
				</a>
			</li>
		{/each}
	</ul>

	<div class="mx-5 my-3 border-t border-outline-variant/60"></div>
	<ul class="px-3 pb-3">
		<li>
			<button type="button" class="state-layer focus-ring flex h-12 w-full items-center gap-4 rounded-lg px-3 body-large text-on-surface" onclick={() => then(onaccount)}>
				<Icon name="manage_accounts" class="text-on-surface-variant" />Account and password
			</button>
		</li>
		<li>
			<a href="/projects" onclick={closeSheet} class="state-layer focus-ring flex h-12 w-full items-center gap-4 rounded-lg px-3 body-large text-on-surface">
				<Icon name="swap_horiz" class="text-on-surface-variant" />Switch project
			</a>
		</li>
		<li>
			<a href="/tokens" onclick={closeSheet} class="state-layer focus-ring flex h-12 w-full items-center gap-4 rounded-lg px-3 body-large text-on-surface">
				<Icon name="key" class="text-on-surface-variant" />API tokens
			</a>
		</li>
		<li>
			<button type="button" class="state-layer focus-ring flex h-12 w-full items-center gap-4 rounded-lg px-3 body-large text-on-surface" onclick={ontheme}>
				<Icon name={dark ? 'light_mode' : 'dark_mode'} class="text-on-surface-variant" />{dark ? 'Light theme' : 'Dark theme'}
			</button>
		</li>
		<li>
			<button type="button" class="state-layer focus-ring flex h-12 w-full items-center gap-4 rounded-lg px-3 body-large text-on-surface" onclick={() => then(onlogout)}>
				<Icon name="logout" class="text-on-surface-variant" />Sign out
			</button>
		</li>
	</ul>
</dialog>
