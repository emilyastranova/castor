<script lang="ts">
	import type { PluginRepository, PluginRepositoryIn } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Dialog, Icon, Switch, TextField } from '$lib/m3';
	import { relativeTime } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Add or edit a plugin repository: a Git repository, public or private, plugins are installed
	 *  from. Saving fetches it; a key made here shows its public half to add as a deploy key, and
	 *  the dialog stays open to fetch again once it's there. */
	interface Props {
		open: boolean;
		/** The repository to edit; null to add one. */
		repo?: PluginRepository | null;
		onsaved?: (r: PluginRepository) => void;
	}
	let { open = $bindable(false), repo = null, onsaved }: Props = $props();

	type Auth = PluginRepository['auth'];
	type KeyMode = 'keep' | 'generate' | 'paste';
	let name = $state('');
	let url = $state('');
	let ref = $state('');
	let auth = $state<Auth>('none');
	let username = $state('');
	let token = $state('');
	let keyMode = $state<KeyMode>('generate');
	let privateKey = $state('');
	let passphrase = $state('');
	let enabled = $state(true);
	let saving = $state(false);
	let fetching = $state(false);
	let copied = $state(false);
	/** After saving: the repository as the server has it now (its fetch, its key). */
	let saved = $state<PluginRepository | null>(null);

	let openedFor = false;
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		const r = repo;
		name = r?.name ?? '';
		url = r?.url ?? '';
		ref = r?.ref ?? '';
		auth = r?.auth ?? 'none';
		username = r?.username ?? '';
		token = '';
		keyMode = r?.auth === 'ssh' && r.has_secret ? 'keep' : 'generate';
		privateKey = '';
		passphrase = '';
		enabled = r?.enabled ?? true;
		saved = null;
		copied = false;
	});

	/** What git would make of the URL: an SSH address wants a key. */
	let sshUrl = $derived(/^(ssh|git\+ssh|ssh\+git):\/\//i.test(url.trim()) || /^[^/:\s]+@[^/:\s]+:/.test(url.trim()));
	let httpUrl = $derived(/^https?:\/\//i.test(url.trim()));
	let keeping = $derived(!!repo && repo.auth === auth && repo.has_secret);
	let canSave = $derived(
		!!name.trim() &&
			!!url.trim() &&
			(auth !== 'https' || keeping || !!token.trim()) &&
			(auth !== 'ssh' || keyMode === 'generate' || (keyMode === 'keep' && keeping) || (keyMode === 'paste' && !!privateKey.trim()))
	);

	async function save() {
		const body: PluginRepositoryIn = { name: name.trim(), url: url.trim(), ref: ref.trim(), auth, username: username.trim(), enabled };
		if (auth === 'https' && token.trim()) body.secret = token.trim();
		if (auth === 'ssh' && keyMode === 'generate') body.generate_key = true;
		if (auth === 'ssh' && keyMode === 'paste') {
			body.secret = privateKey;
			body.passphrase = passphrase;
		}
		saving = true;
		try {
			const r = repo ? await api().plugins.updateRepository(repo.id, body) : await api().plugins.addRepository(body);
			onsaved?.(r);
			// A new key has to be added to the repository first; anything else that failed, say why here.
			if (keyMode === 'generate' && auth === 'ssh') saved = r;
			else if (r.error) saved = r;
			else {
				snackbar.show(`${r.name}: ${r.plugins ?? 0} plugin${r.plugins === 1 ? '' : 's'}`);
				open = false;
			}
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the repository');
		} finally {
			saving = false;
		}
	}

	async function fetchAgain() {
		if (!saved) return;
		fetching = true;
		try {
			saved = await api().plugins.fetchRepository(saved.id);
			onsaved?.(saved);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not fetch it');
		} finally {
			fetching = false;
		}
	}

	async function forgetHostKeys(r: PluginRepository) {
		try {
			const out = await api().plugins.forgetHostKeys(r.id);
			onsaved?.(out);
			if (saved) saved = out;
			snackbar.show('The next fetch trusts the key the server shows then');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not forget the host keys');
		}
	}

	async function copyKey(key: string) {
		try {
			await navigator.clipboard.writeText(key);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			snackbar.error('Copying needs clipboard access; select the text instead');
		}
	}

	const AUTHS: { id: Auth; label: string; icon: string }[] = [
		{ id: 'none', label: 'Public', icon: 'public' },
		{ id: 'https', label: 'Username and token', icon: 'password' },
		{ id: 'ssh', label: 'SSH key', icon: 'key' }
	];
	let keyModes = $derived<{ id: KeyMode; label: string }[]>([
		...(repo?.auth === 'ssh' && repo.has_secret ? [{ id: 'keep' as const, label: 'Keep the key' }] : []),
		{ id: 'generate', label: repo?.auth === 'ssh' && repo.has_secret ? 'Make a new key' : 'Make a key' },
		{ id: 'paste', label: 'Paste a private key' }
	]);
</script>

<Dialog bind:open title={saved ? saved.name : repo ? `Edit ${repo.name}` : 'Add a plugin repository'} icon="source" size="md">
	{#if saved}
		<div class="flex flex-col gap-4 pb-2">
			{#if saved.public_key && keyMode === 'generate'}
				<p class="body-medium text-on-surface">
					Castor signs in with this key. Add it to the repository as a <b class="font-medium">deploy key</b> (read access is all it needs), or to an account that can read it:
				</p>
				<div>
					<div class="mb-1 flex items-center justify-between">
						<span class="label-large text-on-surface">Public key</span>
						<Button variant="text" icon={copied ? 'check' : 'content_copy'} onclick={() => copyKey(saved!.public_key!)}>{copied ? 'Copied' : 'Copy'}</Button>
					</div>
					<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] break-all whitespace-pre-wrap text-on-surface select-all">{saved.public_key}</pre>
					<p class="mt-1.5 body-small text-on-surface-variant">The private key stays in Castor's vault, on the server.</p>
				</div>
			{/if}
			<div
				class="flex items-start gap-3 rounded-md px-3 py-2.5 {saved.error ? 'bg-error-container text-on-error-container' : 'bg-primary-container text-on-primary-container'}"
				role="status"
			>
				<Icon name={saved.error ? 'error' : 'check_circle'} size={20} class="mt-px shrink-0" />
				<div class="min-w-0 flex-1 body-medium">
					{#if saved.error}
						<p class="font-medium">{saved.public_key && keyMode === 'generate' ? 'Not in yet: add the key, then fetch again.' : 'The fetch failed.'}</p>
						<pre class="mt-1 font-mono text-[12px] break-words whitespace-pre-wrap">{saved.error}</pre>
					{:else}
						Fetched {saved.ref || 'its default branch'} ({saved.commit?.slice(0, 10)}): {saved.plugins ?? 0} plugin{saved.plugins === 1 ? '' : 's'}. They're under Browse.
					{/if}
				</div>
			</div>
			{#if saved.host_keys.length && saved.error?.includes("isn't the one trusted")}
				<Button variant="outlined" icon="key_off" onclick={() => forgetHostKeys(saved!)}>Forget the trusted host key</Button>
			{/if}
		</div>
	{:else}
		<div class="flex flex-col gap-4 pt-1 pb-2">
			<p class="body-medium text-on-surface-variant">
				Any Git repository Castor's server can reach. Each folder in it with a <code class="font-mono">castor-plugin.yaml</code> is a plugin you can install from Browse;
				Castor packs the folder as the castor-plugins repository's <code class="font-mono">pack.py</code> does.
			</p>
			<TextField label="Name" bind:value={name} required placeholder="Our plugins" />
			<TextField
				label="URL"
				bind:value={url}
				required
				mono
				placeholder="https://gitlab.example.com/team/castor-plugins.git"
				supporting="Anything git clones: https://…, ssh://…, git@host:team/repo.git, git://…, file:///srv/plugins.git"
			/>
			<TextField label="Branch or tag" bind:value={ref} mono placeholder="its default branch" supporting="Plugins come from this branch or tag; updates when it moves." />

			<div class="flex flex-col gap-2">
				<span class="label-large text-on-surface">Sign in</span>
				<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Sign in">
					{#each AUTHS as a (a.id)}
						<Chip variant="filter" icon={a.icon} selected={auth === a.id} role="radio" aria-checked={auth === a.id} onclick={() => (auth = a.id)}>{a.label}</Chip>
					{/each}
				</div>
				{#if auth === 'none' && sshUrl}
					<p class="body-small text-on-surface-variant">An SSH address usually needs a key, even for a public repository.</p>
				{:else if auth === 'https' && !httpUrl && url.trim()}
					<p class="body-small text-on-surface-variant">A username and token are for https:// addresses.</p>
				{:else if auth === 'ssh' && !sshUrl && url.trim()}
					<p class="body-small text-on-surface-variant">An SSH key is for ssh:// or git@host:… addresses.</p>
				{/if}
			</div>

			{#if auth === 'https'}
				<div class="grid gap-3 sm:grid-cols-2">
					<TextField label="Username" bind:value={username} placeholder="git" autocomplete="off" supporting="GitLab: oauth2. Bitbucket: x-token-auth. GitHub: any." />
					<TextField
						label="Token or password"
						type="password"
						bind:value={token}
						autocomplete="new-password"
						placeholder={keeping ? 'Saved; type to replace it' : ''}
						supporting="Read access is enough. Kept encrypted."
					/>
				</div>
			{:else if auth === 'ssh'}
				<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Key">
					{#each keyModes as k (k.id)}
						<Chip variant="filter" selected={keyMode === k.id} role="radio" aria-checked={keyMode === k.id} onclick={() => (keyMode = k.id)}>{k.label}</Chip>
					{/each}
				</div>
				{#if keyMode === 'generate'}
					<p class="body-small text-on-surface-variant">Castor makes an Ed25519 key and shows its public half, to add to the repository as a read-only deploy key.</p>
				{:else if keyMode === 'paste'}
					<TextField label="Private key" bind:value={privateKey} multiline rows={5} mono placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
					<TextField label="Its passphrase" type="password" bind:value={passphrase} autocomplete="new-password" supporting="If it has one." />
				{/if}
				<TextField label="User" bind:value={username} placeholder="from the URL (usually git)" autocomplete="off" />
			{/if}

			{#if repo}
				{#if repo.host_keys.length}
					<div class="flex flex-col gap-1">
						<span class="label-large text-on-surface">Trusted host keys</span>
						{#each repo.host_keys as k (k.host + k.fingerprint)}
							<p class="font-mono text-[12px] break-all text-on-surface-variant">{k.host} {k.type} {k.fingerprint}</p>
						{/each}
						<Button variant="text" icon="key_off" class="self-start" onclick={() => forgetHostKeys(repo!)}>Forget them</Button>
					</div>
				{/if}
				<p class="body-small text-on-surface-variant">
					{repo.fetched_at ? `Fetched ${relativeTime(repo.fetched_at)}` : 'Not fetched yet'}{repo.commit ? ` · ${repo.commit.slice(0, 10)}` : ''}
				</p>
			{/if}
			<Switch bind:checked={enabled} label="Use it (browse and update checks)" />
		</div>
	{/if}
	{#snippet actions()}
		{#if saved}
			<Button variant="text" onclick={() => (open = false)}>{saved.error ? 'Later' : 'Done'}</Button>
			{#if saved.error}<Button icon="sync" loading={fetching} onclick={fetchAgain}>Fetch again</Button>{/if}
		{:else}
			<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
			<Button icon={repo ? 'save' : 'add'} loading={saving} disabled={!canSave} onclick={save}>{repo ? 'Save and fetch' : 'Add and fetch'}</Button>
		{/if}
	{/snippet}
</Dialog>
