/**
 * Erweiterungen — alles, was MIA über seinen eigenen Quelltext hinaus kann.
 *
 * Drei Arten auf einer Seite: fremde Werkzeugserver (MCP), Fähigkeiten als
 * Text, und was er sich selbst geschrieben hat. Nichts davon ist an einen
 * Anbieter gebunden — Werkzeuge gehen als Deklaration an das Modell, das
 * gerade denkt, Fähigkeiten sind Text. Das gilt für Anthropic, OpenAI, Gemini
 * und ein lokales Modell gleichermaßen.
 */
import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Puzzle, Plug, BookOpen, Code, Plus, RefreshCw, Trash2 } from "@/lib/icons";
import { Panel, Stat, Modal, Badge, EmptyState, ErrorState, Skeleton, Toggle } from "@/components/ui";

interface Server {
  id: string; name: string; slug: string; url: string; enabled: boolean;
  status: string; detail: string; tool_count: number; tools: string[];
  requires_approval: boolean; has_token: boolean; checked_at: string | null;
}
interface Skill {
  name: string; title: string; description: string; content: string;
  enabled: boolean; uses: number; length: number;
}
interface Overview {
  servers: Server[];
  skills: Skill[];
  self_tools: any[];
  totals: { mcp_tools: number; mcp_online: number; skills: number; self_active: number; tools_total: number };
}

const BEISPIEL_SKILL = `---
name: angebot-schreiben
description: Wie ein Angebot für Reyes Service aufgebaut wird — Struktur, Tonfall, Pflichtangaben
---

# Angebot schreiben

1. Anrede mit Namen, kurzer Bezug auf den Termin vor Ort.
2. Leistungen einzeln mit Menge, Einheit und Einzelpreis.
3. ...
`;

export default function ExtensionsPage() {
  const { data, error, loading, reload } = useApi<Overview>("/api/extensions");
  const [addServer, setAddServer] = useState(false);
  const [editSkill, setEditSkill] = useState<Skill | null>(null);
  const [newSkill, setNewSkill] = useState(false);
  const [busy, setBusy] = useState("");

  const fail = (e: any) => toast({ title: "Ging nicht", body: e?.message || String(e), tone: "err" });

  const check = async (s: Server) => {
    setBusy(s.id);
    try {
      await api.post(`/api/extensions/servers/${s.id}/check`);
      reload();
    } catch (e) { fail(e); } finally { setBusy(""); }
  };

  const toggleServer = async (s: Server, enabled: boolean) => {
    try { await api.patch(`/api/extensions/servers/${s.id}`, { enabled }); reload(); } catch (e) { fail(e); }
  };

  const removeServer = async (s: Server) => {
    if (!window.confirm(`Server „${s.name}" entfernen? Seine Werkzeuge stehen danach nicht mehr zur Verfügung.`)) return;
    try { await api.del(`/api/extensions/servers/${s.id}`); reload(); } catch (e) { fail(e); }
  };

  const removeSkill = async (s: Skill) => {
    if (!window.confirm(`Fähigkeit „${s.name}" löschen?`)) return;
    try { await api.del(`/api/extensions/skills/${s.name}`); reload(); } catch (e) { fail(e); }
  };

  if (error) return <div className="page"><ErrorState error={error} retry={reload} /></div>;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1><Puzzle size={20} /> Erweiterungen</h1>
          <p className="muted">Fremde Werkzeugserver, Fähigkeiten und selbstgeschriebene Werkzeuge.
            Alles davon steht jedem Modell zur Verfügung, das gerade denkt.</p>
        </div>
      </header>

      {loading && !data ? <Skeleton rows={6} height={18} /> : data && (
        <>
          <div className="grid cols-4">
            <Stat label="Werkzeuge insgesamt" value={data.totals.tools_total} />
            <Stat label="aus MCP-Servern" value={data.totals.mcp_tools}
              sub={`${data.totals.mcp_online} von ${data.servers.length} Servern erreichbar`} />
            <Stat label="Fähigkeiten" value={data.totals.skills} />
            <Stat label="selbst geschrieben" value={data.totals.self_active} sub="freigegeben" />
          </div>

          <Panel title="MCP-Server" icon={<Plug size={15} />}
            actions={<button className="btn sm primary" onClick={() => setAddServer(true)}><Plus size={14} />Server hinzufügen</button>}
            foot="Model Context Protocol — derselbe Stecker, den auch Claude benutzt. Werkzeuge daraus heißen mcp.<server>.<werkzeug> und laufen durch dieselbe Genehmigung wie alles andere.">
            {data.servers.length === 0 ? (
              <EmptyState icon={<Plug size={22} />} title="Noch kein Server eingetragen">
                Trage die Adresse eines MCP-Servers ein. Seine Werkzeuge stehen MIA dann sofort zur
                Verfügung — ohne dass hier eine Zeile Code dazukommt.
              </EmptyState>
            ) : (
              <table className="table">
                <thead><tr><th>Server</th><th>Adresse</th><th>Zustand</th><th>Werkzeuge</th><th>aktiv</th><th /></tr></thead>
                <tbody>
                  {data.servers.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <strong>{s.name}</strong>
                        <div className="small muted">{s.requires_approval ? "fragt vor jeder Ausführung" : "läuft ohne Rückfrage"}
                          {s.has_token ? " · Token hinterlegt" : ""}</div>
                      </td>
                      <td className="small mono">{s.url}</td>
                      <td>
                        <Badge status={s.status === "healthy" ? "ok" : s.status === "offline" ? "err" : "warn"}>
                          {s.status === "healthy" ? "verbunden" : s.status === "offline" ? "offline" : "ungeprüft"}
                        </Badge>
                        <div className="small muted">{s.detail}</div>
                      </td>
                      <td>{s.tool_count}</td>
                      <td><Toggle checked={s.enabled} onChange={(v) => toggleServer(s, v)} /></td>
                      <td className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        <button className="btn sm" disabled={busy === s.id} onClick={() => check(s)}>
                          <RefreshCw size={13} />{busy === s.id ? "prüfe …" : "prüfen"}
                        </button>
                        <button className="btn sm danger" onClick={() => removeServer(s)}><Trash2 size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Fähigkeiten" icon={<BookOpen size={15} />}
            actions={<button className="btn sm primary" onClick={() => setNewSkill(true)}><Plus size={14} />Fähigkeit anlegen</button>}
            foot="Anleitungen als Text. Im Systemtext steht nur Name und Zweck — den vollen Text schlägt er erst auf, wenn eine Aufgabe danach verlangt.">
            {data.skills.length === 0 ? (
              <EmptyState icon={<BookOpen size={22} />} title="Noch keine Fähigkeit hinterlegt">
                Schreib auf, wie etwas in deinem Betrieb läuft — ein Angebot, eine Baustellenübergabe,
                eine Reklamation. Er hält sich daran, statt es jedes Mal neu zu erfinden.
              </EmptyState>
            ) : (
              <table className="table">
                <thead><tr><th>Name</th><th>Wofür</th><th>Länge</th><th>benutzt</th><th>aktiv</th><th /></tr></thead>
                <tbody>
                  {data.skills.map((s) => (
                    <tr key={s.name}>
                      <td><button className="link" onClick={() => setEditSkill(s)}>{s.name}</button></td>
                      <td className="small muted">{s.description}</td>
                      <td className="small">{s.length.toLocaleString("de-DE")} Zeichen</td>
                      <td>{s.uses}</td>
                      <td><Toggle checked={s.enabled} onChange={async (v) => {
                        try { await api.patch(`/api/extensions/skills/${s.name}?enabled=${v}`); reload(); } catch (e) { fail(e); }
                      }} /></td>
                      <td style={{ textAlign: "right" }}>
                        <button className="btn sm danger" onClick={() => removeSkill(s)}><Trash2 size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Selbstgeschriebene Werkzeuge" icon={<Code size={15} />}
            foot="Was er sich selbst gebaut hat. Freigegeben wird über die Genehmigungen — hier steht nur, was es gibt.">
            {data.self_tools.length === 0 ? (
              <EmptyState icon={<Code size={22} />} title="Er hat sich noch nichts geschrieben">
                Bitte ihn im Gespräch um ein Werkzeug, das ihm fehlt. Er schreibt es, prüft es und fragt
                dich, bevor es scharf wird.
              </EmptyState>
            ) : (
              <table className="table">
                <thead><tr><th>Name</th><th>Beschreibung</th><th>Zustand</th></tr></thead>
                <tbody>
                  {data.self_tools.map((t: any) => (
                    <tr key={t.name}>
                      <td className="mono small">{t.name}</td>
                      <td className="small muted">{t.description}</td>
                      <td><Badge status={t.status === "active" ? "ok" : "warn"}>{t.status}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      )}

      {addServer && <AddServerModal onClose={() => setAddServer(false)} onDone={() => { setAddServer(false); reload(); }} />}
      {(newSkill || editSkill) && (
        <SkillModal skill={editSkill} onClose={() => { setNewSkill(false); setEditSkill(null); }}
          onDone={() => { setNewSkill(false); setEditSkill(null); reload(); }} />
      )}
    </div>
  );
}

function AddServerModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [approval, setApproval] = useState(true);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ server: Server }>("/api/extensions/servers", { name, url, token, requires_approval: approval });
      if (r.server.status !== "healthy") {
        toast({ title: "Eingetragen, aber nicht erreichbar", body: r.server.detail, tone: "warn" });
      } else {
        toast({ title: "Verbunden", body: `${r.server.tool_count} Werkzeuge stehen bereit.`, tone: "ok" });
      }
      onDone();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e?.message, tone: "err" });
    } finally { setBusy(false); }
  };

  return (
    <Modal title="MCP-Server hinzufügen" onClose={onClose} foot={<>
      <button className="btn" onClick={onClose}>Abbrechen</button>
      <button className="btn primary" onClick={save} disabled={busy || !name || !url}>
        {busy ? "Verbinde …" : "Verbinden"}
      </button>
    </>}>
      <div className="field"><label>Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="z. B. github" />
        <span className="small muted">Kurz und klein geschrieben — daraus werden die Werkzeugnamen: mcp.github.…</span>
      </div>
      <div className="field"><label>Adresse</label>
        <input className="input" value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="https://beispiel.de/mcp" />
        <span className="small muted">Der HTTP-Endpunkt des Servers. Server, die als Programm auf diesem
          Rechner gestartet werden müssten (stdio), gehen hier nicht.</span>
      </div>
      <div className="field"><label>Token (falls verlangt)</label>
        <input className="input" type="password" value={token} onChange={(e) => setToken(e.target.value)}
          autoComplete="off" />
        <span className="small muted">Bleibt auf dem Server und wird nie wieder ausgeliefert.</span>
      </div>
      <Toggle checked={approval} onChange={setApproval}
        label="Vor jeder Ausführung nachfragen (empfohlen: fremder Code auf einem fremden Rechner)" />
    </Modal>
  );
}

function SkillModal({ skill, onClose, onDone }: { skill: Skill | null; onClose: () => void; onDone: () => void }) {
  const [content, setContent] = useState(skill ? skill.content : BEISPIEL_SKILL);
  const [name, setName] = useState(skill?.name || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await api.post("/api/extensions/skills", { name, content });
      onDone();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e?.message, tone: "err" });
    } finally { setBusy(false); }
  };

  return (
    <Modal wide title={skill ? `Fähigkeit: ${skill.name}` : "Fähigkeit anlegen"} onClose={onClose} foot={<>
      <button className="btn" onClick={onClose}>Abbrechen</button>
      <button className="btn primary" onClick={save} disabled={busy || !content.trim()}>
        {busy ? "Speichere …" : "Speichern"}
      </button>
    </>}>
      <div className="field"><label>Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)}
          placeholder="angebot-schreiben" disabled={!!skill} />
        <span className="small muted">Kleinbuchstaben, Ziffern, Bindestrich. Steht ein Kopf mit
          „name:" im Text, gilt der.</span>
      </div>
      <div className="field"><label>Anleitung</label>
        <textarea className="input" style={{ minHeight: 320, fontFamily: "var(--mono, monospace)", fontSize: 13 }}
          value={content} onChange={(e) => setContent(e.target.value)} />
        <span className="small muted">Markdown. Ein YAML-Kopf mit name und description wird gelesen —
          so kannst du eine SKILL.md aus einem anderen Werkzeug einfach einfügen.</span>
      </div>
    </Modal>
  );
}
