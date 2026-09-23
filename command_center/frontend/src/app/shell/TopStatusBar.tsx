import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { useEvent, useConnection } from "@/lib/events";
import { pct } from "@/lib/format";
import { Bell, ShieldCheck, Wifi, WifiOff, RefreshCw, Search } from "@/lib/icons";
import { StatusIndicator } from "@/components/ui";
import { paletteOpen } from "@/app/commands/commands";
import { events } from "@/lib/events";

export interface StatusPayload {
  jarvis: { online: boolean; label: string; version: string; uptime_seconds: number };
  master: { mode: string; online: boolean; label: string; provider: { id: string; model: string; label: string } | null; active_runs: any[]; error: string };
  server: { connected: boolean; hostname: string; cpu: number; ram: number; disk: number; load: number; net_rx: number; net_tx: number; docker: { status: string } };
  tasks: Record<string, number>;
  agents: { total: number; enabled: number; active: number };
  approvals_pending: number; notifications_unread: number;
  user: { name: string; role: string };
}

function MiniMeter({ v }: { v: number }) {
  const cls = v >= 90 ? "err" : v >= 75 ? "warn" : "";
  return <span className={`mini-meter ${cls}`}><span style={{ width: `${Math.min(100, v)}%` }} /></span>;
}

export function TopStatusBar() {
  const { data, setData, reload } = useApi<StatusPayload>("/api/status", {
    refreshOn: ["task.*", "agent.status", "approval.*", "notification.*", "master.status", "run.started", "run.finished"], debounce: 400, interval: 30000,
  });
  const conn = useConnection();
  const nav = useNavigate();
  const [label, setLabel] = useState("");

  useEvent("server.metrics", (ev) => setData((d) => d ? { ...d, server: { ...d.server, cpu: ev.data.cpu, ram: ev.data.ram, disk: ev.data.disk, load: ev.data.load } } : d));
  useEvent("run.status", (ev) => { if (ev.data.label) setLabel(ev.data.label); });
  useEvent("run.finished", () => setTimeout(() => setLabel(""), 2500));
  useEffect(() => { if (conn.state === "online") reload(); }, [conn.state, reload]);

  const master = data?.master;
  const masterTone = !master ? "muted" : master.online ? (master.active_runs?.length ? "info" : "ok") : "err";
  const running = data?.tasks?.active ?? 0;
  return (
    <header className="topbar" role="banner">
      <div className="status-group">
        <span className="chip hero"><StatusIndicator status={conn.state === "online" ? "ok" : conn.state === "offline" ? "err" : "warn"} live={conn.state !== "online"} />{conn.state === "online" ? "MIA ONLINE" : conn.state === "offline" ? "MIA OFFLINE" : "VERBINDE NEU"}</span>
        <span className="sep desktop-only" />
        <span className="chip desktop-only" title={master?.error || master?.provider?.label || ""}><StatusIndicator status={masterTone} live={!!master?.active_runs?.length} />{label || (master ? (master.online ? (master.active_runs?.length ? "MASTER AGENT ARBEITET" : "MASTER AGENT BEREIT") : "MASTER AGENT OFFLINE") : "…")}</span>
        <span className="sep desktop-only" />
        <span className="chip desktop-only"><StatusIndicator status={data?.server?.connected ? "ok" : "err"} />SERVER {data?.server?.connected ? "VERBUNDEN" : "—"}</span>
        <span className="sep desktop-only" />
        <span className="chip"><span className="val">{running}</span> AUFGABE{running === 1 ? "" : "N"} LAUFEN</span>
        <span className="chip desktop-only"><span className="val">{data?.agents?.active ?? 0}</span> AGENT{data?.agents?.active === 1 ? "" : "EN"} AKTIV</span>
      </div>
      <div className="status-group right">
        <span className="chip desktop-only" title="CPU">CPU <span className="val">{pct(data?.server?.cpu)}</span><MiniMeter v={data?.server?.cpu ?? 0} /></span>
        <span className="chip desktop-only" title="Arbeitsspeicher">RAM <span className="val">{pct(data?.server?.ram)}</span><MiniMeter v={data?.server?.ram ?? 0} /></span>
        <span className="chip desktop-only" title="Festplatte">PLATTE <span className="val">{pct(data?.server?.disk)}</span><MiniMeter v={data?.server?.disk ?? 0} /></span>
        <span className="sep desktop-only" />
        <span className="chip desktop-only" title={master?.provider?.label || "kein Anbieter eingerichtet"}>{master?.provider ? master.provider.model : "KEIN MODELL"}</span>
        <span className="sep desktop-only" />
        <button className="btn icon ghost sm desktop-only" onClick={() => paletteOpen.set(true)} aria-label="Suchen und Befehle"><Search /></button>
        <Link to="/approvals" className="btn icon ghost sm icon-btn" aria-label={`${data?.approvals_pending ?? 0} wartende Freigaben`} title="Freigaben"><ShieldCheck />{!!data?.approvals_pending && <span className="count warn">{data.approvals_pending}</span>}</Link>
        <Link to="/notifications" className="btn icon ghost sm icon-btn" aria-label={`${data?.notifications_unread ?? 0} ungelesene Meldungen`} title="Meldungen"><Bell />{!!data?.notifications_unread && <span className="count">{data.notifications_unread}</span>}</Link>
        {conn.state === "online" ? <span className="chip desktop-only" title="Laufende Verbindung"><Wifi size={14} style={{ color: "var(--ok)" }} /></span> :
          <button className="btn icon ghost sm" title="Neu verbinden" aria-label="Neu verbinden" onClick={() => events.reconnectNow()}>{conn.state === "offline" ? <WifiOff style={{ color: "var(--err)" }} /> : <RefreshCw style={{ animation: "spin 1s linear infinite" }} />}</button>}
        <button className="chip desktop-only" style={{ background: "none", border: 0 }} onClick={() => nav("/settings")} title="Konto"><strong>{data?.user?.name}</strong><span className="muted">{data?.user?.role}</span></button>
      </div>
    </header>
  );
}
