import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "@/lib/router";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import { useAuth } from "@/lib/auth";
import {
  AlertTriangle,
  MoveRight,
  Bot,
  Calendar,
  Cpu,
  ShieldCheck,
  HardDrive,
  Layers,
  ListChecks,
  MessageSquare,
  Mic,
  Play,
  Plug,
  Search,
  Send,
  Sparkles,
  X,
} from "@/lib/icons";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/ui";
import type { StatusPayload } from "@/app/shell/TopStatusBar";
import { AgentCard, type Agent } from "@/modules/agents/AgentCard";
import { TaskTimeline } from "@/modules/tasks/TaskTimeline";
import { endConversation, openLine, sayOnLine, setListenEnabled, useLive } from "@/app/voice/liveStore";
import { MessageList } from "@/modules/chat/MessageList";
import { ChatComposer } from "@/modules/chat/ChatComposer";
import type { Attachment, Message, RunState } from "@/modules/chat/types";
import "@/modules/chat/chat.css";
import "./ops-home.css";
import "./architecture.css";
import { BrainCore } from "./BrainCore";
import { CoreDeck } from "./CoreDeck";
import { HeartbeatTile } from "./HeartbeatTile";

interface CalendarEvent {
  uid: string;
  title: string;
  start: string;
  end: string;
  location?: string;
  notes?: string;
  backend?: string;
  category?: string;
}

interface CalendarPayload {
  available: boolean;
  detail: string;
  backend: string;
  events: CalendarEvent[];
}

const ACTIVE_AGENT_STATES = new Set(["THINKING", "EXECUTING", "WAITING"]);
const PHASE_LABEL: Record<string, string> = {
  closed: "Ruht",
  connecting: "Verbindet",
  reconnecting: "Verbindet neu",
  standby: "Wartet auf „Hey Mia“",
  listening: "Hört zu",
  thinking: "Denkt nach",
  speaking: "Spricht",
};
const pad = (value: number) => String(value).padStart(2, "0");
const uptime = (seconds: number) => {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d} T ${h} Std` : h ? `${h} Std ${m} Min` : `${m} Min`;
};
const dateKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const eventTime = (event: CalendarEvent) => {
  const start = new Date(event.start);
  const end = new Date(event.end);
  const options: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };
  return `${start.toLocaleTimeString("de-DE", options)}–${end.toLocaleTimeString("de-DE", options)}`;
};
const greeting = (date: Date) => {
  const h = date.getHours();
  return h < 5 ? "Gute Nacht" : h < 11 ? "Guten Morgen" : h < 17 ? "Hallo" : h < 22 ? "Guten Abend" : "Gute Nacht";
};

/** Kleiner Ring für Prozentwerte (Server-Last). */
function Ring({ value, tone }: { value: number; tone: string }) {
  const r = 17;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value));
  return (
    <svg className={`aur-ring ${tone}`} viewBox="0 0 44 44" aria-hidden>
      <defs>
        <linearGradient id="aur-ring-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="55%" stopColor="#8b7bff" />
          <stop offset="100%" stopColor="#f472b6" />
        </linearGradient>
      </defs>
      <circle cx="22" cy="22" r={r} className="track" />
      <circle cx="22" cy="22" r={r} className="bar" strokeDasharray={`${(v / 100) * c} ${c}`} />
    </svg>
  );
}

export default function HomePage() {
  const nav = useNavigate();
  const { user } = useAuth();
  const liveSession = useLive();
  const [mainId, setMainId] = useState("");
  // Getippte Unterhaltung auf der Startseite; das Sprachgespräch zeigt sich nur, wenn MIA wirklich wach ist.
  const [textOpen, setTextOpen] = useState(false);
  const [sessionMessages, setSessionMessages] = useState<Message[]>([]);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [command, setCommand] = useState("");
  const [starting, setStarting] = useState(false);

  const sessionId = liveSession.conversationId || mainId;
  const sessionVisible = liveSession.engaged || textOpen;

  const today = useMemo(() => new Date(), []);
  const tomorrow = useMemo(() => {
    const next = new Date(today);
    next.setDate(next.getDate() + 1);
    return next;
  }, [today]);

  const status = useApi<StatusPayload>("/api/status", {
    refreshOn: ["task.*", "agent.status", "run.*", "master.status", "approval.*", "notification.*"],
    interval: 30000,
  });
  const agents = useApi<{ agents: Agent[] }>("/api/agents", {
    refreshOn: ["agent.status", "run.started", "run.finished"],
    interval: 30000,
  });
  const timeline = useApi<{ tasks: any[] }>("/api/tasks/timeline?limit=8", {
    refreshOn: ["task.*"],
    interval: 30000,
  });
  const calendar = useApi<CalendarPayload>(
    `/api/calendar?start=${dateKey(today)}T00:00:00&end=${dateKey(tomorrow)}T00:00:00`,
    { refreshOn: ["calendar.changed"], interval: 30000 },
  );

  const mainConversation = async () => {
    if (sessionId) return sessionId;
    const response = await api.get<{ conversation: { id: string } }>("/api/chat/main");
    setMainId(response.conversation.id);
    return response.conversation.id;
  };

  const loadSession = async (id: string) => {
    if (!id) return;
    const response = await api.get<{ messages: Message[] }>(`/api/chat/conversations/${id}`);
    setSessionMessages(response.messages || []);
  };

  const scrollToSession = () => window.setTimeout(() => {
    document.querySelector(".aur-session")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 80);

  /** Ausdrücklich sprechen: Knopf, „Hey Mia“ im Browser oder ?wake=1. */
  const talk = async () => {
    if (starting) return;
    setStarting(true);
    try {
      setListenEnabled(true);
      await openLine(await mainConversation());
      scrollToSession();
    } catch {
      /* Grund steht in der Leiste oben */
    } finally {
      setStarting(false);
    }
  };

  const endMiaSession = async () => {
    setTextOpen(false);
    if (liveSession.engaged) await endConversation();
  };

  const sendInSession = async (text: string, attachments: Attachment[]) => {
    const value = text.trim();
    if (!value && !attachments.length) return;
    const id = await mainConversation();
    setTextOpen(true);
    if (!attachments.length && liveSession.engaged && liveSession.conversationId === id) {
      sayOnLine(value);
      return;
    }
    setSessionBusy(true);
    try {
      await api.post(`/api/chat/conversations/${id}/messages`, { content: value, attachments, stream: false });
      await loadSession(id);
    } finally {
      setSessionBusy(false);
    }
  };

  const submitCommand = async (event: FormEvent) => {
    event.preventDefault();
    const value = command.trim();
    if (!value) return;
    setCommand("");
    await sendInSession(value, []);
    scrollToSession();
  };

  useEffect(() => {
    if (sessionVisible && sessionId) void loadSession(sessionId);
  }, [sessionVisible, sessionId, liveSession.heard, liveSession.said]);

  useEffect(() => {
    const wake = () => { void talk(); };
    window.addEventListener("mia:wake", wake);
    return () => window.removeEventListener("mia:wake", wake);
  });

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("wake") !== "1") return;
    url.searchParams.delete("wake");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    void talk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEvent(
    "message.created",
    (event) => {
      if (!sessionId || event.data.conversation_id !== sessionId) return;
      setSessionMessages((messages) =>
        messages.some((message) => message.id === event.data.id) ? messages : [...messages, event.data],
      );
    },
    [sessionId],
  );
  useEvent(
    "message.updated",
    (event) => {
      if (!sessionId || event.data.conversation_id !== sessionId) return;
      setSessionMessages((messages) =>
        messages.map((message) => (message.id === event.data.id ? event.data : message)),
      );
    },
    [sessionId],
  );

  const system = status.data;
  const master = system?.master;
  const taskCounts = system?.tasks || {};
  const hasDetailedRunning = "RUNNING" in taskCounts || "PLANNING" in taskCounts;
  const runningTasks = hasDetailedRunning
    ? Number(taskCounts.RUNNING || 0) + Number(taskCounts.PLANNING || 0)
    : Number(taskCounts.active || 0);
  const queuedTasks = Number(taskCounts.QUEUED || 0) + Number(taskCounts.WAITING || 0);
  const blockedTasks = Number(taskCounts.BLOCKED || 0) + Number(taskCounts.WAITING_FOR_APPROVAL || 0);
  const openTasks = runningTasks + queuedTasks + blockedTasks;
  const activeAgents = system?.agents?.active ?? 0;
  const approvalsPending = system?.approvals_pending ?? 0;
  const serverOk = !!system?.server?.connected;
  const serverLevel = !serverOk ? "" : system!.server.cpu >= 90 ? "err" : system!.server.cpu >= 75 ? "warn" : "ok";
  const miaTone = !master ? "idle" : master.online ? "ok" : "err";
  const phase = liveSession.phase;
  const awake = phase === "listening" || phase === "thinking" || phase === "speaking";

  const todayEvents = [...(calendar.data?.events || [])].sort(
    (a, b) => +new Date(a.start) - +new Date(b.start),
  );
  const prioritizedAgents = [...(agents.data?.agents || [])]
    .sort((a, b) => Number(ACTIVE_AGENT_STATES.has(b.status)) - Number(ACTIVE_AGENT_STATES.has(a.status)))
    .slice(0, 4);
  const criticalMessages = [
    master?.error ? { text: master.error, path: "/server" } : null,
    system && !system.server.connected ? { text: "Serververbindung ist nicht bestätigt.", path: "/server" } : null,
    blockedTasks > 0
      ? {
          text: `${blockedTasks} Auftrag${blockedTasks === 1 ? "" : "e"} blockiert oder wartet auf Freigabe.`,
          path: "/tasks",
        }
      : null,
  ].filter(Boolean) as { text: string; path: string }[];

  const name = String(user?.name || "").split(" ")[0];

  return (
    <div className="page aur-home">
      <ErrorState error={status.error} retry={() => status.reload(false)} />

      {criticalMessages.length > 0 && (
        <section className="aur-alerts" aria-label="Kritische Hinweise">
          {criticalMessages.map((message) => (
            <Link key={`${message.path}-${message.text}`} to={message.path} className="aur-alert">
              <span className="aur-alert-icon"><AlertTriangle size={14} /></span>
              <span className="aur-alert-text">{message.text}</span>
              <MoveRight size={14} />
            </Link>
          ))}
        </section>
      )}

      <section className="aur-hero">
        {/* ── MIA Kern ─────────────────────────────────────────── */}
        <div className={`aur-core phase-${phase}`}>
          <div className="aur-core-bg" aria-hidden />
          <header className="aur-core-head">
            <div>
              <div className="aur-kicker">
                {today.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" })}
              </div>
              <h1 className="aur-title">
                {greeting(today)}{name ? `, ${name}` : ""}.
                <span> Was soll MIA erledigen?</span>
              </h1>
            </div>
            <div className={`aur-pill ${miaTone}`}>
              <span className="aur-pill-dot" />
              {!master ? "Lädt …" : master.online ? "MIA bereit" : "MIA offline"}
            </div>
          </header>

          <div className="aur-stage" aria-label="MIA Kern">
            <BrainCore thinking={phase === "thinking"} />
            <div className="aur-orbit-chips">
              <span className={`aur-chip ${awake ? "live" : ""}`}><b>Zustand</b>{PHASE_LABEL[phase] || phase}</span>
              <span className="aur-chip"><b>Modell</b>{master?.provider?.label || "—"}</span>
              <span className="aur-chip"><b>Laufzeit</b>{system ? uptime(system.jarvis.uptime_seconds) : "—"}</span>
            </div>
            <div className="aur-mic-wrap">
            <button
              type="button"
              className={`aur-mic ${awake ? "awake" : ""}`}
              onClick={() => (liveSession.engaged ? scrollToSession() : void talk())}
              disabled={starting}
              aria-label={liveSession.engaged ? "Zum laufenden Gespräch" : "Mit MIA sprechen"}
              title={liveSession.engaged ? "Zum laufenden Gespräch" : "Mit MIA sprechen"}
            >
              <span className="aur-mic-halo" aria-hidden />
              <Mic size={22} />
            </button>
            <div className="aur-mic-hint">
              {liveSession.engaged ? "Gespräch läuft" : starting ? "Verbinde …" : "Tippen oder „Hey Mia“ sagen"}
            </div>
            </div>
          </div>

          <form className="aur-command" onSubmit={submitCommand}>
            <Sparkles size={16} className="aur-command-icon" />
            <input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              placeholder="Sag MIA, was erledigt werden soll …"
              aria-label="Auftrag an MIA"
            />
            <button className="aur-send" type="submit" disabled={!command.trim()} aria-label="Senden">
              <Send size={15} />
            </button>
          </form>

          <nav className="aur-areas" aria-label="MIA Kernbereiche">
            <Link to="/workflows"><Search size={13} /> Analyse</Link>
            <Link to="/memory"><HardDrive size={13} /> Gedächtnis</Link>
            <Link to="/tasks"><Layers size={13} /> Planung</Link>
            <Link to={sessionId ? `/chat/${sessionId}` : "/chat"}><MessageSquare size={13} /> Chat</Link>
            <Link to="/agents"><Play size={13} /> Ausführung</Link>
            <Link to="/integrations"><Plug size={13} /> Integration</Link>
          </nav>
        </div>

        {/* ── Lage auf einen Blick ─────────────────────────────── */}
        <div className="aur-kpis" aria-label="Lage auf einen Blick">
          <Link className={`aur-kpi ${miaTone}`} to="/chat">
            <span className="aur-kpi-head"><span className="aur-kpi-icon c1"><Sparkles size={16} /></span><span className="aur-kpi-label">MIA</span></span>
            <strong className="aur-kpi-value">{!master ? "—" : master.online ? "Online" : "Offline"}</strong>
            <small>{master?.provider?.label || "kein KI-Anbieter verbunden"}</small>
          </Link>
          <Link className={`aur-kpi ${blockedTasks > 0 ? "warn" : ""}`} to="/tasks">
            <span className="aur-kpi-head"><span className="aur-kpi-icon c2"><ListChecks size={16} /></span><span className="aur-kpi-label">Aufträge</span></span>
            <strong className="aur-kpi-value">{system ? openTasks : "—"}</strong>
            <small>{runningTasks} laufen · {queuedTasks} wartend · {blockedTasks} blockiert</small>
          </Link>
          <Link className="aur-kpi" to="/agents">
            <span className="aur-kpi-head"><span className="aur-kpi-icon c3"><Bot size={16} /></span><span className="aur-kpi-label">Agenten aktiv</span></span>
            <strong className="aur-kpi-value">{system ? activeAgents : "—"}</strong>
            <small>{system ? `${system.agents.enabled}/${system.agents.total} einsatzbereit` : "wird geladen"}</small>
          </Link>
          <Link className={`aur-kpi ${approvalsPending > 0 ? "warn" : ""}`} to="/approvals">
            <span className="aur-kpi-head"><span className="aur-kpi-icon c4"><ShieldCheck size={16} /></span><span className="aur-kpi-label">Freigaben</span></span>
            <strong className="aur-kpi-value">{system ? approvalsPending : "—"}</strong>
            <small>{approvalsPending > 0 ? "warten auf Entscheidung" : "nichts offen"}</small>
          </Link>
          <Link className={`aur-kpi ${serverLevel}`} to="/server">
            <span className="aur-kpi-head"><span className="aur-kpi-icon c5"><Cpu size={16} /></span><span className="aur-kpi-label">Server</span></span>
            <div className="aur-kpi-ring">
              <Ring value={serverOk ? system!.server.cpu : 0} tone={serverLevel} />
              <strong className="aur-kpi-value">{serverOk ? `${Math.round(system!.server.cpu)}%` : "—"}</strong>
            </div>
            <small>{serverOk ? `RAM ${Math.round(system!.server.ram)}% · Platte ${Math.round(system!.server.disk)}%` : "nicht bestätigt"}</small>
          </Link>
          <Link className="aur-kpi" to="/calendar">
            <span className="aur-kpi-head"><span className="aur-kpi-icon c6"><Calendar size={16} /></span><span className="aur-kpi-label">Termine heute</span></span>
            <strong className="aur-kpi-value">{calendar.loading && !calendar.data ? "—" : todayEvents.length}</strong>
            <small>{calendar.data?.available === false ? "nicht eingerichtet" : "Kalender öffnen"}</small>
          </Link>
        </div>
      </section>

      {sessionVisible && (
        <section className="aur-session" aria-label="Gespräch mit MIA">
          <header className="aur-session-head">
            <div className="aur-session-title">
              <span className={`aur-wave ${awake ? "on" : ""}`} aria-hidden><i /><i /><i /><i /></span>
              <div>
                <div className="aur-kicker">Gespräch mit MIA</div>
                <strong>
                  {liveSession.engaged ? PHASE_LABEL[phase] || "Verbunden" : "Textgespräch"}
                </strong>
              </div>
            </div>
            <div className="row wrap">
              {!liveSession.engaged && (
                <button className="btn sm" type="button" onClick={() => void talk()}>
                  <Mic size={13} /> Sprechen
                </button>
              )}
              <Link className="btn sm ghost" to={sessionId ? `/chat/${sessionId}` : "/chat"}>Im Chat öffnen</Link>
              <button className="btn sm danger" type="button" onClick={() => void endMiaSession()}>
                <X size={13} /> Beenden
              </button>
            </div>
          </header>
          {liveSession.error && <div role="alert" className="mia-voice-error">{liveSession.error}</div>}
          <div className="mia-inline-chat chat-col">
            {sessionMessages.length ? (
              <MessageList
                messages={sessionMessages}
                runs={{} as Record<string, RunState>}
                onRegenerate={() => {}}
                onRetry={() => {}}
                canAct={false}
              />
            ) : (
              <div className="mia-inline-empty">
                <Sparkles size={22} />
                <span>Sprich einfach los oder schreib MIA eine Nachricht.</span>
              </div>
            )}
            <ChatComposer onSend={sendInSession} onStop={() => {}} busy={sessionBusy} disabled={false} showPicker={false} />
          </div>
        </section>
      )}

      <section className="aur-bento">
        <Panel
          className="aur-cal"
          title={`Heute · ${todayEvents.length} Termin${todayEvents.length === 1 ? "" : "e"}`}
          icon={<Calendar size={15} />}
          actions={<Link className="btn sm ghost" to="/calendar">Kalender</Link>}
        >
          {calendar.error ? (
            <ErrorState error={calendar.error} retry={() => calendar.reload(false)} />
          ) : calendar.loading && !calendar.data ? (
            <Skeleton rows={4} />
          ) : calendar.data?.available === false ? (
            <EmptyState icon={<Calendar size={25} />} title="Kalender nicht eingerichtet">
              {calendar.data.detail || "Es besteht noch keine bestätigte Kalenderverbindung."}
            </EmptyState>
          ) : todayEvents.length === 0 ? (
            <EmptyState icon={<Calendar size={25} />} title="Heute keine Termine">
              Der Kalender meldet für heute keine Einträge.
            </EmptyState>
          ) : (
            <div className="mia-event-list">
              {todayEvents.slice(0, 5).map((event) => (
                <div className="mia-event" key={event.uid || `${event.title}-${event.start}`}>
                  <time>{eventTime(event)}</time>
                  <div>
                    <strong>{event.title}</strong>
                    {event.location && <span>{event.location}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          className="aur-tasks"
          title={`Aufgaben · ${openTasks} offen`}
          icon={<ListChecks size={15} />}
          actions={<Link className="btn sm ghost" to="/tasks">Verlauf</Link>}
          flush
        >
          {timeline.error ? (
            <div className="panel-body"><ErrorState error={timeline.error} retry={() => timeline.reload(false)} /></div>
          ) : !timeline.data ? (
            <div className="panel-body"><Skeleton rows={4} /></div>
          ) : timeline.data.tasks.length === 0 ? (
            <EmptyState icon={<ListChecks size={25} />} title="Noch keine Aufträge">
              Ein Auftrag erscheint hier erst, wenn er im Backend angelegt wurde.
            </EmptyState>
          ) : (
            <TaskTimeline tasks={timeline.data.tasks.slice(0, 6)} grouped={false} />
          )}
        </Panel>

        <HeartbeatTile />

        <Panel
          className="aur-agents"
          title={`Agenten · ${activeAgents} aktiv`}
          icon={<Bot size={15} />}
          actions={<Link className="btn sm ghost" to="/agents">Alle</Link>}
        >
          {agents.error ? (
            <ErrorState error={agents.error} retry={() => agents.reload(false)} />
          ) : !agents.data ? (
            <Skeleton rows={3} />
          ) : prioritizedAgents.length === 0 ? (
            <EmptyState icon={<Bot size={25} />} title="Keine Agenten eingerichtet">
              Es werden nur Agenten angezeigt, die das Backend tatsächlich meldet.
            </EmptyState>
          ) : (
            <div className="grid auto-sm mia-agent-grid">
              {prioritizedAgents.map((agent) => (
                <AgentCard key={agent.id} agent={agent} compact onClick={() => nav(`/agents/${agent.id}`)} />
              ))}
            </div>
          )}
        </Panel>

        <CoreDeck onTalk={() => void talk()} />
      </section>
    </div>
  );
}
