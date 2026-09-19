import { useEffect, useRef } from "react";
import { time } from "@/lib/format";
import { Activity, X } from "@/lib/icons";
import { EmptyState, StatusIndicator } from "@/components/ui";
import type { RunState } from "./types";

/** Live "what is JARVIS doing" panel — operational steps only, never private reasoning. */
export function ExecutionPanel({ run, onClose }: { run: RunState | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [run?.steps.length]);
  const active = run && ["planning", "executing", "waiting", "delegated"].includes(run.status);
  return (
    <div className="panel exec-panel" style={{ height: "100%" }}>
      <div className="panel-head" style={{ padding: "10px 12px" }}>
        <h2><Activity size={14} />Execution</h2>
        <div className="row">{run && <StatusIndicator status={run.status === "failed" ? "error" : run.status === "waiting" ? "warning" : active ? "running" : run.status} label={run.label} live={!!active} />}<button className="btn icon ghost sm" onClick={onClose} aria-label="Hide execution panel"><X /></button></div>
      </div>
      <div className="exec-list" ref={ref}>
        {!run ? <EmptyState icon={<Activity size={24} />} title="Idle">When JARVIS works, each step appears here: planning, tool activity, delegation, approvals, results.</EmptyState> :
          run.steps.length === 0 ? <EmptyState title={run.label || "Starting"} /> :
            run.steps.map((s, i) => (
              <div key={i} className={`exec-step ${s.kind} ${s.ok === false ? "err" : ""}`}>
                <span className="pin" />
                <span><span className="t">{s.kind === "tool_call" ? "→ " : ""}{s.text}</span> <span className="ts">{time(s.ts)}</span></span>
              </div>
            ))}
        {active && <div className="exec-step"><span className="pin" style={{ animation: "pulse 1s infinite", background: "var(--accent)" }} /><span className="t muted">working…</span></div>}
      </div>
      {run && <div className="panel-foot row between"><span>agent <code>{run.agent_id}</code></span><span>run <code>{run.id.slice(-6)}</code></span></div>}
    </div>
  );
}
