import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import { time, dateTime } from "@/lib/format";
import { ScrollText, Pause, Play, ShieldCheck } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Panel, Skeleton } from "@/components/ui";

const LEVELS = ["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

export default function LogsPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<"logs" | "audit">("logs");
  const [q, setQ] = useState("");
  const [level, setLevel] = useState("INFO");
  const [source, setSource] = useState("");
  const [live, setLive] = useState(true);
  const [rows, setRows] = useState<any[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<any>(null);
  const sources = useApi<{ sources: string[] }>("/api/logs/sources");
  const audit = useApi<{ events: any[] }>(tab === "audit" ? `/api/logs/audit?limit=200&q=${encodeURIComponent(q)}` : null);
  const listRef = useRef<HTMLDivElement>(null);

  const load = async (append = false) => {
    setLoading(true); setError(null);
    try {
      const r = await api.get<{ logs: any[]; next_before: string | null }>(`/api/logs?limit=150&q=${encodeURIComponent(q)}&level=${level}&source=${encodeURIComponent(source)}${append && next ? `&before=${encodeURIComponent(next)}` : ""}`);
      setRows((prev) => append ? [...prev, ...r.logs] : r.logs); setNext(r.next_before);
    } catch (e) { setError(e); } finally { setLoading(false); }
  };
  useEffect(() => {
    const t = setTimeout(() => load(false), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, level, source]);
  useEvent("log.entry", (ev) => {
    if (!live || tab !== "logs") return;
    const l = ev.data;
    if (level && LEVELS.indexOf(l.level) < LEVELS.indexOf(level)) return;
    if (source && l.source !== source) return;
    if (q && !l.message.toLowerCase().includes(q.toLowerCase())) return;
    setRows((prev) => [l, ...prev].slice(0, 500));
  }, [live, level, source, q, tab]);

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Log center</div><h1>Logs</h1></div>
        <div className="actions">
          <div className="row" style={{ gap: 4 }}><button className={`btn sm ${tab === "logs" ? "primary" : ""}`} onClick={() => setTab("logs")}><ScrollText />Logs</button>{can("operator") && <button className={`btn sm ${tab === "audit" ? "primary" : ""}`} onClick={() => setTab("audit")}><ShieldCheck />Audit trail</button>}</div>
          <input className="input" style={{ width: 220 }} placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search logs" />
          {tab === "logs" && <>
            <select className="select" style={{ width: 130 }} value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Minimum level">{LEVELS.map((l) => <option key={l} value={l}>{l || "All levels"}</option>)}</select>
            <select className="select" style={{ width: 160 }} value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source"><option value="">All sources</option>{(sources.data?.sources || []).map((s) => <option key={s}>{s}</option>)}</select>
            <button className={`btn sm ${live ? "success" : ""}`} onClick={() => setLive((v) => !v)} title="Live stream">{live ? <Pause /> : <Play />}{live ? "Live" : "Paused"}</button>
          </>}
        </div>
      </div>
      <ErrorState error={error} retry={() => load(false)} />
      {tab === "logs" ? (
        <Panel title={`${rows.length} entries`} flush foot={next ? <button className="btn sm" onClick={() => load(true)} disabled={loading}>Load older</button> : "Beginning of retained logs"}>
          <div ref={listRef} style={{ maxHeight: "calc(100vh - 260px)", overflow: "auto" }}>
            {loading && rows.length === 0 ? <div className="panel-body"><Skeleton rows={6} /></div> : rows.length === 0 ? <EmptyState icon={<ScrollText size={26} />} title="No log entries match" /> : (
              <table className="table" style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                <tbody>{rows.map((l) => (
                  <tr key={l.id}>
                    <td className="muted" style={{ whiteSpace: "nowrap", width: 90 }} title={dateTime(l.ts)}>{time(l.ts)}</td>
                    <td style={{ width: 90 }}><Badge status={l.level === "ERROR" || l.level === "CRITICAL" ? "error" : l.level === "WARNING" ? "warning" : l.level === "DEBUG" ? "unknown" : "info"}>{l.level}</Badge></td>
                    <td className="muted" style={{ whiteSpace: "nowrap", width: 140 }}>{l.source}</td>
                    <td style={{ wordBreak: "break-word" }}>{l.message}{(l.task_id || l.agent_id || l.run_id) && <span className="tiny muted"> {l.task_id && <a href={`/tasks/${l.task_id}`}>task</a>} {l.agent_id && <span>· {l.agent_id}</span>} {l.run_id && <span>· run {l.run_id.slice(-6)}</span>}</span>}</td>
                  </tr>))}</tbody>
              </table>)}
          </div>
        </Panel>
      ) : (
        <Panel title="Audit trail" flush foot="Who did what, through which agent and tool, with what result. Audit rows are never trimmed.">
          {!audit.data ? <div className="panel-body"><Skeleton rows={6} /></div> : audit.data.events.length === 0 ? <EmptyState title="No audit events" /> : (
            <table className="table" style={{ fontSize: 12.5 }}>
              <thead><tr><th>When</th><th>Actor</th><th>Agent</th><th>Action</th><th>Tool</th><th>Target</th><th>Status</th><th>Result / error</th></tr></thead>
              <tbody>{audit.data.events.map((a) => (
                <tr key={a.id}><td className="muted num" style={{ whiteSpace: "nowrap" }}>{dateTime(a.ts)}</td><td>{a.actor_id}<div className="tiny muted">{a.actor_type}</div></td><td className="muted">{a.agent_id || "—"}</td><td><code>{a.action}</code></td><td className="muted">{a.tool || "—"}</td><td className="truncate" style={{ maxWidth: 220 }} title={a.target}>{a.target}</td><td><Badge status={a.status === "ok" ? "ok" : a.status === "denied" ? "warning" : a.status} /></td><td className="small truncate" style={{ maxWidth: 260, color: a.error ? "var(--err)" : undefined }} title={a.error || a.result}>{a.error || a.result}</td></tr>))}</tbody>
            </table>)}
        </Panel>
      )}
    </div>
  );
}
