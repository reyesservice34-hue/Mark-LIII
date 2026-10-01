import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { bytes, duration } from "@/lib/format";
import { BarChart3 } from "@/lib/icons";
import { EmptyState, ErrorState, Panel, Skeleton, Sparkline, Stat } from "@/components/ui";

function Bars({ data, keys, colors, height = 120 }: { data: any[]; keys: string[]; colors: string[]; height?: number }) {
  if (!data.length) return <EmptyState title="No data in this period" />;
  const max = Math.max(1, ...data.map((d) => keys.reduce((s, k) => s + (d[k] || 0), 0)));
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height }} role="img" aria-label={`Bar chart of ${keys.join(", ")} per day`}>
      {data.map((d, i) => (
        <div key={i} title={`${d.day}: ${keys.map((k) => `${k} ${d[k] || 0}`).join(", ")}`} style={{ flex: 1, display: "flex", flexDirection: "column-reverse", height: "100%", gap: 1 }}>
          {keys.map((k, j) => <span key={k} style={{ height: `${((d[k] || 0) / max) * 100}%`, background: colors[j], borderRadius: 2, minHeight: d[k] ? 2 : 0, transition: "height 500ms var(--ease)" }} />)}
        </div>))}
    </div>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = useState(14);
  const { data, error, loading, reload } = useApi<any>(`/api/analytics/overview?days=${days}`);
  const runs = data?.runs || [];
  const byAgent: Record<string, any> = {};
  runs.forEach((r: any) => { const a = byAgent[r.agent_id] ||= { runs: 0, failed: 0, avg: 0 }; a.runs += r.n; if (r.status === "failed") a.failed += r.n; if (r.status === "completed") a.avg = r.avg_secs; });
  const tools: Record<string, { ok: number; err: number }> = {};
  (data?.tool_calls || []).forEach((t: any) => { const x = tools[t.tool] ||= { ok: 0, err: 0 }; if (t.status === "ok") x.ok += t.n; else x.err += t.n; });
  const tokens = data?.tokens || [];
  const totalTok = tokens.reduce((s: number, t: any) => s + t.input_tokens + t.output_tokens, 0);
  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Real usage data · no estimates</div><h1>Analytics</h1></div>
        <div className="actions"><select className="select" style={{ width: 140 }} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period"><option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select></div>
      </div>
      <ErrorState error={error} retry={() => reload(false)} />
      {loading && !data ? <Skeleton rows={5} height={40} /> : data && (
        <>
          <div className="grid cols-4">
            <Stat label="Tasks completed" value={data.tasks.by_status.COMPLETED?.count ?? 0} sub={`avg ${duration(data.tasks.by_status.COMPLETED?.avg_seconds)} · ${data.tasks.by_status.FAILED?.count ?? 0} failed`} />
            <Stat label="Agent runs" value={runs.reduce((s: number, r: any) => s + r.n, 0)} sub={`${Object.keys(byAgent).length} agents active`} />
            <Stat label="Tokens" value={totalTok >= 1000 ? `${(totalTok / 1000).toFixed(1)}k` : totalTok} sub={`${tokens.reduce((s: number, t: any) => s + t.runs, 0)} runs reported usage`} />
            <Stat label="Approvals" value={data.approvals.approved ?? 0} unit="approved" sub={`${data.approvals.rejected ?? 0} rejected · ${data.approvals.expired ?? 0} expired · ${data.approvals.pending ?? 0} pending`} />
          </div>
          <div className="grid cols-2">
            <Panel title="Tasks per day" icon={<BarChart3 size={15} />} foot={<span className="row" style={{ gap: 12 }}><span className="row" style={{ gap: 4 }}><span className="dot ok" />completed</span><span className="row" style={{ gap: 4 }}><span className="dot err" />failed</span><span className="row" style={{ gap: 4 }}><span className="dot" style={{ background: "var(--text-4)" }} />other</span></span>}>
              <Bars data={data.tasks.daily.map((d: any) => ({ ...d, other: d.total - d.completed - d.failed }))} keys={["completed", "failed", "other"]} colors={["var(--ok)", "var(--err)", "var(--text-4)"]} />
            </Panel>
            <Panel title="Tokens per day" icon={<BarChart3 size={15} />} foot={<span className="row" style={{ gap: 12 }}><span className="row" style={{ gap: 4 }}><span className="dot info" />input</span><span className="row" style={{ gap: 4 }}><span className="dot" style={{ background: "#c792ea" }} />output</span></span>}>
              <Bars data={tokens} keys={["input_tokens", "output_tokens"]} colors={["var(--accent)", "#c792ea"]} />
            </Panel>
          </div>
          <div className="grid cols-3">
            <Panel title="Agents" flush>{Object.keys(byAgent).length === 0 ? <EmptyState title="No runs yet" /> : <table className="table"><thead><tr><th>Agent</th><th className="num">Runs</th><th className="num">Failed</th><th className="num">Avg</th></tr></thead><tbody>{Object.entries(byAgent).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{v.runs}</td><td className="num" style={{ color: v.failed ? "var(--err)" : undefined }}>{v.failed}</td><td className="num">{duration(v.avg)}</td></tr>)}</tbody></table>}</Panel>
            <Panel title="Tool usage" flush>{Object.keys(tools).length === 0 ? <EmptyState title="No tool calls yet" /> : <table className="table"><thead><tr><th>Tool</th><th className="num">OK</th><th className="num">Errors</th></tr></thead><tbody>{Object.entries(tools).sort((a, b) => (b[1].ok + b[1].err) - (a[1].ok + a[1].err)).map(([k, v]) => <tr key={k}><td><code>{k}</code></td><td className="num">{v.ok}</td><td className="num" style={{ color: v.err ? "var(--err)" : undefined }}>{v.err}</td></tr>)}</tbody></table>}</Panel>
            <Panel title="Server load (persisted, 1 min samples)">
              <div className="stack"><div className="tiny muted">CPU</div><Sparkline points={data.metrics.map((m: any) => m.cpu)} height={40} /><div className="tiny muted">RAM</div><Sparkline points={data.metrics.map((m: any) => m.ram)} height={40} color="var(--ok)" /><div className="tiny muted">{data.metrics.length} points · workspace {bytes(data.files.bytes)} in {data.files.files} files · logs: {Object.entries(data.logs).map(([k, v]) => `${v} ${k}`).join(", ") || "none"}</div></div>
            </Panel>
          </div>
        </>)}
    </div>
  );
}
