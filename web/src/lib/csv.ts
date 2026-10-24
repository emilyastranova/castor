/**
 * Build CSV text. Cells that a spreadsheet would treat as a formula (=, +, -, @, tab, CR)
 * get a leading apostrophe: command outputs carry external untrusted strings such as PTR
 * names and HTTP titles, and pasting them into Excel must not run anything.
 */
export function toCsv(header: string[], rows: unknown[][]): string {
	return [header, ...rows].map((row) => row.map(cell).join(',')).join('\r\n') + '\r\n';
}

function cell(value: unknown): string {
	let text = value === null || value === undefined ? '' : Array.isArray(value) ? value.join(' ') : String(value);
	if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
	return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
