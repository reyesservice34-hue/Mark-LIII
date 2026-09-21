import { Link } from "react-router-dom";
import { relative } from "@/lib/format";
import { Badge } from "@/components/ui";

const ORDER = ["QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"];

// Drei Gruppen statt einer flachen Liste: "läuft er gerade dran", "wartet
// er/braucht es dich" und "vorbei" — auf einen Blick, ohne erst den
// Status-Filter zu bemühen. WAITING_FOR_APPROVAL steht bewusst bei "wartet",
// nicht bei "läuft", weil dort jemand hin muss, nicht nur zuschauen kann.
const GROUPS: { key: string; title: string; statuses: string[] }[] = [
  { key: "running", title: "Läuft gerade", statuses: ["RUNNING", "PLANNING"] },
  { key: "waiting", title: "Wartet / angehalten", statuses: ["QUEUED", "WAITING_FOR_APPROVAL", "PAUSED"] },
  { key: "done", title: "Erledigt / gestoppt", statuses: ["COMPLETED", "FAILED", "CANCELLED"] },
];
const PRIORITY_RANK: Record<string, number> = { critical: 3, high: 2, normal: 1, low: 0 };

function TaskRow({ t }: { t: any }) {
  const idx = ORDER.indexOf(t.status);
  const stage = t.status === "COMPLETED" ? 4 : t.status === "FAILED" || t.status === "CANCELLED" ? 4 : Math.min(idx, 3);
  return (
    <Link to={`/tasks/${t.id}`} className="list-item clickable" style={{ color: "inherit" }}>
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
}

export function TaskTimeline({ tasks, grouped = true }: { tasks: any[]; grouped?: boolean }) {
  if (!grouped) {
    return <div className="list">{tasks.map((t) => <TaskRow key={t.id} t={t} />)}</div>;
  }
  const byPriority = (a: any, b: any) =>
    (PRIORITY_RANK[b.priority] ?? 1) - (PRIORITY_RANK[a.priority] ?? 1) || (b.updated_at || "").localeCompare(a.updated_at || "");
  const groups = GROUPS.map((g) => ({ ...g, tasks: tasks.filter((t) => g.statuses.includes(t.status)).sort(byPriority) }))
    .filter((g) => g.tasks.length > 0);
  // Mit nur einer Gruppe (z. B. schon per Statusfilter eingegrenzt) bringt
  // die Überschrift nichts — dann wie zuvor eine schlichte, sortierte Liste.
  if (groups.length <= 1) {
    return <div className="list">{[...tasks].sort(byPriority).map((t) => <TaskRow key={t.id} t={t} />)}</div>;
  }
  return (
    <div className="stack" style={{ gap: 4 }}>
      {groups.map((g) => (
        <div key={g.key}>
          <div className="tiny muted" style={{ padding: "8px 12px 4px" }}>{g.title} · {g.tasks.length}</div>
          <div className="list">{g.tasks.map((t) => <TaskRow key={t.id} t={t} />)}</div>
        </div>
      ))}
    </div>
  );
}
