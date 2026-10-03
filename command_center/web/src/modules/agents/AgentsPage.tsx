import { useState } from "react";
import { useNavigate, useParams } from "@/lib/router";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { relative, dateTime } from "@/lib/format";
import { RefreshCw, Icon, Pencil, Trash2 } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, KeyValue, Modal, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";
import { AgentCard, type Agent } from "./AgentCard";

export default function AgentsPage() {
  const { agentId } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const list = useApi<{ agents: Agent[]; master: any }>("/api/agents", { refreshOn: ["agent.status", "run.started", "run.finished"] });
  const detail = useApi<{ agent: Agent & { instructions: string; runs: any[]; tasks: any[] } }>(agentId ? `/api/agents/${agentId}` : null, { refreshOn: ["agent.status", "run.finished", "task.*"] });
  const [assign, setAssign] = useState<Agent | null>(null);
  const [form, setForm] = useState({ title: "", description: "", priority: "normal" });
  const [checking, setChecking] = useState(false);
  /** Bearbeiten: vor allem die Anweisungen. Ein gelernter Spezialist hat
   *  gelegentlich einen Satz drin, der so nicht gemeint war — das ist kein
   *  Grund, die ganze Vorführung zu wiederholen. */
  const [edit, setEdit] = useState<{ id: string; name: string; description: string; instructions: string } | null>(null);

  const act = async (a: Agent, action: "enable" | "disable" | "stop") => {
    try { await api.post(`/api/agents/${a.id}/${action}`); list.reload(); detail.reload(); toast({ title: `${a.name}: ${action === "enable" ? "eingeschaltet" : action === "disable" ? "ausgeschaltet" : "gestoppt"}`, tone: "ok" }); }
    catch (e: any) { toast({ title: "Aktion fehlgeschlagen", body: e.message, tone: "err" }); }
  };
  const doAssign = async () => {
    if (!assign || !form.title.trim()) return;
    try { const r = await api.post(`/api/agents/${assign.id}/assign`, form); toast({ title: "Aufgabe übergeben", body: r.task.title, tone: "ok" }); setAssign(null); setForm({ title: "", description: "", priority: "normal" }); nav(`/tasks/${r.task.id}`); }
    catch (e: any) { toast({ title: "Zuweisen fehlgeschlagen", body: e.message, tone: "err" }); }
  };
  const saveEdit = async () => {
    if (!edit?.name.trim()) return;
    try {
      await api.patch(`/api/agents/${edit.id}`, { name: edit.name.trim(), description: edit.description, instructions: edit.instructions });
      setEdit(null); list.reload(); detail.reload();
      toast({ title: "Gespeichert", tone: "ok" });
    } catch (e: any) { toast({ title: "Ging nicht", body: e.message, tone: "err" }); }
  };
  const removeAgent = async (ag: Agent) => {
    if (!window.confirm(`„${ag.name}" wirklich löschen? Das lässt sich nicht rückgängig machen.`)) return;
    try {
      await api.del(`/api/agents/${ag.id}`);
      toast({ title: `${ag.name} gelöscht`, tone: "ok" });
      nav("/agents"); list.reload();
    } catch (e: any) { toast({ title: "Ging nicht", body: e.message, tone: "err" }); }
  };
  const checkMaster = async () => { setChecking(true); try { const r = await api.post("/api/master/check"); toast({ title: `Anbieter: ${r.health.status === "healthy" ? "gesund" : r.health.status}`, body: r.health.detail, tone: r.health.status === "healthy" ? "ok" : "warn" }); list.reload(); } catch (e: any) { toast({ title: "Prüfung fehlgeschlagen", body: e.message, tone: "err" }); } finally { setChecking(false); } };

  const master = list.data?.master;
  const a = detail.data?.agent;
  const ags = list.data?.agents || [];
  const nActive = ags.filter((x) => ["THINKING", "EXECUTING", "WAITING"].includes(x.status)).length;
  const nError = ags.filter((x) => x.status === "ERROR").length;
  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Agentenleitstand</div><h1>Agenten</h1></div>
        <div className="actions">
          {master && <span className="row"><StatusIndicator status={master.online ? "ok" : "err"} label={master.label} /><span className="small muted">{master.provider?.label || "kein Anbieter"} · Modus {master.mode}</span></span>}
          {can("operator") && <button className="btn sm" onClick={checkMaster} disabled={checking}><RefreshCw style={checking ? { animation: "spin 1s linear infinite" } : undefined} />Anbieter prüfen</button>}
        </div>
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      {master?.error && <ErrorState error={master.error} />}
      <div className="ops-telemetry cols-4" aria-label="Agenten im Überblick">
        <div className="ops-tile"><span className="ops-tile-label">Agenten gesamt</span><strong className="ops-tile-value">{list.data ? ags.length : "—"}</strong><small>{list.data ? `${ags.filter((x) => x.enabled).length} eingeschaltet` : "wird geladen"}</small></div>
        <div className={`ops-tile ${nActive > 0 ? "ok" : ""}`}><span className="ops-tile-label">Aktiv</span><strong className="ops-tile-value">{list.data ? nActive : "—"}</strong><small>denken oder arbeiten gerade</small></div>
        <div className="ops-tile"><span className="ops-tile-label">Bereit</span><strong className="ops-tile-value">{list.data ? ags.filter((x) => x.enabled && x.status === "IDLE").length : "—"}</strong><small>warten auf Aufträge</small></div>
        <div className={`ops-tile ${nError > 0 ? "err" : ""}`}><span className="ops-tile-label">Fehler</span><strong className="ops-tile-value">{list.data ? nError : "—"}</strong><small>{nError > 0 ? "brauchen Aufmerksamkeit" : "alles ruhig"}</small></div>
      </div>
      <div className={a ? "ops-split detail" : ""}>
        <div className="stack" style={{ gap: 16 }}>
          {!list.data ? <Skeleton rows={3} height={60} /> : <div className="grid auto">{list.data.agents.map((ag) => <AgentCard key={ag.id} agent={ag} onClick={() => nav(`/agents/${ag.id}`)} />)}</div>}
        </div>
        {agentId && (
          <Panel title={a ? <span className="row"><Icon name={a.icon} size={15} />{a.name}</span> : "Agent"} actions={<button className="btn sm ghost" onClick={() => nav("/agents")}>Schließen</button>}>
            {detail.error ? <ErrorState error={detail.error} /> : !a ? <Skeleton rows={5} /> : (
              <div className="stack" style={{ gap: 14 }}>
                <div className="row wrap"><Badge status={a.status === "ERROR" ? "error" : a.status === "OFFLINE" ? "offline" : a.status} /><Badge status={a.health} />{!a.enabled && <span className="badge err">deaktiviert</span>}<span className="badge muted">{a.kind}</span></div>
                <p className="small">{a.description}</p>
                <KeyValue items={[["Rolle", a.role], ["Modell / Anbieter", `${a.model || "Standard"} · ${a.provider || "Standard"}`], ["Aktuelle Aufgabe", a.current_activity || (a.current_task_id ? <a href={`/tasks/${a.current_task_id}`}>{a.current_task_id}</a> : "—")], ["Letzte Aktivität", a.last_activity_at ? relative(a.last_activity_at) : "—"], ["Läufe / erledigt / Fehler", `${a.stats.runs} / ${a.stats.completed} / ${a.stats.errors}`], ["Tool calls", a.stats.tool_calls], ["Letzter Fehler", a.last_error ? <span style={{ color: "var(--err)" }}>{a.last_error}</span> : "—"]]} />
                <div><div className="label" style={{ marginBottom: 6 }}>Capabilities</div><div className="row wrap" style={{ gap: 6 }}>{a.capabilities.map((c) => <span key={c} className="badge">{c}</span>)}</div></div>
                <div><div className="label" style={{ marginBottom: 6 }}>Werkzeuge</div><div className="row wrap" style={{ gap: 6 }}>{a.tools_resolved.length === 0 ? <span className="small muted">keine aufgelöst</span> : a.tools_resolved.map((t) => <span key={t.name} className={`badge ${t.available ? "ok" : "muted"}`}>{t.name}</span>)}</div></div>
                <details><summary className="small muted" style={{ cursor: "pointer" }}>Instructions</summary><pre className="md" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{a.instructions}</pre></details>
                {can("operator") && <div className="row wrap">
                  <button className="btn primary sm" onClick={() => setAssign(a)} disabled={!a.enabled}>Aufgabe übergeben</button>
                  <button className="btn sm" onClick={() => nav(`/chat?new=1`)}>Unterhaltung öffnen</button>
                  {["THINKING", "EXECUTING", "WAITING"].includes(a.status) && <button className="btn sm danger" onClick={() => act(a, "stop")}>Stoppen</button>}
                  {can("admin") && a.kind !== "master" && (a.enabled ? <button className="btn sm" onClick={() => act(a, "disable")}>Ausschalten</button> : <button className="btn sm success" onClick={() => act(a, "enable")}>Einschalten</button>)}
                  {can("admin") && <button className="btn sm" onClick={() => setEdit({ id: a.id, name: a.name, description: a.description || "", instructions: a.instructions || "" })}><Pencil size={13} />Bearbeiten</button>}
                  {/* Der Master ist nicht löschbar — ohne ihn antwortet nichts
                      mehr. Der Knopf fehlt deshalb ganz, statt eine Absage zu
                      zeigen, die niemand vorher erraten konnte. */}
                  {can("admin") && a.kind !== "master" && <button className="btn sm danger" onClick={() => removeAgent(a)}><Trash2 size={13} />Löschen</button>}
                </div>}
                <div><div className="label" style={{ marginBottom: 6 }}>Aufgabenverlauf</div>
                  {a.tasks.length === 0 ? <span className="small muted">noch keine Aufgaben</span> : <div className="list">{a.tasks.slice(0, 8).map((t: any) => <a key={t.id} href={`/tasks/${t.id}`} className="list-item clickable" style={{ padding: "6px 0", color: "inherit" }}><Badge status={t.status} /><span className="grow truncate small">{t.title}</span><span className="tiny muted">{relative(t.updated_at)}</span></a>)}</div>}
                </div>
                <div><div className="label" style={{ marginBottom: 6 }}>Letzte Läufe</div>
                  {a.runs.length === 0 ? <span className="small muted">noch keine Läufe</span> : <div className="list">{a.runs.slice(0, 8).map((r: any) => <div key={r.id} className="list-item" style={{ padding: "6px 0" }}><Badge status={r.status} /><span className="grow small truncate">{r.initiated_by} · {r.steps?.length || 0} Schritte{r.error && <span style={{ color: "var(--err)" }}> · {r.error}</span>}</span><span className="tiny muted">{dateTime(r.started_at)}</span></div>)}</div>}
                </div>
              </div>)}
          </Panel>
        )}
      </div>
      {assign && (
        <Modal title={`Aufgabe an ${assign.name} übergeben`} onClose={() => setAssign(null)} foot={<><button className="btn" onClick={() => setAssign(null)}>Abbrechen</button><button className="btn primary" onClick={doAssign} disabled={!form.title.trim()}>Starten</button></>}>
          <div className="stack">
            <div className="field"><label>Titel</label><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} autoFocus /></div>
            <div className="field"><label>Anweisungen</label><textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Was genau soll der Agent tun?" /></div>
            <div className="field"><label>Priority</label><select className="select" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}><option>low</option><option>normal</option><option>high</option><option>critical</option></select></div>
          </div>
        </Modal>
      )}
      {edit && (
        <Modal wide title="Agent bearbeiten" onClose={() => setEdit(null)} foot={<>
          <button className="btn" onClick={() => setEdit(null)}>Abbrechen</button>
          <button className="btn primary" onClick={saveEdit} disabled={!edit.name.trim()}>Speichern</button>
        </>}>
          <div className="stack">
            <div className="field"><label>Name</label>
              <input className="input" autoFocus value={edit.name}
                onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></div>
            <div className="field"><label>Beschreibung</label>
              <input className="input" value={edit.description}
                onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></div>
            <div className="field"><label>Anweisungen</label>
              <textarea className="textarea" style={{ minHeight: 220, fontFamily: "var(--mono)" }}
                value={edit.instructions}
                onChange={(e) => setEdit({ ...edit, instructions: e.target.value })} />
              <span className="small muted">
                Das ist der Text, mit dem dieser Agent in jeden Auftrag geht. Er wirkt sofort,
                ohne Neustart.
              </span></div>
          </div>
        </Modal>
      )}
      {!list.data && !list.error && <EmptyState title="Agenten werden geladen" />}
    </div>
  );
}
