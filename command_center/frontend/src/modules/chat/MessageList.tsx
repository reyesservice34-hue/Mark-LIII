import { memo, useEffect, useMemo, useRef, useState } from "react";
import { renderMarkdown, handleMarkdownClick } from "@/lib/markdown";
import { bytes, time } from "@/lib/format";
import { Copy, RotateCcw, Paperclip, Sparkles, Check, ChevronDown, ChevronUp } from "@/lib/icons";
import { api } from "@/lib/api";
import type { Message, RunState } from "./types";

function Bubble({ m, run, onRegenerate, onRetry, canAct }: { m: Message; run?: RunState; onRegenerate?: () => void; onRetry?: () => void; canAct: boolean }) {
  const [copied, setCopied] = useState(false);
  const [showSteps, setShowSteps] = useState(false);
  const streaming = m.status === "streaming";
  const html = useMemo(() => (m.role === "assistant" ? renderMarkdown(m.content) : ""), [m.content, m.role]);
  const steps = run?.steps?.length ? run.steps : m.blocks || [];
  const toolChips = run?.toolCalls || steps.filter((s) => s.kind === "tool_result").map((s) => ({ tool: s.tool || "", ok: s.ok, pending: false, output: "" }));
  const agentLabel = m.meta?.agent_id === "jarvis" ? "MIA" : m.meta?.agent_id;
  const copy = () => navigator.clipboard?.writeText(m.content).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); });
  return (
    <div className={`msg ${m.role}`}>
      <div className="msg-avatar" aria-hidden>{m.role === "assistant" ? <Sparkles size={15} /> : m.role === "user" ? "DU" : "SYS"}</div>
      <div className="msg-body">
        {m.role === "user" && m.meta?.attachments?.length ? (
          <div className="attachments">{m.meta.attachments.map((a) => (
            <a key={a.path} className="attachment" href={`${api.base}/api/files/download?path=${encodeURIComponent(a.path)}`} target="_blank" rel="noreferrer">
              {a.mime?.startsWith("image/") ? <img src={`${api.base}/api/files/download?path=${encodeURIComponent(a.path)}&inline=1`} alt={a.name} style={{ height: 40, borderRadius: 4 }} /> : <Paperclip size={12} />}
              <span className="truncate">{a.name}</span><span className="muted">{bytes(a.size)}</span>
            </a>))}</div>) : null}
        {toolChips.length > 0 && <div className="tool-strip">{toolChips.map((t, i) => <span key={i} className={`tool-chip ${t.pending ? "pending" : t.ok === false ? "err" : ""}`} title={t.output || ""}>{t.pending ? "⋯" : t.ok === false ? "✕" : "✓"} {t.tool}</span>)}</div>}
        {(m.content || streaming) && (
          <div className={`bubble ${m.status === "error" ? "error" : ""}`}>
            {m.role === "assistant" ? <div className="md" dangerouslySetInnerHTML={{ __html: html }} onClick={handleMarkdownClick} /> : m.content}
            {streaming && <span className="cursor" aria-label="Antwort wird erstellt" />}
          </div>
        )}
        {m.status === "error" && <div className="error-state small">{m.meta?.error || "MIA konnte diese Anfrage nicht abschließen."}{canAct && onRetry && <button className="btn sm" onClick={onRetry}>Erneut versuchen</button>}</div>}
        {m.status === "stopped" && <div className="tiny muted">Antwort wurde abgebrochen.</div>}
        <div className="msg-meta">
          <span>{time(m.created_at)}</span>
          {agentLabel && agentLabel !== "master" && <span>· {agentLabel}</span>}
          {m.meta?.via === "gateway" && <span>· über Desktop</span>}
          {m.meta?.usage?.output_tokens ? <span>· {m.meta.usage.input_tokens}↑ {m.meta.usage.output_tokens}↓ tok</span> : null}
          {m.content && <button className="btn icon ghost sm" onClick={copy} title="Kopieren" aria-label="Nachricht kopieren">{copied ? <Check /> : <Copy />}</button>}
          {m.role === "assistant" && !streaming && canAct && onRegenerate && <button className="btn icon ghost sm" onClick={onRegenerate} title="Antwort neu erstellen" aria-label="Antwort neu erstellen"><RotateCcw /></button>}
          {steps.length > 0 && <button className="btn ghost sm" onClick={() => setShowSteps((v) => !v)}>{showSteps ? <ChevronUp /> : <ChevronDown />}{steps.length} Schritte</button>}
        </div>
        {showSteps && <div className="panel" style={{ padding: "6px 0" }}>{steps.map((s, i) => <div key={i} className={`exec-step ${s.kind} ${s.ok === false ? "err" : ""}`}><span className="pin" /><span><span className="t">{s.text}</span> <span className="ts">{time(s.ts)}</span></span></div>)}</div>}
      </div>
    </div>
  );
}

const MemoBubble = memo(Bubble);

export function MessageList({ messages, runs, onRegenerate, onRetry, canAct }: { messages: Message[]; runs: Record<string, RunState>; onRegenerate: (m: Message) => void; onRetry: (m: Message) => void; canAct: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => { stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => { if (stick.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [messages]);
  return (
    <div className="messages" ref={ref} role="log" aria-live="polite">
      {messages.map((m) => <MemoBubble key={m.id} m={m} run={m.run_id ? runs[m.run_id] : undefined} canAct={canAct}
        onRegenerate={m.role === "assistant" ? () => onRegenerate(m) : undefined} onRetry={() => onRetry(m)} />)}
    </div>
  );
}
