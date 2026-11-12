/** The pure parts: which files are ready, which action takes a file. Tested without a folder. */

export interface ActionInfo {
  plugin: { id: string; name: string };
  id: string;
  label: string;
  on: string[];
  file: { accept: string[]; max_mb: number } | null;
}

/** A file is ready once its size stayed the same between two looks (it's no longer being copied). */
export class Settle {
  private sizes = new Map<string, number>();
  ready(path: string, size: number): boolean {
    const before = this.sizes.get(path);
    this.sizes.set(path, size);
    return before === size;
  }
  forget(path: string) {
    this.sizes.delete(path);
  }
}

/** The action to hand a file to: one that takes its extension, on a card if it can be. */
export function actionFor(name: string, actions: ActionInfo[], self: string): ActionInfo | null {
  const lower = name.toLowerCase();
  const takes = actions.filter((a) => a.plugin.id !== self && a.file &&
    a.file.accept.some((x) => x.startsWith(".") && lower.endsWith(x.toLowerCase())) &&
    (a.on.includes("card") || a.on.includes("project")));
  return takes.find((a) => a.on.includes("card")) ?? takes[0] ?? null;
}

export function base64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** A name for a file moved aside, so a second file of the same name never overwrites it. */
export const stamped = (name: string, at = new Date()) => `${at.toISOString().replace(/[:.]/g, "-")}-${name}`;

/** A project's folder: its code name, with nothing that could leave the folder or hide it. */
export const folderName = (codename: string) => codename.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^\.+/, "_") || "_";
