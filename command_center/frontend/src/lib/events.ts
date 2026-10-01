/**
 * Live event hub — one SSE connection for the whole dashboard, with
 * Last-Event-ID resume and exponential-backoff reconnect. Components
 * subscribe by event type (trailing `*` wildcard supported).
 */
import { useEffect } from "react";
import { api } from "./api";
import { createStore, useStore } from "./store";

export type ConnState = "connecting" | "online" | "reconnecting" | "offline";
export interface LiveEvent { id: number; type: string; ts: number; data: any; }

export const connection = createStore<{ state: ConnState; since: number; attempts: number; lastEventAt: number }>({
  state: "offline", since: Date.now(), attempts: 0, lastEventAt: 0,
});

type Handler = (ev: LiveEvent) => void;
const handlers = new Map<string, Set<Handler>>();
let source: EventSource | null = null;
let lastId = 0;
let retryTimer: number | null = null;
let attempts = 0;
let wanted = false;

function dispatch(ev: LiveEvent) {
  connection.set((s) => ({ ...s, lastEventAt: Date.now() }));
  for (const [pattern, set] of handlers) {
    if (pattern === "*" || pattern === ev.type || (pattern.endsWith("*") && ev.type.startsWith(pattern.slice(0, -1)))) {
      set.forEach((h) => { try { h(ev); } catch (e) { console.error("event handler failed", e); } });
    }
  }
}

function open() {
  if (!wanted || source) return;
  connection.set((s) => ({ ...s, state: attempts ? "reconnecting" : "connecting", attempts }));
  const url = `${api.base}/api/events/stream${lastId ? `?last_id=${lastId}` : ""}`;
  const es = new EventSource(url, { withCredentials: true });
  source = es;
  es.onopen = () => {
    attempts = 0;
    connection.set({ state: "online", since: Date.now(), attempts: 0, lastEventAt: Date.now() });
  };
  es.onmessage = (m) => handleMessage(m);
  // Named events: EventSource only calls onmessage for "message"; we listen generically.
  const generic = (m: MessageEvent) => handleMessage(m);
  const known = ["chat.delta", "chat.tool_call", "chat.tool_result", "run.started", "run.status", "run.activity",
    "run.finished", "agent.status", "task.created", "task.updated", "task.log", "server.metrics",
    "notification.created", "notification.read", "approval.requested", "approval.decided", "log.entry",
    "audit.event", "message.created", "message.updated", "conversation.created", "conversation.updated",
    "conversation.deleted", "integration.status", "workflow.synced", "workflow.triggered", "job.updated",
    "file.changed", "master.status", "system.started"];
  known.forEach((k) => es.addEventListener(k, generic as EventListener));
  es.onerror = () => {
    es.close();
    source = null;
    if (!wanted) return;
    attempts += 1;
    const delay = Math.min(30000, 1000 * 2 ** Math.min(attempts, 5));
    connection.set((s) => ({ ...s, state: attempts > 3 ? "offline" : "reconnecting", attempts }));
    retryTimer = window.setTimeout(open, delay);
  };
}

function handleMessage(m: MessageEvent) {
  try {
    const parsed = JSON.parse(m.data);
    if (parsed.id) lastId = parsed.id;
    dispatch({ id: parsed.id, type: parsed.type || m.type, ts: parsed.ts, data: parsed.data });
  } catch { /* ignore */ }
}

export const events = {
  connect() { wanted = true; open(); },
  disconnect() {
    wanted = false;
    if (retryTimer) window.clearTimeout(retryTimer);
    source?.close();
    source = null;
    connection.set((s) => ({ ...s, state: "offline" }));
  },
  reconnectNow() { if (retryTimer) window.clearTimeout(retryTimer); source?.close(); source = null; attempts = 0; open(); },
  on(type: string, handler: Handler) {
    if (!handlers.has(type)) handlers.set(type, new Set());
    handlers.get(type)!.add(handler);
    return () => { handlers.get(type)?.delete(handler); };
  },
};

/** Subscribe to one or more event types for the lifetime of a component. */
export function useEvent(types: string | string[], handler: Handler, deps: any[] = []) {
  useEffect(() => {
    const list = Array.isArray(types) ? types : [types];
    const offs = list.map((t) => events.on(t, handler));
    return () => offs.forEach((off) => off());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function useConnection() { return useStore(connection); }

// Reconnect promptly when the tab regains focus or the network returns.
if (typeof window !== "undefined") {
  window.addEventListener("online", () => { if (wanted) events.reconnectNow(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && wanted && !source) events.reconnectNow();
  });
}
