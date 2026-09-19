import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { Bell, Check, Trash2 } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Panel, Skeleton, StatusIndicator } from "@/components/ui";

const CATEGORIES = ["", "task", "agent", "approval", "server", "workflow", "integration", "deployment", "security", "system"];

export default function NotificationsPage() {
  const [category, setCategory] = useState("");
  const [unread, setUnread] = useState(false);
  const list = useApi<{ notifications: any[]; unread: number }>(`/api/notifications?limit=200&category=${category}&unread=${unread}`, { refreshOn: ["notification.*"] });
  const markAll = async () => { await api.post("/api/notifications/read", {}); list.reload(); };
  const markOne = async (id: string, read: boolean) => { await api.post("/api/notifications/read", { ids: [id], read }); list.reload(); };
  const remove = async (id: string) => { await api.del(`/api/notifications/${id}`); list.reload(); };
  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Notification center{list.data ? ` · ${list.data.unread} unread` : ""}</div><h1>Notifications</h1></div>
        <div className="actions">
          <select className="select" style={{ width: 150 }} value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">{CATEGORIES.map((c) => <option key={c} value={c}>{c || "All categories"}</option>)}</select>
          <button className={`btn sm ${unread ? "primary" : ""}`} onClick={() => setUnread((v) => !v)}>Unread only</button>
          <button className="btn sm" onClick={markAll}><Check />Mark all read</button>
        </div>
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      <Panel title="Inbox" icon={<Bell size={15} />} flush>
        {!list.data ? <div className="panel-body"><Skeleton rows={4} /></div> : list.data.notifications.length === 0 ? <EmptyState icon={<Bell size={26} />} title="No notifications">Task results, approvals, server warnings and integration problems land here.</EmptyState> : (
          <div className="list">{list.data.notifications.map((n) => (
            <div key={n.id} className="list-item" style={{ opacity: n.read ? 0.7 : 1 }}>
              <StatusIndicator status={n.severity === "success" ? "ok" : n.severity === "warning" ? "warning" : n.severity === "error" || n.severity === "critical" ? "error" : "info"} live={!n.read} />
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}><span style={{ fontWeight: n.read ? 400 : 600 }} className="truncate">{n.title}</span><Badge>{n.category}</Badge></div>
                {n.body && <div className="small dim" style={{ wordBreak: "break-word" }}>{n.body}</div>}
                <div className="tiny muted">{relative(n.created_at)}{n.link && <> · <Link to={n.link}>open</Link></>}</div>
              </div>
              <button className="btn icon ghost sm" title={n.read ? "Mark unread" : "Mark read"} onClick={() => markOne(n.id, !n.read)}><Check /></button>
              <button className="btn icon ghost sm" title="Delete" onClick={() => remove(n.id)}><Trash2 /></button>
            </div>))}</div>)}
      </Panel>
    </div>
  );
}
