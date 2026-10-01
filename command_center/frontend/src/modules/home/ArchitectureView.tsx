/**
 * Der Kern — klein auf der Startseite, ganz beim Antippen.
 *
 * Der vollständige Aufbau ist beim zweiten Hinsehen interessant und beim
 * ersten im Weg. Auf der Startseite steht deshalb nur eine Reihe: Master
 * Agent, Gedächtnis, Rechteschicht, Ereignisbus, Werkzeuge — jedes mit dem
 * Zustand, den der Server meldet. Wer mehr wissen will, klickt darauf und
 * bekommt die ganze Kette.
 *
 * Die Zustände sind gemessen, nicht gemalt. Ein Kasten, der grün ist, weil er
 * so gezeichnet wurde, wäre Dekoration.
 */
import { useEffect, useState } from "react";
import { useApi } from "@/lib/useApi";
import { Network } from "@/lib/icons";
import { Modal, Skeleton } from "@/components/ui";
import "./architecture.css";

interface Node { title: string; detail: string; status: string }
interface Layer { id: string; title: string; detail: string; status: string; nodes: Node[] }
interface Organ { id: string; title: string; role: string; detail: string; status: string }
interface Arch {
  layers: Layer[];
  organs: Organ[];
  totals: { tools: number; integrations_connected: number; mcp: number; skills: number; devices_online: number };
}

/** Was auf der Startseite Platz hat: der Kern und das, was ihn trägt. */
const KERN = ["master", "core", "tools"];

/** „MASTER AGENT · MASTER AGENT OFFLINE" zweimal zu lesen hilft niemandem. */
function shorten(title: string, detail: string): string {
  return detail.startsWith(title) ? detail.slice(title.length).replace(/^[\s·–-]+/, "") : detail;
}

export function ArchitectureView() {
  const { data, loading } = useApi<Arch>("/api/architecture", {
    interval: 60000,
    refreshOn: ["master.status", "integration.status", "desktop.*", "approval.*"],
  });
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener("mia:brain-map", show);
    return () => window.removeEventListener("mia:brain-map", show);
  }, []);

  if (loading && !data) return <div className="core-strip loading"><Skeleton rows={1} height={16} /></div>;
  if (!data) return null;

  const layers = data.layers.filter((l) => !["input", "device"].includes(l.id));

  // Der Kern als eine Reihe: die Ebene selbst, und was unter ihr hängt.
  const chips: Node[] = [];
  for (const id of KERN) {
    const layer = data.layers.find((l) => l.id === id);
    if (!layer) continue;
    if (layer.nodes.length && id === "core") chips.push(...layer.nodes);
    else chips.push({ title: layer.title, detail: layer.detail, status: layer.status });
  }

  return (
    <>
      <button className="core-strip" onClick={() => setOpen(true)}
        title="Den ganzen Aufbau ansehen">
        <span className="core-strip-label"><Network size={14} />Kern</span>
        {chips.map((c) => (
          <span className={`core-chip ${c.status}`} key={c.title}>
            <span className="dot" />
            <span className="core-chip-title">{c.title}</span>
            <span className="core-chip-detail">{shorten(c.title, c.detail)}</span>
          </span>
        ))}
        <span className="core-strip-more">Aufbau ansehen →</span>
      </button>

      {open && (
        <Modal wide title="MIA · Organismus und Verkabelung" onClose={() => setOpen(false)}
          foot={<span className="small muted">
            {data.totals.tools} Werkzeuge · {data.totals.integrations_connected} Dienste verbunden ·{" "}
            {data.totals.mcp} MCP-Server · {data.totals.skills} Fähigkeiten ·{" "}
            {data.totals.devices_online} Gerät(e) online
          </span>}>
          <div className="body-map" aria-label="MIA als verbundener digitaler Organismus">
            <div className="body-figure" aria-hidden="true">
              <span className="body-head" />
              <span className="body-torso" />
              <span className="body-arm left" />
              <span className="body-arm right" />
              <span className="body-leg left" />
              <span className="body-leg right" />
              <span className="body-spine" />
              {(data.organs || []).map((organ) => (
                <span className={`organ-pin organ-${organ.id} ${organ.status}`} key={organ.id}>
                  <span className="organ-pulse" />
                  <span className="organ-pin-label">{organ.title}</span>
                </span>
              ))}
            </div>
            <div className="organ-list">
              {(data.organs || []).map((organ) => (
                <div className={`organ-card ${organ.status}`} key={organ.id}>
                  <span className="dot" />
                  <div>
                    <strong>{organ.title}</strong>
                    <span>{organ.role}</span>
                    <small>{organ.detail}</small>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="arch-heading">
            <span>Technische Verkabelung</span>
            <small>Jede Ebene darunter nutzt denselben serverseitigen MIA-Kern.</small>
          </div>
          <div className="arch">
            {layers.map((l, i) => (
              <div className="arch-layer" key={l.id}>
                <div className={`arch-box ${l.status}`}>
                  <span className="arch-title">{l.id === "gateway" ? "MIA GATEWAY" : l.title}</span>
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
                {i < layers.length - 1 && <span className="arch-link" aria-hidden />}
              </div>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
