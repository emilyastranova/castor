// The Code map: a project's repositories as folder trees, each file's (or symbol's) relationships
// beside it, and the forms to add files, symbols and relationships by hand. Everything goes through
// the plugin's handlers (ctx.ui.on in main.ts) by Castor.call; the page has no network of its own.
(async () => {
  const app = document.getElementById("app");
  await Castor.ready();

  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (k === "value") node.value = v;
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : String(c));
    return node;
  };
  // Panels can't load Castor's icon font (no network), so the few icons are drawn here, on a 24px grid.
  const ICONS = {
    chevron_right: "M9 6l6 6-6 6", expand_more: "M6 9l6 6 6-6",
    folder: "M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2h7.4a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z",
    source: "M6 4.5h10.5a2 2 0 0 1 2 2V19.5H8a2.5 2.5 0 0 1-2.5-2.5V5zM5.5 17a2.5 2.5 0 0 1 2.5-2.5h10.5M10 8h5",
    code: "M9 7.5L4.5 12 9 16.5M15 7.5l4.5 4.5-4.5 4.5",
    function: "M15 4.5h-.5a3 3 0 0 0-3 3v9a3 3 0 0 1-3 3H8M8.5 10.5h6",
    open_in_new: "M13.5 4.5h6v6M19.5 4.5l-8 8M17.5 14v4.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1H10",
    bug_report: "M8 10a4 4 0 0 1 8 0v4.5a4 4 0 0 1-8 0zM12 10.5v8M4.5 13H8M16 13h3.5M5.5 8.5L8 10M18.5 8.5L16 10M5.5 19L8 17.2M18.5 19L16 17.2M9.5 6.5L8 4.5M14.5 6.5L16 4.5",
    close: "M6.5 6.5l11 11M17.5 6.5l-11 11", add: "M12 5.5v13M5.5 12h13",
    add_link: "M10.5 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1M13.5 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l1-1",
    refresh: "M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4.5H15",
    account_tree: "M4 4.5h6v4.5H4zM14 15h6v4.5h-6zM14 4.5h6v4.5h-6zM7 9v8.2h7M10 6.75h4",
    radio_button_unchecked: "M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 1 0 0-15z",
    clock_loader_40: "M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 1 0 0-15zM12 8v4.2l2.8 1.8",
    check_circle: "M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 1 0 0-15zM8.7 12.2l2.3 2.3 4.4-4.6",
    flag: "M6 20V4.5M6 5h10.5l-2 3.75 2 3.75H6",
  };
  const icon = (name) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICONS[name] ?? ICONS.code);
    svg.append(path);
    return svg;
  };
  const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

  // What the relationships read as, from each end, and which ones a file or symbol can start.
  const KINDS = {
    imports: ["imports", "imported by"], defines: ["defines", "defined in"], calls: ["calls", "called by"],
    flows_to: ["passes data to", "gets data from"], tests: ["tests", "tested by"], serves: ["serves", "served by"],
    in_repo: ["in repository", "contains"], deployed_to: ["deployed to", "runs"], affects: ["affects", "affected by"],
  };
  const ADDABLE = {
    source_file: [["imports", ["source_file"]], ["flows_to", ["source_file", "code_symbol"]], ["tests", ["source_file", "code_symbol"]]],
    code_symbol: [["calls", ["code_symbol"]], ["flows_to", ["code_symbol", "source_file"]], ["defines", ["code_symbol"]]],
  };
  const ROLES = ["entry point", "routes", "auth", "sql", "test", "config"];
  const REVIEW_ICON = { "not started": "radio_button_unchecked", "in progress": "clock_loader_40", reviewed: "check_circle", flagged: "flag" };

  let data = null;
  let loaded = false;
  const state = { repo: "", q: "", role: "", review: "", selected: null, open: new Set([""]), adding: null };
  let byId = new Map(), linksOf = new Map();

  async function load(keep = true) {
    try {
      data = await Castor.call("map");
    } catch (err) {
      app.replaceChildren(el("p", { class: "quiet pad" }, `Couldn’t read the code map: ${err.message}`));
      return;
    }
    byId = new Map([...data.files.map((f) => [f.id, { ...f, type: "source_file" }]), ...data.symbols.map((s) => [s.id, { ...s, type: "code_symbol" }]),
      ...data.repos.map((r) => [r.id, { ...r, type: "repository" }])]);
    linksOf = new Map();
    for (const l of data.links) {
      for (const end of [l.from.id, l.to.id]) (linksOf.get(end) ?? linksOf.set(end, []).get(end)).push(l);
    }
    if (!loaded && data.files.length <= 80) {
      // A small code base starts unfolded.
      for (const f of data.files) {
        const parts = (data.repos.length > 1 ? [f.repo, ...f.path.split("/")] : f.path.split("/")).slice(0, -1);
        parts.forEach((_, i) => state.open.add(parts.slice(0, i + 1).join("/")));
      }
    }
    loaded = true;
    if (!keep || (state.selected && !byId.has(state.selected))) state.selected = null;
    if (state.repo && !data.repos.some((r) => r.name === state.repo)) state.repo = "";
    render();
  }

  const fileLabel = (f) => f.path.split("/").at(-1);

  function visibleFiles() {
    const q = state.q.trim().toLowerCase();
    return data.files.filter((f) => (!state.repo || f.repo === state.repo) && (!state.role || f.roles.includes(state.role)) &&
      (!state.review || (f.review || "not started") === state.review) && (!q || f.path.toLowerCase().includes(q) || f.language.toLowerCase() === q));
  }

  // ---------- the tree ----------

  function tree(files) {
    // repo → folders → files; one repository shows without its own level.
    const repos = [...new Set(files.map((f) => f.repo))].sort();
    const root = { name: "", path: "", dirs: new Map(), files: [] };
    for (const f of files) {
      const parts = (repos.length > 1 ? [f.repo, ...f.path.split("/")] : f.path.split("/"));
      let node = root;
      for (const [i, part] of parts.slice(0, -1).entries()) {
        const path = parts.slice(0, i + 1).join("/");
        if (!node.dirs.has(part)) node.dirs.set(part, { name: part, path, dirs: new Map(), files: [], repo: i === 0 && repos.length > 1 });
        node = node.dirs.get(part);
      }
      node.files.push(f);
    }
    const count = (n) => n.files.length + [...n.dirs.values()].reduce((s, d) => s + count(d), 0);
    const filtering = !!(state.q || state.role || state.review);
    const rows = [];
    const walk = (node, depth) => {
      for (const d of [...node.dirs.values()].sort((a, b) => a.name.localeCompare(b.name))) {
        const open = filtering || state.open.has(d.path);
        rows.push(el("li", { role: "treeitem", "aria-expanded": String(open), "aria-level": depth + 1 },
          el("button", { type: "button", class: "row dir", style: `--depth:${depth}`, onclick: () => {
            if (state.open.has(d.path)) state.open.delete(d.path);
            else state.open.add(d.path);
            render();
          } }, icon(open ? "expand_more" : "chevron_right"), icon(d.repo ? "source" : "folder"),
          el("span", { class: "name" }, d.name), el("span", { class: "count" }, count(d)))));
        if (open) walk(d, depth + 1);
      }
      for (const f of node.files.sort((a, b) => fileLabel(a).localeCompare(fileLabel(b)))) {
        const review = f.review || "not started";
        rows.push(el("li", { role: "treeitem", "aria-level": depth + 1, "aria-selected": String(state.selected === f.id) },
          el("button", { type: "button", class: `row file${state.selected === f.id ? " on" : ""}`, style: `--depth:${depth}`,
            onclick: () => select(f.id) },
          el("span", { class: `review ${review.replace(" ", "-")}`, title: `Review: ${review}` }, icon(REVIEW_ICON[review] ?? REVIEW_ICON["not started"])),
          el("span", { class: "name" }, fileLabel(f)),
          f.roles.filter((r) => r !== "config").slice(0, 3).map((r) => el("span", { class: `role role-${r.replace(" ", "-")}` }, r)),
          el("span", { class: "lang" }, f.language))));
      }
    };
    walk(root, 0);
    return rows;
  }

  function select(id) {
    state.selected = id;
    state.adding = null;
    render();
    document.querySelector(".detail")?.scrollTo({ top: 0 });
  }

  // ---------- the detail pane ----------

  function linkRow(l, me) {
    const out = l.from.id === me;
    const other = out ? l.to : l.from;
    const known = byId.get(other.id);
    const words = KINDS[l.kind]?.[out ? 0 : 1] ?? l.kind;
    const detail = [l.fields?.line && `line ${l.fields.line}`, l.fields?.data].filter(Boolean).join(" · ");
    const here = byId.get(me);
    const name = known?.type === "source_file" ? known.path
      : known?.type === "code_symbol" ? (known.file === here?.path || known.file === here?.file ? known.name : `${known.name} · ${known.file}`)
      : other.label;
    return el("li", { class: "link" },
      el("button", { type: "button", class: "target", title: `${words} ${other.label}`,
        onclick: () => (known && known.type !== "repository" ? select(other.id) : Castor.open({ asset: other.id })) },
        icon(known?.type === "code_symbol" ? "function" : known?.type === "source_file" ? "code" : other.type === "vulnerability" ? "bug_report" : "open_in_new"),
        el("span", { class: "name" }, name)),
      detail ? el("span", { class: "meta" }, detail) : null,
      l.note ? el("span", { class: "note" }, l.note) : null,
      data.can_write && l.kind !== "in_repo" ? el("button", { type: "button", class: "icon-btn", title: "Remove this relationship",
        "aria-label": `Remove: ${words} ${other.label}`, onclick: () => unlink(l) }, icon("close")) : null);
  }

  function groups(id) {
    const links = linksOf.get(id) ?? [];
    const by = new Map();
    for (const l of links) {
      if (l.kind === "in_repo") continue;
      const out = l.from.id === id;
      const key = `${l.kind}:${out ? "out" : "in"}`;
      (by.get(key) ?? by.set(key, { words: KINDS[l.kind]?.[out ? 0 : 1] ?? l.kind, kind: l.kind, out, links: [] }).get(key)).links.push(l);
    }
    const order = ["affects:in", "imports:out", "imports:in", "defines:out", "defines:in", "calls:out", "calls:in", "flows_to:in", "flows_to:out",
      "tests:in", "tests:out", "serves:out"];
    return [...by.entries()].sort(([a], [b]) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99).map(([, g]) => g);
  }

  function detail() {
    const item = state.selected && byId.get(state.selected);
    if (!item) {
      const files = visibleFiles();
      const flagged = data.files.filter((f) => f.review === "flagged").length;
      const reviewed = data.files.filter((f) => f.review === "reviewed").length;
      const roles = ROLES.map((r) => [r, data.files.filter((f) => f.roles.includes(r)).length]).filter(([, n]) => n);
      return el("div", { class: "summary" },
        el("h2", {}, data.repos.length === 1 ? data.repos[0].name : plural(data.repos.length, "repository", "repositories")),
        el("p", { class: "stats" }, el("span", {}, el("b", {}, data.files.length), " files"), el("span", {}, el("b", {}, data.symbols.length), " symbols"),
          el("span", {}, el("b", {}, data.links.filter((l) => l.kind === "imports").length), " imports"),
          el("span", {}, el("b", {}, reviewed), " reviewed"), flagged ? el("span", { class: "flag" }, el("b", {}, flagged), " flagged") : null),
        roles.length ? el("div", {}, el("h3", {}, "Where to look first"),
          el("ul", { class: "chips" }, roles.map(([r, n]) => el("li", {}, el("button", { type: "button", class: `chip${state.role === r ? " on" : ""}`,
            "aria-pressed": String(state.role === r), onclick: () => { state.role = state.role === r ? "" : r; render(); } }, `${r} · ${n}`))))) : null,
        el("p", { class: "quiet" }, files.length ? "Choose a file to see what it imports, what uses it, its symbols and its findings." : "No files match."));
    }
    const isFile = item.type === "source_file";
    const review = item.review || "not started";
    const head = el("header", { class: "head" },
      el("div", { class: "title" }, icon(isFile ? "code" : "function"),
        el("h2", {}, isFile ? item.path : item.name),
        el("button", { type: "button", class: "text-btn", onclick: () => Castor.open({ asset: item.id }) }, icon("open_in_new"), "Open asset")),
      el("p", { class: "sub" }, isFile
        ? [item.repo, item.language, item.lines ? plural(item.lines, "line") : null].filter(Boolean).join(" · ")
        : [item.kind, item.file + (item.line ? `:${item.line}` : ""), item.repo].filter(Boolean).join(" · ")),
      isFile && item.roles.length ? el("ul", { class: "chips" }, item.roles.map((r) => el("li", { class: `role role-${r.replace(" ", "-")}` }, r))) : null,
      el("div", { class: "seg", role: "group", "aria-label": "Review" }, data.review.map((r) => el("button", { type: "button", "aria-pressed": String(review === r),
        disabled: !data.can_write, class: r.replace(" ", "-"), onclick: () => setReview(item, r) }, icon(REVIEW_ICON[r]), r))));

    const sections = groups(item.id).map((g) => el("section", {},
      el("h3", {}, `${g.words[0].toUpperCase()}${g.words.slice(1)}`, el("span", { class: "count" }, g.links.length)),
      el("ul", { class: "links" }, g.links.map((l) => linkRow(l, item.id)))));
    if (!sections.length) sections.push(el("p", { class: "quiet" }, "No relationships yet."));
    const packages = isFile && item.dependencies.length
      ? el("section", {}, el("h3", {}, "Packages it uses", el("span", { class: "count" }, item.dependencies.length)),
        el("ul", { class: "chips" }, item.dependencies.map((d) => el("li", { class: "chip mono" }, d)))) : null;

    const actions = data.can_write ? el("div", { class: "actions" },
      el("button", { type: "button", class: "tonal", onclick: () => { state.adding = "link"; render(); } }, icon("add_link"), "Add relationship"),
      isFile ? el("button", { type: "button", class: "tonal", onclick: () => { state.adding = "symbol"; render(); } }, icon("function"), "Add symbol") : null) : null;
    const form = state.adding === "link" ? linkForm(item) : state.adding === "symbol" ? symbolForm(item) : null;
    return el("div", {}, head, actions, form, sections, packages);
  }

  // ---------- forms ----------

  function field(label, input) {
    return el("label", { class: "field" }, el("span", {}, label), input);
  }

  function linkForm(item) {
    const options = ADDABLE[item.type];
    const kind = el("select", {}, options.map(([k]) => el("option", { value: k }, KINDS[k][0])));
    const list = el("datalist", { id: "targets" });
    const target = el("input", { type: "text", list: "targets", placeholder: "A path or a symbol", required: true, autocomplete: "off" });
    const line = el("input", { type: "number", min: "1", placeholder: "optional", class: "short" });
    const what = el("input", { type: "text", placeholder: "user input, a token…" });
    const whatField = field("What passes", what);
    const candidates = () => {
      const allowed = options.find(([k]) => k === kind.value)[1];
      return [...byId.values()].filter((x) => x.id !== item.id && allowed.includes(x.type) && (!item.repo || x.repo === item.repo || !x.repo));
    };
    const label = (x) => (x.type === "source_file" ? x.path : `${x.name} (${x.file})`);
    const fill = () => {
      list.replaceChildren(...candidates().slice(0, 500).map((x) => el("option", { value: label(x) })));
      whatField.hidden = kind.value !== "flows_to";
    };
    kind.addEventListener("change", fill);
    fill();
    return el("form", { class: "form", onsubmit: async (e) => {
      e.preventDefault();
      const to = candidates().find((x) => label(x) === target.value.trim());
      if (!to) return Castor.notify("Pick one of the files or symbols listed");
      await act(() => Castor.call("link", { from: item.id, to: to.id, kind: kind.value, fields: { line: line.value, data: kind.value === "flows_to" ? what.value : "" } }),
        `${KINDS[kind.value][0]} ${label(to)}`);
    } },
    el("h3", {}, "Add a relationship"),
    el("div", { class: "fields" }, field("This one", kind), field("Which", target), field("Line", line), whatField), list,
    el("div", { class: "buttons" }, el("button", { type: "button", class: "text-btn", onclick: () => { state.adding = null; render(); } }, "Cancel"),
      el("button", { type: "submit", class: "filled" }, "Add")));
  }

  function symbolForm(file) {
    const name = el("input", { type: "text", required: true, maxlength: "200", placeholder: "create_order, UserController, POST /orders" });
    const kind = el("select", {}, data.symbol_kinds.map((k) => el("option", { value: k }, k)));
    const line = el("input", { type: "number", min: "1", class: "short" });
    const signature = el("input", { type: "text", maxlength: "500", placeholder: "optional", class: "mono" });
    return el("form", { class: "form", onsubmit: async (e) => {
      e.preventDefault();
      await act(() => Castor.call("add_symbol", { file: file.id, name: name.value, kind: kind.value, line: line.value, signature: signature.value }),
        `Added ${name.value}`);
    } },
    el("h3", {}, `A symbol in ${fileLabel(file)}`),
    el("div", { class: "fields" }, field("Name", name), field("Kind", kind), field("Line", line), field("Signature", signature)),
    el("div", { class: "buttons" }, el("button", { type: "button", class: "text-btn", onclick: () => { state.adding = null; render(); } }, "Cancel"),
      el("button", { type: "submit", class: "filled" }, "Add")));
  }

  function filesForm() {
    const repos = data.repos.map((r) => r.name).sort();
    const repo = el("input", { type: "text", list: "repos", required: true, value: state.repo || repos[0] || "", placeholder: "shop-api" });
    const paths = el("textarea", { rows: "8", required: true, class: "mono", placeholder: "src/app.py\nsrc/db/session.py\nweb/src/routes/+page.svelte" });
    return el("form", { class: "form wide", onsubmit: async (e) => {
      e.preventDefault();
      const list = paths.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      await act(async () => {
        const r = await Castor.call("add_files", { repo: repo.value.trim(), paths: list });
        state.repo = repo.value.trim();
        return r;
      }, `${plural(list.length, "path")} added`);
    } },
    el("h3", {}, "Add files"),
    el("p", { class: "quiet" }, "One path per line, from the repository’s root. Files already in the map stay as they are."),
    el("datalist", { id: "repos" }, repos.map((r) => el("option", { value: r }))),
    el("div", { class: "fields" }, field("Repository", repo), field("Paths", paths)),
    el("div", { class: "buttons" }, el("button", { type: "button", class: "text-btn", onclick: () => { state.adding = null; render(); } }, "Cancel"),
      el("button", { type: "submit", class: "filled" }, "Add files")));
  }

  async function act(fn, done) {
    try {
      await fn();
      state.adding = null;
      Castor.notify(done);
      await load();
    } catch (err) {
      Castor.notify(err.message);
    }
  }

  async function setReview(item, review) {
    if ((item.review || "not started") === review) return;
    await act(() => Castor.call("review", { id: item.id, review }), `Marked ${review}`);
  }

  async function unlink(l) {
    await act(() => Castor.call("unlink", { id: l.id }), "Removed");
  }

  // ---------- the page ----------

  function render() {
    if (!data.files.length && state.adding !== "files") {
      app.replaceChildren(el("div", { class: "empty" },
        icon("account_tree"),
        el("h2", {}, "No code mapped yet"),
        el("p", {}, "Import a repository archive with ", el("b", {}, "Import source code"), " (on the Assets page or a card) to list its files and what imports what, or add files by hand."),
        data.can_write ? el("button", { type: "button", class: "filled", onclick: () => { state.adding = "files"; render(); } }, icon("add"), "Add files") : null));
      return;
    }
    const files = visibleFiles();
    const focus = document.activeElement?.id;
    const search = el("input", { id: "q", type: "search", placeholder: "Filter paths", "aria-label": "Filter paths", value: state.q,
      oninput: (e) => { state.q = e.target.value; render(); } });
    const toolbar = el("div", { class: "toolbar" },
      data.repos.length > 1 ? el("select", { "aria-label": "Repository", onchange: (e) => { state.repo = e.target.value; render(); } },
        el("option", { value: "" }, "All repositories"), data.repos.map((r) => el("option", { value: r.name, selected: state.repo === r.name }, r.name))) : null,
      search,
      el("select", { "aria-label": "Role", onchange: (e) => { state.role = e.target.value; render(); } },
        el("option", { value: "" }, "Any role"), ROLES.map((r) => el("option", { value: r, selected: state.role === r }, r))),
      el("select", { "aria-label": "Review", onchange: (e) => { state.review = e.target.value; render(); } },
        el("option", { value: "" }, "Any review"), data.review.map((r) => el("option", { value: r, selected: state.review === r }, r))),
      el("span", { class: "count" }, files.length === data.files.length ? plural(files.length, "file") : `${files.length} of ${data.files.length}`),
      el("span", { class: "grow" }),
      el("button", { type: "button", class: "icon-btn", title: "Reload", "aria-label": "Reload", onclick: () => load() }, icon("refresh")),
      data.can_write ? el("button", { type: "button", class: "tonal", onclick: () => { state.adding = "files"; render(); } }, icon("add"), "Add files") : null);
    const body = state.adding === "files"
      ? el("div", { class: "detail solo" }, filesForm())
      : el("div", { class: "split" },
        el("nav", { class: "tree", "aria-label": "Files" }, files.length ? el("ul", { role: "tree" }, tree(files)) : el("p", { class: "quiet pad" }, "No files match.")),
        el("div", { class: "detail" }, detail()));
    app.replaceChildren(toolbar, body);
    if (focus === "q") {
      search.focus();
      search.setSelectionRange(search.value.length, search.value.length);
    }
  }

  await load();
})();
