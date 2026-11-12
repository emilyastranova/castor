/**
 * Watch folder: files dropped in <folder>/<project code name>/ on the Castor server become cards
 * in that project, with the file attached, and are handed to a plugin action that imports that
 * kind of file. Unrestricted, because it reads and moves files on the server; it reaches Castor
 * only through its own account, like any plugin.
 */
import { definePlugin } from "@castor/plugin";
import { type ActionInfo, actionFor, base64, folderName, Settle, stamped } from "./watch.ts";

const MAX_MB = 20; // what a plugin can attach
const DONE = ".imported";
const SKIPPED = ".skipped";

definePlugin({
  activate(ctx) {
    const root = String(ctx.settings.folder || "/data/inbox").replace(/\/+$/, "");
    const every = Math.max(5, Number(ctx.settings.every) || 30) * 1000;
    const runImports = ctx.settings.run_imports !== false;
    const settle = new Settle();
    let busy = false;

    async function moveAside(dir: string, name: string, to: string) {
      await Deno.mkdir(`${dir}/${to}`, { recursive: true });
      await Deno.rename(`${dir}/${name}`, `${dir}/${to}/${stamped(name)}`);
    }

    async function take(project: { id: string; codename: string }, dir: string, name: string, size: number) {
      if (size > MAX_MB * 1024 * 1024) {
        ctx.log.warn(`${project.codename}/${name} is over ${MAX_MB} MB; moved to ${SKIPPED}/`);
        await moveAside(dir, name, SKIPPED);
        return;
      }
      const bytes = await Deno.readFile(`${dir}/${name}`);
      const card = await ctx.api.post<{ id: string; title: string }>(`/api/projects/${project.id}/tasks`, {
        title: `Dropped: ${name}`,
        description: `Dropped in the watched folder on the Castor server (${dir}).`,
      });
      await ctx.api.upload(`/api/tasks/${card.id}/attachments`, { name, bytes });
      let note = "";
      if (runImports) {
        const actions = await ctx.api.get<ActionInfo[]>(`/api/projects/${project.id}/plugin-actions`);
        const action = actionFor(name, actions, ctx.plugin.id);
        if (action && size <= action.file!.max_mb * 1024 * 1024) {
          await ctx.api.post("/api/plugin-actions/run", {
            plugin_id: action.plugin.id, action: action.id, project_id: project.id,
            task_id: action.on.includes("card") ? card.id : null, filename: name, content_base64: base64(bytes),
          });
          note = `, handed to ${action.plugin.name} (${action.label})`;
        }
      }
      await moveAside(dir, name, DONE);
      ctx.log.info(`${project.codename}/${name}: card “${card.title}”${note}`);
    }

    async function look() {
      if (busy) return;
      busy = true;
      try {
        await Deno.mkdir(root, { recursive: true });
        const projects = await ctx.api.get<{ id: string; codename: string }[]>("/api/projects");
        for (const project of projects) {
          const dir = `${root}/${folderName(project.codename)}`;
          await Deno.mkdir(dir, { recursive: true }); // so people see where to drop files
          for await (const entry of Deno.readDir(dir)) {
            if (!entry.isFile || entry.name.startsWith(".")) continue;
            const path = `${dir}/${entry.name}`;
            const { size } = await Deno.stat(path);
            if (!settle.ready(path, size)) continue;
            settle.forget(path);
            try {
              await take(project, dir, entry.name, size);
            } catch (err) {
              ctx.log.error(`${project.codename}/${entry.name}: ${(err as Error).message}; moved to ${SKIPPED}/`);
              await moveAside(dir, entry.name, SKIPPED).catch(() => {});
            }
          }
        }
      } catch (err) {
        ctx.log.error(`Looking in ${root}: ${(err as Error).message}`);
      } finally {
        busy = false;
      }
    }

    ctx.log.info(`Watching ${root} every ${every / 1000} s`);
    setInterval(look, every);
    void look();
  },
});
