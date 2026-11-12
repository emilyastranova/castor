// The BloodHound bridge's panels: the project page (who has a path to Domain Admins) and the
// asset panel (this user's or computer's path). Everything comes from the plugin's own handlers
// (ctx.ui.on in main.ts) through Castor.call; the page has no network of its own.
(async () => {
  const app = document.getElementById("app");
  const ctx = await Castor.ready();
  const compact = ctx.panel.slot === "asset.panel";

  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : String(c));
    return node;
  };
  const show = (...nodes) => app.replaceChildren(...nodes);
  const failed = (err) => show(el("p", { class: "quiet" }, `Couldn’t read the graph: ${err.message}`));

  // Open the asset Castor has for a node (groups aren't assets).
  async function open(sid) {
    try {
      const id = await Castor.call("asset", { sid });
      if (id) Castor.open({ asset: id });
      else Castor.notify("That one isn’t in the project’s assets (groups stay in the graph)");
    } catch (err) {
      Castor.notify(err.message);
    }
  }

  const shortName = (name) => name.replace(/@.*$/, "").toLowerCase();
  const node = (step, last) =>
    el("button", { type: "button", class: `node${last ? " target" : ""}`, title: `${step.name} (${step.kind})`, onclick: () => open(step.sid) },
      el("span", { class: "kind" }, step.kind), step.kind === "group" || step.kind === "domain" ? step.name : shortName(step.name));
  const path = (steps) =>
    el("div", { class: "path" }, steps.map((s, i) => [node(s, i === steps.length - 1), s.rel ? el("span", { class: "rel" }, s.rel) : null]));

  if (compact) {
    try {
      const r = await Castor.call("path", {});
      if (!r.imported) return show(el("div", { class: "compact" }, el("p", { class: "lead" }, "No BloodHound data in this project yet: import a SharpHound collection from the Assets page.")));
      if (!r.found) return show(el("div", { class: "compact" }, el("p", { class: "lead" }, "Not in the imported BloodHound graph.")));
      if (!r.path) return show(el("div", { class: "compact" }, el("p", { class: "lead" }, el("b", {}, "No path"), " to Domain Admins from here in the imported graph.")));
      const hops = r.path.length - 1;
      return show(el("div", { class: "compact" },
        el("p", { class: "lead" }, hops === 0 ? el("b", {}, "This is a Domain Admins group") : [el("b", {}, `${hops} step${hops === 1 ? "" : "s"}`), " to Domain Admins:"]),
        path(r.path)));
    } catch (err) {
      return failed(err);
    }
  }

  let data;
  try {
    data = await Castor.call("summary", {});
  } catch (err) {
    return failed(err);
  }
  if (!data.imported) {
    return show(el("section", {},
      el("h2", {}, "Nothing imported yet"),
      el("p", { class: "quiet" }, "Run SharpHound (BloodHound CE), then use “Import SharpHound data” on the Inventory tab with its .zip. Users, computers and domains become assets; the paths show here.")));
  }

  const c = data.counts;
  const when = new Date(data.at).toLocaleString();
  const stats = el("p", { class: "stats" },
    ...[["users", c.users], ["computers", c.computers], ["groups", c.groups], ["domains", c.domains], ["edges", c.edges]]
      .map(([label, n]) => el("span", {}, el("b", {}, n.toLocaleString()), ` ${label}`)),
    el("span", {}, `last import ${when}`));

  const admins = el("section", {}, el("h2", {}, "Domain Admins"),
    el("div", { class: "admins" }, data.admins.length
      ? data.admins.map((a) => el("div", {}, el("p", {}, `${a.group}: ${a.members.length || "no"} direct member${a.members.length === 1 ? "" : "s"}`),
        el("div", { class: "chips" }, a.members.map((m) => el("span", { class: "chip" }, m)))))
      : el("p", {}, "No Domain Admins group in the data (collect groups too).")));

  // Who can get there: filter, search, and each row opens to its path.
  let filter = "users";
  let query = "";
  const opened = new Map();
  const tbody = el("tbody");
  const more = el("p", { class: "more" });
  const FILTERS = [["users", "Enabled users"], ["all", "Everyone"], ["roast", "Roastable"], ["computers", "Computers"]];
  const seg = el("div", { class: "seg", role: "group", "aria-label": "Show" });
  const segButtons = FILTERS.map(([id, label]) => el("button", { type: "button", "aria-pressed": String(id === filter), onclick: () => { filter = id; render(); } }, label));
  seg.append(...segButtons);
  const search = el("input", { type: "search", placeholder: "Find a user or computer", "aria-label": "Find", oninput: (e) => { query = e.target.value.toLowerCase(); render(); } });

  function keep(r) {
    if (query && !r.name.toLowerCase().includes(query)) return false;
    if (filter === "users") return r.kind === "user" && r.enabled;
    if (filter === "roast") return r.kind === "user" && (r.kerberoastable || r.asreproastable);
    if (filter === "computers") return r.kind === "computer";
    return true;
  }

  async function toggle(r, row) {
    const next = row.nextElementSibling;
    if (next?.classList.contains("detail")) {
      next.remove();
      row.classList.remove("open");
      row.setAttribute("aria-expanded", "false");
      return;
    }
    row.classList.add("open");
    row.setAttribute("aria-expanded", "true");
    const cell = el("td", { colspan: 2 }, el("span", { class: "quiet" }, "Finding the path…"));
    row.after(el("tr", { class: "detail" }, cell));
    try {
      const p = opened.get(r.sid) ?? (await Castor.call("path", { sid: r.sid }));
      opened.set(r.sid, p);
      cell.replaceChildren(p.path ? path(p.path) : el("span", { class: "quiet" }, "No path"));
    } catch (err) {
      cell.replaceChildren(el("span", { class: "quiet" }, err.message));
    }
  }

  function render() {
    segButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(FILTERS[i][0] === filter)));
    const rows = data.reach.filter(keep);
    tbody.replaceChildren(...rows.slice(0, 200).map((r) => {
      const row = el("tr", { class: "row", tabindex: 0, "aria-expanded": "false" },
        el("td", {}, el("span", { class: "kind" }, r.kind), " ", el("span", { class: r.enabled || r.kind !== "user" ? "" : "off" }, r.name),
          r.kerberoastable ? el("span", { class: "tag" }, "kerberoastable") : null, r.asreproastable ? el("span", { class: "tag" }, "AS-REP roastable") : null,
          r.kind === "user" && !r.enabled ? el("span", { class: "tag" }, "disabled") : null,
          r.domain_admin ? el("span", { class: "tag" }, "already a Domain Admin") : null),
        el("td", { class: "hops" }, r.hops));
      row.addEventListener("click", () => toggle(r, row));
      row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(r, row); } });
      return row;
    }));
    const hidden = Math.max(0, rows.length - 200) + (filter === "all" && !query ? data.more : 0);
    more.textContent = rows.length ? (hidden ? `And ${hidden.toLocaleString()} more: search to narrow it down.` : "") : "No one matches.";
  }

  const table = el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Who"), el("th", { class: "hops" }, "Steps"))), tbody);
  show(stats, admins, el("section", {}, el("h2", {}, `Paths to Domain Admins (${data.reach.length.toLocaleString()}${data.more ? "+" : ""})`),
    el("div", { class: "tools" }, seg, search), table, more));
  render();
})();
