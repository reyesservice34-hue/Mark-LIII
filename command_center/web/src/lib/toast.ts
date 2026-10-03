import { createStore } from "./store";

export interface Toast { id: number; title: string; body?: string; tone?: "ok" | "warn" | "err" | "info"; link?: string; }
export const toasts = createStore<Toast[]>([]);
let seq = 1;

export function toast(t: Omit<Toast, "id">, ttl = 6000) {
  const id = seq++;
  toasts.set((list) => [...list.slice(-4), { ...t, id }]);
  window.setTimeout(() => toasts.set((list) => list.filter((x) => x.id !== id)), ttl);
}

export function dismissToast(id: number) { toasts.set((list) => list.filter((x) => x.id !== id)); }
