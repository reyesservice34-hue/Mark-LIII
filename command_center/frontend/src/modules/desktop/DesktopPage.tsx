import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import { Monitor, Play, RefreshCw, Terminal } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, KeyValue, Modal, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";

interface Device {
  id: string; name: string; platform: string; version: string; online: boolean; queued: number;
  last_seen_at: string; registered_at: string; actor: string;
  actions: { name: string; description?: string; parameters?: any }[];
}

export default function DesktopPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<{ devices: Device[]; stats: any; history: any[] }>(
    "/api/desktop/devices", { refreshOn: ["desktop.device", "desktop.command"], interval: 20000 });
  const [selected, setSelected] = useState<Device | null>(null);
  const [form, setForm] = useState<{ action: string; params: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const devices = data?.devices || [];
  const device = selected ? devices.find((d) => d.id === selected.id) || selected : null;

  const send = async () => {
    if (!device || !form) return;
    let params: any = {};
    try { params = form.params.trim() ? JSON.parse(form.params) : {}; }
    catch { toast({ title: "Parameters are not valid JSON", tone: "err" }); return; }
    setBusy(true);
    try {
      const r = await api.post(`/api/desktop/devices/${device.id}/command`,
        { action: form.action, params, timeout: 90 });
      const cmd = r.command;
      toast({ title: cmd.status === "done" ? "Done on the PC" : "The PC reported a problem",
        body: cmd.result || cmd.error, tone: cmd.status === "done" ? "ok" : "err" });
      setForm(null);
      reload();
    } catch (e: any) {
      toast({ title: "Command not delivered", body: e.message, tone: "err" });
    } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">Paired computers{data?.stats ? ` · ${data.stats.online} of ${data.stats.devices} online` : ""}</div>
          <h1>Desktop</h1>
        </div>
        <div className="actions"><button className="btn sm" onClick={() => reload(false)}><RefreshCw />Refresh</button></div>
      </div>
      <ErrorState error={error} retry={() => reload(false)} />
      <p className="small muted">
        The server never dials into your PC. JARVIS on the desktop holds a connection open outward, picks up
        commands and runs them with the actions it already has. If it is closed, a command is refused rather
        than queued forever.
      </p>

      {loading && !data ? <Skeleton rows={3} height={50} /> : devices.length === 0 ? (
        <Panel title="No desktop paired yet" icon={<Monitor size={15} />}>
          <div className="stack small">
            <p>Start JARVIS on the PC with a machine token configured, or run <code>python desktop_agent.py</code> there.
              It registers itself and appears here.</p>
            <p className="muted">Create the token under Settings, then set <code>JARVIS_GATEWAY_TOKEN</code> and
              <code> jarvis_gateway_url</code> on the PC.</p>
          </div>
        </Panel>
      ) : (
        <div className="grid auto">
          {devices.map((d) => (
            <div key={d.id} className={`panel ${device?.id === d.id ? "" : ""}`} style={{ padding: 14, gap: 10, cursor: "pointer" }}
              onClick={() => setSelected(d)} role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter") setSelected(d); }}>
              <div className="row between">
                <span className="row"><span className="agent-avatar" style={{ width: 32, height: 32 }}><Monitor size={16} /></span>
                  <div><div style={{ fontWeight: 600 }}>{d.name}</div><div className="tiny muted">{d.platform || "unknown OS"}</div></div></span>
                <StatusIndicator status={d.online ? "ok" : "offline"} label={d.online ? "online" : "away"} live={d.online} />
              </div>
              <div className="small muted">{d.actions.length} actions · {d.queued} queued · seen {relative(d.last_seen_at)}</div>
              <div className="row wrap" style={{ gap: 5 }}>
                {d.actions.slice(0, 6).map((a) => <span key={a.name} className="badge muted">{a.name}</span>)}
                {d.actions.length > 6 && <span className="badge muted">+{d.actions.length - 6}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {device && (
        <Panel title={`${device.name} — what it can do`} icon={<Monitor size={15} />}
          actions={<>
            {can("operator") && <button className="btn sm primary" onClick={() => setForm({ action: "open_app", params: '{"app_name": "Chrome"}' })}><Play />Send command</button>}
            <button className="btn sm ghost" onClick={() => setSelected(null)}>Close</button>
          </>}>
          <div className="stack">
            <KeyValue items={[["Status", <Badge status={device.online ? "healthy" : "offline"}>{device.online ? "online" : "away"}</Badge>],
              ["Platform", `${device.platform} ${device.version}`], ["Paired as", <code>{device.actor}</code>],
              ["Registered", dateTime(device.registered_at)], ["Last seen", dateTime(device.last_seen_at)]]} />
            <table className="table">
              <thead><tr><th>Action</th><th>What it does</th><th /></tr></thead>
              <tbody>{device.actions.map((a) => (
                <tr key={a.name}>
                  <td><code>{a.name}</code></td>
                  <td className="small muted" style={{ maxWidth: 520 }}>{a.description || "—"}</td>
                  <td style={{ textAlign: "right" }}>{can("operator") &&
                    <button className="btn sm" onClick={() => setForm({ action: a.name, params: "{}" })}><Terminal />Run</button>}</td>
                </tr>))}</tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel title="Recent commands" flush foot="Every command is in the audit trail with who asked for it.">
        {!data ? <div className="panel-body"><Skeleton /></div> : data.history.length === 0 ?
          <EmptyState title="Nothing sent yet" /> : (
            <table className="table">
              <thead><tr><th>When</th><th>Action</th><th>Parameters</th><th>By</th><th>Status</th><th>Result</th></tr></thead>
              <tbody>{data.history.map((h) => (
                <tr key={h.id}>
                  <td className="small muted" style={{ whiteSpace: "nowrap" }}>{relative(h.created_at)}</td>
                  <td><code>{h.action}</code></td>
                  <td className="small muted truncate" style={{ maxWidth: 220 }}>{JSON.stringify(h.params)}</td>
                  <td className="small">{h.agent_id || h.requested_by}</td>
                  <td><Badge status={h.status === "done" ? "ok" : h.status === "queued" || h.status === "running" ? "running" : "error"}>{h.status}</Badge></td>
                  <td className="small truncate" style={{ maxWidth: 280, color: h.error ? "var(--err)" : undefined }}>{h.error || h.result}</td>
                </tr>))}</tbody>
            </table>)}
      </Panel>

      {form && device && (
        <Modal title={`Run “${form.action}” on ${device.name}`} onClose={() => setForm(null)}
          foot={<><button className="btn" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn primary" onClick={send} disabled={busy}>{busy ? "Waiting for the PC…" : "Send"}</button></>}>
          <div className="stack">
            {!device.online && <ErrorState error="This desktop is not connected right now — the command would be refused." />}
            <div className="field"><label>Action</label>
              <select className="select" value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })}>
                {device.actions.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
              </select></div>
            <div className="field"><label>Parameters (JSON)</label>
              <textarea className="textarea" style={{ fontFamily: "var(--mono)", minHeight: 100 }}
                value={form.params} onChange={(e) => setForm({ ...form, params: e.target.value })} /></div>
            <details><summary className="small muted" style={{ cursor: "pointer" }}>What this action expects</summary>
              <pre className="md" style={{ marginTop: 8, maxHeight: 220, overflow: "auto" }}>
                {JSON.stringify(device.actions.find((a) => a.name === form.action)?.parameters ?? {}, null, 2)}</pre></details>
          </div>
        </Modal>
      )}
    </div>
  );
}
