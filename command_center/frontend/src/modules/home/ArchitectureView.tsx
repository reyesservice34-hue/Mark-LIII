/**
 * Der Aufbau, wie er gerade wirklich ist.
 *
 * Kein gemaltes Schaubild: Jede Ebene und jeder Kasten trägt den Zustand, den
 * der Server meldet. Was nicht verbunden ist, ist hier rot — genau dafür steht
 * die Zeichnung. Ein Diagramm, in dem alles grün ist, weil es so gezeichnet
 * wurde, wäre Dekoration.
 */
import { useApi } from "@/lib/useApi";
import { Network, ChevronDown, ChevronUp } from "@/lib/icons";
import { Panel, Skeleton, ErrorState } from "@/components/ui";
import { useState } from "react";
import "./architecture.css";

interface Node { title: string; detail: string; status: string }
interface Layer { id: string; title: string; detail: string; status: string; nodes: Node[] }
interface Arch {
  layers: Layer[];
  totals: { tools: number; integrations_connected: number; mcp: number; skills: number; devices_online: number };
}

export function ArchitectureView() {
  const { data, error, loading, reload } = useApi<Arch>("/api/architecture", {
    interval: 60000,
    refreshOn: ["master.status", "integration.status", "desktop.*", "approval.*"],
  });
  const [open, setOpen] = useState(true);

  return (
    <Panel title="Aufbau" icon={<Network size={15} />}
      actions={<button className="btn sm ghost" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}{open ? "einklappen" : "ausklappen"}
      </button>}
      foot={data ? `${data.totals.tools} Werkzeuge · ${data.totals.integrations_connected} Dienste verbunden · `
        + `${data.totals.mcp} MCP-Server · ${data.totals.skills} Fähigkeiten · `
        + `${data.totals.devices_online} Gerät(e) online` : undefined}>
      {error ? <ErrorState error={error} retry={reload} />
        : loading && !data ? <div className="panel-body"><Skeleton rows={6} height={16} /></div>
          : !open ? null
            : (
              <div className="arch">
                {data!.layers.map((l, i) => (
                  <div className="arch-layer" key={l.id}>
                    <div className={`arch-box ${l.status}`}>
                      <span className="arch-title">{l.title}</span>
                      {l.detail && <span className="arch-detail">{l.detail}</span>}
                    </div>
                    {l.nodes.length > 0 && (
                      <div className="arch-nodes">
                        {l.nodes.map((n) => (
                          <div className={`arch-node ${n.status}`} key={`${l.id}-${n.title}`}>
                            <span className="arch-node-title">{n.title}</span>
                            {n.detail && <span className="arch-node-detail">{n.detail}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                    {i < data!.layers.length - 1 && <span className="arch-link" aria-hidden />}
                  </div>
                ))}
              </div>
            )}
    </Panel>
  );
}
