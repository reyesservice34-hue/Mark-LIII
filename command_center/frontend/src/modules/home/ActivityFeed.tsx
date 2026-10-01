import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import { relative } from "@/lib/format";
import { StatusIndicator, EmptyState } from "@/components/ui";
import { Activity } from "@/lib/icons";

interface Item { id: string; ts: string; text: string; tone: string; link?: string; source: string; }

function fromAudit(a: any): Item {
  const map: Record<string, string> = { "tool.call": `${a.agent_id || a.actor_id} used ${a.tool}`, "task.create": "Task created", "chat.message": `${a.actor_id} sent a message`,
    "approval.approved": `${a.actor_id} approved ${a.tool}`, "approval.rejected": `${a.actor_id} rejected ${a.tool}`, "auth.login": `${a.actor_id} signed in`,
    "workflow.trigger": `Workflow triggered: ${a.target}`, "file.upload": `File uploaded: ${a.target}`, "gateway.command": `Desktop command from ${a.actor_id}` };
  return { id: a.id, ts: a.ts, text: map[a.action] || `${a.actor_id}: ${a.action} ${a.target || ""}`, tone: a.status === "ok" ? "ok" : a.status === "denied" ? "warn" : "err",
    source: "audit", link: a.task_id ? `/tasks/${a.task_id}` : undefined };
}

export function ActivityFeed({ limit = 25 }: { limit?: number }) {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    api.get<{ events: any[] }>(`/api/logs/audit?limit=${limit}`).then((r) => setItems(r.events.map(fromAudit))).catch(() => undefined);
  }, [limit]);
  const push = (it: Item) => setItems((l) => [it, ...l.filter((x) => x.id !== it.id)].slice(0, limit));
  useEvent("audit.event", (ev) => push(fromAudit(ev.data)));
  useEvent("task.created", (ev) => push({ id: `t${ev.data.id}`, ts: ev.data.created_at, text: `Task created: ${ev.data.title}`, tone: "info", source: "tasks", link: `/tasks/${ev.data.id}` }));
  useEvent("task.updated", (ev) => { if (["COMPLETED", "FAILED"].includes(ev.data.status)) push({ id: `tu${ev.data.id}${ev.data.status}`, ts: ev.data.updated_at, text: `Task ${ev.data.status.toLowerCase()}: ${ev.data.title}`, tone: ev.data.status === "COMPLETED" ? "ok" : "err", source: "tasks", link: `/tasks/${ev.data.id}` }); });
  useEvent("approval.requested", (ev) => push({ id: `a${ev.data.id}`, ts: ev.data.created_at, text: `Approval requested: ${ev.data.action} → ${ev.data.target}`, tone: "warn", source: "approvals", link: `/approvals/${ev.data.id}` }));
  useEvent("file.changed", (ev) => push({ id: `f${ev.data.path}${Date.now()}`, ts: new Date().toISOString(), text: `File ${ev.data.source === "upload" ? "uploaded" : "created"}: ${ev.data.path}`, tone: "info", source: "files", link: "/files" }));
  useEvent("workflow.triggered", (ev) => push({ id: `w${Date.now()}`, ts: new Date().toISOString(), text: `Workflow executed: ${ev.data.workflow_id}`, tone: "info", source: "workflows", link: "/workflows" }));
  useEvent("integration.status", (ev) => { if (ev.data.status === "offline") push({ id: `i${ev.data.id}${Date.now()}`, ts: new Date().toISOString(), text: `Integration disconnected: ${ev.data.name}`, tone: "err", source: "integrations", link: "/integrations" }); });

  if (!items.length) return <EmptyState icon={<Activity size={26} />} title="No activity yet">Everything meaningful that happens shows up here.</EmptyState>;
  return (
    <div className="list" style={{ maxHeight: 420, overflow: "auto" }}>
      {items.map((it) => (
        <div key={it.id} className="list-item" style={{ padding: "8px 16px" }}>
          <StatusIndicator status={it.tone} />
          <div className="grow small truncate">{it.link ? <Link to={it.link} style={{ color: "inherit" }}>{it.text}</Link> : it.text}</div>
          <span className="tiny muted num" style={{ flex: "none" }}>{relative(it.ts)}</span>
        </div>
      ))}
    </div>
  );
}
