/** Display helpers for variable kinds and sets. */
import type { VarField, VarFieldType, VariableSet } from '$lib/api';

const KIND_ICONS: Record<string, string> = {
	credential: 'key',
	'ssh-key': 'vpn_key',
	listener: 'settings_input_antenna',
	'api-token': 'token'
};

export const kindIcon = (kindId: string) => KIND_ICONS[kindId] ?? 'data_object';

export const FIELD_TYPES: { value: VarFieldType; label: string }[] = [
	{ value: 'text', label: 'Text' },
	{ value: 'secret', label: 'Secret' },
	{ value: 'host', label: 'Host / IP' },
	{ value: 'port', label: 'Port' },
	{ value: 'number', label: 'Number' },
	{ value: 'boolean', label: 'Yes / no' },
	{ value: 'list', label: 'List' },
	{ value: 'file', label: 'File' },
	{ value: 'secret_file', label: 'Secret file' }
];

export const fieldLabel = (f: VarField) => f.label || f.name.replace(/_/g, ' ');
export const isSecret = (t: VarFieldType) => t === 'secret' || t === 'secret_file';
export const isFile = (t: VarFieldType) => t === 'file' || t === 'secret_file';

/** One-line summary of a field's stored value for cards and lists. */
export function fieldSummary(set: VariableSet, f: VarField): string | null {
	if (isFile(f.type)) {
		const file = set.files[f.name];
		return file ? file.filename : null;
	}
	if (f.type === 'secret') return set.secrets[f.name] ? '••••••••' : null;
	const v = set.values[f.name] ?? f.default;
	if (v === undefined || v === null || v === '') return null;
	if (Array.isArray(v)) return v.join(', ');
	if (typeof v === 'boolean') return v ? 'yes' : 'no';
	return String(v);
}

/** The env var a command sees for slot.field (also how templates render). */
export const envName = (slot: string, field: string) => `CASTOR_VAR_${slot.toUpperCase()}_${field.toUpperCase()}`;
