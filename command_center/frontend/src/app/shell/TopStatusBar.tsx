import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { events, useConnection, useEvent } from "@/lib/events";
import { Bell, RefreshCw, Search, ShieldCheck, Wifi, WifiOff } from "@/lib/icons";
import { StatusIndicator } from "@/components/ui";
import { paletteOpen } from "@/app/commands/commands";

export interface StatusPayload {
  jarvis: { online: boolean; label: string; version: string; uptime_seconds: number };
  master: {
    mode: string;
    online: boolean;
    label: string;
    provider: { id: string; model: string; label: string } | null;
    active_runs: any[];
    error: string;
  };
  server: {
    connected: boolean;
    hostname: string;
    cpu: number;
    ram: number;
    disk: number;
    load: number;
    net_rx: number;
    net_tx: number;
    docker: { status: string };
  };
  tasks: Record<string, number>;
  agents: { total: number; enabled: number; active: number };
  approvals_pending: number;
  notifications_unread: number;
  user: { name: string; role: string };
}

function runningTasks(tasks?: Record<string, number>) {
  if (!tasks) return 0;
  if ("RUNNING" in tasks || "PLANNING" in tasks) {
    return Number(tasks.RUNNING || 0) + Number(tasks.PLANNING || 0);
  }
  return Number(tasks.active || 0);
}

export function TopStatusBar() {
  const { data, reload } = useApi<StatusPayload>("/api/status", {
    refreshOn: [
      "task.*",
      "agent.status",
      "approval.*",
      "notification.*",
      "master.status",
      "run.started",
      "run.finished",
    ],
    debounce: 400,
    interval: 30000,
  });
  const connection = useConnection();
  const navigate = useNavigate();
  const [runLabel, setRunLabel] = useState("");

  useEvent("run.status", (event) => {
    if (event.data.label) setRunLabel(String(event.data.label));
  });
  useEvent("run.finished", () => window.setTimeout(() => setRunLabel(""), 2500));
  useEffect(() => {
    if (connection.state === "online") reload();
  }, [connection.state, reload]);

  const master = data?.master;
  const masterTone = !master ? "muted" : master.online ? (master.active_runs?.length ? "info" : "ok") : "err";
  const activeTasks = runningTasks(data?.tasks);
  const connectionLabel =
    connection.state === "online" ? "MIA ONLINE" : connection.state === "offline" ? "MIA OFFLINE" : "VERBINDE NEU";
  const activityLabel =
    runLabel ||
    (master
      ? master.online
        ? master.active_runs?.length
          ? "MIA ARBEITET"
          : "MIA BEREIT"
        : "MIA NICHT VERFÜGBAR"
      : "STATUS WIRD GELADEN");

  return (
    <header className="topbar mia-topbar" role="banner">
      <div className="status-group">
        <span className="chip hero">
          <StatusIndicator
            status={connection.state === "online" ? "ok" : connection.state === "offline" ? "err" : "warn"}
            live={connection.state !== "online"}
          />
          {connectionLabel}
        </span>
        <span className="chip desktop-only" title={master?.error || master?.provider?.label || ""}>
          <StatusIndicator status={masterTone} live={!!master?.active_runs?.length} />
          {activityLabel}
        </span>
        <span className="chip">
          <span className="val">{activeTasks}</span> AUFTRAG{activeTasks === 1 ? "" : "E"} AKTIV
        </span>
        <span className="chip desktop-only">
          <span className="val">{data?.agents?.active ?? 0}</span> AGENT{data?.agents?.active === 1 ? "" : "EN"} AKTIV
        </span>
      </div>

      <div className="status-group right">
        <button
          className="btn icon ghost sm desktop-only"
          type="button"
          onClick={() => paletteOpen.set(true)}
          aria-label="Suchen und Befehle"
          title="Suchen und Befehle"
        >
          <Search />
        </button>
        <Link
          to="/approvals"
          className="btn icon ghost sm icon-btn"
          aria-label={`${data?.approvals_pending ?? 0} wartende Freigaben`}
          title="Freigaben"
        >
          <ShieldCheck />
          {!!data?.approvals_pending && <span className="count warn">{data.approvals_pending}</span>}
        </Link>
        <Link
          to="/notifications"
          className="btn icon ghost sm icon-btn"
          aria-label={`${data?.notifications_unread ?? 0} ungelesene Meldungen`}
          title="Meldungen"
        >
          <Bell />
          {!!data?.notifications_unread && <span className="count">{data.notifications_unread}</span>}
        </Link>
        {connection.state === "online" ? (
          <span className="chip desktop-only" title="Live-Verbindung aktiv">
            <Wifi size={14} style={{ color: "var(--ok)" }} />
          </span>
        ) : (
          <button
            className="btn icon ghost sm"
            type="button"
            title="Live-Verbindung neu aufbauen"
            aria-label="Live-Verbindung neu aufbauen"
            onClick={() => events.reconnectNow()}
          >
            {connection.state === "offline" ? (
              <WifiOff style={{ color: "var(--err)" }} />
            ) : (
              <RefreshCw style={{ animation: "spin 1s linear infinite" }} />
            )}
          </button>
        )}
        <button
          className="chip desktop-only mia-account"
          type="button"
          onClick={() => navigate("/settings")}
          title="Konto und Einstellungen"
        >
          <strong>{data?.user?.name || "…"}</strong>
          <span className="muted">{data?.user?.role || ""}</span>
        </button>
      </div>
    </header>
  );
}
