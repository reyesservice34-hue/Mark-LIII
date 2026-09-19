import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { Icon, RefreshCw, Plug } from "@/lib/icons";
import { Badge, ErrorState, Skeleton, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";

export default function IntegrationsPage() {
  const { can } = useAuth();
  const list = useApi<{ integrations: any[]; summary: any }>("/api/integrations", { refreshOn: ["integration.status"] });
  const [busy, setBusy] = useState<string | null>(null);
  const check = async (id: string) => { setBusy(id); try { await api.post(`/api/integrations/${id}/check`); } catch (e: any) { toast({ title: "Check failed", body: e.message, tone: "err" }); } finally { setBusy(null); } };
  const checkAll = async () => { setBusy("*"); try { await api.post("/api/integrations/check-all"); list.reload(); } finally { setBusy(null); } };
  const s = list.data?.summary;
  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Integration registry{s ? ` · ${s.connected} connected · ${s.configured} configured · ${s.total} available` : ""}</div><h1>Integrations</h1></div>
        {can("operator") && <div className="actions"><button className="btn sm" onClick={checkAll} disabled={busy === "*"}><RefreshCw style={busy === "*" ? { animation: "spin 1s linear infinite" } : undefined} />Check all</button></div>}
      </div>
      <ErrorState error={list.error} retry={() => list.reload(false)} />
      <p className="small muted">Credentials are read from the server environment only and are never shown here. A card shows NOT CONNECTED until the required variables are set; “checked” means a real request to the service succeeded.</p>
      {!list.data ? <Skeleton rows={4} height={60} /> : (
        <div className="grid auto">
          {list.data.integrations.map((i) => (
            <div key={i.id} className="panel" style={{ padding: 14, gap: 10 }}>
              <div className="row between">
                <span className="row"><span className="agent-avatar" style={{ width: 32, height: 32 }}><Icon name={i.icon} size={16} /></span><div><div style={{ fontWeight: 600 }}>{i.name}</div><div className="tiny muted">{i.kind}</div></div></span>
                <Badge status={i.status === "not_configured" ? "offline" : i.status}>{i.status === "not_configured" ? "not connected" : i.status}</Badge>
              </div>
              <div className="small" style={{ minHeight: 18 }}>{i.status === "not_configured" ? <span className="muted">Set {[...i.required_env, ...(i.any_env.length ? [i.any_env.join(" or ")] : [])].map((k: string) => <code key={k} style={{ marginRight: 4 }}>{k}</code>)}</span> : <span className={i.status === "healthy" ? "dim" : ""} style={i.status === "offline" ? { color: "var(--err)" } : undefined}>{i.detail}</span>}</div>
              <div className="row wrap" style={{ gap: 5 }}>{i.capabilities.map((c: string) => <span key={c} className={`badge ${c.includes("planned") ? "muted" : ""}`}>{c}</span>)}</div>
              <div className="row between tiny muted">
                <span className="row" style={{ gap: 6 }}>{Object.entries(i.config_state).map(([k, v]: any) => <span key={k} className="row" style={{ gap: 3 }} title={k}><StatusIndicator status={v ? "ok" : "offline"} />{k.replace(/^(JARVIS_CC_|JARVIS_)/, "").slice(0, 18)}</span>)}</span>
                <span>{i.last_checked_at ? `checked ${relative(i.last_checked_at)}` : "never checked"}</span>
              </div>
              {can("operator") && <button className="btn sm" onClick={() => check(i.id)} disabled={busy === i.id || !i.configured}><Plug />{busy === i.id ? "Checking…" : "Check connection"}</button>}
            </div>
          ))}
        </div>)}
    </div>
  );
}
