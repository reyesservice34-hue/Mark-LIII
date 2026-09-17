import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { duration, ms, relative } from "@/lib/format";
import { Timer, Play } from "@/lib/icons";
import { Badge, ErrorState, Panel, Skeleton, Toggle } from "@/components/ui";
import { toast } from "@/lib/toast";

export default function AutomationsPage() {
  const { can } = useAuth();
  const jobs = useApi<{ jobs: any[] }>("/api/automations/jobs", { refreshOn: ["job.updated"], interval: 15000 });
  const run = async (id: string) => { try { await api.post(`/api/automations/jobs/${id}/run`); } catch (e: any) { toast({ title: "Run failed", body: e.message, tone: "err" }); } };
  const toggle = async (id: string, on: boolean) => { try { await api.post(`/api/automations/jobs/${id}/${on ? "enable" : "disable"}`); jobs.reload(); } catch (e: any) { toast({ title: "Change failed", body: e.message, tone: "err" }); } };
  return (
    <div className="page">
      <div className="page-head"><div><div className="eyebrow">Background jobs</div><h1>Automations</h1></div></div>
      <ErrorState error={jobs.error} retry={() => jobs.reload(false)} />
      <Panel title="Scheduled jobs" icon={<Timer size={15} />} flush foot="These are the command center's own recurring jobs (sampling, health checks, housekeeping). External workflows live under Workflows.">
        {!jobs.data ? <div className="panel-body"><Skeleton rows={4} /></div> : (
          <table className="table">
            <thead><tr><th>Job</th><th>Interval</th><th>Last run</th><th>Result</th><th>Runs</th><th>Enabled</th><th /></tr></thead>
            <tbody>{jobs.data.jobs.map((j) => (
              <tr key={j.id}>
                <td><div>{j.name}</div><div className="tiny muted">{j.description}</div></td>
                <td className="num small">{duration(j.interval_seconds)}</td>
                <td className="small">{j.last_run_at ? relative(j.last_run_at) : "never"}{j.enabled && j.next_run_in != null && <div className="tiny muted">next in {duration(j.next_run_in)}</div>}</td>
                <td>{j.running ? <span className="row"><span className="spinner" />running</span> : <><Badge status={j.last_status === "ok" ? "ok" : j.last_status === "never" ? "unknown" : "error"} />{j.last_error && <div className="tiny" style={{ color: "var(--err)", maxWidth: 320 }}>{j.last_error}</div>}</>}</td>
                <td className="num small">{j.runs}{j.errors ? <span style={{ color: "var(--err)" }}> / {j.errors} err</span> : ""} · {ms(j.last_duration_ms)}</td>
                <td><Toggle checked={j.enabled} onChange={(v) => can("admin") && toggle(j.id, v)} label={`Enable ${j.name}`} /></td>
                <td style={{ textAlign: "right" }}>{can("operator") && <button className="btn sm" onClick={() => run(j.id)} disabled={j.running}><Play />Run now</button>}</td>
              </tr>))}</tbody>
          </table>)}
      </Panel>
    </div>
  );
}
