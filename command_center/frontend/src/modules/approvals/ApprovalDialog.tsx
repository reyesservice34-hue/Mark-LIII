import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useEvent } from "@/lib/events";
import { useAuth } from "@/lib/auth";
import { relative } from "@/lib/format";
import { ShieldCheck } from "@/lib/icons";
import { Badge, KeyValue, Modal } from "@/components/ui";
import { toast } from "@/lib/toast";

export interface Approval {
  id: string; action: string; reason: string; target: string; risk: string; requested_by: string; agent_id: string;
  status: string; created_at: string; expires_at: string; decided_by?: string; decision_note?: string; code: string;
  task_id?: string; run_id?: string; payload: any;
}

/** Global approval prompt: appears wherever the user is when an agent asks. */
export function ApprovalDialog() {
  const { can } = useAuth();
  const [queue, setQueue] = useState<Approval[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ approvals: Approval[] }>("/api/approvals?status=pending").then((r) => setQueue(r.approvals)).catch(() => undefined);
  }, []);
  useEvent("approval.requested", (ev) => setQueue((q) => q.some((a) => a.id === ev.data.id) ? q : [...q, ev.data]));
  useEvent("approval.decided", (ev) => setQueue((q) => q.filter((a) => a.id !== ev.data.id)));

  const current = queue[0];
  if (!current || !can("operator")) return null;

  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      await api.post(`/api/approvals/${current.id}/${approve ? "approve" : "reject"}`, { note });
      toast({ title: approve ? "Approved" : "Rejected", body: current.action, tone: approve ? "ok" : "warn" });
      setNote("");
    } catch (e: any) { toast({ title: "Decision failed", body: e.message, tone: "err" }); }
    finally { setBusy(false); }
  };

  return (
    <Modal title={<span className="row"><ShieldCheck size={16} style={{ color: "var(--warn)" }} />Approval required{queue.length > 1 && <span className="badge warn">{queue.length} pending</span>}</span>} onClose={() => setQueue((q) => q.slice(1).concat(q[0]))}
      foot={<>
        <button className="btn danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
        <button className="btn success" disabled={busy} onClick={() => decide(true)} autoFocus>Approve</button>
      </>}>
      <ApprovalDetails a={current} />
      <div className="field" style={{ marginTop: 14 }}>
        <label htmlFor="apr-note">Note (optional, recorded in the audit trail)</label>
        <input id="apr-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why you decided this way" />
      </div>
    </Modal>
  );
}

export function ApprovalDetails({ a }: { a: Approval }) {
  return (
    <div className="stack">
      <div className="row wrap">
        <Badge status={a.risk === "critical" || a.risk === "high" ? "error" : "warning"}>{a.risk} risk</Badge>
        <Badge status={a.status} />
        <span className="small muted">requested {relative(a.created_at)} · expires {relative(a.expires_at)}</span>
      </div>
      <KeyValue items={[
        ["Requested action", <code>{a.action}</code>],
        ["Reason", a.reason || "—"],
        ["Affected system / target", <code>{a.target || "—"}</code>],
        ["Requested by agent", a.agent_id ? <code>{a.agent_id}</code> : <span className="muted">— (user action)</span>],
        ["On behalf of", a.requested_by],
        ["Approval code", <code>{a.code}</code>],
        ...(a.task_id ? [["Task", <a href={`/tasks/${a.task_id}`}>{a.task_id}</a>] as [any, any]] : []),
        ...(a.decided_by ? [["Decided by", `${a.decided_by}${a.decision_note ? ` — ${a.decision_note}` : ""}`] as [any, any]] : []),
      ]} />
      {a.payload && Object.keys(a.payload).length > 0 && (
        <details><summary className="small muted" style={{ cursor: "pointer" }}>Full request payload</summary>
          <pre className="md" style={{ marginTop: 8 }}><code>{JSON.stringify(a.payload, null, 2)}</code></pre></details>
      )}
    </div>
  );
}
