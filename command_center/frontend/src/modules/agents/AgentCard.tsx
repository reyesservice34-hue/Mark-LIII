import { Link } from "react-router-dom";
import { relative } from "@/lib/format";
import { Icon } from "@/lib/icons";
import { Badge, StatusIndicator } from "@/components/ui";
import "./agents.css";

export interface Agent {
  id: string; name: string; description: string; role: string; kind: string; capabilities: string[]; tools: string[];
  tools_resolved: { name: string; available: boolean }[]; available_tools: number; model: string; provider: string;
  enabled: boolean; icon: string; status: string; current_task_id: string | null; current_run_id: string | null;
  current_activity: string; last_activity_at: string | null; last_error: string; started_at: string | null;
  stats: { runs: number; errors: number; tool_calls: number; completed: number }; health: string;
  missing_tools?: string[]; health_detail?: string;
}

export function AgentCard({ agent, compact = false, onClick }: { agent: Agent; compact?: boolean; onClick?: () => void }) {
  const active = ["THINKING", "EXECUTING", "WAITING"].includes(agent.status);
  return (
    <div className={`agent-card ${active ? "active" : ""} ${agent.status === "OFFLINE" ? "offline" : ""}`} onClick={onClick} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined}
      onKeyDown={(e) => { if (onClick && (e.key === "Enter" || e.key === " ")) onClick(); }}>
      <div className="agent-avatar"><Icon name={agent.icon} size={18} />{active && <span className="halo" />}</div>
      <div className="grow" style={{ minWidth: 0 }}>
        <div className="row between"><strong className="truncate">{agent.name}</strong><StatusIndicator status={agent.status === "ERROR" ? "error" : agent.status === "OFFLINE" ? "offline" : active ? "running" : "idle"} label={agent.status} live={active} /></div>
        <div className="small muted truncate" title={agent.role}>{agent.role}</div>
        {!compact && <div className="small" style={{ marginTop: 6 }}>{agent.description}</div>}
        <div className="small" style={{ marginTop: 6 }}>
          {active ? <span className="dim">{agent.current_activity || "working"}{agent.current_task_id && <> · <Link to={`/tasks/${agent.current_task_id}`} onClick={(e) => e.stopPropagation()}>task</Link></>}</span>
            : agent.status === "ERROR" ? <span style={{ color: "var(--err)" }} className="truncate">{agent.last_error}</span>
              : <span className="muted">{agent.last_activity_at ? `last active ${relative(agent.last_activity_at)}` : "no activity yet"}</span>}
        </div>
        {!compact && <>
          <div className="row wrap" style={{ marginTop: 8, gap: 6 }}>
            <Badge status={agent.health} />
            <span className="badge muted">{agent.available_tools}/{agent.tools_resolved.length} tools</span>
            <span className="badge muted">{agent.stats.runs} runs</span>
            {agent.stats.errors > 0 && <span className="badge err">{agent.stats.errors} errors</span>}
          </div>
          {agent.health_detail && <div className="tiny" style={{ color: "var(--warn)", marginTop: 5 }}>{agent.health_detail}</div>}
        </>}
      </div>
    </div>
  );
}
