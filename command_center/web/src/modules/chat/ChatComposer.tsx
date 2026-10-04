import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { Bot, Paperclip, Send, ShieldCheck, Square, X } from "@/lib/icons";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { Attachment } from "./types";

interface PickerAgent { id: string; name: string; enabled: boolean; rights: { autonomy: string } }
interface PickerBoard { agents: PickerAgent[]; autonomy_levels: string[] }
const AUTONOMY_LABEL: Record<string, string> = { readonly: "Nur lesen", approval: "Mit Freigabe", full: "Autonom" };
const MASTER_ID = "master";

export function ChatComposer({ onSend, onStop, busy, disabled, initial, offlineHint = "", showPicker = true, queueWhileBusy = false }: {
  onSend: (text: string, attachments: Attachment[], agentId?: string) => void; onStop: () => void; busy: boolean; disabled: boolean;
  initial?: string; offlineHint?: string; showPicker?: boolean;
  /** Senden bleibt erlaubt, während MIA antwortet; der Aufrufer reiht die Nachricht ein. */
  queueWhileBusy?: boolean;
}) {
  const [text, setText] = useState(initial || "");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [agentId, setAgentId] = useState(MASTER_ID);
  const { can } = useAuth();
  const board = useApi<PickerBoard>("/api/agents-office", { refreshOn: ["agent.updated"], enabled: showPicker });
  const agents = (board.data?.agents || []).filter((a) => a.enabled || a.id === MASTER_ID);
  const current = agents.find((a) => a.id === agentId);
  const levels = board.data?.autonomy_levels?.length ? board.data.autonomy_levels : Object.keys(AUTONOMY_LABEL);
  const isAdmin = can("admin");

  const changeAutonomy = async (level: string) => {
    if (!current || level === current.rights.autonomy) return;
    if (level === "full" && !window.confirm(`„${current.name}“ dauerhaft autonom schalten? Alle Aktionen laufen dann ohne Freigabe.`)) return;
    try {
      await api.put(`/api/agents-office/${current.id}/rights`, { autonomy: level });
      toast({ title: `${current.name}: ${AUTONOMY_LABEL[level] || level}`, body: "Gilt dauerhaft für diesen Agenten.", tone: "ok" });
      board.reload();
    } catch (e: any) { toast({ title: "Modus nicht geändert", body: e.message, tone: "err" }); }
  };
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => { if (initial) setText(initial); }, [initial]);
  useEffect(() => { const el = ta.current; if (el) { el.style.height = "auto"; el.style.height = `${Math.min(200, el.scrollHeight)}px`; } }, [text]);

  const submit = () => {
    const t = text.trim();
    if ((!t && !files.length) || (busy && !queueWhileBusy) || disabled) return;
    onSend(t, files, agentId === MASTER_ID ? undefined : agentId);
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
    } catch (e: any) { toast({ title: "Hochladen fehlgeschlagen", body: e.message, tone: "err" }); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  };

  return (
    <div className="composer" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files); }}>
      {files.length > 0 && <div className="attachments">{files.map((f) => <span key={f.path} className="attachment"><Paperclip size={12} /><span className="truncate">{f.name}</span><span className="muted">{bytes(f.size)}</span><button className="btn icon ghost sm" style={{ width: 18, height: 18 }} onClick={() => setFiles((x) => x.filter((y) => y.path !== f.path))} aria-label="Entfernen"><X size={12} /></button></span>)}</div>}
      {offlineHint && <div className="row small" style={{ color: "var(--warn)", gap: 6 }} role="status"><span className="dot warn" />{offlineHint}</div>}
      <div className="composer-box">
        <textarea ref={ta} rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} disabled={disabled}
          placeholder={disabled ? "Deine Rolle darf keine Befehle senden" : busy && queueWhileBusy ? "Weitere Nachricht – geht nach der Antwort an MIA …" : "Nachricht an MIA schreiben …"} aria-label="Nachricht" />
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} aria-hidden />
        <button className="btn icon ghost" onClick={() => fileInput.current?.click()} disabled={uploading || disabled} title="Dateien anhängen" aria-label="Dateien anhängen">{uploading ? <span className="spinner" /> : <Paperclip />}</button>
        {busy && <button className="btn danger icon" onClick={onStop} title="Abbrechen" aria-label="Abbrechen"><Square /></button>}
        {(!busy || queueWhileBusy) && <button className="btn primary icon" onClick={submit} disabled={disabled || (!text.trim() && !files.length)} title={busy ? "Einreihen (Enter)" : "Senden (Enter)"} aria-label="Senden"><Send /></button>}
      </div>
      {showPicker && agents.length > 0 && (
        <div className="composer-bar">
          <label className="pick" title="Agent für die nächste Nachricht">
            <Bot size={14} />
            <select value={current ? agentId : MASTER_ID} onChange={(e) => setAgentId(e.target.value)} aria-label="Agent wählen" disabled={disabled}>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.id === MASTER_ID ? `${a.name} (Standard)` : a.name}</option>)}
            </select>
          </label>
          {current && (
            <label className={`pick mode-${current.rights.autonomy}`} title={isAdmin ? `Autonomie von ${current.name} (gilt dauerhaft)` : "Nur Admins dürfen den Modus ändern"}>
              <ShieldCheck size={14} />
              <select value={current.rights.autonomy} onChange={(e) => changeAutonomy(e.target.value)} aria-label="Autonomie-Modus" disabled={!isAdmin}>
                {levels.map((l) => <option key={l} value={l}>{AUTONOMY_LABEL[l] || l}</option>)}
              </select>
            </label>
          )}
          <span className="muted small desktop-only">Modus gilt dauerhaft für den gewählten Agenten</span>
        </div>
      )}
      <div className="hint">
        <span className="desktop-only">Enter sendet · Umschalt+Enter neue Zeile · Dateien hierher ziehen</span>
        <span>Textchat bleibt ohne Mikrofon. Sprachchat startest du oben ausdrücklich.</span>

      </div>
    </div>
  );
}
