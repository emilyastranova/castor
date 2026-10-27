/** The signed-in user's notifications: loaded once, kept live from realtime events, and
 *  mirrored as desktop notifications when the user allows it. */
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import type { AppNotification } from '$lib/api';
import { api } from '$lib/api';
import { realtime, type ServerEvent } from '$lib/realtime.svelte';

const DESKTOP_KEY = 'castor.desktopNotifications';

class Notifications {
	items = $state<AppNotification[]>([]);
	unread = $state(0);
	loaded = $state(false);
	/** default | granted | denied | unsupported */
	permission = $state<NotificationPermission | 'unsupported'>('default');
	desktop = $state(false);
	#userId: string | null = null;
	#unsubscribe: (() => void) | null = null;

	start(userId: string) {
		if (!browser || this.#userId === userId) return;
		this.#userId = userId;
		this.permission = 'Notification' in window ? Notification.permission : 'unsupported';
		try {
			this.desktop = this.permission === 'granted' && localStorage.getItem(DESKTOP_KEY) !== 'off';
		} catch {
			this.desktop = this.permission === 'granted';
		}
		this.#unsubscribe?.();
		this.#unsubscribe = realtime.subscribe((e) => this.#onEvent(e));
		void this.load();
	}

	stop() {
		this.#unsubscribe?.();
		this.#unsubscribe = null;
		this.#userId = null;
		this.items = [];
		this.unread = 0;
		this.loaded = false;
	}

	async load() {
		try {
			const res = await api().notifications.list(false, 50);
			this.items = res.items;
			this.unread = res.unread;
			this.loaded = true;
		} catch {
			/* signed out or offline; the next event or open retries */
		}
	}

	async read(n: AppNotification) {
		if (n.read_at) return;
		this.#markRead([n.id]);
		try {
			await api().notifications.read(n.id);
		} catch {
			void this.load();
		}
	}

	async readAll() {
		this.items = this.items.map((n) => (n.read_at ? n : { ...n, read_at: new Date().toISOString() }));
		this.unread = 0;
		await api().notifications.readAll();
	}

	async enableDesktop() {
		if (this.permission === 'unsupported') return;
		if (this.permission !== 'granted') this.permission = await Notification.requestPermission();
		this.desktop = this.permission === 'granted';
		this.#remember();
	}

	disableDesktop() {
		this.desktop = false;
		this.#remember();
	}

	#remember() {
		try {
			localStorage.setItem(DESKTOP_KEY, this.desktop ? 'on' : 'off');
		} catch {
			/* private window: per-session only */
		}
	}

	#markRead(ids: string[]) {
		const now = new Date().toISOString();
		let changed = 0;
		this.items = this.items.map((n) => {
			if (!ids.includes(n.id) || n.read_at) return n;
			changed++;
			return { ...n, read_at: now };
		});
		this.unread = Math.max(0, this.unread - changed);
	}

	#onEvent(e: ServerEvent) {
		if (e.name === 'notification.created') {
			const n = e.data as AppNotification;
			if (n.user_id !== this.#userId || this.items.some((x) => x.id === n.id)) return;
			this.items = [n, ...this.items].slice(0, 100);
			this.unread++;
			this.#desktop(n);
		} else if (e.name === 'notification.read') {
			const data = e.data as { ids?: string[]; all?: boolean; user_id?: string };
			if (data.all && data.user_id === this.#userId) {
				this.#markRead(this.items.map((n) => n.id));
				this.unread = 0;
			} else if (data.ids) {
				// Read state can change for ids not loaded here; recount from the server.
				const known = data.ids.filter((id) => this.items.some((n) => n.id === id));
				this.#markRead(known);
				if (known.length !== data.ids.length) void this.load();
			}
		}
	}

	#desktop(n: AppNotification) {
		if (!this.desktop || this.permission !== 'granted' || (document.hasFocus() && n.severity !== 'warning')) return;
		try {
			const shown = new Notification(n.title, { body: n.body, tag: n.subject || n.id, requireInteraction: n.severity === 'warning' });
			shown.onclick = () => {
				window.focus();
				void this.read(n);
				if (n.link) void goto(n.link);
				shown.close();
			};
		} catch {
			/* some browsers only allow notifications from a service worker */
		}
	}
}

export const notifications = new Notifications();

export const SEVERITY: Record<AppNotification['severity'], { icon: string; tone: string }> = {
	info: { icon: 'info', tone: 'text-on-surface-variant' },
	success: { icon: 'check_circle', tone: 'text-primary' },
	warning: { icon: 'front_hand', tone: 'text-tertiary' },
	error: { icon: 'error', tone: 'text-error' }
};
