import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { relative, dateTime } from "@/lib/format";
import { Bot, RefreshCw, Icon, Pencil, Trash2 } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, KeyValue, Modal, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";
import { AgentCard, type Agent } from "./AgentCard";

export default function AgentsPage() {
  const { agentId } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const list = useApi<{ agents: Agent[]; master: any }>("/api/agents", { refreshOn: ["agent.status", "run.started", "run.finished"] });
  const detail = useApi<{ agent: Agent & { instructions: string; runs: any[]; tasks: any[] } }>(agentId ? `/api/agents/${agentId}` : null, { refreshOn: ["agent.status", "run.finished", "task.*"] });
  const tools = useApi<{ tools: any[]; categories: string[] }>("/api/tools");
  const [assign, setAssign] = useState<Agent | null>(null);
  const [form, setForm] = useState({ title: "", description: "", priority: "normal" });
  const [checking, setChecking] = useState(false);
  /** Bearbeiten: vor allem die Anweisungen. Ein gelernter Spezialist hat
   *  gelegentlich einen Satz drin, der so nicht gemeint war — das ist kein
   *  Grund, die ganze Vorführung zu wiederholen. */
  const [edit, setEdit] = useState<{ id: string; name: string; description: string; instructions: string } | null>(null);

  const act = async (a: Agent, action: "enable" | "disable" | "stop") => {
    try { await api.post(`/api/agents/${a.id}/${action}`); list.reload(); detail.reload(); toast({ title: `${a.name}: ${action}`, tone: "ok" }); }
    catch (e: any) { toast({ title: "Action failed", body: e.message, tone: "err" }); }
  };
  const doAssign = async () => {
    if (!assign || !form.title.trim()) return;
    try { const r = await api.post(`/api/agents/${assign.id}/assign`, form); toast({ title: "Task assigned", body: r.task.title, tone: "ok" }); setAssign(null); setForm({ title: "", description: "", priority: "normal" }); nav(`/tasks/${r.task.id}`); }
    catch (e: any) { toast({ title: "Assign failed", body: e.message, tone: "err" }); }
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
  const checkMaster = async () => { setChecking(true); try { const r = await api.post("/api/master/check"); toast({ title: `Provider ${r.health.status}`, body: r.health.detail, tone: r.health.status === "healthy" ? "ok" : "warn" }); list.reload(); } catch (e: any) { toast({ title: "Check failed", body: e.message, tone: "err" }); } finally { setChecking(false); } };

  const master = list.data?.master;
  const a = detail.data?.agent;
  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Agent control center</div><h1>Agents</h1></div>
        <div className="actions">
          {master && <span className="row"><StatusIndicator status={master.online ? "ok" : "err"} label={master.label} /><span className="small muted">{master.provider?.label || "no provider"} · mode {master.mode}</span></span>}
          {can("operator") && <button className="btn sm" onClick={checkMaster} disabled={checking}><RefreshCw style={checking ? { animation: "spin 1s linear infinite" } : undefined} />Check provider</button>}
        </div>
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      {master?.error && <ErrorState error={master.error} />}
      <div className="grid" style={{ gridTemplateColumns: a ? "minmax(0, 1fr) minmax(360px, 480px)" : "1fr" }}>
        <div className="stack" style={{ gap: 16 }}>
          {!list.data ? <Skeleton rows={3} height={60} /> : <div className="grid auto">{list.data.agents.map((ag) => <AgentCard key={ag.id} agent={ag} onClick={() => nav(`/agents/${ag.id}`)} />)}</div>}
          <Panel title="Tool registry" icon={<Bot size={15} />} flush foot={tools.data ? `${tools.data.tools.filter((t) => t.available).length} of ${tools.data.tools.length} tools available · approval required from risk “${(tools.data as any).approval_threshold || "high"}”` : undefined}>
            {!tools.data ? <div className="panel-body"><Skeleton /></div> : (
              <table className="table">
                <thead><tr><th>Tool</th><th>Category</th><th>Risk</th><th>Role</th><th>Availability</th><th className="num">Calls</th></tr></thead>
                <tbody>{tools.data.tools.map((t) => (
                  <tr key={t.name} title={t.description}><td><code>{t.name}</code>{t.requires_approval && <span className="badge warn" style={{ marginLeft: 6 }}>approval</span>}</td><td className="muted">{t.category}</td><td><Badge status={t.risk === "high" || t.risk === "critical" ? "error" : t.risk === "medium" ? "warning" : "ok"}>{t.risk}</Badge></td><td className="muted">{t.permissions[0]}</td>
                    <td>{t.available ? <StatusIndicator status="ok" label="available" /> : <span className="row"><StatusIndicator status="offline" /><span className="small muted">{t.reason}</span></span>}</td><td className="num">{t.calls}{t.errors ? <span style={{ color: "var(--err)" }}> / {t.errors} err</span> : ""}</td></tr>
                ))}</tbody>
              </table>)}
          </Panel>
        </div>
        {agentId && (
          <Panel title={a ? <span className="row"><Icon name={a.icon} size={15} />{a.name}</span> : "Agent"} actions={<button className="btn sm ghost" onClick={() => nav("/agents")}>Close</button>}>
            {detail.error ? <ErrorState error={detail.error} /> : !a ? <Skeleton rows={5} /> : (
              <div className="stack" style={{ gap: 14 }}>
                <div className="row wrap"><Badge status={a.status === "ERROR" ? "error" : a.status === "OFFLINE" ? "offline" : a.status} /><Badge status={a.health} />{!a.enabled && <span className="badge err">disabled</span>}<span className="badge muted">{a.kind}</span></div>
                <p className="small">{a.description}</p>
                <KeyValue items={[["Role", a.role], ["Model / provider", `${a.model || "default"} · ${a.provider || "default"}`], ["Current job", a.current_activity || (a.current_task_id ? <a href={`/tasks/${a.current_task_id}`}>{a.current_task_id}</a> : "—")], ["Last activity", a.last_activity_at ? relative(a.last_activity_at) : "—"], ["Runs / completed / errors", `${a.stats.runs} / ${a.stats.completed} / ${a.stats.errors}`], ["Tool calls", a.stats.tool_calls], ["Last error", a.last_error ? <span style={{ color: "var(--err)" }}>{a.last_error}</span> : "—"]]} />
                <div><div className="label" style={{ marginBottom: 6 }}>Capabilities</div><div className="row wrap" style={{ gap: 6 }}>{a.capabilities.map((c) => <span key={c} className="badge">{c}</span>)}</div></div>
                <div><div className="label" style={{ marginBottom: 6 }}>Tools</div><div className="row wrap" style={{ gap: 6 }}>{a.tools_resolved.length === 0 ? <span className="small muted">none resolved</span> : a.tools_resolved.map((t) => <span key={t.name} className={`badge ${t.available ? "ok" : "muted"}`}>{t.name}</span>)}</div></div>
                <details><summary className="small muted" style={{ cursor: "pointer" }}>Instructions</summary><pre className="md" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{a.instructions}</pre></details>
                {can("operator") && <div className="row wrap">
                  <button className="btn primary sm" onClick={() => setAssign(a)} disabled={!a.enabled}>Assign task</button>
                  <button className="btn sm" onClick={() => nav(`/chat?new=1`)}>Open conversation</button>
                  {["THINKING", "EXECUTING", "WAITING"].includes(a.status) && <button className="btn sm danger" onClick={() => act(a, "stop")}>Stop</button>}
                  {can("admin") && a.kind !== "master" && (a.enabled ? <button className="btn sm" onClick={() => act(a, "disable")}>Disable</button> : <button className="btn sm success" onClick={() => act(a, "enable")}>Enable</button>)}
                  {can("admin") && <button className="btn sm" onClick={() => setEdit({ id: a.id, name: a.name, description: a.description || "", instructions: a.instructions || "" })}><Pencil size={13} />Bearbeiten</button>}
                  {/* Der Master ist nicht löschbar — ohne ihn antwortet nichts
                      mehr. Der Knopf fehlt deshalb ganz, statt eine Absage zu
                      zeigen, die niemand vorher erraten konnte. */}
                  {can("admin") && a.kind !== "master" && <button className="btn sm danger" onClick={() => removeAgent(a)}><Trash2 size={13} />Löschen</button>}
                </div>}
                <div><div className="label" style={{ marginBottom: 6 }}>Task history</div>
                  {a.tasks.length === 0 ? <span className="small muted">no tasks yet</span> : <div className="list">{a.tasks.slice(0, 8).map((t: any) => <a key={t.id} href={`/tasks/${t.id}`} className="list-item clickable" style={{ padding: "6px 0", color: "inherit" }}><Badge status={t.status} /><span className="grow truncate small">{t.title}</span><span className="tiny muted">{relative(t.updated_at)}</span></a>)}</div>}
                </div>
                <div><div className="label" style={{ marginBottom: 6 }}>Recent runs</div>
                  {a.runs.length === 0 ? <span className="small muted">no runs yet</span> : <div className="list">{a.runs.slice(0, 8).map((r: any) => <div key={r.id} className="list-item" style={{ padding: "6px 0" }}><Badge status={r.status} /><span className="grow small truncate">{r.initiated_by} · {r.steps?.length || 0} steps{r.error && <span style={{ color: "var(--err)" }}> · {r.error}</span>}</span><span className="tiny muted">{dateTime(r.started_at)}</span></div>)}</div>}
                </div>
              </div>)}
          </Panel>
        )}
      </div>
      {assign && (
        <Modal title={`Assign task to ${assign.name}`} onClose={() => setAssign(null)} foot={<><button className="btn" onClick={() => setAssign(null)}>Cancel</button><button className="btn primary" onClick={doAssign} disabled={!form.title.trim()}>Start</button></>}>
          <div className="stack">
            <div className="field"><label>Title</label><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} autoFocus /></div>
            <div className="field"><label>Instructions</label><textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="What exactly should the agent do?" /></div>
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
      {!list.data && !list.error && <EmptyState title="Loading agents" />}
    </div>
  );
}
