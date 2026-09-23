import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, streamPost, type SSEEvent } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useEvent } from "@/lib/events";
import { useAuth } from "@/lib/auth";
import { toast } from "@/lib/toast";
import { Activity, MessageSquare, Mic, Sparkles, Volume2, VolumeX } from "@/lib/icons";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import type { StatusPayload } from "@/app/shell/TopStatusBar";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";
import { ChatComposer } from "./ChatComposer";
import { ExecutionPanel } from "./ExecutionPanel";
import type { Attachment, Conversation, Message, RunState } from "./types";
import "./chat.css";
import { closeLine, openLine, sayOnLine, toggleLineMuted, useLive } from "@/app/voice/liveStore";

const LABELS: Record<string, string> = { planning: "ANFRAGE WIRD GEPRÜFT", executing: "AUFGABE WIRD BEARBEITET", waiting: "WARTET AUF FREIGABE", completed: "AUFGABE ERLEDIGT", failed: "AUFGABE FEHLGESCHLAGEN", cancelled: "ABGEBROCHEN", delegated: "AN SPEZIALAGENT ÜBERGEBEN" };

export default function ChatPage() {
  const { conversationId } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [query, setQuery] = useState("");
  const convs = useApi<{ conversations: Conversation[] }>(`/api/chat/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`, { refreshOn: ["conversation.*"] });
  const status = useApi<StatusPayload>("/api/status", { refreshOn: ["master.status"] });
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadErr, setLoadErr] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [currentRun, setCurrentRun] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => window.innerWidth > 1100);
  const [listOpen, setListOpen] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const masterOffline = status.data ? !status.data.master.online : false;
  const live = useLive();

  // ── load conversation ────────────────────────────────────────────────
  useEffect(() => {
    if (!conversationId) { setMessages([]); return; }
    let alive = true;
    setLoading(true); setLoadErr(null);
    api.get<{ messages: Message[]; active_runs: any[] }>(`/api/chat/conversations/${conversationId}`)
      .then((r) => { if (!alive) return; setMessages(r.messages); r.active_runs.forEach((run) => attachRun(run.id, run.message_id)); })
      .catch((e) => alive && setLoadErr(e)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [conversationId]);

  // ── new conversation from ?new=1 (optionally ?q=) ────────────────────
  useEffect(() => {
    if (params.get("new") !== "1") return;
    const q = params.get("q") || "";
    const wantLive = params.get("live") === "1";
    api.post<{ conversation: Conversation }>("/api/chat/conversations", {}).then((r) => {
      convs.reload();
      const next = new URLSearchParams();
      if (q) next.set("q", q);
      if (wantLive) next.set("live", "1");
      nav(`/chat/${r.conversation.id}${next.toString() ? `?${next.toString()}` : ""}`, { replace: true });
    }).catch((e) => toast({ title: "Sitzung konnte nicht erstellt werden", body: e.message, tone: "err" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  // Eine neue oder verlinkte Sitzung bleibt Textchat. Das Mikrofon startet nur
  // nach einem ausdrücklichen Klick auf „Sprachchat starten“.
  useEffect(() => {
    if (!conversationId || params.get("live") !== "1") return;
    setParams({}, { replace: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // ── live updates that can arrive outside our own stream (desktop, other tabs) ──
  useEvent("message.created", (ev) => { if (ev.data.conversation_id === conversationId) setMessages((ms) => ms.some((m) => m.id === ev.data.id) ? ms : [...ms, ev.data]); }, [conversationId]);
  useEvent("message.updated", (ev) => { if (ev.data.conversation_id === conversationId) setMessages((ms) => ms.map((m) => m.id === ev.data.id ? { ...ev.data, content: ev.data.status === "streaming" && m.content.length > ev.data.content.length ? m.content : ev.data.content } : m)); }, [conversationId]);
  useEvent("chat.delta", (ev) => { if (ev.data.conversation_id === conversationId && !stopRef.current) applyDelta(ev.data.message_id, ev.data.text); }, [conversationId]);
  useEvent("run.activity", (ev) => { if (ev.data.conversation_id === conversationId) addStep(ev.data.run_id || ev.data.parent_run_id, ev.data); }, [conversationId]);
  useEvent("run.status", (ev) => { if (ev.data.conversation_id === conversationId) setRunStatus(ev.data.id, ev.data.status, ev.data.label); }, [conversationId]);
  useEvent("run.finished", (ev) => { if (ev.data.conversation_id === conversationId) setRunStatus(ev.data.id, ev.data.status, LABELS[ev.data.status]); }, [conversationId]);

  const attachRun = (runId: string, messageId: string | null) => {
    setRuns((r) => r[runId] ? r : { ...r, [runId]: { id: runId, status: "planning", label: LABELS.planning, agent_id: "master", steps: [], toolCalls: [] } });
    setCurrentRun(runId);
    if (messageId) setMessages((ms) => ms.map((m) => m.id === messageId ? { ...m, run_id: runId } : m));
  };
  const applyDelta = (messageId: string, text: string) => setMessages((ms) => ms.map((m) => m.id === messageId ? { ...m, content: m.content + text, status: "streaming" } : m));
  const addStep = (runId: string, step: any) => setRuns((r) => {
    const run = r[runId] || { id: runId, status: "executing", label: LABELS.executing, agent_id: step.agent_id || "master", steps: [], toolCalls: [] };
    const steps = [...run.steps, { ts: step.ts, kind: step.kind, text: step.text, tool: step.tool, ok: step.ok, agent_id: step.agent_id }];
    let toolCalls = run.toolCalls;
    if (step.kind === "tool_call") toolCalls = [...toolCalls, { tool: step.tool, pending: true }];
    if (step.kind === "tool_result") toolCalls = toolCalls.map((t, i) => i === toolCalls.length - 1 || (t.tool === step.tool && t.pending) ? { ...t, pending: false, ok: step.ok, output: step.text } : t);
    return { ...r, [runId]: { ...run, steps, toolCalls, agent_id: run.agent_id } };
  });
  const setRunStatus = (runId: string, st: string, label?: string) => setRuns((r) => ({ ...r, [runId]: { ...(r[runId] || { id: runId, agent_id: "master", steps: [], toolCalls: [] }), status: st, label: label || r[runId]?.label || LABELS[st] || st } }));

  // ── send ─────────────────────────────────────────────────────────────
  const handleStream = useCallback((ev: SSEEvent) => {
    const d = ev.data;
    switch (ev.type) {
      case "run.accepted": attachRun(d.run_id, d.message_id); break;
      case "chat.delta": applyDelta(d.message_id, d.text); break;
      case "run.activity": addStep(d.run_id || d.parent_run_id, d); break;
      case "run.status": setRunStatus(d.id, d.status, d.label); break;
      case "message.updated": setMessages((ms) => ms.map((m) => m.id === d.id ? { ...d, content: d.status === "streaming" && m.content.length > d.content.length ? m.content : d.content } : m)); break;
      case "run.finished": setRunStatus(d.id, d.status, LABELS[d.status]); break;
      default: break;
    }
  }, []);

  const send = async (text: string, attachments: Attachment[], agentId?: string) => {
    if (!conversationId) return;
    if (live.open && live.conversationId === conversationId && !attachments.length && !agentId) {
      sayOnLine(text);
      return;
    }
    setBusy(true);
    const stop = streamPost(`/api/chat/conversations/${conversationId}/messages`, { content: text, attachments, agent_id: agentId || null },
      handleStream, (err) => { stopRef.current = null; setBusy(false); if (err) toast({ title: "Nachricht fehlgeschlagen", body: err.message, tone: "err" }); convs.reload(); });
    stopRef.current = stop;
  };
  const stop = async () => {
    const runId = currentRun;
    stopRef.current?.();
    if (runId) { try { await api.post(`/api/chat/runs/${runId}/cancel`); } catch { /* run may already be done */ } }
    setBusy(false);
    // refresh authoritative state
    if (conversationId) api.get<{ messages: Message[] }>(`/api/chat/conversations/${conversationId}`).then((r) => setMessages(r.messages)).catch(() => undefined);
  };
  const regenerate = (m: Message) => {
    if (!conversationId) return;
    setBusy(true);
    setMessages((ms) => ms.filter((x) => x.created_at < m.created_at || x.id === m.id).map((x) => x.id === m.id ? { ...x, content: "", status: "streaming", blocks: [] } : x));
    stopRef.current = streamPost(`/api/chat/conversations/${conversationId}/messages/${m.id}/regenerate`, {}, (ev) => {
      if (ev.type === "run.accepted") { setMessages((ms) => ms.filter((x) => x.id !== m.id)); api.get<{ messages: Message[] }>(`/api/chat/conversations/${conversationId}`).then((r) => setMessages(r.messages)); }
      handleStream(ev);
    }, (err) => { stopRef.current = null; setBusy(false); if (err) toast({ title: "Antwort konnte nicht neu erstellt werden", body: err.message, tone: "err" }); });
  };
  const retry = (m: Message) => {
    const idx = messages.findIndex((x) => x.id === m.id);
    const user = [...messages.slice(0, idx + 1)].reverse().find((x) => x.role === "user");
    if (user) send(user.content, user.meta?.attachments || []);
  };

  // auto-send ?q= once the conversation is loaded
  const [prefill, setPrefill] = useState("");
  useEffect(() => {
    const q = params.get("q");
    if (q && conversationId && !loading && !busy) {
      setParams({}, { replace: true });
      setPrefill("");
      send(q, []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, loading]);

  const newConversation = () => nav("/chat?new=1");
  const rename = async (c: Conversation) => { const t = window.prompt("Sitzung umbenennen", c.title); if (t && t !== c.title) { await api.patch(`/api/chat/conversations/${c.id}`, { title: t }); convs.reload(); } };
  const remove = async (c: Conversation) => { if (window.confirm(`Sitzung „${c.title}“ wirklich löschen? Das kann nicht rückgängig gemacht werden.`)) { await api.del(`/api/chat/conversations/${c.id}`); convs.reload(); if (c.id === conversationId) nav("/chat"); } };

  const run = currentRun ? runs[currentRun] : null;
  const runActive = run && ["planning", "executing", "waiting", "delegated"].includes(run.status);
  const list = convs.data?.conversations || [];

  return (
    <div className={`chat-layout ${panelOpen ? "" : "no-panel"}`}>
      <div className={`side chat-col ${listOpen ? "open" : ""}`}>
        <ConversationList items={list} activeId={conversationId} onNew={newConversation} onSearch={setQuery} query={query} onRename={rename} onDelete={remove} />
      </div>
      <div className="panel chat-col">
        <div className="panel-head" style={{ padding: "8px 12px" }}>
          <div className="row grow" style={{ minWidth: 0 }}>
            <button className="btn sm ghost mobile-only" onClick={() => setListOpen((v) => !v)} aria-label="Sitzungsverläufe"><MessageSquare /></button>
            <h2 className="truncate">{(() => {
              const title = list.find((c) => c.id === conversationId)?.title;
              return title === "New conversation" ? "Neue Sitzung" : title || (conversationId ? "Sitzung" : "MIA Sitzung");
            })()}</h2>
          </div>
          <div className="row">
            {live.open && <span className="state-line" style={{ padding: 0 }}><span className="dot ok live" />SPRACHCHAT · {live.muted ? "STUMM" : "AKTIV"}</span>}
            {runActive && <span className="state-line" style={{ padding: 0 }}><span className="dot info live" />{LABELS[run?.status || ""] || run?.label}</span>}
            {!runActive && !busy && <span className="state-line" style={{ padding: 0, color: masterOffline ? "var(--err)" : "var(--text-3)" }}>{masterOffline ? "MASTER-AGENT OFFLINE" : "MIA BEREIT"}</span>}
            {conversationId && live.open && live.conversationId === conversationId && <button className={`btn sm ${live.muted ? "primary" : "ghost"}`} onClick={toggleLineMuted} title={live.muted ? "Mikrofon einschalten" : "Mikrofon stummschalten"}>{live.muted ? <VolumeX size={14} /> : <Volume2 size={14} />}{live.muted ? "Stumm" : "Mikrofon an"}</button>}
            {conversationId && <button className={`btn sm ${live.open && live.conversationId === conversationId ? "danger" : "ghost"}`} onClick={() => live.open ? void closeLine() : void openLine(conversationId).catch((e: any) => toast({ title: "Sprachchat konnte nicht gestartet werden", body: e?.message, tone: "err" }))} title={live.open ? "Sprachchat beenden" : "Sprachchat mit Mikrofon starten"}><Mic size={14} />{live.open && live.conversationId === conversationId ? "Sprachchat beenden" : "Sprachchat starten"}</button>}
            <button className={`btn icon sm ${panelOpen ? "" : "ghost"}`} onClick={() => setPanelOpen((v) => !v)} title="Arbeitsschritte anzeigen" aria-label="Arbeitsschritte ein- oder ausblenden"><Activity /></button>
          </div>
        </div>
        {loadErr && <div style={{ padding: 12 }}><ErrorState error={loadErr} /></div>}
        {!conversationId ? (
          <EmptyState icon={<Sparkles size={30} />} title="Mit MIA sprechen">
            <div className="stack" style={{ alignItems: "center" }}>
              <span>Wähle einen Sitzungsverlauf oder starte eine neue Sitzung.</span>
              <button className="btn primary" onClick={newConversation}><MessageSquare size={14} />Live-Chat starten</button>
              <span className="tiny muted">Zum Beispiel: „Prüfe den Server.“ · „Erstelle eine Aufgabe zur Protokollprüfung.“ · „Repariere das Dashboard.“</span>
            </div>
          </EmptyState>
        ) : loading && messages.length === 0 ? <div style={{ padding: 20 }}><Skeleton rows={4} height={40} /></div> : (
          <>
            {messages.length === 0 && <EmptyState icon={<Sparkles size={26} />} title="MIA BEREIT">Sag oder schreib, was du brauchst. Mehrstufige Arbeit wird als Aufgabe sichtbar.</EmptyState>}
            <MessageList messages={messages} runs={runs} onRegenerate={regenerate} onRetry={retry} canAct={can("operator")} />
            <ChatComposer onSend={send} onStop={stop} busy={busy || !!runActive} disabled={!can("operator")} initial={prefill}
              offlineHint={masterOffline ? "Master-Agent offline – bitte den KI-Anbieter unter Einstellungen prüfen" : ""} />
          </>
        )}
      </div>
      {panelOpen && <div className="side chat-col open" style={{ position: "static", padding: 0 }}><ExecutionPanel run={run} onClose={() => setPanelOpen(false)} /></div>}
    </div>
  );
}
