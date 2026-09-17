import { Link } from "react-router-dom";
import { relative } from "@/lib/format";
import { Badge } from "@/components/ui";

const ORDER = ["QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"];

export function TaskTimeline({ tasks }: { tasks: any[] }) {
  return (
    <div className="list">
      {tasks.map((t) => {
        const idx = ORDER.indexOf(t.status);
        const stage = t.status === "COMPLETED" ? 4 : t.status === "FAILED" || t.status === "CANCELLED" ? 4 : Math.min(idx, 3);
        return (
          <Link key={t.id} to={`/tasks/${t.id}`} className="list-item clickable" style={{ color: "inherit" }}>
            <div style={{ display: "flex", gap: 3, flex: "none" }} aria-hidden>
              {[0, 1, 2, 3, 4].map((i) => <span key={i} style={{ width: 14, height: 4, borderRadius: 2, background: i <= stage ? (t.status === "FAILED" ? "var(--err)" : t.status === "CANCELLED" ? "var(--text-4)" : t.status === "WAITING_FOR_APPROVAL" ? "var(--warn)" : "var(--accent)") : "var(--bg-4)", opacity: i === stage && idx < 5 ? 1 : 0.9, animation: i === stage && idx < 5 ? "pulse 1.5s infinite" : undefined }} />)}
            </div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="truncate">{t.parent_id && <span className="muted">↳ </span>}{t.title}</div>
              <div className="small muted truncate">{t.assigned_agent || "unassigned"} · {t.priority} · {relative(t.updated_at)}{t.error && <span style={{ color: "var(--err)" }}> · {t.error}</span>}</div>
            </div>
            <Badge status={t.status} />
          </Link>
        );
      })}
    </div>
  );
}
