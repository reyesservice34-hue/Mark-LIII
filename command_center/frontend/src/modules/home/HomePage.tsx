import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import {
  Bell,
  Bot,
  Calendar,
  ListChecks,
  MessageSquare,
  Mic,
  ShieldCheck,
  Sparkles,
} from "@/lib/icons";
import { EmptyState, ErrorState, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import type { StatusPayload } from "@/app/shell/TopStatusBar";
import { AgentCard, type Agent } from "@/modules/agents/AgentCard";
import { TaskTimeline } from "@/modules/tasks/TaskTimeline";
import { closeLine, openLine, sayOnLine, useLive } from "@/app/voice/liveStore";
import { MessageList } from "@/modules/chat/MessageList";
import { ChatComposer } from "@/modules/chat/ChatComposer";
import type { Attachment, Message, RunState } from "@/modules/chat/types";
import "@/modules/chat/chat.css";
import "./noir-home.css";

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
const pad = (value: number) => String(value).padStart(2, "0");
const dateKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const eventTime = (event: CalendarEvent) => {
  const start = new Date(event.start);
  const end = new Date(event.end);
  const options: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };
  return `${start.toLocaleTimeString("de-DE", options)}–${end.toLocaleTimeString("de-DE", options)}`;
};

export default function HomePage() {
  const nav = useNavigate();
  const [openingSession, setOpeningSession] = useState(false);
  const liveSession = useLive();
  const sessionId = liveSession.conversationId;
  const [sessionMessages, setSessionMessages] = useState<Message[]>([]);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [command, setCommand] = useState("");

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

  const loadSession = async (id: string) => {
    if (!id) return;
    const response = await api.get<{ messages: Message[] }>(`/api/chat/conversations/${id}`);
    setSessionMessages(response.messages || []);
  };

  const openMiaSession = useCallback(async () => {
    if (openingSession || sessionId) return;
    setOpeningSession(true);
    try {
      const response = await api.post<{ conversation: { id: string } }>("/api/chat/conversations", {
        title: "MIA Live-Sitzung",
      });
      const id = response.conversation.id;
      await api.patch(`/api/chat/conversations/${id}`, { archived: true });
      setSessionMessages([]);
      await openLine(id);
    } finally {
      setOpeningSession(false);
    }
  }, [openingSession, sessionId]);

  const endMiaSession = async () => {
    const id = sessionId;
    if (!id) return;
    await closeLine();
    await api.patch(`/api/chat/conversations/${id}`, { archived: false });
    setSessionMessages([]);
  };

  const focusMiaSession = async () => {
    if (!sessionId) await openMiaSession();
    window.setTimeout(() => {
      document.querySelector(".mia-inline-session")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
  };

  const sendInSession = async (text: string, attachments: Attachment[]) => {
    const value = text.trim();
    if (!sessionId || (!value && !attachments.length)) return;
    if (!attachments.length && liveSession.open) {
      sayOnLine(value);
      return;
    }
    setSessionBusy(true);
    try {
      await api.post(`/api/chat/conversations/${sessionId}/messages`, {
        content: value,
        attachments,
        stream: false,
      });
      await loadSession(sessionId);
    } finally {
      setSessionBusy(false);
    }
  };

  const submitCommand = async (event: FormEvent) => {
    event.preventDefault();
    const value = command.trim();
    if (!value) return;
    if (sessionId) {
      await sendInSession(value, []);
    } else {
      nav(`/chat?new=1&q=${encodeURIComponent(value)}`);
    }
    setCommand("");
  };

  useEffect(() => {
    if (sessionId) void loadSession(sessionId);
  }, [sessionId, liveSession.heard, liveSession.said]);

  useEffect(() => {
    const wake = () => {
      if (!sessionId) void openMiaSession();
    };
    window.addEventListener("mia:wake", wake);
    return () => window.removeEventListener("mia:wake", wake);
  }, [openMiaSession, sessionId]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("wake") !== "1") return;
    url.searchParams.delete("wake");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    if (!sessionId) void openMiaSession();
  }, [openMiaSession, sessionId]);

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
  const approvals = system?.approvals_pending ?? 0;
  const unread = system?.notifications_unread ?? 0;
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

  return (
    <div className="page mia-home">
      <ErrorState error={status.error} retry={() => status.reload(false)} />

      <section className="mia-noir-hero" aria-labelledby="mia-hero-title">
        <img className="mia-noir-visual" src="/mia-noir.webp" alt="MIA in ihrer digitalen Kommandozentrale" />
        <div className="mia-noir-content">
          <div className="mia-presence">
            <StatusIndicator
              status={!master ? "muted" : master.online ? "ok" : "err"}
              live={!!master?.active_runs?.length}
            />
            <span>{!master ? "Status wird geladen" : master.online ? "MIA ist online" : "MIA ist offline"}</span>
            <span className="mia-presence-model">{master?.provider?.label || "Kein KI-Provider verbunden"}</span>
          </div>
          <div className="mia-noir-kicker">Zentrale Intelligenz · ein Kontext · eine MIA</div>
          <h1 id="mia-hero-title">MIA</h1>
          <p className="mia-noir-lead">
            Deine Kommandozentrale für Gespräche, Aufgaben, Agenten und echte Ausführung. Ruhig, klar und ohne
            erfundene Zustände.
          </p>

          <form className="mia-command" onSubmit={submitCommand}>
            <input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              placeholder="Sag MIA, was erledigt werden soll …"
              aria-label="Auftrag an MIA"
            />
            <button className="btn primary" type="submit" disabled={!command.trim()}>
              <Sparkles size={15} /> Senden
            </button>
          </form>

          <div className="mia-hero-actions">
            <button className="btn primary" type="button" onClick={() => void focusMiaSession()} disabled={openingSession}>
              <Mic size={15} />
              {sessionId
                ? "Live-Sitzung anzeigen"
                : openingSession
                  ? "Verbindung wird aufgebaut"
                  : "Talk to MIA"}
            </button>
            <Link className="btn" to="/chat?new=1">
              <MessageSquare size={15} /> Chat öffnen
            </Link>
            <Link className="btn ghost" to="/memory">
              <Sparkles size={15} /> Gedächtnis
            </Link>
          </div>

          <div className="mia-truth-note">
            <ShieldCheck size={14} /> Alle Statuswerte auf dieser Seite stammen aus dem Backend. Nicht eingerichtete
            Funktionen werden nicht als verbunden dargestellt.
          </div>
        </div>
        <div className="mia-core-pulse" aria-hidden="true">
          <span />
        </div>
      </section>

      {sessionId && (
        <section className="mia-inline-session panel" aria-label="Aktive MIA-Sitzung">
          <div className="mia-inline-head">
            <div>
              <div className="eyebrow">Aktive MIA-Sitzung</div>
              <strong>
                {liveSession.phase === "speaking"
                  ? "MIA spricht"
                  : liveSession.phase === "thinking"
                    ? "MIA denkt"
                    : liveSession.phase === "listening"
                      ? "MIA hört zu"
                      : "Verbunden"}
              </strong>
            </div>
            <div className="row wrap">
              <span className="state-line">
                <span className={`dot ${liveSession.open ? "ok live" : "warn"}`} />
                {liveSession.open ? "LIVE" : "TEXT"}
              </span>
              {!liveSession.open && (
                <button className="btn sm" type="button" onClick={() => void openLine(sessionId)}>
                  Wieder verbinden
                </button>
              )}
              <button className="btn sm danger" type="button" onClick={() => void endMiaSession()}>
                Sitzung beenden
              </button>
            </div>
          </div>
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
            <ChatComposer onSend={sendInSession} onStop={() => {}} busy={sessionBusy} disabled={false} />
          </div>
        </section>
      )}

      <section className="mia-metrics" aria-label="MIA Übersicht">
        <Link className="mia-metric" to="/calendar">
          <span className="mia-metric-icon"><Calendar size={17} /></span>
          <span className="mia-metric-label">Termine heute</span>
          <strong>{calendar.loading && !calendar.data ? "—" : todayEvents.length}</strong>
          <small>{calendar.data?.available === false ? "nicht eingerichtet" : "Kalender öffnen"}</small>
        </Link>
        <Link className="mia-metric" to="/tasks">
          <span className="mia-metric-icon"><ListChecks size={17} /></span>
          <span className="mia-metric-label">Offene Aufgaben</span>
          <strong>{system ? openTasks : "—"}</strong>
          <small>{runningTasks} laufen · {blockedTasks} blockiert</small>
        </Link>
        <Link className="mia-metric" to="/agents">
          <span className="mia-metric-icon"><Bot size={17} /></span>
          <span className="mia-metric-label">Aktive Agenten</span>
          <strong>{system ? activeAgents : "—"}</strong>
          <small>{system ? `${system.agents.enabled}/${system.agents.total} einsatzbereit` : "wird geladen"}</small>
        </Link>
        <Link className={`mia-metric ${approvals ? "needs-attention" : ""}`} to="/approvals">
          <span className="mia-metric-icon"><ShieldCheck size={17} /></span>
          <span className="mia-metric-label">Freigaben</span>
          <strong>{system ? approvals : "—"}</strong>
          <small>{approvals ? "Entscheidung erforderlich" : "nichts offen"}</small>
        </Link>
        <Link className={`mia-metric ${unread ? "has-news" : ""}`} to="/notifications">
          <span className="mia-metric-icon"><Bell size={17} /></span>
          <span className="mia-metric-label">Meldungen</span>
          <strong>{system ? unread : "—"}</strong>
          <small>{unread ? "ungelesen" : "alles gesehen"}</small>
        </Link>
      </section>

      {criticalMessages.length > 0 && (
        <section className="mia-alerts" aria-label="Kritische Hinweise">
          <div className="mia-section-title">
            <span>Kritische Hinweise</span>
            <small>Nur reale, bestätigte Zustände</small>
          </div>
          <div className="mia-alert-list">
            {criticalMessages.map((message) => (
              <Link key={`${message.path}-${message.text}`} to={message.path} className="mia-alert">
                <span className="dot warn" />
                <span>{message.text}</span>
                <span aria-hidden>→</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <div className="mia-work-grid">
        <Panel
          title="Heute"
          icon={<Calendar size={15} />}
          actions={<Link className="btn sm ghost" to="/calendar">Kalender & Aufgaben</Link>}
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
          title="Laufende Aufträge"
          icon={<ListChecks size={15} />}
          actions={<Link className="btn sm ghost" to="/tasks">Aufgabenverlauf</Link>}
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
      </div>

      <Panel
        title="Agent Control Center"
        icon={<Bot size={15} />}
        actions={<Link className="btn sm ghost" to="/agents">Alle Abteilungen</Link>}
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
    </div>
  );
}
