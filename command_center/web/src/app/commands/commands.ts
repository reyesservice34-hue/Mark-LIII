/** Command palette registry — modules and pages can add commands at runtime. */
import { createStore } from "@/lib/store";

export interface Command { id: string; title: string; hint?: string; shortcut?: string; group?: string; path?: string; run?: () => void; keywords?: string; }

export const commandStore = createStore<Command[]>([]);

export function registerCommands(cmds: Command[]) {
  commandStore.set((list) => [...list.filter((c) => !cmds.some((n) => n.id === c.id)), ...cmds]);
  return () => commandStore.set((list) => list.filter((c) => !cmds.some((n) => n.id === c.id)));
}

export const paletteOpen = createStore(false);
