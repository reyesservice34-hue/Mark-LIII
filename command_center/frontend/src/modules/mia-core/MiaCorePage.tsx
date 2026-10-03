import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { bytes, relative } from "@/lib/format";
import { Cpu, Search, HardDrive, RefreshCw } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import "./mia-core.css";

interface Layer {
  id: string; label: string; color: string; purpose: string; path: string;
  status: "active" | "missing" | "error"; size: number | null; modified_at: string | null;
  entries: number | null; detail?: string;
}

export default function MiaCorePage() {
  const ov = useApi<{ root_connected: boolean; layers: Layer[] }>("/api/mia-core/overview", { interval: 60000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(50);
  const det = useApi<{ entries: any[]; total: number }>(
    selected ? `/api/mia-core/${selected}/entries?limit=${limit}&q=${encodeURIComponent(q)}` : null
  );
  const layer = ov.data?.layers.find((l) => l.id === selected) || null;

  const open = (l: Layer) => { setSelected(l.id === selected ? null : l.id); setQ(""); setLimit(50); };

  return (
    <div className="page mia-core">
      <div className="page-head">
        <div>
          <div className="eyebrow">Kapitel 43 · nur lesend</div>
          <h1><Cpu size={20} style={{ marginRight: 6, verticalAlign: -3 }} />MIA Core</h1>
        </div>
        <div className="actions">
          <StatusIndicator status={ov.data?.root_connected ? "ok" : "offline"}
            label={ov.data?.root_connected ? "Mark-LIII verbunden" : "Mark-LIII nicht erreichbar"} />
          <button className="btn sm" onClick={() => ov.reload(false)}><RefreshCw size={13} />Aktualisieren</button>
        </div>
      </div>
      <ErrorState error={ov.error} retry={() => ov.reload(false)} />

      {!ov.data ? <Skeleton rows={4} height={70} /> : (
        <div className="mc-layers">
          {ov.data.layers.map((l) => (
            <div key={l.id} className={`mc-card ${selected === l.id ? "active" : ""}`}
              style={{ ["--mc-card-color" as any]: `var(--mc-${l.color})`, ["--mc-card-soft" as any]: `var(--mc-${l.color}-soft)` }}
              onClick={() => open(l)} role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") open(l); }}>
              <div className="mc-card-head">
                <span className="row" style={{ gap: 8 }}><span className="mc-dot" /><strong>{l.label}</strong></span>
                <Badge status={l.status === "active" ? "ok" : l.status === "error" ? "error" : "offline"}>
                  {l.status === "active" ? "aktiv" : l.status === "error" ? "fehler" : "fehlt"}
                </Badge>
              </div>
              <div className="mc-purpose">{l.purpose}</div>
              <div className="mc-stats">
                <div className="mc-stat"><span className="num">{l.entries ?? "wird bei Bedarf berechnet"}</span><span className="label">Einträge</span></div>
                <div className="mc-stat"><span className="num">{l.size != null ? bytes(l.size) : "—"}</span><span className="label">Größe</span></div>
                <div className="mc-stat"><span className="num">{l.modified_at ? relative(l.modified_at) : "—"}</span><span className="label">Geändert</span></div>
              </div>
              {l.detail && <div className="tiny" style={{ color: "var(--err)", marginTop: 8 }}>{l.detail}</div>}
              <div className="tiny muted" style={{ marginTop: 8 }}>{l.path}</div>
            </div>
          ))}
        </div>
      )}

      {layer && (
        <Panel
          title={<span className="mc-detail-head"><HardDrive size={15} />{layer.label}</span>}
          actions={<div className="row" style={{ gap: 6 }}>
            <Search size={14} style={{ color: "var(--text-3)" }} />
            <input className="input sm" placeholder="Suchen in dieser Ebene …" value={q}
              onChange={(e) => { setQ(e.target.value); setLimit(50); }} style={{ width: 220 }} />
          </div>}
          foot="Nur Lesezugriff. Ändern oder Löschen ist hier bewusst nicht möglich — Kapitel 43.7."
          flush
        >
          {det.error ? <div className="panel-body"><ErrorState error={det.error} /></div> :
            !det.data ? <div className="panel-body"><Skeleton rows={4} /></div> :
              det.data.entries.length === 0 ? <EmptyState icon={<HardDrive size={22} />}
                title={q ? "Nichts gefunden" : "Keine Einträge"}>
                {q ? "Andere Suche versuchen." : "Diese Ebene ist leer oder nicht vorhanden."}
              </EmptyState> : (
                <div className="stack" style={{ gap: 8, padding: "10px 14px" }}>
                  {det.data.entries.map((e, i) => (
                    <div key={i} className="mc-entry">{
                      typeof e === "object" ? JSON.stringify(e, null, 2) : String(e)
                    }</div>
                  ))}
                  <div className="row between" style={{ marginTop: 4 }}>
                    <span className="tiny muted">{det.data.entries.length} von {det.data.total}</span>
                    {det.data.total > det.data.entries.length &&
                      <button className="btn sm" onClick={() => setLimit((n) => n + 50)}>Weitere laden</button>}
                  </div>
                </div>
              )}
        </Panel>
      )}
    </div>
  );
}
