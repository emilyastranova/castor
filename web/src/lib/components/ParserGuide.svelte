<script lang="ts">
	import { api } from '$lib/api';
	import { Button, Dialog, Icon, Progress } from '$lib/m3';

	/** Everything about writing a parser: the keys, each format with an example to try, the
	 *  output types and their fields (from the server, so it can't drift), and the limits. */
	interface Props {
		open: boolean;
		/** Load an example into the editor (its definition and some output to test it on). */
		onuse?: (yaml: string, sample: string) => void;
	}
	let { open = $bindable(false), onuse }: Props = $props();

	type Reference = Awaited<ReturnType<ReturnType<typeof api>['parsers']['reference']>>;
	let ref = $state<Reference | null>(null);
	$effect(() => {
		if (open && !ref) api().parsers.reference().then((r) => (ref = r)).catch(() => {});
	});

	const SECTIONS = [
		['how', 'How a parser works'],
		['keys', 'Every key'],
		['formats', 'Formats, with examples'],
		['types', 'Output types and fields'],
		['fields', 'Mapping fields'],
		['javascript', 'JavaScript'],
		['using', 'Using it in a command'],
		['limits', 'Limits and tips']
	] as const;
	let body = $state<HTMLElement | null>(null);
	function go(id: string) {
		body?.querySelector(`#guide-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	interface Example {
		title: string;
		what: string;
		yaml: string;
		sample: string;
	}
	const EXAMPLES: Record<string, Example> = {
		lines: {
			title: 'lines',
			what: 'One result per non-empty line. A `pattern` keeps only matching lines; its first group (or a named group) becomes the value.',
			yaml: `id: subdomains
name: Subdomains
type: list<string>
format: lines
pattern: '^([a-z0-9.-]+\\.example\\.com)$'
`,
			sample: `www.example.com
api.example.com
[INF] Enumerating subdomains
mail.example.com
`
		},
		regex: {
			title: 'regex',
			what: 'One result per match anywhere in the output. Named groups (?P<name>…) are the fields. Patterns are Python regex with MULTILINE on, so ^ and $ match at each line.',
			yaml: `id: ssh-logins
name: SSH logins
type: list<object>
format: regex
pattern: '^(?P<time>\\w{3}\\s+\\d+ [\\d:]+) \\S+ sshd\\[\\d+\\]: (?P<result>Accepted|Failed) (?P<method>\\S+) for (?P<user>\\S+) from (?P<ip>\\S+) port (?P<port>\\d+)'
types: {port: int}
`,
			sample: `Nov  8 10:01:02 web sshd[123]: Accepted publickey for alice from 10.0.0.5 port 50222 ssh2
Nov  8 10:03:00 web sshd[124]: Received disconnect from 10.0.0.5 port 50222:11: bye
Nov  8 10:05:13 web sshd[130]: Failed password for root from 203.0.113.9 port 40111 ssh2
`
		},
		json: {
			title: 'json',
			what: 'A JSON document. `records` is a dot path to the list of results (leave it out when the document is the list). Fields are read with dot paths inside each record.',
			yaml: `id: scanner-urls
name: Scanner URLs
type: list<url>
format: json
records: results.items
fields:
  url: target.url
  status: response.code
  title: response.title
`,
			sample: `{"results": {"items": [
  {"target": {"url": "https://a.example.com"}, "response": {"code": 200, "title": "Home"}},
  {"target": {"url": "https://b.example.com/login"}, "response": {"code": 401, "title": "Sign in"}}
]}}
`
		},
		jsonl: {
			title: 'jsonl',
			what: 'One JSON object per line (many tools write this with -json or -jsonl). `records` can pick a path inside each line.',
			yaml: `id: hosts-jsonl
name: Hosts from JSON lines
type: list<host>
format: jsonl
fields: {ip: addr, hostnames: names}
`,
			sample: `{"addr": "10.0.0.5", "names": ["web01.corp.local"]}
{"addr": "10.0.0.6", "names": []}
`
		},
		csv: {
			title: 'csv',
			what: 'Rows of a CSV (or TSV with `delimiter: "\\t"`). With `header: true` (the default) the first row names the columns; otherwise columns are 0, 1, 2…',
			yaml: `id: user-list
name: Users
type: list<object>
format: csv
types: {logins: int, admin: bool}
`,
			sample: `user,logins,admin
alice,14,yes
bob,3,no
`
		},
		xml: {
			title: 'xml',
			what: '`records` is an element path such as .//host. A field is @attribute, child/path (its text), child/@attribute, or . for the element’s own text. XML is read safely (no external entities).',
			yaml: `id: scan-xml-hosts
name: Hosts from XML
type: list<host>
format: xml
records: .//host
fields:
  ip: '@address'
  hostnames: name
`,
			sample: `<scan>
  <host address="10.0.0.5"><name>web01.corp.local</name></host>
  <host address="10.0.0.9"/>
</scan>
`
		},
		javascript: {
			title: 'javascript',
			what: 'When the output needs logic the formats can’t express (records spread over several lines, sections, state): `code` defines parse(text) and returns an array of objects.',
			yaml: `id: block-hosts
name: Hosts from report blocks
type: list<service>
format: javascript
code: |
  // Each "Host:" line starts a block; the port lines under it belong to it.
  function parse(text) {
    const out = [];
    let host = null;
    for (const line of text.split('\\n')) {
      const h = line.match(/^Host: (\\S+)/);
      if (h) { host = h[1]; continue; }
      const p = line.match(/^\\s+(\\d+)\\/(tcp|udp)\\s+open\\s+(\\S*)/);
      if (p && host) out.push({ host, port: Number(p[1]), proto: p[2], name: p[3] });
    }
    return out;
  }
`,
			sample: `Host: 10.0.0.5
   22/tcp  open  ssh
   80/tcp  open  http
Host: 10.0.0.9
   445/tcp open  microsoft-ds
`
		}
	};

	const KEYS: [string, string][] = [
		['id', 'What commands call it (lowercase letters, digits, dots, dashes, underscores). Can’t be a built-in’s name.'],
		['name, description', 'For people: shown on the Parsers page and in the library.'],
		['type', 'What it produces. One of the output types below; commands and playbook steps are checked against it.'],
		['format', 'How the output is read: lines, regex, json, jsonl, csv, xml or javascript.'],
		['pattern', 'regex: what to match (named groups are fields). lines: which lines to keep.'],
		['ignore_case', 'Match the pattern case-insensitively (default false).'],
		['records', 'json / jsonl: dot path to the records. xml: element path to them.'],
		['delimiter, header', 'csv: the column separator (default ","; "\\t" for tabs) and whether the first row names the columns (default true).'],
		['fields', 'Output field → where it comes from in a record. Leave a field out to read the record’s field of the same name.'],
		['values', 'Constants every result gets, e.g. {state: open, proto: tcp}.'],
		['types', 'object types: turn a field’s text into int, float, bool or list.'],
		['unique', 'Drop duplicate results (default true).'],
		['code', 'javascript: defines parse(text).'],
		['sample', 'Output it was written for. Saved from the test input; shown on the Parsers page.']
	];

	function use(e: Example) {
		onuse?.(e.yaml, e.sample);
		open = false;
	}
</script>

{#snippet example(e: Example)}
	<div class="flex flex-col gap-2 rounded-md border border-outline-variant p-3">
		<div class="flex flex-wrap items-center gap-2">
			<code class="rounded-xs bg-secondary-container px-1.5 font-mono text-[12.5px] text-on-secondary-container">format: {e.title}</code>
			<span class="flex-1"></span>
			{#if onuse}<Button variant="tonal" icon="play_arrow" onclick={() => use(e)}>Try this</Button>{/if}
		</div>
		<p class="body-medium text-on-surface">{e.what}</p>
		<div class="grid gap-2 lg:grid-cols-2">
			<pre class="overflow-x-auto rounded-sm bg-surface-container-highest p-2.5 font-mono text-[12px] leading-5 whitespace-pre text-on-surface">{e.yaml}</pre>
			<pre class="overflow-x-auto rounded-sm bg-surface-container p-2.5 font-mono text-[12px] leading-5 whitespace-pre text-on-surface-variant">{e.sample}</pre>
		</div>
	</div>
{/snippet}

<Dialog bind:open title="Writing a parser" icon="menu_book" size="lg">
	<div class="flex min-h-0 gap-6">
		<nav class="sticky top-0 hidden w-48 shrink-0 flex-col gap-0.5 self-start lg:flex" aria-label="Sections">
			{#each SECTIONS as [id, label] (id)}
				<button type="button" class="state-layer rounded-sm px-2 py-1.5 text-left body-medium text-on-surface-variant" onclick={() => go(id)}>{label}</button>
			{/each}
		</nav>
		<div bind:this={body} class="flex min-w-0 flex-1 flex-col gap-6 pb-4">
			<section id="guide-how" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">How a parser works</h3>
				<p class="max-w-[70ch] body-medium text-on-surface">
					A parser turns what a tool printed (or a file it wrote) into typed results: a list of hosts, services, URLs, rows of a table… Commands name a parser
					on each output, Castor runs it after the job, and the results show on the card, feed later playbook steps, and (for hosts, services, URLs and SMB
					hosts) become the project’s assets.
				</p>
				<p class="max-w-[70ch] body-medium text-on-surface">
					A parser here is a short YAML definition: what it produces (<code class="font-mono">type</code>), how to read the output (<code class="font-mono">format</code>),
					and which part of each record goes in which field. When the formats aren’t enough, it can be JavaScript instead. Either way, test it on real output in
					the Test input box: the results update as you type.
				</p>
			</section>

			<section id="guide-keys" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">Every key</h3>
				<div class="overflow-x-auto rounded-md border border-outline-variant">
					<table class="w-full body-small">
						<tbody>
							{#each KEYS as [k, d] (k)}
								<tr class="border-t border-outline-variant/60 align-top first:border-t-0">
									<td class="px-3 py-2 font-mono text-[12.5px] whitespace-nowrap text-on-surface">{k}</td>
									<td class="px-3 py-2 text-on-surface-variant">{d}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>

			<section id="guide-formats" class="flex flex-col gap-3">
				<h3 class="title-medium text-on-surface">Formats, with examples</h3>
				<p class="body-small text-on-surface-variant">Each example shows the definition and some output it reads. Try this loads both into the editor.</p>
				{#each Object.values(EXAMPLES) as e (e.title)}{@render example(e)}{/each}
			</section>

			<section id="guide-types" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">Output types and fields</h3>
				<p class="max-w-[70ch] body-medium text-on-surface">
					A result missing a required field (<span class="text-primary">*</span>) is left out. Use
					<code class="font-mono">object</code> / <code class="font-mono">list&lt;object&gt;</code> for anything else: a table with fields you choose.
				</p>
				{#if !ref}
					<Progress variant="circular" size={20} />
				{:else}
					<div class="grid gap-2 md:grid-cols-2">
						{#each ref.types.filter((t) => t.type.startsWith('list<') || t.free || ['string', 'number', 'boolean'].includes(t.type)) as t (t.type)}
							<div class="rounded-md bg-surface-container p-2.5">
								<code class="font-mono text-[12.5px] text-on-surface">{t.type}</code>
								<p class="mt-1 body-small text-on-surface-variant">
									{#if t.free}any fields you choose{:else}{#each t.fields as f, i (f.name)}{i ? ', ' : ''}<span class="font-mono">{f.name}</span>{#if f.required}<span class="text-primary">*</span>{/if} <span class="opacity-70">({f.kind})</span>{/each}{/if}
								</p>
							</div>
						{/each}
					</div>
					<p class="body-small text-on-surface-variant">The single types (host, service…) take the first result; the list types take them all.</p>
				{/if}
			</section>

			<section id="guide-fields" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">Mapping fields</h3>
				<ul class="flex max-w-[70ch] list-disc flex-col gap-1.5 pl-5 body-medium text-on-surface">
					<li>Without <code class="font-mono">fields</code>, each output field is read from the record’s field of the same name: name your regex groups or CSV columns after the type’s fields and there’s nothing to map.</li>
					<li>In <code class="font-mono">fields</code>, the value says where it comes from: a regex group name, a CSV column name (or 0, 1, 2…), a JSON dot path such as <code class="font-mono">response.code</code> or <code class="font-mono">tags.0</code>, or for XML <code class="font-mono">@attr</code>, <code class="font-mono">child/path</code>, <code class="font-mono">child/@attr</code>, <code class="font-mono">.</code>.</li>
					<li>Fields are typed for the built-in types: ports and status codes become numbers, <code class="font-mono">yes / true / 1 / enabled</code> become true, and list fields (hostnames, tech) split text on commas or spaces.</li>
					<li>For <code class="font-mono">object</code> types, text stays text unless <code class="font-mono">types</code> says otherwise{#if ref} ({ref.field_types.join(', ')}){/if}. JSON and JavaScript values keep their own types.</li>
					<li><code class="font-mono">values</code> sets a field to the same thing on every result, e.g. <code class="font-mono">{'{state: open}'}</code>.</li>
				</ul>
			</section>

			<section id="guide-javascript" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">JavaScript</h3>
				<ul class="flex max-w-[70ch] list-disc flex-col gap-1.5 pl-5 body-medium text-on-surface">
					<li>Define <code class="font-mono">function parse(text)</code>: <code class="font-mono">text</code> is everything the tool printed. Return an array of objects with the type’s fields (or of strings for list&lt;string&gt;).</li>
					<li>It runs in a sandbox: plain modern JavaScript, with no <code class="font-mono">require</code>, files, network, <code class="font-mono">process</code> or timers.{#if ref} Each run has {ref.limits.js_seconds} s and {ref.limits.js_memory_mb} MB.{/if}</li>
					<li>What it returns goes through the same field typing as the other formats, so returning <code class="font-mono">port: '22'</code> still gives a number.</li>
					<li>Errors show in the editor with their line number. Code outside <code class="font-mono">parse</code> runs when the parser is checked, so keep the top level to definitions.</li>
				</ul>
			</section>

			<section id="guide-using" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">Using it in a command</h3>
				<p class="max-w-[70ch] body-medium text-on-surface">Name it on an output of a command (Library → the command → YAML):</p>
				<pre class="overflow-x-auto rounded-sm bg-surface-container-highest p-2.5 font-mono text-[12px] leading-5 whitespace-pre text-on-surface">{`outputs:
  - name: logins
    parser: ssh-logins
    source: "{{work}}/auth.log"   # a file the tool writes; leave out to read stdout`}</pre>
				<p class="max-w-[70ch] body-medium text-on-surface">
					A parser a command uses can’t be deleted or change its type. When a job’s output doesn’t match its parser, the card offers to draft a new one for that
					output (and a playbook step asking on failure waits with the draft ready).
				</p>
			</section>

			<section id="guide-limits" class="flex flex-col gap-2">
				<h3 class="title-medium text-on-surface">Limits and tips</h3>
				<ul class="flex max-w-[70ch] list-disc flex-col gap-1.5 pl-5 body-medium text-on-surface">
					{#if ref}
						<li>A pattern has {ref.limits.regex_seconds} s per output; JavaScript {ref.limits.js_seconds} s and {ref.limits.js_memory_mb} MB; at most {ref.limits.records.toLocaleString()} records.</li>
						<li>The test input takes up to {ref.limits.test_input_mb} MB.</li>
					{/if}
					<li>Prefer the simplest format that fits: json over regex when the tool can write JSON, regex over JavaScript when one line is one result.</li>
					<li>Anchor patterns with <code class="font-mono">^</code> and <code class="font-mono">$</code> so stray lines don’t match.</li>
					<li>Paste real output, including the lines you don’t want: the test shows what gets left out.</li>
					<li>With an AI model set up, “Draft from the sample” writes a parser for the test input and checks it before you see it.</li>
				</ul>
			</section>
		</div>
	</div>
	{#snippet actions()}<Button variant="text" onclick={() => (open = false)}>Close</Button>{/snippet}
</Dialog>
