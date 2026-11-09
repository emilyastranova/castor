/** Admin settings, one section per page; add new ones here (the layout's nav and the phone
 *  list at /settings both read it). */
export const SECTIONS = [
	{ href: '/settings/general', icon: 'tune', label: 'General', detail: 'Limits and defaults' },
	{ href: '/settings/users', icon: 'group', label: 'Users', detail: 'Accounts and roles' },
	{ href: '/settings/groups', icon: 'groups', label: 'Groups', detail: 'Teams to mention and share with' },
	{ href: '/settings/sso', icon: 'login', label: 'Sign-on', detail: 'Single sign-on and passwords' },
	{ href: '/settings/tokens', icon: 'key', label: 'API tokens', detail: "Everyone's personal tokens" },
	{ href: '/settings/alerts', icon: 'campaign', label: 'Alerts', detail: 'Chat and webhook channels' },
	{ href: '/settings/ai', icon: 'auto_awesome', label: 'AI', detail: 'Providers, models and fallbacks' },
	{ href: '/settings/activity', icon: 'history', label: 'Activity log', detail: 'Everything, across projects' },
	{ href: '/settings/transfer', icon: 'sync_alt', label: 'Import and export', detail: 'Archives of projects and libraries' }
];
