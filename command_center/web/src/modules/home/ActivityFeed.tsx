import { useEffect, useState } from "react";
import { Link } from "@/lib/router";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import { relative } from "@/lib/format";
import { StatusIndicator, EmptyState } from "@/components/ui";
import { Activity } from "@/lib/icons";

interface Item { id: string; ts: string; text: string; tone: string; link?: string; source: string; }

function fromAudit(a: any): Item {
  const map: Record<string, string> = { "tool.call": `${a.agent_id || a.actor_id} nutzte ${a.tool}`, "task.create": "Aufgabe angelegt", "chat.message": `${a.actor_id} schrieb eine Nachricht`,
    "approval.approved": `${a.actor_id} genehmigte ${a.tool}`, "approval.rejected": `${a.actor_id} lehnte ${a.tool} ab`, "auth.login": `${a.actor_id} hat sich angemeldet`,
    "workflow.trigger": `Workflow ausgelöst: ${a.target}`, "file.upload": `Datei hochgeladen: ${a.target}`, "gateway.command": `Desktop-Befehl von ${a.actor_id}` };
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
  useEvent("task.created", (ev) => push({ id: `t${ev.data.id}`, ts: ev.data.created_at, text: `Aufgabe angelegt: ${ev.data.title}`, tone: "info", source: "tasks", link: `/tasks/${ev.data.id}` }));
  useEvent("task.updated", (ev) => { if (["COMPLETED", "FAILED"].includes(ev.data.status)) push({ id: `tu${ev.data.id}${ev.data.status}`, ts: ev.data.updated_at, text: `Aufgabe ${ev.data.status === "COMPLETED" ? "erledigt" : "fehlgeschlagen"}: ${ev.data.title}`, tone: ev.data.status === "COMPLETED" ? "ok" : "err", source: "tasks", link: `/tasks/${ev.data.id}` }); });
  useEvent("approval.requested", (ev) => push({ id: `a${ev.data.id}`, ts: ev.data.created_at, text: `Freigabe angefordert: ${ev.data.action} → ${ev.data.target}`, tone: "warn", source: "approvals", link: `/approvals/${ev.data.id}` }));
  useEvent("file.changed", (ev) => push({ id: `f${ev.data.path}${Date.now()}`, ts: new Date().toISOString(), text: `Datei ${ev.data.source === "upload" ? "hochgeladen" : "erstellt"}: ${ev.data.path}`, tone: "info", source: "files", link: "/files" }));
  useEvent("workflow.triggered", (ev) => push({ id: `w${Date.now()}`, ts: new Date().toISOString(), text: `Workflow ausgeführt: ${ev.data.workflow_id}`, tone: "info", source: "workflows", link: "/workflows" }));
  useEvent("integration.status", (ev) => { if (ev.data.status === "offline") push({ id: `i${ev.data.id}${Date.now()}`, ts: new Date().toISOString(), text: `Integration getrennt: ${ev.data.name}`, tone: "err", source: "integrations", link: "/integrations" }); });

  if (!items.length) return <EmptyState icon={<Activity size={26} />} title="Noch keine Aktivität">Alles Wichtige, was passiert, erscheint hier.</EmptyState>;
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
