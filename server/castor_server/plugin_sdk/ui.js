/* Castor's panel SDK, put inline into every plugin page Castor serves (docs/PLUGINS.md, phase C).
 *
 * A plugin's page runs in a sandboxed iframe with no network and no access to Castor's page or
 * cookies. This is how it reaches anything: messages to the Castor page around it, which asks
 * the server, which acts as the plugin (never as the person looking).
 *
 *   const ctx = await Castor.ready();            // { plugin, panel, project, task, asset, user }
 *   const hosts = await Castor.api.get(`/api/projects/${ctx.project.id}/assets`, { type: "host" });
 *   const paths = await Castor.call("paths", { asset: ctx.asset.id });   // ctx.ui.on("paths", …) in main.ts
 *   Castor.open({ asset: id });                  // open an asset (or { card: id })
 *   Castor.notify("Saved");
 *
 * The page's height follows its content. The theme's colours are CSS variables on :root
 * (--primary, --on-surface, --surface-container…) and change with Castor's.
 */
(() => {
  "use strict";
  const pending = new Map();
  let next = 1;
  let context = null;
  const waiting = [];
  const themed = [];

  const send = (message) => window.parent.postMessage({ castor: 1, ...message }, "*");

  function applyTheme(theme) {
    if (!theme) return;
    const root = document.documentElement;
    for (const [name, value] of Object.entries(theme.vars || {})) root.style.setProperty(`--${name}`, value);
    root.dataset.theme = theme.dark ? "dark" : "light";
    root.style.colorScheme = theme.dark ? "dark" : "light";
    for (const fn of themed) fn(theme);
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const m = event.data;
    if (!m || m.castor !== 1) return;
    if (m.type === "context") {
      context = m.context;
      applyTheme(m.theme);
      waiting.splice(0).forEach((resolve) => resolve(context));
    } else if (m.type === "theme") {
      applyTheme(m.theme);
    } else if (m.type === "reply" && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) reject(Object.assign(new Error(m.error), { status: m.status || 0 }));
      else resolve(m.result);
    }
  });

  function request(method, params) {
    const id = next++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      send({ type: "request", id, method, params });
    });
  }

  const api = (method) => (path, data) =>
    request("api", { method, path, query: method === "GET" ? data || null : null, body: method === "GET" ? null : data ?? null });

  let lastHeight = 0;
  // The content's own height: <html>'s scrollHeight never drops below the frame's, so it can't shrink.
  const contentHeight = () => {
    const body = document.body;
    if (!body) return document.documentElement.scrollHeight;
    const style = getComputedStyle(body);
    return body.getBoundingClientRect().height + parseFloat(style.marginTop) + parseFloat(style.marginBottom);
  };
  function resize(height) {
    const h = Math.ceil(height ?? contentHeight());
    if (h !== lastHeight) {
      lastHeight = h;
      send({ type: "resize", height: h });
    }
  }

  window.Castor = {
    /** The context (who, where), once Castor has sent it. */
    ready: () => (context ? Promise.resolve(context) : new Promise((resolve) => waiting.push(resolve))),
    get context() {
      return context;
    },
    /** Castor's REST API, as the plugin (its permissions, its projects). Throws on 4xx/5xx. */
    api: { get: api("GET"), post: api("POST"), patch: api("PATCH"), put: api("PUT"), delete: api("DELETE") },
    /** A handler in the plugin's own code: ctx.ui.on(name, handler) in main.ts. */
    call: (name, args) => request("call", { name, args: args ?? null }),
    /** Open something in Castor: { asset: id } or { card: id }. */
    open: (target) => send({ type: "open", target }),
    /** A short message at the bottom of Castor's page. */
    notify: (text) => send({ type: "notify", text: String(text).slice(0, 300) }),
    resize,
    /** Called again whenever Castor's theme changes. */
    onTheme: (fn) => themed.push(fn),
  };

  // Height follows the content.
  const watch = new ResizeObserver(() => resize());
  const observe = () => document.body && watch.observe(document.body);
  if (document.body) observe();
  else document.addEventListener("DOMContentLoaded", observe);
  window.addEventListener("load", () => resize());
  send({ type: "hello" });
})();
