import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { useEvent } from "@/lib/events";
import { bytes, duration, pct, relative, time } from "@/lib/format";
import { Activity, Bot, Cpu, ListChecks, Server, Sparkles, Zap, HardDrive } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Meter, Panel, Skeleton, Sparkline, StatusIndicator } from "@/components/ui";
import type { StatusPayload } from "@/app/shell/TopStatusBar";
import { AgentCard } from "@/modules/agents/AgentCard";
import { TaskTimeline } from "@/modules/tasks/TaskTimeline";
import { ActivityFeed } from "./ActivityFeed";

export default function HomePage() {
  const nav = useNavigate();
  const status = useApi<StatusPayload>("/api/status", { refreshOn: ["task.*", "agent.status", "run.*", "master.status", "approval.*"], interval: 30000 });
  const health = useApi<any>("/api/health", { interval: 60000, refreshOn: ["integration.status", "master.status"] });
  const agents = useApi<{ agents: any[] }>("/api/agents", { refreshOn: ["agent.status", "run.started", "run.finished"] });
  const timeline = useApi<{ tasks: any[] }>("/api/tasks/timeline?limit=12", { refreshOn: ["task.*"] });
  const history = useApi<{ points: any[] }>("/api/server/metrics/history?limit=120");
  const [live, setLive] = useState<any[]>([]);
  useEvent("server.metrics", (ev) => setLive((l) => [...l.slice(-119), ev.data]));
  const points = [...(history.data?.points || []), ...live].slice(-120);
  const s = status.data;
  const master = s?.master;
  const gw = health.data?.components?.agent_gateway;
  const [cmd, setCmd] = useState("");

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Command center · {new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div><h1>{master?.label || "JARVIS"}</h1></div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (cmd.trim()) nav(`/chat?new=1&q=${encodeURIComponent(cmd.trim())}`); }} style={{ minWidth: "min(460px, 100%)" }}>
          <input className="input" placeholder="Tell JARVIS what to do… (⌘K for commands)" value={cmd} onChange={(e) => setCmd(e.target.value)} aria-label="Command for JARVIS" />
          <button className="btn primary" type="submit"><Sparkles size={14} />Send</button>
        </form>
      </div>
      <ErrorState error={status.error} retry={() => status.reload(false)} />

      <div className="grid cols-4">
        <Panel title="JARVIS core" icon={<Sparkles size={15} />}>
          {!s ? <Skeleton /> : <div className="stack">
            <div className="row between"><StatusIndicator status={master?.online ? "ok" : "err"} label={master?.online ? "online" : "offline"} live={!!master?.active_runs?.length} /><Badge status={gw?.status}>{gw?.status || "…"}</Badge></div>
            <div className="small"><span className="muted">Uptime</span> <span className="num">{duration(s.jarvis.uptime_seconds)}</span></div>
            <div className="small truncate"><span className="muted">Model</span> {master?.provider?.label || <span className="muted">none configured</span>}</div>
            <div className="small"><span className="muted">Mode</span> {master?.mode} · <span className="muted">active runs</span> {master?.active_runs?.length ?? 0}</div>
            <div className="small"><span className="muted">Agents</span> {s.agents.enabled}/{s.agents.total} enabled · {s.agents.active} active</div>
            {master?.error && <div className="error-state small">{master.error}</div>}
          </div>}
        </Panel>
        <Panel title="Server health" icon={<Server size={15} />} actions={<Link className="btn sm ghost" to="/server">Details</Link>}>
          {!s ? <Skeleton /> : <div className="stack">
            <div className="row between small"><span className="muted">{s.server.hostname}</span><Badge status={s.server.connected ? "healthy" : "offline"} /></div>
            {[["CPU", s.server.cpu], ["RAM", s.server.ram], ["Storage", s.server.disk]].map(([k, v]) => (
              <div key={k as string} className="stack" style={{ gap: 4 }}><div className="row between small"><span>{k}</span><span className="num">{pct(v as number)}</span></div><Meter value={v as number} /></div>
            ))}
            <div className="row between small"><span className="muted">Load</span><span className="num">{s.server.load?.toFixed(2)}</span><span className="muted">Net</span><span className="num">{bytes(s.server.net_rx, 0)}/s ↓ {bytes(s.server.net_tx, 0)}/s ↑</span></div>
            <div className="row between small"><span className="muted">Docker</span><Badge status={s.server.docker?.status}>{s.server.docker?.status?.replace("_", " ")}</Badge></div>
          </div>}
        </Panel>
        <Panel title="Live load · CPU / RAM" icon={<Cpu size={15} />}>
          <div className="stack">
            <div><div className="row between tiny muted"><span>CPU</span><span className="num">{pct(points.at(-1)?.cpu)}</span></div><Sparkline points={points.map((p) => p.cpu)} height={44} /></div>
            <div><div className="row between tiny muted"><span>RAM</span><span className="num">{pct(points.at(-1)?.ram)}</span></div><Sparkline points={points.map((p) => p.ram)} height={44} color="var(--ok)" /></div>
            <div className="tiny muted">last {Math.round(points.length * 5 / 60)} min · 5 s samples</div>
          </div>
        </Panel>
        <Panel title="Tasks" icon={<ListChecks size={15} />} actions={<Link className="btn sm ghost" to="/tasks">All</Link>}>
          {!s ? <Skeleton /> : <div className="grid cols-2" style={{ gap: 8 }}>
            {[["Running", s.tasks.RUNNING + s.tasks.PLANNING, "info"], ["Queued", s.tasks.QUEUED, "muted"], ["Waiting", s.tasks.WAITING_FOR_APPROVAL, "warn"], ["Failed", s.tasks.FAILED, "err"], ["Completed", s.tasks.COMPLETED, "ok"], ["Approvals", s.approvals_pending, "warn"]].map(([k, v, t]) => (
              <div key={k as string} className="stack" style={{ gap: 0 }}><span className="tiny label">{k}</span><span className="num" style={{ fontSize: 20, fontWeight: 600, color: `var(--${t === "muted" ? "text-2" : t})` }}>{v as number}</span></div>
            ))}
          </div>}
        </Panel>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)" }}>
        <div className="stack" style={{ gap: 16 }}>
          <Panel title="Active agents" icon={<Bot size={15} />} actions={<Link className="btn sm ghost" to="/agents">Control center</Link>}>
            {agents.error ? <ErrorState error={agents.error} retry={() => agents.reload(false)} /> : !agents.data ? <Skeleton rows={2} /> :
              <div className="grid auto-sm" style={{ gap: 10 }}>{agents.data.agents.map((a) => <AgentCard key={a.id} agent={a} compact />)}</div>}
          </Panel>
          <Panel title="Task timeline" icon={<Zap size={15} />} flush>
            {timeline.error ? <div className="panel-body"><ErrorState error={timeline.error} /></div> : !timeline.data ? <div className="panel-body"><Skeleton /></div> :
              timeline.data.tasks.length === 0 ? <EmptyState icon={<ListChecks size={26} />} title="No tasks yet">Ask JARVIS for something that takes a few steps and it will appear here.</EmptyState> :
                <TaskTimeline tasks={timeline.data.tasks} />}
          </Panel>
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <Panel title="Recent activity" icon={<Activity size={15} />} flush><ActivityFeed /></Panel>
          <Panel title="Services" icon={<HardDrive size={15} />}>
            {!health.data ? <Skeleton rows={3} /> : <div className="stack" style={{ gap: 8 }}>
              {Object.entries(health.data.components).map(([k, v]: [string, any]) => (
                <div key={k} className="row between small"><span className="row"><StatusIndicator status={v.status} />{k.replace(/_/g, " ")}</span><span className="muted truncate" style={{ maxWidth: "60%" }} title={v.detail}>{v.detail}</span></div>
              ))}
              <div className="tiny muted">checked {time(new Date().toISOString())} · overall <Badge status={health.data.status} /></div>
            </div>}
          </Panel>
        </div>
      </div>
      <div className="tiny muted">{s ? `Status refreshed ${relative(new Date().toISOString())}` : ""}</div>
    </div>
  );
}
