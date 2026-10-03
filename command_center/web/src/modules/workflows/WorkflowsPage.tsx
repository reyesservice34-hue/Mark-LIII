import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { dateTime, ms, relative } from "@/lib/format";
import { Workflow, RefreshCw, Play, Eye, RotateCcw } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Modal, Panel, Skeleton, StatusIndicator, Toggle } from "@/components/ui";
import { toast } from "@/lib/toast";

export default function WorkflowsPage() {
  const { can } = useAuth();
  const list = useApi<{ workflows: any[]; providers: any[]; configured: boolean; stats: any }>("/api/workflows", { refreshOn: ["workflow.*"] });
  const [selected, setSelected] = useState<any>(null);
  const runs = useApi<{ runs: any[] }>(selected ? `/api/workflows/runs?workflow_id=${encodeURIComponent(selected.id)}` : "/api/workflows/runs?limit=30", { refreshOn: ["workflow.*"] });
  const [inspect, setInspect] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);

  const sync = async () => { setSyncing(true); try { await api.get("/api/workflows?refresh=true"); list.reload(); runs.reload(); } finally { setSyncing(false); } };
  const trigger = async (w: any) => {
    try { const r = await api.post(`/api/workflows/${encodeURIComponent(w.id)}/trigger`, { payload: {} }); toast({ title: r.result.ok ? "Workflow triggered" : "Trigger failed", body: `HTTP ${r.result.http_status}`, tone: r.result.ok ? "ok" : "err" }); }
    catch (e: any) { toast({ title: "Trigger failed", body: e.message, tone: "err" }); }
  };
  const setActive = async (w: any, active: boolean) => {
    try { await api.post(`/api/workflows/${encodeURIComponent(w.id)}/active`, { active }); list.reload(); }
    catch (e: any) { toast({ title: "Could not change state", body: e.message, tone: "err" }); }
  };
  const open = async (run: any) => {
    try { const r = await api.get(`/api/workflows/runs/${encodeURIComponent(run.id)}`); setInspect(r.run); }
    catch (e: any) { toast({ title: "Could not load execution", body: e.message, tone: "err" }); }
  };
  const retry = async (run: any) => {
    try { await api.post(`/api/workflows/runs/${encodeURIComponent(run.id)}/retry`); toast({ title: "Retry requested", tone: "ok" }); runs.reload(); }
    catch (e: any) { toast({ title: "Retry failed", body: e.message, tone: "err" }); }
  };
  const providers = list.data?.providers || [];

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Workflow center</div><h1>Workflows</h1></div>
        <div className="actions">
          {providers.map((p) => <span key={p.provider} className="row small"><StatusIndicator status={p.configured ? (p.error ? "error" : "ok") : "offline"} label={`${p.label} ${p.configured ? (p.error ? "error" : "connected") : "not connected"}`} /></span>)}
          {can("operator") && <button className="btn sm" onClick={sync} disabled={syncing || !list.data?.configured}><RefreshCw style={syncing ? { animation: "spin 1s linear infinite" } : undefined} />Sync</button>}
        </div>
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      {providers.some((p) => p.error) && <ErrorState error={providers.map((p) => p.error).filter(Boolean).join(" · ")} />}
      {list.data && !list.data.configured && (
        <Panel title="No workflow engine connected" icon={<Workflow size={15} />}>
          <div className="stack small">
            <p>Connect n8n by setting <code>N8N_BASE_URL</code> and <code>N8N_API_KEY</code> (optionally <code>N8N_WEBHOOK_BASE_URL</code>) in the server environment. Workflows, executions, activation and webhook triggers will appear here; nothing is shown until a real engine answers.</p>
            <p className="muted">Other engines can be added through the adapter interface in <code>command_center/backend/adapters/workflows.py</code>.</p>
          </div>
        </Panel>
      )}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)" }}>
        <Panel title="Workflows" icon={<Workflow size={15} />} flush foot={list.data?.stats && Object.keys(list.data.stats).length ? `last 7 days: ${Object.entries(list.data.stats).map(([k, v]: any) => `${v.count} ${k}`).join(" · ")}` : undefined}>
          {!list.data ? <div className="panel-body"><Skeleton rows={4} /></div> : list.data.workflows.length === 0 ? <EmptyState icon={<Workflow size={26} />} title={list.data.configured ? "No workflows found" : "Not connected"}>{list.data.configured ? "The connected engine reports no workflows yet." : "Configure an engine to see workflows."}</EmptyState> : (
            <table className="table">
              <thead><tr><th>Name</th><th>Trigger</th><th>Last run</th><th>Status</th><th>Active</th><th /></tr></thead>
              <tbody>{list.data.workflows.map((w) => (
                <tr key={w.id} className={`clickable ${selected?.id === w.id ? "active" : ""}`} onClick={() => setSelected(w)}>
                  <td><div>{w.name}</div><div className="tiny muted">{w.provider} · {w.external_id}</div></td>
                  <td className="muted">{w.trigger}</td>
                  <td className="small">{w.last_execution_at ? relative(w.last_execution_at) : "—"}</td>
                  <td>{w.last_status ? <Badge status={w.last_status} /> : <span className="muted">—</span>}</td>
                  <td onClick={(e) => e.stopPropagation()}><Toggle checked={w.active} onChange={(v) => can("operator") && setActive(w, v)} label={`Activate ${w.name}`} /></td>
                  <td onClick={(e) => e.stopPropagation()} className="row" style={{ justifyContent: "flex-end" }}>{can("operator") && <button className="btn sm" onClick={() => trigger(w)} disabled={w.trigger !== "webhook"} title={w.trigger === "webhook" ? "Trigger via webhook" : "Only webhook workflows can be triggered here"}><Play />Run</button>}</td>
                </tr>))}</tbody>
            </table>)}
        </Panel>
        <Panel title={selected ? `Executions · ${selected.name}` : "Recent executions"} actions={selected && <button className="btn sm ghost" onClick={() => setSelected(null)}>All</button>} flush>
          {!runs.data ? <div className="panel-body"><Skeleton /></div> : runs.data.runs.length === 0 ? <EmptyState title="No executions" /> : (
            <div className="list">{runs.data.runs.map((r) => (
              <div key={r.id} className="list-item">
                <Badge status={r.status} />
                <div className="grow small"><div className="truncate">{r.workflow_id}</div><div className="tiny muted">{dateTime(r.started_at)} · {ms(r.duration_ms)}{r.error && <span style={{ color: "var(--err)" }}> · {r.error}</span>}</div></div>
                <button className="btn icon ghost sm" onClick={() => open(r)} title="Inspect"><Eye /></button>
                {can("operator") && r.status !== "success" && <button className="btn icon ghost sm" onClick={() => retry(r)} title="Retry"><RotateCcw /></button>}
              </div>))}</div>)}
        </Panel>
      </div>
      {inspect && (
        <Modal title={`Execution ${inspect.external_id}`} onClose={() => setInspect(null)} wide>
          <div className="stack">
            <div className="row wrap"><Badge status={inspect.status} /><span className="small muted">{dateTime(inspect.started_at)} → {dateTime(inspect.finished_at)} · {ms(inspect.duration_ms)} · mode {inspect.mode}</span></div>
            {inspect.error && <ErrorState error={inspect.error} />}
            {inspect.last_node && <div className="small">Last node: <code>{inspect.last_node}</code></div>}
            {inspect.node_results?.length > 0 && <div className="small">Nodes executed: {inspect.node_results.map((n: string) => <code key={n} style={{ marginRight: 6 }}>{n}</code>)}</div>}
          </div>
        </Modal>
      )}
    </div>
  );
}
