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
		SideNav,
		SideNavItems,
		SideNavMenu,
		SideNavMenuItem,
		SideNavLink,
		SkipToContent,
		Dropdown,
		Content,
		Grid,
		Row,
		Column,
	} from "carbon-components-svelte";
	import SettingsAdjust from "carbon-icons-svelte/lib/SettingsAdjust.svelte";
	import Notification from "carbon-icons-svelte/lib/Notification.svelte";

	let isSideNavOpen = false;
	const engagements = [
		{ id: "0", text: "MPR23-307", subtext: "BHSI" },
		{ id: "1", text: "MPR22-595", subtext: "Northshore" },
		{ id: "2", text: "MPR23-709", subtext: "Cheniere" },
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
		<HeaderNavMenu text="Tools">
			<HeaderNavItem href="/" text="KingBot" />
			<HeaderNavItem href="/" text="CyberChef" />
			<HeaderNavItem href="/" text="ReachDB" />
		</HeaderNavMenu>
		<HeaderNavMenu text="Manage">
			<HeaderNavItem href="/agents" text="Agents" />
			<HeaderNavItem href="/jobs" text="Jobs" />
		</HeaderNavMenu>
		<HeaderNavMenu text="Bookmarks">
			<HeaderNavItem href="/" text="Delusion" />
			<HeaderNavItem href="/" text="FiercePhish" />
			<HeaderNavItem href="/" text="Client Sharepoint" />
		</HeaderNavMenu>
		<HeaderNavItem href="/console" text="Console" />
	</HeaderNav>
	<HeaderUtilities>
		<Dropdown
			class="mr-4 w-64"
			hideLabel
			titleText="Engagement"
			selectedId="0"
			items={engagements}
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

<SideNav bind:isOpen={isSideNavOpen}>
	<SideNavItems>
		<SideNavLink text="Link 1" />
		<SideNavLink text="Link 2" />
		<SideNavLink text="Link 3" />
		<SideNavMenu text="Tools">
			<SideNavMenuItem href="/" text="Link 1" />
			<SideNavMenuItem href="/" text="Link 2" />
			<SideNavMenuItem href="/" text="Link 3" />
		</SideNavMenu>
	</SideNavItems>
</SideNav>

<slot />

<style>
	:global(.bx--list-box__menu-item, .bx--list-box__menu-item__option) {
		height: auto;
	}
</style>
