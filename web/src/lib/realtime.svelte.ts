/** WebSocket client for /ws/ui with reconnect; dispatches server events to subscribers. */
import { browser } from '$app/environment';

export interface ServerEvent<T = unknown> {
	type: 'event';
	name: string;
	project_id: string | null;
	data: T;
}

type Listener = (event: ServerEvent) => void;

class Realtime {
	connected = $state(false);
	/** Times the socket came back after dropping. Events sent meanwhile were missed, so
	 *  the layout reloads the page's data whenever this goes up. */
	reconnects = $state(0);
	private everConnected = false;
	private socket: WebSocket | null = null;
	private listeners = new Set<Listener>();
	private retry = 1000;
	private timer: ReturnType<typeof setTimeout> | null = null;
	private stopped = false;

	start(): void {
		if (!browser || this.socket) return;
		this.stopped = false;
		this.open();
	}

	stop(): void {
		this.stopped = true;
		this.everConnected = false;
		if (this.timer) clearTimeout(this.timer);
		this.socket?.close();
		this.socket = null;
		this.connected = false;
	}

	subscribe(listener: Listener): () => void {
		this.listeners.add(listener);
		return () => this.listeners.delete(listener);
	}

	private open(): void {
		const proto = location.protocol === 'https:' ? 'wss' : 'ws';
		const socket = new WebSocket(`${proto}://${location.host}/ws/ui`);
		this.socket = socket;
		socket.onopen = () => {
			this.connected = true;
			this.retry = 1000;
			if (this.everConnected) this.reconnects++;
			this.everConnected = true;
		};
		socket.onmessage = (msg) => {
			let event: ServerEvent;
			try {
				event = JSON.parse(msg.data);
			} catch {
				return;
			}
			if (event?.type !== 'event') return;
			for (const listener of this.listeners) {
				try {
					listener(event);
				} catch (err) {
					console.error('realtime listener failed', err);
				}
			}
		};
		socket.onclose = (ev) => {
			this.connected = false;
			this.socket = null;
			if (this.stopped || ev.code === 4401) return;
			this.timer = setTimeout(() => this.open(), this.retry);
			this.retry = Math.min(this.retry * 2, 15000);
		};
		socket.onerror = () => socket.close();
	}
}

export const realtime = new Realtime();
