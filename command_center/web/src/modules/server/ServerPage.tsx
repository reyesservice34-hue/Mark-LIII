import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { useEvent } from "@/lib/events";
import { api } from "@/lib/api";
import { bytes, duration, pct, rate, relative } from "@/lib/format";
import { Server, Cpu, HardDrive, Network, Layers, RefreshCw, Terminal, CircleAlert } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, KeyValue, Meter, Modal, Panel, Skeleton, Sparkline, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";

// Ampelfarbe der Kachel nach Auslastung in Prozent (gleiche Schwellen wie Meter).
const lvl = (v?: number | null) => (v == null ? "" : v >= 90 ? "err" : v >= 75 ? "warn" : "ok");

export default function ServerPage() {
  const { can } = useAuth();
  const ov = useApi<any>("/api/server/overview", { interval: 30000, refreshOn: ["log.entry"], debounce: 5000 });
  const hist = useApi<{ points: any[] }>("/api/server/metrics/history?limit=240");
  const procs = useApi<{ processes: any[] }>("/api/server/processes?limit=12", { interval: 15000 });
  const containers = useApi<{ status: string; detail: string; containers: any[] }>("/api/server/containers", { interval: 20000 });
  const [live, setLive] = useState<any[]>([]);
  const [logs, setLogs] = useState<{ name: string; text: string } | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "container" | "service"; id: string; name: string } | null>(null);
  const [reason, setReason] = useState("");
  useEvent("server.metrics", (ev) => setLive((l) => [...l.slice(-239), ev.data]));
  const points = [...(hist.data?.points || []), ...live].slice(-240);
  const latest = live.at(-1) || ov.data?.overview?.sample;
  const o = ov.data?.overview;
  const caps = ov.data?.capabilities || {};

  const showLogs = async (c: any) => { try { const r = await api.get(`/api/server/containers/${c.id}/logs?tail=300`); setLogs({ name: c.name, text: r.logs }); } catch (e: any) { toast({ title: "Logs unavailable", body: e.message, tone: "err" }); } };
  const doRestart = async () => {
    if (!confirm) return;
    const path = confirm.kind === "container" ? `/api/server/containers/${confirm.id}/restart` : `/api/server/services/${confirm.id}/restart`;
    try { await api.post(path, { reason }); toast({ title: `Restarted ${confirm.name}`, tone: "ok" }); containers.reload(); ov.reload(); }
    catch (e: any) { toast({ title: "Restart failed", body: e.message, tone: "err" }); }
    finally { setConfirm(null); setReason(""); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Serverleitstand · {o?.hostname}</div><h1>Server</h1></div>
        <div className="actions"><button className="btn sm" onClick={() => { ov.reload(); containers.reload(); procs.reload(); }}><RefreshCw />Aktualisieren</button></div>
      </div>
      <ErrorState error={ov.error} retry={() => ov.reload(false)} />
      <div className="ops-telemetry cols-4">
        <div className={`ops-tile ${lvl(latest?.cpu)}`}>
          <span className="ops-tile-label"><Cpu size={13} /> CPU</span>
          <strong className="ops-tile-value">{pct(latest?.cpu)}</strong>
          <Meter value={latest?.cpu} />
          <small>{o?.cpu_count ?? "?"} Kerne · Last {latest?.load?.toFixed(2) ?? "—"}</small>
        </div>
        <div className={`ops-tile ${lvl(latest?.ram)}`}>
          <span className="ops-tile-label"><Layers size={13} /> Arbeitsspeicher</span>
          <strong className="ops-tile-value">{pct(latest?.ram)}</strong>
          <Meter value={latest?.ram} />
          <small>{bytes(latest?.ram_used)} von {bytes(latest?.ram_total)} · Swap {pct(latest?.swap)}</small>
        </div>
        <div className={`ops-tile ${lvl(latest?.disk)}`}>
          <span className="ops-tile-label"><HardDrive size={13} /> Speicher /</span>
          <strong className="ops-tile-value">{pct(latest?.disk)}</strong>
          <Meter value={latest?.disk} />
          <small>{bytes(latest?.disk_used)} von {bytes(latest?.disk_total)}</small>
        </div>
        <div className="ops-tile">
          <span className="ops-tile-label"><Server size={13} /> Laufzeit</span>
          <strong className="ops-tile-value">{duration(latest?.uptime)}</strong>
          <small>{latest?.processes ?? "—"} Prozesse · ↓ {rate(latest?.net_rx)} ↑ {rate(latest?.net_tx)}</small>
        </div>
      </div>
      <div className="ops-charts">
        <Panel title="CPU · letzte 20 Min." icon={<Cpu size={15} />} actions={<span className="num small">{pct(latest?.cpu)}</span>}><Sparkline points={points.map((p) => p.cpu)} height={70} /></Panel>
        <Panel title="Arbeitsspeicher" icon={<Layers size={15} />} actions={<span className="num small">{pct(latest?.ram)}</span>}><Sparkline points={points.map((p) => p.ram)} height={70} color="var(--ok)" /></Panel>
        <Panel title="Netzwerk (KB/s)" icon={<Network size={15} />} actions={<span className="num small">{rate(latest?.net_rx)} / {rate(latest?.net_tx)}</span>}><Sparkline points={points.map((p) => (p.net_rx + p.net_tx) / 1024)} height={70} max={Math.max(64, ...points.map((p) => (p.net_rx + p.net_tx) / 1024))} color="var(--warn)" /></Panel>
      </div>
      <div className="ops-split">
        <div className="stack">
          <Panel title="Docker-Container" icon={<Layers size={15} />} actions={containers.data && <Badge status={containers.data.status}>{containers.data.status.replace("_", " ")}</Badge>} flush foot={containers.data?.detail}>
            {!containers.data ? <div className="panel-body"><Skeleton rows={3} /></div> : containers.data.containers.length === 0 ? <EmptyState icon={<Layers size={26} />} title={containers.data.status === "healthy" ? "No containers" : "Docker not connected"}>{containers.data.detail} — mount <code>/var/run/docker.sock</code> into the command center container to see and manage containers.</EmptyState> : (
              <table className="table">
                <thead><tr><th>Name</th><th>Image</th><th>Zustand</th><th>CPU</th><th>Speicher</th><th>Ports</th><th /></tr></thead>
                <tbody>{containers.data.containers.map((c) => (
                  <tr key={c.id}><td><div>{c.name}</div><div className="tiny muted">{c.id}</div></td><td className="small muted truncate" style={{ maxWidth: 200 }}>{c.image}</td><td><StatusIndicator status={c.state === "running" ? "ok" : c.state === "exited" ? "error" : "warning"} label={c.status} /></td><td className="num small">{c.cpu != null ? pct(c.cpu, 1) : "—"}</td><td className="num small">{c.memory != null ? `${bytes(c.memory)}${c.memory_limit ? ` / ${bytes(c.memory_limit)}` : ""}` : "—"}</td><td className="small muted">{c.ports.join(", ") || "—"}</td>
                    <td className="row" style={{ justifyContent: "flex-end" }}>{can("operator") && <button className="btn sm ghost" onClick={() => showLogs(c)}>Logs</button>}{can("admin") && caps.docker_actions && <button className="btn sm danger" onClick={() => setConfirm({ kind: "container", id: c.id, name: c.name })}>Restart</button>}</td></tr>))}</tbody>
              </table>)}
          </Panel>
          <Panel title="Top-Prozesse" icon={<Terminal size={15} />} flush>
            {!procs.data ? <div className="panel-body"><Skeleton /></div> : <table className="table"><thead><tr><th>PID</th><th>Name</th><th>User</th><th className="num">CPU %</th><th className="num">MEM %</th><th>State</th></tr></thead>
              <tbody>{procs.data.processes.map((p) => <tr key={p.pid}><td className="num muted">{p.pid}</td><td>{p.name}</td><td className="muted small">{p.user}</td><td className="num">{p.cpu.toFixed(1)}</td><td className="num">{p.mem.toFixed(1)}</td><td className="small muted">{p.status}</td></tr>)}</tbody></table>}
          </Panel>
        </div>
        <div className="stack">
          <Panel title="System" icon={<Server size={15} />}>{!o ? <Skeleton /> : <KeyValue items={[["Hostname", o.hostname], ["OS", o.os], ["Platform", <span className="small">{o.platform}</span>], ["Cores", `${o.cpu_physical ?? "?"} physical / ${o.cpu_count} logical`], ["Python", o.python], ["Runs in container", o.in_container ? "yes" : "no"], ["Docker", <Badge status={o.docker.status}>{o.docker.status.replace("_", " ")}</Badge>]]} />}</Panel>
          <Panel title="Speicher" icon={<HardDrive size={15} />}>{!ov.data ? <Skeleton /> : <div className="stack">{ov.data.disks.map((d: any) => <div key={d.mountpoint}><div className="row between small"><span className="truncate">{d.mountpoint} <span className="muted">{d.fstype}</span></span><span className="num">{bytes(d.used)} / {bytes(d.total)}</span></div><Meter value={d.percent} /></div>)}</div>}</Panel>
          <Panel title="Netzwerk" icon={<Network size={15} />}>{!ov.data ? <Skeleton /> : ov.data.network.length === 0 ? <span className="small muted">no interfaces</span> : <div className="stack" style={{ gap: 6 }}>{ov.data.network.map((n: any) => <div key={n.name} className="row between small"><span className="row"><StatusIndicator status={n.up ? "ok" : "offline"} />{n.name} <span className="muted">{n.addresses.join(", ")}</span></span><span className="num muted">↓{bytes(n.bytes_recv)} ↑{bytes(n.bytes_sent)}</span></div>)}</div>}</Panel>
          <Panel title="Überwachte Dienste" icon={<Layers size={15} />}>{!ov.data ? <Skeleton /> : ov.data.services.length === 0 ? <span className="small muted">None configured — set <code>JARVIS_CC_MONITORED_SERVICES</code> (comma-separated systemd units).</span> : <div className="stack" style={{ gap: 6 }}>{ov.data.services.map((s: any) => <div key={s.name} className="row between small"><span className="row"><StatusIndicator status={s.status} />{s.name} <span className="muted">{s.detail}</span></span>{can("admin") && caps.service_restart && <button className="btn sm danger" onClick={() => setConfirm({ kind: "service", id: s.name, name: s.name })}>Restart</button>}</div>)}</div>}</Panel>
          <Panel title="Letzte Fehler" icon={<CircleAlert size={15} />} flush>{!ov.data ? <div className="panel-body"><Skeleton /></div> : ov.data.recent_errors.length === 0 ? <EmptyState title="No recent errors" /> : <div className="list">{ov.data.recent_errors.map((l: any) => <div key={l.id} className="list-item small" style={{ padding: "6px 14px" }}><Badge status="error">{l.source}</Badge><span className="grow truncate" title={l.message}>{l.message}</span><span className="tiny muted">{relative(l.ts)}</span></div>)}</div>}</Panel>
        </div>
      </div>
      {logs && <Modal title={`Logs · ${logs.name}`} onClose={() => setLogs(null)} wide><pre className="md" style={{ maxHeight: "60vh", overflow: "auto", fontSize: 11.5 }}>{logs.text || "(empty)"}</pre></Modal>}
      {confirm && (
        <Modal title={`Restart ${confirm.kind} “${confirm.name}”?`} onClose={() => setConfirm(null)} foot={<><button className="btn" onClick={() => setConfirm(null)}>Cancel</button><button className="btn danger" onClick={doRestart}>Restart now</button></>}>
          <div className="stack">
            <p className="small">This is a sensitive action. It will be recorded in the audit trail with your name and reason.</p>
            <div className="field"><label>Reason</label><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this restart needed?" autoFocus /></div>
          </div>
        </Modal>
      )}
    </div>
  );
}
