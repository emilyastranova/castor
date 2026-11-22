/**
 * @castor/plugin: what a Castor plugin imports (docs/PLUGINS.md).
 *
 *   import { definePlugin } from "@castor/plugin";
 *   definePlugin({
 *     activate(ctx) {
 *       ctx.actions.on("import", async (run) => {
 *         const text = await run.file!.text();
 *         await ctx.assets.bulk(run.project!.id, { assets: [...], links: [...] });
 *         return `Imported ${n} hosts`;
 *       });
 *     },
 *   });
 *
 * Castor runs the plugin with Deno and talks to it over stdin/stdout, one JSON-RPC 2.0 message per
 * line. Everything a plugin does in Castor goes through `ctx.api`, as the plugin's own service
 * account, with only the permissions its manifest asked for and an admin approved. `console.log`
 * and friends go to the plugin's log in Settings → Plugins.
 */

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

/** An error from Castor's API (`status` is the HTTP status) or from Castor itself. */
export class CastorError extends Error {
  constructor(message: string, readonly status = 0, readonly detail: unknown = null) {
    super(message);
    this.name = "CastorError";
  }
}

export interface PluginInfo {
  id: string;
  version: string;
}

export interface Logger {
  debug(...args: unknown[]): void;
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

export interface Api {
  /** Any endpoint of Castor's REST API (`/api/...`; see /api/docs). Throws CastorError on 4xx/5xx. */
  request<T = unknown>(method: string, path: string, opts?: { query?: Record<string, unknown>; body?: unknown }): Promise<T>;
  get<T = unknown>(path: string, query?: Record<string, unknown>): Promise<T>;
  post<T = unknown>(path: string, body?: unknown): Promise<T>;
  patch<T = unknown>(path: string, body?: unknown): Promise<T>;
  put<T = unknown>(path: string, body?: unknown): Promise<T>;
  delete<T = unknown>(path: string): Promise<T>;
  /** A file to an endpoint that takes one (multipart `file`, plus form fields), up to 20 MB:
   *  `ctx.api.upload(`/api/tasks/${id}/attachments`, { name: "report.docx", bytes })`. */
  upload<T = unknown>(path: string, file: { name: string; bytes: Uint8Array; contentType?: string }, fields?: Record<string, string>): Promise<T>;
}

/** An asset to record: `ref` names it within the call so links can point at it. */
export interface BulkAsset {
  ref?: string;
  type: string;
  fields: Record<string, unknown>;
  /** A credential's secret (stored in Castor's vault). */
  secret?: string;
}

export interface BulkLink {
  /** A `ref` from this call's assets, or an asset id. */
  from: string;
  to: string;
  kind: string;
  fields?: Record<string, unknown>;
  note?: string;
}

export interface BulkResult {
  created: number;
  updated: number;
  links: number;
  skipped: { ref: string | null; reason: string }[];
  refs: Record<string, string>;
}

export interface Assets {
  /** Record many assets and the relationships between them in one call (POST /api/projects/{id}/assets/bulk).
   *  Each one merges with what the project already knows, like a tool's results. */
  bulk(projectId: string, payload: { assets: BulkAsset[]; links?: BulkLink[]; task_id?: string }): Promise<BulkResult>;
}

export interface Storage {
  /** The plugin's own key-value space; with `project`, that project's (it goes with the project). */
  get<T = Json>(key: string, opts?: { project?: string }): Promise<T | null>;
  set(key: string, value: Json, opts?: { project?: string }): Promise<void>;
  delete(key: string, opts?: { project?: string }): Promise<void>;
  list(opts?: { prefix?: string; project?: string }): Promise<string[]>;
}

/** A file someone gave an action. Read it whole, or stream it (for big scans). */
export interface PluginFile {
  name: string;
  size: number;
  bytes(): Promise<Uint8Array>;
  text(): Promise<string>;
  stream(): ReadableStream<Uint8Array>;
}

export interface ActionRun {
  runId: string;
  action: string;
  /** Who ran it. */
  user: { id: string; username: string; display_name: string } | null;
  project: { id: string; codename: string; name: string } | null;
  task: { id: string; title: string } | null;
  asset: { id: string; type: string; label: string } | null;
  file: PluginFile | null;
  /** The settings for this run: the shared ones plus the person's own (an API key of theirs). */
  settings: Record<string, unknown>;
  /** Shown on the run while it goes: "120 of 400 hosts". */
  progress(p: { done?: number; total?: number; text?: string }): void;
  log: Logger;
  /** Library actions (manifest `library_actions`), run from a card or a playbook step: the card's
   *  argument values, the variable sets bound to its slots ({slot: {field: value}}, secrets
   *  included: don't log them), and the job. Empty for button actions. */
  args: Record<string, unknown>;
  vars: Record<string, Record<string, string>>;
  job: { id: string } | null;
  /** Aborted when someone cancels the card's job: stop early (pass it to fetch). */
  signal: AbortSignal;
}

/** Something that happened in a project the plugin is enabled in (manifest `events:`). */
export interface PluginEvent<T = Record<string, unknown>> {
  name: "asset.created" | "asset.linked" | "job.finished" | "run.finished" | "task.moved" | "comment.created";
  /** When it happened (ISO 8601). */
  at: string;
  project: { id: string; codename: string; name: string };
  /** The person who caused it, when there is one (who added it, whose run found it). */
  user: { id: string; username: string; display_name: string } | null;
  /** The settings for this event: the shared ones plus that person's own (when there's a person). */
  settings: Record<string, unknown>;
  data: T;
}

/** asset.created's data. */
export interface AssetCreated {
  asset: { id: string; type: string; key: string; label: string; fields: Record<string, unknown>; in_scope: boolean; scope_groups: string[]; confidence: number };
}

/** What an action returns: a sentence for the person who ran it ("Imported 42 hosts"). */
/** What an action hands back: a message for the run, and (library actions) its `output`, the
 *  job's output that the definition's parsers read: text, or anything JSON (an array of records
 *  for json-records, an object for json-value). */
/** `error` (library actions): what it did went wrong (an outside job that failed); the card's job fails
 *  with it, and its `output` is still read by the definition's parsers. Throwing fails it with no output. */
export type ActionResult = string | { message?: string; output?: unknown; error?: string } | void;

/** A machine an agent type reaches (a Salt minion, a Puppet node): each one an admin brings in
 *  becomes a Castor agent. `id` is the machine's id in your system and must stay the same; `tags`
 *  (from grains, facts, inventory groups) go on the agent and are refreshed. */
export interface Machine {
  id: string;
  name?: string;
  hostname?: string;
  os?: string;
  tags?: string[];
  facts?: Record<string, string | number | boolean>;
}

/** How a job's tool would be provided: the tool's image, build recipe files and host check and
 *  install, for your system to provide as it can (or fail the job saying why). */
export interface JobTool {
  id: string;
  name: string;
  kind: "plugin";
  image: string | null;
  build: Record<string, string> | null;
  build_ref: string | null;
  check: string;
  install: string;
}

/** A job to run on one of your machines. Variable values are in `env` and `files` only: hand them
 *  to the machine without putting them in a command line (your system's env parameter, a file). */
export interface AgentJob {
  id: string;
  command: string;
  /** The command as people see it (values filled in, secrets masked): log this, not `command`. */
  displayCommand: string;
  /** "container" when it should run in `image` (only for an agent type with `containers: true`). */
  runtime: "host" | "container";
  image: string | null;
  containerFlags: string[];
  /** Run it with these environment variables (variables' values, CASTOR_WORK…). */
  env: Record<string, string>;
  /** Write these before it runs (relative `path` under the job's directory, `data` base64, `mode`),
   *  set `env` to each one's full path, and delete them afterwards. */
  files: { path: string; env: string; mode: number; data: string }[];
  timeoutS: number | null;
  tool: JobTool | null;
  /** Files to send back afterwards, relative to the job's directory ("scan.xml", "artifacts/**": everything
   *  under artifacts/): io.artifact each, named by that relative path. Never anything under .castor/. */
  artifacts: string[];
}

export interface JobIO {
  machine: Machine;
  /** Output, as it comes (stdout unless said). Castor masks secrets in it. */
  log(data: string, stream?: "stdout" | "stderr"): void;
  /** A file the job produced, kept as the job's artifact (parsers read them). */
  artifact(name: string, content: Uint8Array | string, contentType?: string): Promise<void>;
  /** Aborted when someone cancels the job: stop it on the machine and return. */
  signal: AbortSignal;
}

/** What a job ended with: its exit code, or an error saying why it couldn't run. */
export interface AgentResult {
  exitCode: number | null;
  error?: string;
}

/** An agent type of the manifest's `agent_types`: list the machines, run jobs on them. */
export interface AgentTypeHandler {
  list(): Machine[] | Promise<Machine[]>;
  run(job: AgentJob, io: JobIO): AgentResult | Promise<AgentResult>;
}

export interface Context {
  plugin: PluginInfo;
  castorVersion: string;
  /** The shared settings an admin set (secret ones included). Settings each person sets for
   *  themselves aren't here: read them from `run.settings` and `event.settings`. */
  settings: Record<string, unknown>;
  api: Api;
  assets: Assets;
  storage: Storage;
  log: Logger;
  actions: { on(id: string, handler: (run: ActionRun) => ActionResult | Promise<ActionResult>): void };
  /** Handle an event the manifest asks for. Events come one plugin-queue at a time, retried a few
   *  times if the handler throws; ones the plugin causes itself aren't sent to it. */
  events: { on<T = Record<string, unknown>>(name: PluginEvent["name"], handler: (event: PluginEvent<T>) => void | Promise<void>): void };
  /** Answer `Castor.call(name, args)` from the plugin's panels; what it returns (JSON) goes back
   *  to the panel. Register these in `activate`: Castor learns them when it returns. */
  ui: { on<A = unknown>(name: string, handler: (call: UiCall<A>) => unknown, options?: UiOptions): void };
  /** Answer one of the manifest's `mcp_tools` when an AI assistant calls it through Castor's MCP
   *  server (as `<plugin id>__<name>`). What it returns (text, or JSON) is the tool's answer; throw a
   *  `CastorError` to say it failed. Register these in `activate`: Castor learns them when it returns. */
  mcp: { on<A = Record<string, unknown>>(name: string, handler: (call: McpCall<A>) => unknown): void };
  /** Answer one of the manifest's `agent_types`: the machines it reaches, and running jobs on them. */
  agents: { on(type: string, handler: AgentTypeHandler): void };
  /** Answer one of the manifest's `hooks`: decisions Castor asks for while it works, with a deadline
   *  (the hook's `timeout_seconds`). Register these in `activate`. */
  hooks: {
    on(kind: "job.prepare", handler: (call: JobPrepareCall) => JobPrepared | void | Promise<JobPrepared | void>): void;
    on(kind: "assets.map", handler: (call: AssetsMapCall) => AssetsMapped | void | Promise<AssetsMapped | void>): void;
  };
}

/** `job.prepare`: one of the plugin's commands is about to be sent to a machine. */
export interface JobPrepareCall {
  /** The command's library id. */
  command: string;
  /** Its arguments, checked and with defaults (lists as arrays). */
  args: Record<string, unknown>;
  /** Its input files as its definition filled them: {name: content}. */
  inputs: Record<string, string>;
  job: { id: string; task_id: string };
  card: { id: string; title: string };
  project: { id: string; codename: string; name: string };
  /** The machine it's going to. */
  agent: { id: string; name: string; tags: string[] } | null;
  log: Logger;
}

/** What the job gets: input files (new ones, or the definition's replaced) and plain environment
 *  (names in capitals, not CASTOR_…). Nothing secret: secrets come from the vault. */
export interface JobPrepared {
  inputs?: Record<string, string | { content: string; filename?: string; executable?: boolean }>;
  env?: Record<string, string>;
}

/** `assets.map`: one of the plugin's parsers read a run. */
export interface AssetsMapCall {
  parser: string;
  output: string;
  type: string;
  /** What the parser read. */
  value: unknown;
  job: { id: string; task_id: string; finished_at: string | null };
  card: { id: string; title: string };
  project: { id: string; codename: string; name: string };
  log: Logger;
}

/** The assets and relationships that is, as `ctx.assets.bulk` takes them. */
export interface AssetsMapped {
  assets?: { ref?: string; type: string; fields: Record<string, unknown> }[];
  links?: { from: string; to: string; kind: string; fields?: Record<string, unknown>; note?: string }[];
}

/** A call of one of the plugin's MCP tools (manifest `mcp_tools`), by an assistant acting for a person. */
export interface McpCall<A = Record<string, unknown>> {
  /** The tool's name in the manifest. */
  name: string;
  /** Its arguments, as the assistant sent them (check them: Castor only checks they're an object). */
  args: A;
  /** Who the assistant acts for. `can_write` is false for a read-only tool. */
  user: { id: string; username: string; display_name: string; can_write: boolean };
  /** The project it's called in (one the plugin is on in). */
  project: { id: string; codename: string; name: string };
  /** The shared settings plus the person's own. */
  settings: Record<string, unknown>;
  log: Logger;
}

/** A request from one of the plugin's panels (manifest `ui:`), made by the person looking at it. */
export interface UiCall<A = unknown> {
  name: string;
  args: A;
  /** The panel's id in the manifest. */
  panel: string;
  /** Who's looking. `can_write` is false for viewers: only handlers declared `readOnly` reach them. */
  user: { id: string; username: string; display_name: string; can_write: boolean };
  project: { id: string; codename: string; name: string };
  /** The card or asset the panel is shown on, if any. */
  task: { id: string; title: string } | null;
  asset: { id: string; type: string; label: string } | null;
  /** The shared settings plus the person's own. */
  settings: Record<string, unknown>;
  log: Logger;
}

export interface UiOptions {
  /** Viewers (who can't change the project) may call it too. Only say so if it changes nothing. */
  readOnly?: boolean;
}

export interface PluginDefinition {
  activate?(ctx: Context): void | Promise<void>;
  deactivate?(): void | Promise<void>;
}

// ---------- the wire ----------

const encoder = new TextEncoder();
type Pending = { resolve(v: unknown): void; reject(e: unknown): void };
const pending = new Map<number, Pending>();
let nextId = 1;

function send(message: Record<string, unknown>): void {
  const bytes = encoder.encode(JSON.stringify({ jsonrpc: "2.0", ...message }) + "\n");
  let written = 0;
  while (written < bytes.length) written += Deno.stdout.writeSync(bytes.subarray(written));
}

function call<T>(method: string, params: Record<string, unknown>): Promise<T> {
  const id = nextId++;
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
    send({ id, method, params });
  });
}

function notify(method: string, params: Record<string, unknown>): void {
  send({ method, params });
}

function text(args: unknown[]): string {
  return args.map((a) => (typeof a === "string" ? a : a instanceof Error ? (a.stack ?? a.message) : JSON.stringify(a))).join(" ");
}

function logger(runId?: string): Logger {
  const at = (level: string) => (...args: unknown[]) => notify("log", { level, message: text(args).slice(0, 20000), run_id: runId ?? null });
  return { debug: at("debug"), info: at("info"), warn: at("warn"), error: at("error") };
}

// stdout is the wire: anything printed goes to the log instead.
const rootLog = logger();
console.log = rootLog.info;
console.info = rootLog.info;
console.debug = rootLog.debug;
console.warn = rootLog.warn;
console.error = rootLog.error;

const api: Api = {
  async request<T>(method: string, path: string, opts: { query?: Record<string, unknown>; body?: unknown } = {}): Promise<T> {
    const r = await call<{ status: number; body: unknown }>("api", { method: method.toUpperCase(), path, query: opts.query ?? null, body: opts.body ?? null });
    if (r.status >= 400) {
      const detail = (r.body as { detail?: unknown } | null)?.detail ?? r.body;
      throw new CastorError(`${method.toUpperCase()} ${path}: HTTP ${r.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`, r.status, detail);
    }
    return r.body as T;
  },
  get: (path, query) => api.request("GET", path, { query }),
  post: (path, body) => api.request("POST", path, { body }),
  patch: (path, body) => api.request("PATCH", path, { body }),
  put: (path, body) => api.request("PUT", path, { body }),
  delete: (path) => api.request("DELETE", path),
  async upload<T>(path: string, file: { name: string; bytes: Uint8Array; contentType?: string }, fields: Record<string, string> = {}): Promise<T> {
    if (file.bytes.length > 20 * 1024 * 1024) throw new CastorError(`${file.name} is over 20 MB`);
    let binary = "";
    for (let i = 0; i < file.bytes.length; i += 0x8000) binary += String.fromCharCode(...file.bytes.subarray(i, i + 0x8000));
    const r = await call<{ status: number; body: unknown }>("api", {
      method: "POST", path, form: fields, file: { name: file.name, content_type: file.contentType ?? "", base64: btoa(binary) },
    });
    if (r.status >= 400) {
      const detail = (r.body as { detail?: unknown } | null)?.detail ?? r.body;
      throw new CastorError(`POST ${path}: HTTP ${r.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`, r.status, detail);
    }
    return r.body as T;
  },
};

const storage: Storage = {
  get: (key, opts = {}) => call("storage.get", { key, project: opts.project ?? null }),
  set: (key, value, opts = {}) => call("storage.set", { key, value, project: opts.project ?? null }),
  delete: (key, opts = {}) => call("storage.delete", { key, project: opts.project ?? null }),
  list: (opts = {}) => call("storage.list", { prefix: opts.prefix ?? "", project: opts.project ?? null }),
};

const CHUNK = 1024 * 1024;

function fromBase64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function pluginFile(runId: string, name: string, size: number): PluginFile {
  const read = (offset: number) => call<{ data: string; eof: boolean }>("file.read", { run_id: runId, offset, length: CHUNK });
  const file: PluginFile = {
    name,
    size,
    stream() {
      let offset = 0;
      let done = false;
      return new ReadableStream<Uint8Array>({
        async pull(controller) {
          if (done) return controller.close();
          const chunk = await read(offset);
          const bytes = fromBase64(chunk.data);
          offset += bytes.length;
          if (bytes.length) controller.enqueue(bytes);
          if (chunk.eof || !bytes.length) {
            done = true;
            controller.close();
          }
        },
      });
    },
    async bytes() {
      return new Uint8Array(await new Response(file.stream()).arrayBuffer());
    },
    async text() {
      return new TextDecoder().decode(await file.bytes());
    },
  };
  return file;
}

// ---------- the plugin ----------

const handlers = new Map<string, (run: ActionRun) => ActionResult | Promise<ActionResult>>();
const aborts = new Map<string, AbortController>();
const uiHandlers = new Map<string, { handler: (call: UiCall) => unknown; readOnly: boolean }>();
const mcpHandlers = new Map<string, (call: McpCall) => unknown>();
const agentTypes = new Map<string, AgentTypeHandler>();
const hookHandlers = new Map<string, (call: never) => unknown>();
const eventHandlers = new Map<string, ((event: PluginEvent) => void | Promise<void>)[]>();
let definition: PluginDefinition | null = null;

async function onRequest(method: string, params: Record<string, unknown>): Promise<unknown> {
  if (method === "activate") {
    const ctx: Context = {
      plugin: params.plugin as PluginInfo,
      castorVersion: String(params.castor_version ?? ""),
      settings: (params.settings ?? {}) as Record<string, unknown>,
      api,
      assets: { bulk: (projectId, payload) => api.post(`/api/projects/${projectId}/assets/bulk`, payload) },
      storage,
      log: rootLog,
      actions: { on: (id, handler) => void handlers.set(id, handler) },
      ui: {
        on: (name, handler, options) => {
          if (!/^[A-Za-z0-9_.-]{1,64}$/.test(name)) throw new CastorError(`ctx.ui.on: “${name}” isn't a usable name (letters, digits, _ . -)`);
          uiHandlers.set(name, { handler: handler as (call: UiCall) => unknown, readOnly: !!options?.readOnly });
        },
      },
      mcp: {
        on: (name, handler) => {
          if (!/^[a-z][a-z0-9_]{0,39}$/.test(name)) throw new CastorError(`ctx.mcp.on: “${name}” isn't a tool name (manifest mcp_tools)`);
          mcpHandlers.set(name, handler as (call: McpCall) => unknown);
        },
      },
      hooks: {
        on: (kind: string, handler: (call: never) => unknown) => {
          if (kind !== "job.prepare" && kind !== "assets.map") throw new CastorError(`ctx.hooks.on: “${kind}” isn't a hook (job.prepare, assets.map)`);
          hookHandlers.set(kind, handler);
        },
      },
      agents: {
        on: (type, handler) => {
          if (!/^[a-z][a-z0-9-]{0,39}$/.test(type)) throw new CastorError(`ctx.agents.on: “${type}” isn't an agent type name (manifest agent_types)`);
          agentTypes.set(type, handler);
        },
      },
      events: {
        on: (name, handler) => {
          const list = eventHandlers.get(name) ?? [];
          list.push(handler as (event: PluginEvent) => void | Promise<void>);
          eventHandlers.set(name, list);
        },
      },
    };
    await definition?.activate?.(ctx);
    return {
      actions: [...handlers.keys()],
      events: [...eventHandlers.keys()],
      ui: [...uiHandlers].map(([name, h]) => ({ name, read_only: h.readOnly })),
      mcp: [...mcpHandlers.keys()],
      agents: [...agentTypes.keys()],
      hooks: [...hookHandlers.keys()],
    };
  }
  if (method === "agent.list") {
    const handler = agentTypes.get(String(params.type));
    if (!handler) throw new CastorError(`This plugin has no handler for the agent type “${params.type}” (ctx.agents.on)`);
    return await handler.list();
  }
  if (method === "agent.run") {
    const handler = agentTypes.get(String(params.type));
    if (!handler) throw new CastorError(`This plugin has no handler for the agent type “${params.type}” (ctx.agents.on)`);
    const j = params.job as Record<string, unknown>;
    const jobId = String(j.id);
    const abort = new AbortController();
    aborts.set(jobId, abort);
    let logged = 0; // numbered, so Castor keeps them in order and waits for the last before the end
    const job: AgentJob = {
      id: jobId,
      command: String(j.command ?? ""),
      displayCommand: String(j.display_command ?? ""),
      runtime: j.runtime === "container" ? "container" : "host",
      image: (j.image ?? null) as string | null,
      containerFlags: (j.container_flags ?? []) as string[],
      env: (j.env ?? {}) as Record<string, string>,
      files: (j.files ?? []) as AgentJob["files"],
      timeoutS: (j.timeout_s ?? null) as number | null,
      tool: (j.tool ?? null) as JobTool | null,
      artifacts: (j.artifacts ?? []) as string[],
    };
    const io: JobIO = {
      machine: params.machine as Machine,
      log: (data, stream = "stdout") => notify("agent.log", { job_id: jobId, stream, data: String(data), n: ++logged }),
      async artifact(name, content, contentType) {
        const bytes = typeof content === "string" ? new TextEncoder().encode(content) : content;
        let binary = "";
        for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        await call("agent.artifact", { job_id: jobId, name, base64: btoa(binary), content_type: contentType ?? "" });
      },
      signal: abort.signal,
    };
    try {
      const result = await handler.run(job, io);
      return { exit_code: result?.exitCode ?? null, error: result?.error ?? null, logged };
    } finally {
      aborts.delete(jobId);
    }
  }
  if (method === "event") {
    const event = params as unknown as PluginEvent;
    for (const handler of eventHandlers.get(event.name) ?? []) await handler(event);
    return null;
  }
  if (method === "action") {
    const handler = handlers.get(String(params.action));
    if (!handler) throw new CastorError(`This plugin has no handler for the action “${params.action}” (ctx.actions.on)`);
    const runId = String(params.run_id);
    const f = params.file as { name: string; size: number } | null;
    const abort = new AbortController();
    aborts.set(runId, abort);
    const run: ActionRun = {
      runId,
      action: String(params.action),
      user: (params.user ?? null) as ActionRun["user"],
      project: (params.project ?? null) as ActionRun["project"],
      task: (params.task ?? null) as ActionRun["task"],
      asset: (params.asset ?? null) as ActionRun["asset"],
      file: f ? pluginFile(runId, f.name, f.size) : null,
      settings: (params.settings ?? {}) as Record<string, unknown>,
      progress: (p) => notify("progress", { run_id: runId, ...p }),
      log: logger(runId),
      args: (params.args ?? {}) as Record<string, unknown>,
      vars: (params.vars ?? {}) as Record<string, Record<string, string>>,
      job: (params.job ?? null) as ActionRun["job"],
      signal: abort.signal,
    };
    try {
      const result = await handler(run);
      if (typeof result === "string") return { message: result, output: result };
      return { message: result?.message ?? "", output: result?.output ?? null, ...(result?.error ? { error: String(result.error) } : {}) };
    } finally {
      aborts.delete(runId);
    }
  }
  if (method === "cancel") {
    aborts.get(String(params.run_id))?.abort();
    return null;
  }
  if (method === "ui") {
    const entry = uiHandlers.get(String(params.name));
    if (!entry) throw new CastorError(`This plugin has no handler “${params.name}” (ctx.ui.on)`);
    const call = { ...(params as unknown as UiCall), log: rootLog };
    const result = await entry.handler(call);
    return result === undefined ? null : result;
  }
  if (method === "hook") {
    const handler = hookHandlers.get(String(params.kind)) as ((call: unknown) => unknown) | undefined;
    if (!handler) throw new CastorError(`This plugin has no handler for the hook “${params.kind}” (ctx.hooks.on)`);
    const result = await handler({ ...params, log: rootLog });
    return result === undefined ? null : result;
  }
  if (method === "mcp") {
    const handler = mcpHandlers.get(String(params.name));
    if (!handler) throw new CastorError(`This plugin has no handler for the tool “${params.name}” (ctx.mcp.on)`);
    const result = await handler({ ...(params as unknown as McpCall), log: rootLog });
    return result === undefined ? null : result;
  }
  if (method === "deactivate") {
    await definition?.deactivate?.();
    setTimeout(() => Deno.exit(0), 10);
    return null;
  }
  throw new CastorError(`Unknown method ${method}`);
}

function onMessage(msg: { id?: number; method?: string; params?: Record<string, unknown>; result?: unknown; error?: { message: string; data?: unknown } }): void {
  if (msg.method !== undefined) {
    const { id, method, params } = msg;
    onRequest(method, params ?? {}).then(
      (result) => id !== undefined && send({ id, result: result ?? null }),
      (err) => {
        rootLog.error(err);
        if (id !== undefined) send({ id, error: { code: -32000, message: err instanceof Error ? err.message : String(err) } });
      },
    );
    return;
  }
  const waiting = msg.id !== undefined ? pending.get(msg.id) : undefined;
  if (!waiting) return;
  pending.delete(msg.id!);
  if (msg.error) waiting.reject(new CastorError(msg.error.message, 0, msg.error.data ?? null));
  else waiting.resolve(msg.result);
}

async function listen(): Promise<void> {
  let buffer = "";
  for await (const chunk of Deno.stdin.readable.pipeThrough(new TextDecoderStream())) {
    buffer += chunk;
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      try {
        onMessage(JSON.parse(line));
      } catch (err) {
        rootLog.error("a message Castor sent didn't parse:", err);
      }
    }
  }
  Deno.exit(0); // Castor closed the pipe: it stopped this plugin
}

/** Describe the plugin; Castor calls `activate` once it starts. Call it once, from the main module. */
export function definePlugin(def: PluginDefinition): PluginDefinition {
  if (definition) throw new CastorError("definePlugin was called twice");
  definition = def;
  void listen();
  return def;
}
