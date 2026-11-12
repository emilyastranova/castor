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
export type ActionResult = string | { message: string } | void;

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
const uiHandlers = new Map<string, { handler: (call: UiCall) => unknown; readOnly: boolean }>();
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
    };
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
    };
    const result = await handler(run);
    return { message: typeof result === "string" ? result : (result?.message ?? "") };
  }
  if (method === "ui") {
    const entry = uiHandlers.get(String(params.name));
    if (!entry) throw new CastorError(`This plugin has no handler “${params.name}” (ctx.ui.on)`);
    const call = { ...(params as unknown as UiCall), log: rootLog };
    const result = await entry.handler(call);
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
