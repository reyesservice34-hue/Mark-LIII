export function bytes(n?: number | null, digits = 1): string {
  if (n == null || isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${units[i]}`;
}

export function rate(bps?: number | null): string {
  if (bps == null) return "—";
  return `${bytes(bps, 0)}/s`;
}

export function pct(n?: number | null, digits = 0): string {
  if (n == null || isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function duration(seconds?: number | null): string {
  if (seconds == null || isNaN(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export function ms(msv?: number | null): string {
  if (msv == null) return "—";
  return msv < 1000 ? `${Math.round(msv)} ms` : duration(msv / 1000);
}

export function relative(iso?: string | null, now = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return iso;
  const diff = Math.round((now - t) / 1000);
  if (diff < 5) return "gerade eben";
  if (diff < 60) return `vor ${diff} Sek.`;
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  if (diff < 86400 * 7) {
    const days = Math.floor(diff / 86400);
    return days === 1 ? "vor 1 Tag" : `vor ${days} Tagen`;
  }
  return new Date(t).toLocaleDateString("de-DE");
}

export function time(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function clamp(n: number, a: number, b: number) { return Math.min(b, Math.max(a, n)); }

export function tone(status?: string): "ok" | "warn" | "err" | "info" | "muted" {
  const s = (status || "").toLowerCase();
  if (["healthy", "ok", "online", "completed", "success", "approved", "active", "running", "idle", "connected", "succeeded"].includes(s)) return s === "running" || s === "active" ? "info" : "ok";
  if (["degraded", "warning", "waiting", "waiting_for_approval", "pending", "paused", "planning", "thinking", "executing", "queued"].includes(s)) return s === "planning" || s === "thinking" || s === "executing" || s === "queued" ? "info" : "warn";
  if (["offline", "error", "failed", "critical", "rejected", "expired", "cancelled", "canceled", "crashed"].includes(s)) return "err";
  return "muted";
}
