import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { dateTime, ms, relative } from "@/lib/format";
import { Workflow, RefreshCw, Play, Eye, RotateCcw, Pencil, Trash2 } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Modal, Panel, Skeleton, StatusIndicator, Toggle } from "@/components/ui";
import { toast } from "@/lib/toast";

// Anzeigenamen auf Deutsch. Nur die Beschriftung ändert sich, die Werte der API bleiben.
const AUSLOESER: Record<string, string> = { webhook: "Webhook", schedule: "Zeitplan", manual: "Manuell", executeworkflow: "Von anderem Workflow", trigger: "Auslöser" };
const MODUS: Record<string, string> = { trigger: "Auslöser", webhook: "Webhook", manual: "Manuell", retry: "Wiederholung", cli: "Kommandozeile", error: "Fehler-Workflow", integrated: "Integriert", internal: "Intern", evaluation: "Test" };
const LAUF: Record<string, string> = { success: "Erfolg", error: "Fehler", crashed: "Abgestürzt", canceled: "Abgebrochen", waiting: "Wartend", running: "Läuft", new: "Neu" };
const de = (map: Record<string, string>, v?: string) => map[(v || "").toLowerCase()] ?? v ?? "—";

export default function WorkflowsPage() {
  const { can } = useAuth();
  const list = useApi<{ workflows: any[]; providers: any[]; configured: boolean; stats: any }>("/api/workflows", { refreshOn: ["workflow.*"] });
  const [selected, setSelected] = useState<any>(null);
  const runs = useApi<{ runs: any[] }>(selected ? `/api/workflows/runs?workflow_id=${encodeURIComponent(selected.id)}` : "/api/workflows/runs?limit=30", { refreshOn: ["workflow.*"] });
  const [inspect, setInspect] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);
  // Bearbeiten (Umbenennen) und Löschen: je ein eigenes Fenster, damit nichts aus Versehen passiert.
  const [edit, setEdit] = useState<{ w: any; name: string } | null>(null);
  const [del, setDel] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const saveName = async () => {
    const name = edit?.name.trim();
    if (!edit || !name) return;
    setBusy(true);
    try {
      await api.patch(`/api/workflows/${encodeURIComponent(edit.w.id)}`, { name });
      toast({ title: "Umbenannt", body: name, tone: "ok" });
      setEdit(null); list.reload();
    } catch (e: any) { toast({ title: "Umbenennen fehlgeschlagen", body: e.message, tone: "err" }); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!del) return;
    setBusy(true);
    try {
      await api.del(`/api/workflows/${encodeURIComponent(del.id)}`);
      toast({ title: "Workflow gelöscht", body: del.name, tone: "ok" });
      if (selected?.id === del.id) setSelected(null);
      setDel(null); list.reload(); runs.reload();
    } catch (e: any) { toast({ title: "Löschen fehlgeschlagen", body: e.message, tone: "err" }); }
    finally { setBusy(false); }
  };

  const sync = async () => { setSyncing(true); try { await api.get("/api/workflows?refresh=true"); list.reload(); runs.reload(); } finally { setSyncing(false); } };
  const trigger = async (w: any) => {
    try { const r = await api.post(`/api/workflows/${encodeURIComponent(w.id)}/trigger`, { payload: {} }); toast({ title: r.result.ok ? "Workflow gestartet" : "Starten fehlgeschlagen", body: `HTTP ${r.result.http_status}`, tone: r.result.ok ? "ok" : "err" }); }
    catch (e: any) { toast({ title: "Starten fehlgeschlagen", body: e.message, tone: "err" }); }
  };
  const setActive = async (w: any, active: boolean) => {
    try { await api.post(`/api/workflows/${encodeURIComponent(w.id)}/active`, { active }); list.reload(); }
    catch (e: any) { toast({ title: "Zustand ließ sich nicht ändern", body: e.message, tone: "err" }); }
  };
  const open = async (run: any) => {
    try { const r = await api.get(`/api/workflows/runs/${encodeURIComponent(run.id)}`); setInspect(r.run); }
    catch (e: any) { toast({ title: "Ausführung ließ sich nicht laden", body: e.message, tone: "err" }); }
  };
  const retry = async (run: any) => {
    try { await api.post(`/api/workflows/runs/${encodeURIComponent(run.id)}/retry`); toast({ title: "Wiederholung angefordert", tone: "ok" }); runs.reload(); }
    catch (e: any) { toast({ title: "Wiederholung fehlgeschlagen", body: e.message, tone: "err" }); }
  };
  const providers = list.data?.providers || [];

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Workflow-Zentrale</div><h1>Workflows</h1></div>
        <div className="actions">
          {providers.map((p) => <span key={p.provider} className="row small"><StatusIndicator status={p.configured ? (p.error ? "error" : "ok") : "offline"} label={`${p.label} ${p.configured ? (p.error ? "Fehler" : "verbunden") : "nicht verbunden"}`} /></span>)}
          {can("operator") && <button className="btn sm" onClick={sync} disabled={syncing || !list.data?.configured}><RefreshCw style={syncing ? { animation: "spin 1s linear infinite" } : undefined} />Abgleichen</button>}
        </div>
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      {providers.some((p) => p.error) && <ErrorState error={providers.map((p) => p.error).filter(Boolean).join(" · ")} />}
      {list.data && !list.data.configured && (
        <Panel title="Keine Workflow-Engine verbunden" icon={<Workflow size={15} />}>
          <div className="stack small">
            <p>n8n verbinden: <code>N8N_BASE_URL</code> und <code>N8N_API_KEY</code> (optional <code>N8N_WEBHOOK_BASE_URL</code>) in der Server-Umgebung setzen. Workflows, Ausführungen, Aktivierung und Webhook-Auslöser erscheinen dann hier; solange keine echte Engine antwortet, wird nichts angezeigt.</p>
            <p className="muted">Weitere Engines lassen sich über die Adapter-Schnittstelle in <code>command_center/backend/adapters/workflows.py</code> anbinden.</p>
          </div>
        </Panel>
      )}
      <div className="ops-split wide">
        <Panel title="Workflows" icon={<Workflow size={15} />} flush foot={list.data?.stats && Object.keys(list.data.stats).length ? `letzte 7 Tage: ${Object.entries(list.data.stats).map(([k, v]: any) => `${v.count} ${de(LAUF, k)}`).join(" · ")}` : undefined}>
          {!list.data ? <div className="panel-body"><Skeleton rows={4} /></div> : list.data.workflows.length === 0 ? <EmptyState icon={<Workflow size={26} />} title={list.data.configured ? "Keine Workflows gefunden" : "Nicht verbunden"}>{list.data.configured ? "Die verbundene Engine meldet noch keine Workflows." : "Engine einrichten, um Workflows zu sehen."}</EmptyState> : (
            <table className="table">
              <thead><tr><th>Name</th><th>Auslöser</th><th>Letzter Lauf</th><th>Status</th><th>Aktiv</th><th /></tr></thead>
              <tbody>{list.data.workflows.map((w) => (
                <tr key={w.id} className={`clickable ${selected?.id === w.id ? "active" : ""}`} onClick={() => setSelected(w)}>
                  <td><div>{w.name}</div><div className="tiny muted">{w.provider} · {w.external_id}</div></td>
                  <td className="muted">{de(AUSLOESER, w.trigger)}</td>
                  <td className="small">{w.last_execution_at ? relative(w.last_execution_at) : "—"}</td>
                  <td>{w.last_status ? <Badge status={w.last_status} /> : <span className="muted">—</span>}</td>
                  <td onClick={(e) => e.stopPropagation()}><Toggle checked={w.active} onChange={(v) => can("operator") && setActive(w, v)} label={`${w.name} aktivieren`} /></td>
                  <td onClick={(e) => e.stopPropagation()} className="row" style={{ justifyContent: "flex-end", gap: 6, flexWrap: "nowrap", whiteSpace: "nowrap" }}>
                    {can("operator") && <button className="btn sm" onClick={() => trigger(w)} disabled={w.trigger !== "webhook"} title={w.trigger === "webhook" ? "Per Webhook auslösen" : "Hier lassen sich nur Webhook-Workflows auslösen"}><Play />Starten</button>}
                    {can("operator") && <button className="btn sm" onClick={() => setEdit({ w, name: w.name })} title="Bearbeiten (Namen ändern, im n8n-Editor öffnen)"><Pencil size={13} />Bearbeiten</button>}
                    {can("admin") && <button className="btn sm danger" onClick={() => setDel(w)} title="Workflow endgültig löschen"><Trash2 size={13} />Löschen</button>}
                  </td>
                </tr>))}</tbody>
            </table>)}
        </Panel>
        <Panel title={selected ? `Ausführungen · ${selected.name}` : "Letzte Ausführungen"} actions={selected && <button className="btn sm ghost" onClick={() => setSelected(null)}>Alle</button>} flush>
          {!runs.data ? <div className="panel-body"><Skeleton /></div> : runs.data.runs.length === 0 ? <EmptyState title="Keine Ausführungen" /> : (
            <div className="list">{runs.data.runs.map((r) => (
              <div key={r.id} className="list-item">
                <Badge status={r.status} />
                <div className="grow small"><div className="truncate">{(list.data?.workflows || []).find((w) => w.id === r.workflow_id)?.name ?? r.workflow_id}</div><div className="tiny muted">{dateTime(r.started_at)} · {ms(r.duration_ms)}{r.error && <span style={{ color: "var(--err)" }}> · {r.error}</span>}</div></div>
                <button className="btn icon ghost sm" onClick={() => open(r)} title="Ansehen"><Eye /></button>
                {can("operator") && r.status !== "success" && <button className="btn icon ghost sm" onClick={() => retry(r)} title="Wiederholen"><RotateCcw /></button>}
              </div>))}</div>)}
        </Panel>
      </div>
      {edit && (
        <Modal title="Workflow bearbeiten" onClose={() => !busy && setEdit(null)} foot={<>
          <button className="btn" onClick={() => setEdit(null)} disabled={busy}>Abbrechen</button>
          <button className="btn primary" onClick={saveName} disabled={busy || !edit.name.trim() || edit.name.trim() === edit.w.name}>
            {busy ? "Speichere …" : "Speichern"}</button></>}>
          <div className="stack">
            <label className="stack small"><span>Name</span>
              <input className="input" autoFocus value={edit.name} maxLength={120}
                onChange={(e) => setEdit({ w: edit.w, name: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") void saveName(); }} />
            </label>
            <p className="small muted">Hier änderst du den Namen. Knoten, Verbindungen und Einstellungen bleiben unverändert{edit.w.active ? ", und der Workflow bleibt aktiv" : ""}. Den Ablauf selbst bearbeitest du im n8n-Editor.</p>
            {edit.w.meta?.editor_url && <a className="btn sm" href={edit.w.meta.editor_url} target="_blank" rel="noreferrer">Im n8n-Editor öffnen ↗</a>}
          </div>
        </Modal>
      )}
      {del && (
        <Modal title="Workflow löschen?" onClose={() => !busy && setDel(null)} foot={<>
          <button className="btn" onClick={() => setDel(null)} disabled={busy}>Abbrechen</button>
          <button className="btn danger" onClick={remove} disabled={busy}><Trash2 size={14} />{busy ? "Lösche …" : "Endgültig löschen"}</button></>}>
          <div className="stack">
            <p>„<strong>{del.name}</strong>“ wird in n8n endgültig gelöscht. Das lässt sich nicht rückgängig machen.</p>
            {del.active && <p className="small" style={{ color: "var(--warn)" }}>Der Workflow ist aktiv. Er wird vor dem Löschen ausgeschaltet, seine Webhooks und Zeitpläne laufen danach nicht mehr.</p>}
            <p className="small muted">Seine bisherigen Ausführungen verschwinden aus dieser Liste.</p>
          </div>
        </Modal>
      )}
      {inspect && (
        <Modal title={`Ausführung ${inspect.external_id}`} onClose={() => setInspect(null)} wide>
          <div className="stack">
            <div className="row wrap"><Badge status={inspect.status} /><span className="small muted">{dateTime(inspect.started_at)} → {dateTime(inspect.finished_at)} · {ms(inspect.duration_ms)} · Modus {de(MODUS, inspect.mode)}</span></div>
            {inspect.error && <ErrorState error={inspect.error} />}
            {inspect.last_node && <div className="small">Letzter Knoten: <code>{inspect.last_node}</code></div>}
            {inspect.node_results?.length > 0 && <div className="small">Ausgeführte Knoten: {inspect.node_results.map((n: string) => <code key={n} style={{ marginRight: 6 }}>{n}</code>)}</div>}
          </div>
        </Modal>
      )}
    </div>
  );
}
