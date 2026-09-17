import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { Paperclip, Send, Square, X } from "@/lib/icons";
import { VoiceControl } from "@/app/voice/VoiceControl";
import { toast } from "@/lib/toast";
import type { Attachment } from "./types";

export function ChatComposer({ onSend, onStop, busy, disabled, agents, initial, offlineHint = "" }: {
  onSend: (text: string, attachments: Attachment[], agentId?: string) => void; onStop: () => void; busy: boolean; disabled: boolean;
  agents: { id: string; name: string; kind: string; enabled: boolean }[]; initial?: string; offlineHint?: string;
}) {
  const [text, setText] = useState(initial || "");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [agent, setAgent] = useState("");
  const [uploading, setUploading] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => { if (initial) setText(initial); }, [initial]);
  useEffect(() => { const el = ta.current; if (el) { el.style.height = "auto"; el.style.height = `${Math.min(200, el.scrollHeight)}px`; } }, [text]);

  const submit = () => {
    const t = text.trim();
    if ((!t && !files.length) || busy || disabled) return;
    onSend(t, files, agent || undefined);
    setText(""); setFiles([]);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } };

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setUploading(true);
    try {
      for (const f of Array.from(list).slice(0, 6)) {
        const form = new FormData(); form.append("file", f);
        const r = await api.upload<{ attachment: Attachment }>("/api/chat/attachments", form);
        setFiles((x) => [...x, r.attachment]);
      }
    } catch (e: any) { toast({ title: "Upload failed", body: e.message, tone: "err" }); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  };

  return (
    <div className="composer" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files); }}>
      {files.length > 0 && <div className="attachments">{files.map((f) => <span key={f.path} className="attachment"><Paperclip size={12} /><span className="truncate">{f.name}</span><span className="muted">{bytes(f.size)}</span><button className="btn icon ghost sm" style={{ width: 18, height: 18 }} onClick={() => setFiles((x) => x.filter((y) => y.path !== f.path))} aria-label="Remove"><X size={12} /></button></span>)}</div>}
      {offlineHint && <div className="row small" style={{ color: "var(--warn)", gap: 6 }} role="status"><span className="dot warn" />{offlineHint}</div>}
      <div className="composer-box">
        <textarea ref={ta} rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} disabled={disabled}
          placeholder={disabled ? "Your role cannot send commands" : "Message JARVIS…"} aria-label="Message" />
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} aria-hidden />
        <button className="btn icon ghost" onClick={() => fileInput.current?.click()} disabled={uploading || disabled} title="Attach files" aria-label="Attach files">{uploading ? <span className="spinner" /> : <Paperclip />}</button>
        <VoiceControl onTranscript={(t) => setText((x) => (x ? `${x} ${t}` : t))} />
        {busy ? <button className="btn danger icon" onClick={onStop} title="Stop generation" aria-label="Stop generation"><Square /></button>
          : <button className="btn primary icon" onClick={submit} disabled={disabled || (!text.trim() && !files.length)} title="Send (Enter)" aria-label="Send"><Send /></button>}
      </div>
      <div className="hint">
        <span className="desktop-only">Enter to send · Shift+Enter newline · drop files to attach</span>
        <label className="row" style={{ gap: 6 }}>route to
          <select className="select" style={{ height: 22, padding: "0 22px 0 6px", fontSize: 11, width: "auto" }} value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Route to agent">
            <option value="">JARVIS (master)</option>
            {agents.filter((a) => a.kind !== "master" && a.enabled).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
      </div>
    </div>
  );
}
