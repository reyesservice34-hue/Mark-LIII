import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, streamPost, type SSEEvent } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useEvent } from "@/lib/events";
import { useAuth } from "@/lib/auth";
import { toast } from "@/lib/toast";
import { Activity, MessageSquare, Sparkles } from "@/lib/icons";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import type { StatusPayload } from "@/app/shell/TopStatusBar";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";
import { ChatComposer } from "./ChatComposer";
import { ExecutionPanel } from "./ExecutionPanel";
import type { Attachment, Conversation, Message, RunState } from "./types";
import "./chat.css";

const LABELS: Record<string, string> = { planning: "ANALYZING REQUEST", executing: "TASK IN PROGRESS", waiting: "AWAITING APPROVAL", completed: "TASK COMPLETED", failed: "TASK FAILED", cancelled: "STOPPED" };

export default function ChatPage() {
  const { conversationId } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [query, setQuery] = useState("");
  const convs = useApi<{ conversations: Conversation[] }>(`/api/chat/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`, { refreshOn: ["conversation.*"] });
  const status = useApi<StatusPayload>("/api/status", { refreshOn: ["master.status"] });
  const agents = useApi<{ agents: any[] }>("/api/agents");
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
    api.post<{ conversation: Conversation }>("/api/chat/conversations", {}).then((r) => {
      convs.reload();
      nav(`/chat/${r.conversation.id}${q ? `?q=${encodeURIComponent(q)}` : ""}`, { replace: true });
    }).catch((e) => toast({ title: "Could not create conversation", body: e.message, tone: "err" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  // ── live updates that can arrive outside our own stream (desktop, other tabs) ──
  useEvent("message.created", (ev) => { if (ev.data.conversation_id === conversationId) setMessages((ms) => ms.some((m) => m.id === ev.data.id) ? ms : [...ms, ev.data]); }, [conversationId]);
  useEvent("message.updated", (ev) => { if (ev.data.conversation_id === conversationId) setMessages((ms) => ms.map((m) => m.id === ev.data.id ? { ...ev.data, content: ev.data.status === "streaming" && m.content.length > ev.data.content.length ? m.content : ev.data.content } : m)); }, [conversationId]);
  useEvent("chat.delta", (ev) => { if (ev.data.conversation_id === conversationId && !stopRef.current) applyDelta(ev.data.message_id, ev.data.text); }, [conversationId]);
  useEvent("run.activity", (ev) => { if (ev.data.conversation_id === conversationId) addStep(ev.data.run_id || ev.data.parent_run_id, ev.data); }, [conversationId]);
  useEvent("run.status", (ev) => { if (ev.data.conversation_id === conversationId) setRunStatus(ev.data.id, ev.data.status, ev.data.label); }, [conversationId]);
  useEvent("run.finished", (ev) => { if (ev.data.conversation_id === conversationId) setRunStatus(ev.data.id, ev.data.status, LABELS[ev.data.status]); }, [conversationId]);

  const attachRun = (runId: string, messageId: string | null) => {
    setRuns((r) => r[runId] ? r : { ...r, [runId]: { id: runId, status: "planning", label: "ANALYZING REQUEST", agent_id: "master", steps: [], toolCalls: [] } });
    setCurrentRun(runId);
    if (messageId) setMessages((ms) => ms.map((m) => m.id === messageId ? { ...m, run_id: runId } : m));
  };
  const applyDelta = (messageId: string, text: string) => setMessages((ms) => ms.map((m) => m.id === messageId ? { ...m, content: m.content + text, status: "streaming" } : m));
  const addStep = (runId: string, step: any) => setRuns((r) => {
    const run = r[runId] || { id: runId, status: "executing", label: "TASK IN PROGRESS", agent_id: step.agent_id || "master", steps: [], toolCalls: [] };
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
    setBusy(true);
    const stop = streamPost(`/api/chat/conversations/${conversationId}/messages`, { content: text, attachments, agent_id: agentId || null },
      handleStream, (err) => { stopRef.current = null; setBusy(false); if (err) toast({ title: "Message failed", body: err.message, tone: "err" }); convs.reload(); });
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
    }, (err) => { stopRef.current = null; setBusy(false); if (err) toast({ title: "Regenerate failed", body: err.message, tone: "err" }); });
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
  const rename = async (c: Conversation) => { const t = window.prompt("Rename conversation", c.title); if (t && t !== c.title) { await api.patch(`/api/chat/conversations/${c.id}`, { title: t }); convs.reload(); } };
  const remove = async (c: Conversation) => { if (window.confirm(`Delete “${c.title}”? This cannot be undone.`)) { await api.del(`/api/chat/conversations/${c.id}`); convs.reload(); if (c.id === conversationId) nav("/chat"); } };

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
            <button className="btn sm ghost mobile-only" onClick={() => setListOpen((v) => !v)} aria-label="Conversations"><MessageSquare /></button>
            <h2 className="truncate">{list.find((c) => c.id === conversationId)?.title || (conversationId ? "Conversation" : "JARVIS Chat")}</h2>
          </div>
          <div className="row">
            {runActive && <span className="state-line" style={{ padding: 0 }}><span className="dot info live" />{run?.label}</span>}
            {!runActive && !busy && <span className="state-line" style={{ padding: 0, color: masterOffline ? "var(--err)" : "var(--text-3)" }}>{masterOffline ? "MASTER AGENT OFFLINE" : "JARVIS READY"}</span>}
            <button className={`btn icon sm ${panelOpen ? "" : "ghost"}`} onClick={() => setPanelOpen((v) => !v)} title="Execution panel" aria-label="Toggle execution panel"><Activity /></button>
          </div>
        </div>
        {loadErr && <div style={{ padding: 12 }}><ErrorState error={loadErr} /></div>}
        {!conversationId ? (
          <EmptyState icon={<Sparkles size={30} />} title="Talk to JARVIS">
            <div className="stack" style={{ alignItems: "center" }}>
              <span>Pick a conversation or start a new one.</span>
              <button className="btn primary" onClick={newConversation}>New conversation</button>
              <span className="tiny muted">Try: “Check the server.” · “Create a task to review the logs.” · “Ask the coding agent to fix the dashboard.”</span>
            </div>
          </EmptyState>
        ) : loading && messages.length === 0 ? <div style={{ padding: 20 }}><Skeleton rows={4} height={40} /></div> : (
          <>
            {messages.length === 0 && <EmptyState icon={<Sparkles size={26} />} title="JARVIS READY">Say what you need. Multi-step work becomes a task you can follow.</EmptyState>}
            <MessageList messages={messages} runs={runs} onRegenerate={regenerate} onRetry={retry} canAct={can("operator")} />
            <ChatComposer onSend={send} onStop={stop} busy={busy || !!runActive} disabled={!can("operator")} agents={agents.data?.agents || []} initial={prefill}
              offlineHint={masterOffline ? "Master agent offline — configure a provider in Settings" : ""} />
          </>
        )}
      </div>
      {panelOpen && <div className="side chat-col open" style={{ position: "static", padding: 0 }}><ExecutionPanel run={run} onClose={() => setPanelOpen(false)} /></div>}
    </div>
  );
}
