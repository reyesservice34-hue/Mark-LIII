import { useMemo, useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { Icon } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Modal, Panel, Skeleton, Toggle } from "@/components/ui";
import { toast } from "@/lib/toast";

interface OfficeAgent {
  id: string; name: string; icon: string; department: string; status: string; health: string;
  current_activity: string; current_task_id: string | null; queue: number; enabled: boolean;
  capabilities: string[]; tools_resolved: { name: string; available: boolean }[];
  rights: { autonomy: string; categories: string[] };
  connected_to_mia: boolean;
}

interface BoardResponse {
  agents: OfficeAgent[]; departments: string[]; categories: string[]; autonomy_levels: string[];
}

const AUTONOMY_LABEL: Record<string, string> = {
  readonly: "Nur lesen", approval: "Mit Freigabe", full: "Autonom",
};

export default function AgentsOfficePage() {
  const { can } = useAuth();
  const board = useApi<BoardResponse>("/api/agents-office", { refreshOn: ["agent.status", "agent.updated", "task.*"] });
  const [rightsFor, setRightsFor] = useState<OfficeAgent | null>(null);

  const grouped = useMemo(() => {
    const m = new Map<string, OfficeAgent[]>();
    for (const a of board.data?.agents || []) {
      if (!m.has(a.department)) m.set(a.department, []);
      m.get(a.department)!.push(a);
    }
    return m;
  }, [board.data]);

  if (board.error) return <div className="page"><ErrorState error={board.error} retry={() => board.reload(false)} /></div>;
  if (!board.data) return <div className="page"><Skeleton rows={4} height={110} /></div>;
  if (board.data.agents.length === 0) return <div className="page"><EmptyState title="Keine Agenten registriert" /></div>;

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="eyebrow">Digitales Unternehmen</div><h1>Agenten Office</h1></div>
      </div>
      <div className="stack" style={{ gap: 22 }}>
        {[...grouped.entries()].map(([dept, agents]) => (
          <div key={dept} className="stack" style={{ gap: 10 }}>
            <div className="label" style={{ fontSize: 12, letterSpacing: ".08em" }}>{dept.toUpperCase()}</div>
            <div className="grid auto" style={{ gap: 12 }}>
              {agents.map((a) => (
                <Panel key={a.id}
                  title={<span className="row"><Icon name={a.icon} size={15} />{a.name}{a.connected_to_mia && <span className="badge muted" style={{ marginLeft: 6 }} title="Mit Mia verbunden">↔ Mia</span>}</span>}
                  actions={can("admin") && <button className="btn sm ghost" onClick={() => setRightsFor(a)}>Rechte</button>}>
                  <div className="panel-body stack" style={{ gap: 10 }}>
                    <div className="row wrap" style={{ gap: 6 }}>
                      <Badge status={a.status === "ERROR" ? "error" : a.status === "OFFLINE" ? "offline" : a.status} />
                      <Badge status={a.health} />
                      {!a.enabled && <span className="badge err">deaktiviert</span>}
                      <span className="badge muted">{AUTONOMY_LABEL[a.rights.autonomy] || a.rights.autonomy}</span>
                    </div>
                    <div className="small muted">{a.current_activity || "Keine laufende Tätigkeit"}</div>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="small">Warteschlange: <strong>{a.queue}</strong></span>
                      <span className="small muted">{a.tools_resolved.filter((t) => t.available).length} Werkzeuge verfügbar</span>
                    </div>
                  </div>
                </Panel>
              ))}
            </div>
          </div>
        ))}
      </div>
      {rightsFor && (
        <RightsModal agent={rightsFor} categories={board.data.categories} autonomyLevels={board.data.autonomy_levels}
          onClose={() => setRightsFor(null)} onSaved={() => { setRightsFor(null); board.reload(); }} />
      )}
    </div>
  );
}

function RightsModal({ agent, categories, autonomyLevels, onClose, onSaved }: {
  agent: OfficeAgent; categories: string[]; autonomyLevels: string[];
  onClose: () => void; onSaved: () => void;
}) {
  const [autonomy, setAutonomy] = useState(agent.rights.autonomy);
  const [cats, setCats] = useState<string[]>(agent.rights.categories);
  const [saving, setSaving] = useState(false);

  const toggleCat = (c: string) => setCats((prev) => prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]);

  const save = async () => {
    setSaving(true);
    try {
      await api.put(`/api/agents-office/${agent.id}/rights`, { autonomy, categories: cats });
      toast({ title: `Rechte für ${agent.name} gespeichert`, tone: "ok" });
      onSaved();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e.message, tone: "err" });
    } finally { setSaving(false); }
  };

  return (
    <Modal title={`Rechte — ${agent.name}`} onClose={onClose}
      foot={<><button className="btn" onClick={onClose}>Abbrechen</button>
        <button className="btn primary" onClick={save} disabled={saving}>Speichern</button></>}>
      <div className="stack" style={{ gap: 16 }}>
        <div className="field">
          <label>Autonomie</label>
          <select className="select" value={autonomy} onChange={(e) => setAutonomy(e.target.value)}>
            {autonomyLevels.map((lvl) => <option key={lvl} value={lvl}>{AUTONOMY_LABEL[lvl] || lvl}</option>)}
          </select>
          <span className="small muted">
            „Nur lesen" blockt alles außer risikoarme Werkzeuge. „Mit Freigabe" verlangt bei jedem
            riskanteren Schritt eine Zustimmung, auch wenn das Werkzeug sonst keine braucht. „Autonom"
            folgt nur noch der normalen, risikobasierten Freigabe.
          </span>
        </div>
        <div className="field">
          <label>Erlaubte Werkzeug-Kategorien</label>
          <span className="small muted" style={{ marginBottom: 6, display: "block" }}>
            Keine ausgewählt = keine zusätzliche Einschränkung über die zugewiesenen Werkzeuge hinaus.
          </span>
          <div className="stack" style={{ gap: 6 }}>
            {categories.map((c) => (
              <div key={c} className="row" style={{ justifyContent: "space-between" }}>
                <span className="small">{c}</span>
                <Toggle checked={cats.includes(c)} onChange={() => toggleCat(c)} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}
