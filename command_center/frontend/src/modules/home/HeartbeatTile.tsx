/**
 * Herzschlag: zeigt, dass Jarvis sich selbst prüft — nicht nur, ob er läuft.
 * Alles hier ist der echte Stand des letzten Systemchecks; ein Problem steht
 * mit Namen da, statt in einer grünen Gesamtampel zu verschwinden.
 */
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { toast } from "@/lib/toast";
import { Activity } from "@/lib/icons";
import { Panel, Skeleton } from "@/components/ui";
import "./heartbeat.css";

interface Check { id: string; label: string; level: "ok" | "warn" | "err"; detail: string }
interface Beat {
  status: "ok" | "warn" | "err" | "unknown"; beats: number; interval_seconds: number; alive_seconds: number;
  last_at: number | null; next_in: number | null; checks: Check[];
  recent: { id: string; ts: number; text: string; severity: string }[];
}

function span(sec: number): string {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d} T ${h} Std` : h ? `${h} Std ${m} Min` : `${m} Min`;
}
function ago(ts: number | null): string {
  if (!ts) return "noch nie";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  return s < 90 ? "gerade eben" : `vor ${span(s)}`;
}

export function HeartbeatTile() {
  const hb = useApi<Beat>("/api/heartbeat", { interval: 30000 });
  const [busy, setBusy] = useState(false);
  const d = hb.data;

  const now = async () => {
    setBusy(true);
    try { await api.post("/api/heartbeat/beat"); await hb.reload(false); }
    catch { toast({ title: "Prüfung fehlgeschlagen", body: "Der Server hat nicht geantwortet.", tone: "err" }); }
    finally { setBusy(false); }
  };

  const problems = (d?.checks || []).filter((c) => c.level !== "ok");
  const tone = d?.status === "err" ? "err" : d?.status === "warn" ? "warn" : "ok";

  return (
    <Panel title="Herzschlag" icon={<Activity size={15} />}
      actions={<button className="btn sm ghost" onClick={now} disabled={busy}>{busy ? "Prüfe …" : "Jetzt prüfen"}</button>}
      foot={d ? `Prüft sich alle ${Math.round(d.interval_seconds / 60)} Min. von selbst · ${d.beats} Prüfungen bisher` : undefined}>
      {!d ? <Skeleton rows={3} /> : (
        <div className="hb">
          <div className={`hb-heart ${tone}`} aria-hidden><span className="hb-ring" /><span className="hb-core" /></div>
          <div className="hb-body">
            <div className="hb-line"><strong>Schlägt seit {span(d.alive_seconds)}</strong>
              <span className="muted"> · letzte Prüfung {ago(d.last_at)}</span></div>
            {problems.length === 0
              ? <div className="hb-ok">{d.beats ? "Alles in Ordnung, ich habe nichts gefunden." : "Erste Prüfung läuft gleich …"}</div>
              : <ul className="hb-list">{problems.map((c) => (
                  <li key={c.id} className={c.level}><span className="dot" /><strong>{c.label}</strong> — {c.detail}</li>))}</ul>}
            {d.recent.length > 0 && <div className="hb-recent muted small">Zuletzt gemeldet: {d.recent[0].text}</div>}
          </div>
        </div>
      )}
    </Panel>
  );
}
