export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
	if (!iso) return '';
	const then = new Date(iso).getTime();
	const diff = Math.round((now - then) / 1000);
	const abs = Math.abs(diff);
	const future = diff < 0;
	const fmt = (n: number, unit: string) => `${future ? 'in ' : ''}${n}${unit}${future ? '' : ' ago'}`;
	if (abs < 45) return future ? 'soon' : 'just now';
	if (abs < 3600) return fmt(Math.round(abs / 60), 'm');
	if (abs < 86400) return fmt(Math.round(abs / 3600), 'h');
	if (abs < 86400 * 14) return fmt(Math.round(abs / 86400), 'd');
	return new Date(iso).toLocaleDateString();
}

export function absoluteTime(iso: string | null | undefined): string {
	if (!iso) return '';
	return new Date(iso).toLocaleString();
}

export function duration(start: string | null | undefined, end: string | null | undefined): string {
	if (!start) return '';
	const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime();
	if (ms < 1000) return `${ms}ms`;
	const s = Math.round(ms / 1000);
	if (s < 60) return `${s}s`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m}m ${s % 60}s`;
	return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function bytes(n: number): string {
	if (n < 1024) return `${n} B`;
	if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
	return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function initials(name: string): string {
	return name
		.split(/[\s_.-]+/)
		.filter(Boolean)
		.slice(0, 2)
		.map((p) => p[0]!.toUpperCase())
		.join('');
}
