import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { useEvent } from "@/lib/events";
import { api } from "@/lib/api";
import { relative, time } from "@/lib/format";
import { GraduationCap, Play, Plus, Square, Trash2, Sparkles, BookOpen } from "@/lib/icons";
import { EmptyState, ErrorState, KeyValue, Modal, Panel, Skeleton, StatusIndicator, Toggle } from "@/components/ui";
import { toast } from "@/lib/toast";
import "./teach.css";

interface Recording {
  id: string; title: string; goal: string; status: string; started_at: string; ended_at: string | null;
  event_count: number; procedure_id: string | null; events?: any[];
}
interface Procedure {
  id: string; name: string; description: string; goal: string; steps: { text: string; tool: string }[];
  tools: string[]; trigger: any; agent_id: string; enabled: boolean; runs: number; last_run_at: string | null;
  last_status: string; meta: any; recording_id: string | null;
}

export default function TeachPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const recordings = useApi<{ recordings: Recording[]; active: string | null }>("/api/teach/recordings",
    { refreshOn: ["recording.started", "recording.stopped", "procedure.created"] });
  const procedures = useApi<{ procedures: Procedure[] }>("/api/teach/procedures",
    { refreshOn: ["procedure.*"] });
  const [starting, setStarting] = useState(params.get("record") === "1");
  const [form, setForm] = useState({ title: "", goal: "" });
  const [note, setNote] = useState("");
  const [openRec, setOpenRec] = useState<string | null>(null);
  const [openProc, setOpenProc] = useState<Procedure | null>(null);
  const [live, setLive] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  const active = recordings.data?.active || null;
  const detail = useApi<{ recording: Recording; transcript: string }>(
    openRec ? `/api/teach/recordings/${openRec}` : null, { refreshOn: ["recording.event"], debounce: 600 });

  useEvent("recording.event", (ev) => { if (ev.data.recording_id === active) setLive((l) => [...l.slice(-40), ev.data]); }, [active]);
  useEffect(() => { if (!active) setLive([]); }, [active]);

  const start = async () => {
    setBusy(true);
    try {
      const r = await api.post("/api/teach/recordings", { title: form.title, goal: form.goal });
      toast({ title: "Recording", body: "Do the work with MIA now. Everything is captured.", tone: "ok" });
      setStarting(false); setParams({}); setForm({ title: "", goal: "" });
      setOpenRec(r.recording.id); recordings.reload();
    } catch (e: any) { toast({ title: "Could not start", body: e.message, tone: "err" }); }
    finally { setBusy(false); }
  };
  const stop = async (id: string) => {
    await api.post(`/api/teach/recordings/${id}/stop`);
    recordings.reload();
    toast({ title: "Recording stopped", body: "Turn it into a procedure when you are ready.", tone: "ok" });
  };
  const addNote = async (id: string) => {
    if (!note.trim()) return;
    await api.post(`/api/teach/recordings/${id}/note`, { text: note });
    setNote(""); detail.reload();
  };
  const learn = async (id: string, createAgent: boolean) => {
    setBusy(true);
    try {
      const r = await api.post(`/api/teach/recordings/${id}/distill`, { create_agent: createAgent });
      toast({ title: `Learned: ${r.procedure.name}`,
        body: r.agent ? `Specialist “${r.agent.name}” created.` : `${r.procedure.steps.length} steps stored.`,
        tone: "ok" });
      recordings.reload(); procedures.reload(); setOpenProc(r.procedure);
    } catch (e: any) { toast({ title: "Could not learn from it", body: e.message, tone: "err" }); }
    finally { setBusy(false); }
  };

  const removeRec = async (id: string, title: string) => {
    if (!window.confirm(`Aufnahme „${title}" löschen? Eine daraus gelernte Prozedur bleibt bestehen.`)) return;
    try {
      await api.del(`/api/teach/recordings/${id}`);
      if (openRec === id) setOpenRec("");
      recordings.reload();
      toast({ title: "Aufnahme gelöscht", body: title, tone: "ok" });
    } catch (e: any) { toast({ title: "Ging nicht", body: e.message, tone: "err" }); }
  };
  const run = async (p: Procedure) => {
    try {
      const r = await api.post(`/api/teach/procedures/${p.id}/run`, { inputs: {} });
      toast({ title: "Running", body: r.task.title, tone: "ok" });
      procedures.reload();
    } catch (e: any) { toast({ title: "Could not run it", body: e.message, tone: "err" }); }
  };
  const schedule = async (p: Procedure, every: number) => {
    await api.patch(`/api/teach/procedures/${p.id}`,
      { trigger: every ? { type: "schedule", every_seconds: every } : { type: "manual" } });
    procedures.reload();
    setOpenProc(null);
  };
  const remove = async (p: Procedure) => {
    if (!window.confirm(`Delete “${p.name}”? The recording it came from stays.`)) return;
    await api.del(`/api/teach/procedures/${p.id}`);
    procedures.reload(); setOpenProc(null);
  };

  const events = openRec === active && live.length ? live : (detail.data?.recording.events || []);

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Show it once, keep the lesson</div><h1>Teach</h1></div>
        {can("operator") && <div className="actions">
          {active ? <button className="btn danger" onClick={() => stop(active)}><Square />Stop recording</button>
            : <button className="btn primary" onClick={() => setStarting(true)}><Plus />Record a demonstration</button>}
        </div>}
      </div>
      <ErrorState error={recordings.error || procedures.error} retry={() => recordings.reload(false)} />

      {active && (
        <Panel title={<span className="row"><span className="rec-dot" />Recording</span>}
          actions={<button className="btn sm danger" onClick={() => stop(active)}><Square />Stop</button>}>
          <div className="stack">
            <p className="small">Work with MIA as you normally would. What you say, every tool it runs and every
              action on your PC is being written down. Add a note whenever the reason behind a step matters.</p>
            <div className="row">
              <input className="input" placeholder="Note: why this step happens, or a rule to remember"
                value={note} onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") addNote(active); }} />
              <button className="btn" onClick={() => addNote(active)} disabled={!note.trim()}>Add note</button>
              <Link className="btn" to="/chat">Open chat</Link>
            </div>
          </div>
        </Panel>
      )}

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
        <Panel title="Recordings" icon={<GraduationCap size={15} />} flush>
          {!recordings.data ? <div className="panel-body"><Skeleton /></div> :
            recordings.data.recordings.length === 0 ?
              <EmptyState icon={<GraduationCap size={26} />} title="Nothing recorded yet">
                Start a recording, then do the job once with MIA. It writes down what really happened and can
                turn it into a procedure and a specialist.
              </EmptyState> : (
                <div className="list">
                  {recordings.data.recordings.map((r) => (
                    <div key={r.id} className={`list-item clickable ${openRec === r.id ? "active" : ""}`}
                      onClick={() => setOpenRec(r.id)}>
                      <StatusIndicator status={r.status === "recording" ? "running" : r.status === "learned" ? "ok" : "unknown"}
                        live={r.status === "recording"} />
                      <div className="grow" style={{ minWidth: 0 }}>
                        <div className="truncate">{r.title}</div>
                        <div className="tiny muted">{r.event_count} steps · {relative(r.started_at)}
                          {r.procedure_id && " · learned"}</div>
                      </div>
                      {can("operator") && r.status !== "recording" && !r.procedure_id &&
                        <button className="btn sm primary" onClick={(e) => { e.stopPropagation(); learn(r.id, true); }}
                          disabled={busy}><Sparkles />Learn</button>}
                      {/* Eine Aufnahme ist der Rohstoff: was gesagt wurde und jeder
                          Werkzeugaufruf mit Ergebnis. Ein abgebrochener Versuch soll
                          weggeworfen werden können — das Gelernte bleibt davon
                          unberührt, das steht in der Liste darunter. */}
                      {can("operator") && r.status !== "recording" &&
                        <button className="btn sm danger" title="Aufnahme löschen"
                          onClick={(e) => { e.stopPropagation(); removeRec(r.id, r.title); }}
                          disabled={busy}><Trash2 size={13} /></button>}
                    </div>))}
                </div>)}
        </Panel>

        <Panel title="What MIA has learned" icon={<BookOpen size={15} />} flush>
          {!procedures.data ? <div className="panel-body"><Skeleton /></div> :
            procedures.data.procedures.length === 0 ?
              <EmptyState icon={<BookOpen size={26} />} title="No procedures yet">
                A recording becomes a procedure: named steps, the tools they need, and optionally a specialist
                agent that runs it from then on.
              </EmptyState> : (
                <div className="list">
                  {procedures.data.procedures.map((p) => (
                    <div key={p.id} className="list-item clickable" onClick={() => setOpenProc(p)}>
                      <div className="grow" style={{ minWidth: 0 }}>
                        <div className="row" style={{ gap: 8 }}><span className="truncate">{p.name}</span>
                          {p.agent_id && <span className="badge info">{p.agent_id}</span>}
                          {p.trigger?.type === "schedule" && <span className="badge ok">scheduled</span>}
                          {!p.enabled && <span className="badge muted">off</span>}</div>
                        <div className="tiny muted truncate">{p.steps.length} steps · {p.runs} runs
                          {p.last_run_at && ` · last ${relative(p.last_run_at)}`}</div>
                      </div>
                      {can("operator") && <button className="btn sm" onClick={(e) => { e.stopPropagation(); run(p); }}>
                        <Play />Run</button>}
                    </div>))}
                </div>)}
        </Panel>
      </div>

      {openRec && (
        <Panel title={detail.data?.recording.title || "Recording"} icon={<GraduationCap size={15} />}
          actions={<>
            {can("operator") && detail.data?.recording.status !== "recording" && !detail.data?.recording.procedure_id &&
              <>
                <button className="btn sm" onClick={() => learn(openRec, false)} disabled={busy}>Procedure only</button>
                <button className="btn sm primary" onClick={() => learn(openRec, true)} disabled={busy}>
                  <Sparkles />{busy ? "Reading it…" : "Learn + specialist"}</button>
              </>}
            <button className="btn sm ghost" onClick={() => setOpenRec(null)}>Close</button></>} flush>
          {!detail.data ? <div className="panel-body"><Skeleton /></div> : (
            <div className="teach-trace">
              {events.length === 0 && <div className="panel-body small muted">Nothing captured yet.</div>}
              {events.map((e: any, i: number) => (
                <div key={e.id || i} className={`trace-row ${e.kind}`}>
                  <span className="trace-kind">{e.kind}</span>
                  <span className="grow">
                    {e.tool ? <><code>{e.tool}</code>
                      <span className="muted small"> {JSON.stringify(e.params || {}).slice(0, 120)}</span>
                      {e.result && <div className="tiny muted truncate">→ {e.result}</div>}</>
                      : e.text}
                  </span>
                  <span className="tiny muted">{time(e.ts)}</span>
                </div>))}
            </div>)}
        </Panel>
      )}

      {openProc && (
        <Modal title={openProc.name} onClose={() => setOpenProc(null)} wide
          foot={<>
            {can("operator") && <button className="btn danger" onClick={() => remove(openProc)}><Trash2 />Delete</button>}
            {can("operator") && <button className="btn primary" onClick={() => { run(openProc); setOpenProc(null); }}>
              <Play />Run now</button>}
          </>}>
          <div className="stack">
            <p className="small">{openProc.description}</p>
            <KeyValue items={[
              ["Goal", openProc.goal || "—"],
              ["Specialist", openProc.agent_id ? <Link to={`/agents/${openProc.agent_id}`}>{openProc.agent_id}</Link> : "none — the master agent runs it"],
              ["Tools", openProc.tools.length ? openProc.tools.map((t) => <code key={t} style={{ marginRight: 6 }}>{t}</code>) : "—"],
              ["Runs", `${openProc.runs}${openProc.last_status ? ` · last ${openProc.last_status}` : ""}`],
              ["Confidence when learned", openProc.meta?.confidence || "—"],
            ]} />
            {openProc.meta?.needs_setup?.length > 0 && (
              <div className="error-state small">Still needs a connection: {openProc.meta.needs_setup.join(", ")}.
                Those steps will be reported as not done until you configure them.</div>)}
            {openProc.meta?.notes && <p className="small dim">Note: {openProc.meta.notes}</p>}
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Steps</div>
              <ol className="teach-steps">{openProc.steps.map((s, i) => (
                <li key={i}>{s.text}{s.tool && <code style={{ marginLeft: 6 }}>{s.tool}</code>}</li>))}</ol>
            </div>
            {openProc.meta?.inputs?.length > 0 && (
              <div><div className="label" style={{ marginBottom: 6 }}>Asks for</div>
                <div className="row wrap">{openProc.meta.inputs.map((i: any) => (
                  <span key={i.name} className="badge" title={i.description}>{`{${i.name}}`}</span>))}</div></div>)}
            {can("operator") && (
              <div className="row between" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
                <label className="row small" style={{ gap: 8 }}>
                  <Toggle checked={openProc.enabled} label="Enabled"
                    onChange={async (v) => { await api.patch(`/api/teach/procedures/${openProc.id}`, { enabled: v }); procedures.reload(); setOpenProc({ ...openProc, enabled: v }); }} />
                  Enabled
                </label>
                <label className="row small" style={{ gap: 8 }}>Run by itself
                  <select className="select" style={{ width: 170 }}
                    value={openProc.trigger?.type === "schedule" ? String(openProc.trigger.every_seconds) : "0"}
                    onChange={(e) => schedule(openProc, Number(e.target.value))}>
                    <option value="0">only when asked</option>
                    <option value="3600">every hour</option>
                    <option value="21600">every 6 hours</option>
                    <option value="86400">every day</option>
                    <option value="604800">every week</option>
                  </select>
                </label>
              </div>)}
          </div>
        </Modal>
      )}

      {starting && (
        <Modal title="Record a demonstration" onClose={() => { setStarting(false); setParams({}); }}
          foot={<><button className="btn" onClick={() => { setStarting(false); setParams({}); }}>Cancel</button>
            <button className="btn primary" onClick={start} disabled={!form.title.trim() || busy}>Start recording</button></>}>
          <div className="stack">
            <p className="small">Give it a name, then do the job once with MIA. What you say and every tool it
              uses is written down. Afterwards it can turn that into a repeatable procedure and a specialist agent.</p>
            <div className="field"><label>What are you showing it?</label>
              <input className="input" autoFocus value={form.title} placeholder="e.g. Angebot für einen Kunden erstellen"
                onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
            <div className="field"><label>What counts as done? (optional)</label>
              <textarea className="textarea" value={form.goal} placeholder="e.g. Das fertige Angebot liegt als PDF im Workspace"
                onChange={(e) => setForm({ ...form, goal: e.target.value })} /></div>
          </div>
        </Modal>
      )}
    </div>
  );
}
