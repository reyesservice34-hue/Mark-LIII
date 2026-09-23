import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import { bytes, duration, pct, relative, time } from "@/lib/format";
import { Activity, Bot, Cpu, ListChecks, Server, Sparkles, Zap, HardDrive } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Meter, Panel, Skeleton, Sparkline, StatusIndicator } from "@/components/ui";
import type { StatusPayload } from "@/app/shell/TopStatusBar";
import { AgentCard } from "@/modules/agents/AgentCard";
import { TaskTimeline } from "@/modules/tasks/TaskTimeline";
import { ActivityFeed } from "./ActivityFeed";
import { ArchitectureView } from "./ArchitectureView";
import { CoreDeck } from "./CoreDeck";
import { HeartbeatTile } from "./HeartbeatTile";
import { BrainCore } from "./BrainCore";
import { closeLine, openLine, sayOnLine, useLive } from "@/app/voice/liveStore";
import { MessageList } from "@/modules/chat/MessageList";
import { ChatComposer } from "@/modules/chat/ChatComposer";
import type { Attachment, Message, RunState } from "@/modules/chat/types";
import "@/modules/chat/chat.css";

export default function HomePage() {
  const [openingSession, setOpeningSession] = useState(false);
  const liveSession = useLive();
  const sessionId = liveSession.conversationId;
  const [sessionMessages, setSessionMessages] = useState<Message[]>([]);
  const [sessionBusy, setSessionBusy] = useState(false);

  const loadSession = async (id: string) => {
    if (!id) return;
    const r = await api.get<{ messages: Message[] }>(`/api/chat/conversations/${id}`);
    setSessionMessages(r.messages || []);
  };

  const openMiaSession = async () => {
    if (openingSession || sessionId) return;
    setOpeningSession(true);
    try {
      const r = await api.post<{ conversation: { id: string } }>("/api/chat/conversations", { title: "MIA Live-Sitzung" });
      const id = r.conversation.id;
      await api.patch(`/api/chat/conversations/${id}`, { archived: true });
      setSessionMessages([]);
      await openLine(id);
    } finally { setOpeningSession(false); }
  };

  const endMiaSession = async () => {
    const id = sessionId;
    if (!id) return;
    await closeLine();
    await api.patch(`/api/chat/conversations/${id}`, { archived: false });
    setSessionMessages([]);
  };

  const focusMiaSession = async () => {
    if (!sessionId) await openMiaSession();
    requestAnimationFrame(() => {
      document.querySelector(".mia-inline-session")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const sendInSession = async (text: string, attachments: Attachment[]) => {
    const t = text.trim();
    if (!sessionId || (!t && !attachments.length)) return;
    if (!attachments.length && liveSession.open) {
      sayOnLine(t);
      return;
    }
    setSessionBusy(true);
    try {
      await api.post(`/api/chat/conversations/${sessionId}/messages`, { content: t, attachments, stream: false });
      await loadSession(sessionId);
    } finally { setSessionBusy(false); }
  };

  useEffect(() => {
    if (sessionId) void loadSession(sessionId);
  }, [sessionId, liveSession.heard, liveSession.said]);

  useEffect(() => {
    const wake = () => { if (!sessionId) void openMiaSession(); };
    window.addEventListener("mia:wake", wake);
    return () => window.removeEventListener("mia:wake", wake);
  }, [sessionId]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("wake") !== "1") return;
    url.searchParams.delete("wake");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    if (!sessionId) void openMiaSession();
  // intentionally run on page entry / session changes only
  }, [sessionId]);
  const status = useApi<StatusPayload>("/api/status", { refreshOn: ["task.*", "agent.status", "run.*", "master.status", "approval.*"], interval: 30000 });
  const health = useApi<any>("/api/health", { interval: 60000, refreshOn: ["integration.status", "master.status"] });
  const agents = useApi<{ agents: any[] }>("/api/agents", { refreshOn: ["agent.status", "run.started", "run.finished"] });
  const timeline = useApi<{ tasks: any[] }>("/api/tasks/timeline?limit=12", { refreshOn: ["task.*"] });
  const history = useApi<{ points: any[] }>("/api/server/metrics/history?limit=120");
  const [live, setLive] = useState<any[]>([]);
  useEvent("server.metrics", (ev) => setLive((l) => [...l.slice(-119), ev.data]));
  useEvent("message.created", (ev) => { if (sessionId && ev.data.conversation_id === sessionId) setSessionMessages((ms) => ms.some((m) => m.id === ev.data.id) ? ms : [...ms, ev.data]); }, [sessionId]);
  useEvent("message.updated", (ev) => { if (sessionId && ev.data.conversation_id === sessionId) setSessionMessages((ms) => ms.map((m) => m.id === ev.data.id ? ev.data : m)); }, [sessionId]);
  const points = [...(history.data?.points || []), ...live].slice(-120);
  const s = status.data;
  const master = s?.master;
  const gw = health.data?.components?.agent_gateway;
  const [cmd, setCmd] = useState("");

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Kommandozentrale · {new Date().toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" })}</div><h1>{master?.label?.replace(/MIA/gi, "MIA") || "MIA"}</h1></div>
        <form className="row" onSubmit={async (e) => { e.preventDefault(); const t = cmd.trim(); if (!t) return; if (!sessionId) { await openMiaSession(); setCmd(""); return; } await sendInSession(t, []); setCmd(""); }} style={{ minWidth: "min(460px, 100%)" }}>
          <input className="input" placeholder="Sag MIA, was zu tun ist … (⌘K für Befehle)" value={cmd} onChange={(e) => setCmd(e.target.value)} aria-label="Befehl an MIA" />
          <button className="btn primary" type="submit"><Sparkles size={14} />Senden</button>
        </form>
      </div>
      <ErrorState error={status.error} retry={() => status.reload(false)} />

      <section className="hero-core core-launch" aria-label="MIA Kern" role="button" tabIndex={0} onClick={() => void focusMiaSession()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") void focusMiaSession(); }}>
        <div className="hero-stage" title="MIA Sitzung öffnen">
          <BrainCore thinking={!!master?.active_runs?.length} />
          <div className="hud tl"><b>MIA · KERN</b><span className={master?.online ? "on" : "off"}>{master?.online ? "SYSTEM ONLINE" : "OFFLINE"}</span></div>
          <div className="hud tr"><b>DENKT MIT</b><span>{master?.provider?.label || "—"}</span></div>
          <div className="hud bl"><b>LAUFZEIT</b><span>{s ? duration(s.jarvis.uptime_seconds) : "—"}</span></div>
          <div className="hud br"><b>BETRIEBSART</b><span>{master?.mode || "—"}</span></div>
          <div className="hero-caption">{master?.active_runs?.length ? "Ich arbeite gerade, Master." : "Core antippen · MIA Sitzung öffnen · „Hey MIA“ startet die Sitzung"}</div>
        </div>
      </section>

      {sessionId && <section className="mia-inline-session panel">
        <div className="mia-inline-head">
          <div><div className="eyebrow">AKTIVE MIA-SITZUNG</div><strong>{liveSession.phase === "speaking" ? "MIA spricht" : liveSession.phase === "thinking" ? "MIA denkt" : liveSession.phase === "listening" ? "MIA hört zu" : "Verbunden"}</strong></div>
          <div className="row"><span className="state-line"><span className={`dot ${liveSession.open ? "ok live" : "warn"}`} />{liveSession.open ? "LIVE" : "TEXT"}</span><button className="btn sm danger" onClick={() => void endMiaSession()}>Sitzung beenden</button></div>
        </div>
        <div className="mia-inline-chat chat-col">
          {sessionMessages.length ? <MessageList messages={sessionMessages} runs={{} as Record<string, RunState>} onRegenerate={() => {}} onRetry={() => {}} canAct={false} /> : <div className="mia-inline-empty"><Sparkles size={22} /><span>Sprich einfach los oder schreib MIA eine Nachricht.</span></div>}
          <ChatComposer onSend={sendInSession} onStop={() => {}} busy={sessionBusy} disabled={false} />
        </div>
      </section>}

      <ArchitectureView />

      <div style={{ marginTop: 14 }}><HeartbeatTile /></div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <CoreDeck />
      </div>

      <div className="grid cols-4">
        <Panel title="MIA-Kern" icon={<Sparkles size={15} />}>
          {!s ? <Skeleton /> : <div className="stack">
            <div className="row between"><StatusIndicator status={master?.online ? "ok" : "err"} label={master?.online ? "online" : "offline"} live={!!master?.active_runs?.length} /><Badge status={gw?.status}>{gw?.status || "…"}</Badge></div>
            <div className="small"><span className="muted">Laufzeit</span> <span className="num">{duration(s.jarvis.uptime_seconds)}</span></div>
            <div className="small truncate"><span className="muted">Modell</span> {master?.provider?.label || <span className="muted">keiner eingerichtet</span>}</div>
            <div className="small"><span className="muted">Betriebsart</span> {master?.mode} · <span className="muted">laufende Aufträge</span> {master?.active_runs?.length ?? 0}</div>
            <div className="small"><span className="muted">Agenten</span> {s.agents.enabled}/{s.agents.total} enabled · {s.agents.active} active</div>
            {master?.error && <div className="error-state small">{master.error}</div>}
          </div>}
        </Panel>
        <Panel title="Zustand des Servers" icon={<Server size={15} />} actions={<Link className="btn sm ghost" to="/server">Einzelheiten</Link>}>
          {!s ? <Skeleton /> : <div className="stack">
            <div className="row between small"><span className="muted">{s.server.hostname}</span><Badge status={s.server.connected ? "healthy" : "offline"} /></div>
            {[["CPU", s.server.cpu], ["RAM", s.server.ram], ["Platte", s.server.disk]].map(([k, v]) => (
              <div key={k as string} className="stack" style={{ gap: 4 }}><div className="row between small"><span>{k}</span><span className="num">{pct(v as number)}</span></div><Meter value={v as number} /></div>
            ))}
            <div className="row between small"><span className="muted">Last</span><span className="num">{s.server.load?.toFixed(2)}</span><span className="muted">Net</span><span className="num">{bytes(s.server.net_rx, 0)}/s ↓ {bytes(s.server.net_tx, 0)}/s ↑</span></div>
            <div className="row between small"><span className="muted">Docker</span><Badge status={s.server.docker?.status}>{s.server.docker?.status?.replace("_", " ")}</Badge></div>
          </div>}
        </Panel>
        <Panel title="Auslastung · CPU / RAM" icon={<Cpu size={15} />}>
          <div className="stack">
            <div><div className="row between tiny muted"><span>CPU</span><span className="num">{pct(points.at(-1)?.cpu)}</span></div><Sparkline points={points.map((p) => p.cpu)} height={44} /></div>
            <div><div className="row between tiny muted"><span>RAM</span><span className="num">{pct(points.at(-1)?.ram)}</span></div><Sparkline points={points.map((p) => p.ram)} height={44} color="var(--ok)" /></div>
            <div className="tiny muted">last {Math.round(points.length * 5 / 60)} min · 5 s samples</div>
          </div>
        </Panel>
        <Panel title="Aufgaben" icon={<ListChecks size={15} />} actions={<Link className="btn sm ghost" to="/tasks">Alle</Link>}>
          {!s ? <Skeleton /> : <div className="grid cols-2" style={{ gap: 8 }}>
            {[["Laufen", s.tasks.RUNNING + s.tasks.PLANNING, "info"], ["Wartet", s.tasks.QUEUED, "muted"], ["Freigabe", s.tasks.WAITING_FOR_APPROVAL, "warn"], ["Fehler", s.tasks.FAILED, "err"], ["Fertig", s.tasks.COMPLETED, "ok"], ["Freigaben", s.approvals_pending, "warn"]].map(([k, v, t]) => (
              <div key={k as string} className="stack" style={{ gap: 0 }}><span className="tiny label">{k}</span><span className="num" style={{ fontSize: 20, fontWeight: 600, color: `var(--${t === "muted" ? "text-2" : t})` }}>{v as number}</span></div>
            ))}
          </div>}
        </Panel>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)" }}>
        <div className="stack" style={{ gap: 16 }}>
          <Panel title="Agent Control Center" icon={<Bot size={15} />} actions={<Link className="btn sm ghost" to="/agents">Leitstand</Link>}>
            {agents.error ? <ErrorState error={agents.error} retry={() => agents.reload(false)} /> : !agents.data ? <Skeleton rows={2} /> :
              <div className="grid auto-sm" style={{ gap: 10 }}>{agents.data.agents.map((a) => <AgentCard key={a.id} agent={a} compact />)}</div>}
          </Panel>
          <Panel title="Verlauf der Aufgaben" icon={<Zap size={15} />} flush>
            {timeline.error ? <div className="panel-body"><ErrorState error={timeline.error} /></div> : !timeline.data ? <div className="panel-body"><Skeleton /></div> :
              timeline.data.tasks.length === 0 ? <EmptyState icon={<ListChecks size={26} />} title="Noch keine Aufgaben">Gib MIA eine mehrstufige Aufgabe, dann erscheint sie hier.</EmptyState> :
                <TaskTimeline tasks={timeline.data.tasks} grouped={false} />}
          </Panel>
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <Panel title="Zuletzt passiert" icon={<Activity size={15} />} flush><ActivityFeed /></Panel>
          <Panel title="Dienste" icon={<HardDrive size={15} />}>
            {!health.data ? <Skeleton rows={3} /> : <div className="stack" style={{ gap: 8 }}>
              {Object.entries(health.data.components).map(([k, v]: [string, any]) => (
                <div key={k} className="row between small"><span className="row"><StatusIndicator status={v.status} />{k.replace(/_/g, " ")}</span><span className="muted truncate" style={{ maxWidth: "60%" }} title={v.detail}>{String(v.detail || "").replace(/JARVIS/gi, "MIA")}</span></div>
              ))}
              <div className="tiny muted">checked {time(new Date().toISOString())} · overall <Badge status={health.data.status} /></div>
            </div>}
          </Panel>
        </div>
      </div>
      <div className="tiny muted">{s ? `Status refreshed ${relative(new Date().toISOString())}` : ""}</div>
    </div>
  );
}
