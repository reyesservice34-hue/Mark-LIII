import { useState } from "react";
import { useParams } from "@/lib/router";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { ShieldCheck } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Panel, Skeleton } from "@/components/ui";
import { ApprovalDetails, type Approval } from "./ApprovalDialog";

export default function ApprovalsPage() {
  const { approvalId } = useParams();
  const { can } = useAuth();
  const [filter, setFilter] = useState("");
  const { data, error, loading, reload } = useApi<{ approvals: Approval[]; pending: number }>(`/api/approvals?limit=200${filter ? `&status=${filter}` : ""}`, { refreshOn: ["approval.*"] });
  const [open, setOpen] = useState<string | null>(approvalId || null);
  const list = data?.approvals || [];
  const selected = list.find((a) => a.id === open);

  const decide = async (a: Approval, approve: boolean) => {
    await api.post(`/api/approvals/${a.id}/${approve ? "approve" : "reject"}`, {});
    reload();
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Freigabe-Schleuse{data ? ` · ${data.pending} offen` : ""}</div><h1>Freigaben</h1></div>
        <div className="actions">
          <div className="seg" role="group" aria-label="Statusfilter">
            {([["", "Alle"], ["pending", "Offen"], ["approved", "Genehmigt"], ["rejected", "Abgelehnt"], ["expired", "Abgelaufen"]] as const).map(([value, label]) => (
              <button key={value} type="button" className={filter === value ? "on" : ""} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>
            ))}
          </div>
        </div>
      </div>
      <ErrorState error={error} retry={() => reload(false)} />
      <div className={selected ? "ops-split detail" : ""}>
        <Panel title="Requests" icon={<ShieldCheck size={15} />} flush>
          {loading && !data ? <div className="panel-body"><Skeleton rows={4} /></div> : list.length === 0 ?
            <EmptyState icon={<ShieldCheck size={28} />} title="No approval requests">High-impact actions from agents will pause here until you decide.</EmptyState> :
            <div className="list">
              {list.map((a) => (
                <div key={a.id} className={`list-item clickable ${open === a.id ? "active" : ""}`} onClick={() => setOpen(a.id)}>
                  <Badge status={a.status} />
                  <div className="grow" style={{ minWidth: 0 }}>
                    <div className="truncate"><code>{a.action}</code> <span className="muted">→</span> {a.target}</div>
                    <div className="small muted truncate">{a.reason} · by {a.agent_id || a.requested_by} · {relative(a.created_at)}</div>
                  </div>
                  <Badge status={a.risk === "high" || a.risk === "critical" ? "error" : "warning"}>{a.risk}</Badge>
                  {a.status === "pending" && can("operator") && (
                    <span className="row" onClick={(e) => e.stopPropagation()}>
                      <button className="btn sm danger" onClick={() => decide(a, false)}>Reject</button>
                      <button className="btn sm success" onClick={() => decide(a, true)}>Approve</button>
                    </span>
                  )}
                </div>
              ))}
            </div>}
        </Panel>
        {selected && <Panel title="Details" actions={<button className="btn sm ghost" onClick={() => setOpen(null)}>Close</button>}><ApprovalDetails a={selected} /></Panel>}
      </div>
    </div>
  );
}
