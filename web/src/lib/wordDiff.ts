/** A word-level diff of two commands, for showing what changed (whitespace kept). */
export type DiffPart = { text: string; kind: 'same' | 'removed' | 'added' };

export function wordDiff(before: string, after: string): DiffPart[] {
	const a = before.split(/(\s+)/);
	const b = after.split(/(\s+)/);
	// Very long commands: show them whole rather than run a big table.
	if (a.length * b.length > 400_000) return [{ text: before, kind: 'removed' }, { text: '\n', kind: 'same' }, { text: after, kind: 'added' }];
	const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
	for (let i = a.length - 1; i >= 0; i--)
		for (let j = b.length - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
	const out: DiffPart[] = [];
	const push = (text: string, kind: DiffPart['kind']) => {
		const last = out[out.length - 1];
		if (last && last.kind === kind) last.text += text;
		else out.push({ text, kind });
	};
	let i = 0;
	let j = 0;
	while (i < a.length && j < b.length) {
		if (a[i] === b[j]) {
			push(a[i], 'same');
			i++;
			j++;
		} else if (lcs[i + 1][j] >= lcs[i][j + 1]) push(a[i++], 'removed');
		else push(b[j++], 'added');
	}
	while (i < a.length) push(a[i++], 'removed');
	while (j < b.length) push(b[j++], 'added');
	return out;
}
