<script>
	import "../app.css";
	import "carbon-components-svelte/css/g100.css";
	import '@carbon/charts-svelte/styles.css'
	import {
		Header,
		HeaderNav,
		HeaderNavItem,
		HeaderNavMenu,
		HeaderUtilities,
		HeaderGlobalAction,
		SkipToContent,
		Dropdown,
	} from "carbon-components-svelte";
	import SettingsAdjust from "carbon-icons-svelte/lib/SettingsAdjust.svelte";
	import Notification from "carbon-icons-svelte/lib/Notification.svelte";

	let isSideNavOpen = false;
	const projects = [
		{ id: "0", text: "CODE-307", subtext: "Aperture Labs" },
		{ id: "1", text: "CODE-595", subtext: "Black Mesa" },
		{ id: "2", text: "CODE-709", subtext: "Xen" },
	];
</script>

<Header
	platformName="Castor"
	expandedByDefault={false}
	bind:isSideNavOpen
>
	<svelte:fragment slot="skip-to-content">
		<SkipToContent />
	</svelte:fragment>

	<HeaderNav>
		<HeaderNavItem href="/" text="Dashboard" />
		<HeaderNavItem href="/tasks" text="Tasks" />
		<HeaderNavItem href="/journal" text="Journal" />
		<HeaderNavMenu text="Manage">
			<HeaderNavItem href="/jobs" text="Jobs" />
			<HeaderNavItem href="/agents" text="Agents" />
		</HeaderNavMenu>
		<HeaderNavMenu text="Tools">
			<HeaderNavItem href="/" text="CyberChef" />
		</HeaderNavMenu>
		<HeaderNavMenu text="Bookmarks">
			<HeaderNavItem href="/" text="Test Bookmark" />
		</HeaderNavMenu>
		<HeaderNavItem href="/console" text="Console" />
	</HeaderNav>
	<HeaderUtilities>
		<Dropdown
			class="mr-4 w-64"
			hideLabel
			titleText="Project"
			selectedId="0"
			items={projects}
			let:item
		>
			<div>
				<strong>{item.text}</strong>
			</div>
			<div>
				{#if item.subtext}
					{item.subtext}
				{/if}
			</div>
		</Dropdown>
		<HeaderGlobalAction aria-label="Settings" icon={SettingsAdjust} />
		<HeaderGlobalAction aria-label="Notifications" icon={Notification} />
	</HeaderUtilities>
</Header>

<slot />

<style>
	:global(.bx--list-box__menu-item, .bx--list-box__menu-item__option) {
		height: auto;
	}
</style>
