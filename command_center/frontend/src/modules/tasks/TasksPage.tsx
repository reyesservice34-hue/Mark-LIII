import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { dateTime, relative, time } from "@/lib/format";
import { ListChecks, Plus } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, KeyValue, Modal, Panel, Skeleton } from "@/components/ui";
import { toast } from "@/lib/toast";
import { TaskTimeline } from "./TaskTimeline";

const STATUSES = ["", "active", "QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"];

export default function TasksPage() {
  const { taskId } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [status, setStatus] = useState("active");
  const [q, setQ] = useState("");
  const list = useApi<{ tasks: any[]; counts: Record<string, number> }>(`/api/tasks?limit=200&roots_only=true&status=${status}&q=${encodeURIComponent(q)}`, { refreshOn: ["task.*"] });
  const detail = useApi<{ task: any }>(taskId ? `/api/tasks/${taskId}` : null, { refreshOn: ["task.*", "run.*", "approval.*"] });
  const agents = useApi<{ agents: any[] }>("/api/agents");
  const [creating, setCreating] = useState(params.get("new") === "1");
  const [edit, setEdit] = useState<{ id: string; title: string; description: string; priority: string } | null>(null);
  const [form, setForm] = useState({ title: "", description: "", priority: "normal", assigned_agent: "", start: true });

  const create = async () => {
    try {
      const r = await api.post("/api/tasks", form);
      toast({ title: "Task created", body: r.task.title, tone: "ok" });
      setCreating(false); setParams({}); setForm({ title: "", description: "", priority: "normal", assigned_agent: "", start: true });
      nav(`/tasks/${r.task.id}`);
    } catch (e: any) { toast({ title: "Could not create task", body: e.message, tone: "err" }); }
  };
  const act = async (id: string, action: "cancel" | "retry") => {
    try { await api.post(`/api/tasks/${id}/${action}`); detail.reload(); list.reload(); } catch (e: any) { toast({ title: `${action} failed`, body: e.message, tone: "err" }); }
  };
  // Löschen ist endgültig und braucht deshalb eine Rückfrage, die den Titel
  // nennt — „Aufgabe gelöscht" ohne zu wissen welche, ist keine Bestätigung.
  const remove = async (id: string, title: string) => {
    if (!window.confirm(`Aufgabe „${title}" endgültig löschen? Das lässt sich nicht rückgängig machen.`)) return;
    try {
      await api.del(`/api/tasks/${id}`);
      toast({ title: "Gelöscht", body: title, tone: "ok" });
      nav("/tasks");
      list.reload();
    } catch (e: any) { toast({ title: "Löschen ging nicht", body: e.message, tone: "err" }); }
  };
  const saveEdit = async () => {
    if (!edit?.title.trim()) return;
    try {
      await api.patch(`/api/tasks/${edit.id}`, { title: edit.title.trim(), description: edit.description, priority: edit.priority });
      setEdit(null); detail.reload(); list.reload();
      toast({ title: "Gespeichert", tone: "ok" });
    } catch (e: any) { toast({ title: "Ging nicht", body: e.message, tone: "err" }); }
  };
  const t = detail.data?.task;
  const counts = list.data?.counts;

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Task manager{counts ? ` · ${counts.active} active · ${counts.COMPLETED} completed · ${counts.FAILED} failed` : ""}</div><h1>Tasks</h1></div>
        <div className="actions">
          <input className="input" style={{ width: 200 }} placeholder="Search tasks" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search tasks" />
          <select className="select" style={{ width: 190 }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">{STATUSES.map((s) => <option key={s} value={s}>{s || "All"}</option>)}</select>
          {can("operator") && <button className="btn primary" onClick={() => setCreating(true)}><Plus />New task</button>}
        </div>
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      <div className="grid" style={{ gridTemplateColumns: taskId ? "minmax(0, 1fr) minmax(380px, 520px)" : "1fr" }}>
        <Panel title="Tasks" icon={<ListChecks size={15} />} flush>
          {!list.data ? <div className="panel-body"><Skeleton rows={4} /></div> : list.data.tasks.length === 0 ? <EmptyState icon={<ListChecks size={26} />} title="No tasks match">Tasks appear when JARVIS plans multi-step work or when you create one.</EmptyState> : <TaskTimeline tasks={list.data.tasks} />}
        </Panel>
        {taskId && (
          <Panel title={t ? t.title : "Task"} actions={<button className="btn sm ghost" onClick={() => nav("/tasks")}>Close</button>}>
            {detail.error ? <ErrorState error={detail.error} /> : !t ? <Skeleton rows={6} /> : (
              <div className="stack" style={{ gap: 14 }}>
                <div className="row wrap"><Badge status={t.status} /><span className="badge muted">{t.priority}</span><span className="badge">{t.assigned_agent || "unassigned"}</span>{t.parent_id && <a className="small" href={`/tasks/${t.parent_id}`}>↑ parent task</a>}</div>
                {t.description && <p className="small" style={{ whiteSpace: "pre-wrap" }}>{t.description}</p>}
                <KeyValue items={[["Task ID", <code>{t.id}</code>], ["Created by", t.created_by || "—"], ["Created", dateTime(t.created_at)], ["Started", t.started_at ? dateTime(t.started_at) : "—"], ["Completed", t.completed_at ? dateTime(t.completed_at) : "—"], ["Conversation", t.conversation_id ? <a href={`/chat/${t.conversation_id}`}>open chat</a> : "—"], ["Run", t.run_id ? <code>{t.run_id}</code> : "—"]]} />
                {t.error && <ErrorState error={t.error} />}
                {t.output && <div><div className="label" style={{ marginBottom: 6 }}>Output</div><pre className="md" style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>{t.output}</pre></div>}
                {can("operator") && <div className="row wrap">
                  {!["COMPLETED", "FAILED", "CANCELLED"].includes(t.status) && <button className="btn sm danger" onClick={() => act(t.id, "cancel")}>Cancel</button>}
                  {["FAILED", "CANCELLED", "COMPLETED", "QUEUED"].includes(t.status) && <button className="btn sm" onClick={() => act(t.id, "retry")}>{t.status === "QUEUED" ? "Start" : "Retry"}</button>}
                  {/* Bearbeiten stand nur im Server zur Verfügung, nicht auf
                      der Seite: Ein Tippfehler im Titel war nur zu beheben,
                      indem man die Aufgabe wegwarf und neu anlegte. */}
                  <button className="btn sm" onClick={() => setEdit({ id: t.id, title: t.title, description: t.description || "", priority: t.priority || "normal" })}>Bearbeiten</button>
                  <button className="btn sm danger" onClick={() => remove(t.id, t.title)} title="Aufgabe endgültig löschen">Löschen</button>
                </div>}
                {t.subtasks?.length > 0 && <div><div className="label" style={{ marginBottom: 6 }}>Subtasks</div><div className="panel"><TaskTimeline tasks={t.subtasks} /></div></div>}
                {t.approvals?.length > 0 && <div><div className="label" style={{ marginBottom: 6 }}>Approvals</div>{t.approvals.map((a: any) => <div key={a.id} className="row small" style={{ gap: 8 }}><Badge status={a.status} /><a href={`/approvals/${a.id}`}>{a.action} → {a.target}</a></div>)}</div>}
                {t.files?.length > 0 && <div><div className="label" style={{ marginBottom: 6 }}>Files</div>{t.files.map((f: any) => <div key={f.id} className="small"><a href={`${api.base}/api/files/download?path=${encodeURIComponent(f.path)}`}>{f.path}</a></div>)}</div>}
                <div><div className="label" style={{ marginBottom: 6 }}>Log</div>
                  {t.logs.length === 0 ? <span className="small muted">no log entries</span> : <div className="stack" style={{ gap: 4, maxHeight: 260, overflow: "auto" }}>{t.logs.map((l: any) => <div key={l.id} className="small row" style={{ alignItems: "flex-start" }}><span className="tiny muted num" style={{ flex: "none", width: 64 }}>{time(l.ts)}</span><Badge status={l.level === "WARNING" ? "warning" : l.level === "ERROR" ? "error" : "info"}>{l.level}</Badge><span style={{ wordBreak: "break-word" }}>{l.message}</span></div>)}</div>}
                </div>
                <div className="tiny muted">updated {relative(t.updated_at)}</div>
              </div>)}
          </Panel>
        )}
      </div>
      {edit && (
        <Modal title="Aufgabe bearbeiten" onClose={() => setEdit(null)} foot={<>
          <button className="btn" onClick={() => setEdit(null)}>Abbrechen</button>
          <button className="btn primary" onClick={saveEdit} disabled={!edit.title.trim()}>Speichern</button>
        </>}>
          <div className="stack">
            <div className="field"><label>Titel</label>
              <input className="input" autoFocus value={edit.title}
                onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></div>
            <div className="field"><label>Beschreibung / Anweisung</label>
              <textarea className="textarea" value={edit.description}
                onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></div>
            <div className="field"><label>Dringlichkeit</label>
              <select className="select" value={edit.priority}
                onChange={(e) => setEdit({ ...edit, priority: e.target.value })}>
                <option value="low">niedrig</option><option value="normal">normal</option>
                <option value="high">hoch</option><option value="critical">kritisch</option>
              </select></div>
          </div>
        </Modal>
      )}
      {creating && (
        <Modal title="New task" onClose={() => { setCreating(false); setParams({}); }} foot={<><button className="btn" onClick={() => setCreating(false)}>Cancel</button><button className="btn primary" onClick={create} disabled={!form.title.trim()}>{form.start ? "Create & start" : "Create"}</button></>}>
          <div className="stack">
            <div className="field"><label>Title</label><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} autoFocus /></div>
            <div className="field"><label>Description / instructions</label><textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
            <div className="grid cols-2">
              <div className="field"><label>Priority</label><select className="select" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}><option>low</option><option>normal</option><option>high</option><option>critical</option></select></div>
              <div className="field"><label>Agent</label><select className="select" value={form.assigned_agent} onChange={(e) => setForm({ ...form, assigned_agent: e.target.value })}><option value="">JARVIS (master)</option>{(agents.data?.agents || []).filter((a) => a.kind !== "master").map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></div>
            </div>
            <label className="row small"><input type="checkbox" checked={form.start} onChange={(e) => setForm({ ...form, start: e.target.checked })} /> Start immediately</label>
          </div>
        </Modal>
      )}
    </div>
  );
}
