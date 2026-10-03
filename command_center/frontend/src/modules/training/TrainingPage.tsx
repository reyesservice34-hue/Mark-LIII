import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { bytes, relative, dateTime } from "@/lib/format";
import { GraduationCap, BookOpen, Code, Lightbulb, ListChecks, Puzzle, ScrollText, Search, RefreshCw, ChevronRight, Icon } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";
import "./training.css";

interface IntegrationCard {
  id: string; name: string; kind: string; icon: string; status: string; detail: string;
  last_checked_at: string | null; configured: boolean;
}

interface Category {
  id: string; label: string; icon: string; purpose: string;
  status: "active" | "missing" | "error"; file_count: number; size_bytes: number;
}

interface Item {
  name: string; path: string; size: number; modified_at: number;
}

const ICON_MAP: Record<string, any> = {
  "code": Code, "book-open": BookOpen, "graduation-cap": GraduationCap,
  "lightbulb": Lightbulb, "list-checks": ListChecks, "puzzle": Puzzle,
};

export default function TrainingPage() {
  const { can } = useAuth();
  const ov = useApi<{ root_connected: boolean; categories: Category[]; recent_log_entries: any[] }>(
    "/api/training/overview", { interval: 120000 }
  );
  const [selectedCat, setSelectedCat] = useState<string | null>(null);
  const items = useApi<{ items: Item[]; total: number }>(
    selectedCat ? `/api/training/${selectedCat}/items?limit=100` : null
  );
  const logs = useApi<{ entries: any[]; total: number }>("/api/training/log/entries?limit=50");
  const integrations = useApi<{ integrations: IntegrationCard[] }>("/api/integrations", { refreshOn: ["integration.status"] });
  const aiSystems = (integrations.data?.integrations || []).filter((i) => i.kind === "ai");
  const [checking, setChecking] = useState<string | null>(null);
  const checkProvider = async (id: string) => {
    setChecking(id);
    try { await api.post(`/api/integrations/${id}/check`); integrations.reload(); }
    catch (e: any) { toast({ title: "Check fehlgeschlagen", body: e.message, tone: "err" }); }
    finally { setChecking(null); }
  };

  const cat = ov.data?.categories.find((c) => c.id === selectedCat) || null;
  const IconComp = cat ? ICON_MAP[cat.icon] : null;

  return (
    <div className="page training">
      <div className="page-head">
        <div>
          <div className="eyebrow">Kapitel 45 · Trainings-Hub</div>
          <h1><GraduationCap size={20} style={{ marginRight: 6, verticalAlign: -3 }} />Training</h1>
        </div>
        <div className="actions">
          <StatusIndicator status={ov.data?.root_connected ? "ok" : "offline"}
            label={ov.data?.root_connected ? "Trainings-Verzeichnis vorhanden" : "Trainings-Verzeichnis fehlt"} />
          <button className="btn sm" onClick={() => { ov.reload(false); items.reload(false); logs.reload(false); }}>
            <RefreshCw size={13} />Aktualisieren
          </button>
        </div>
      </div>
      <ErrorState error={ov.error} retry={() => ov.reload(false)} />

      {!ov.data ? <Skeleton rows={4} height={80} /> : (
        <>
          <div className="training-categories">
            {ov.data.categories.map((c) => {
              const Ico = ICON_MAP[c.icon];
              return (
                <div key={c.id} className={`training-card ${selectedCat === c.id ? "active" : ""}`}
                  onClick={() => setSelectedCat(selectedCat === c.id ? null : c.id)} role="button" tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSelectedCat(selectedCat === c.id ? null : c.id); }}>
                  <div className="training-card-head">
                    {Ico && <Ico size={16} />}
                    <strong>{c.label}</strong>
                    <ChevronRight size={14} className={`chevron ${selectedCat === c.id ? "open" : ""}`} />
                  </div>
                  <div className="training-purpose">{c.purpose}</div>
                  <div className="training-stats">
                    <span className="stat"><strong>{c.file_count}</strong> Dateien</span>
                    <span className="stat"><strong>{bytes(c.size_bytes)}</strong></span>
                  </div>
                </div>
              );
            })}
          </div>

          {selectedCat && cat && (
            <Panel title={<span className="row" style={{ gap: 8 }}>{IconComp && <IconComp size={15} />}{cat.label}</span>}
              actions={<div className="row" style={{ gap: 6 }}>
                <Search size={14} style={{ color: "var(--text-3)" }} />
                <span className="small muted">{items.data?.total || "wird berechnet"} Dateien</span>
              </div>} flush>
              {items.error ? <div className="panel-body"><ErrorState error={items.error} /></div> :
                !items.data ? <div className="panel-body"><Skeleton rows={3} /></div> :
                  items.data.items.length === 0 ? <EmptyState title="Keine Dateien in dieser Kategorie" /> :
                    <div className="training-items">
                      {items.data.items.map((item) => (
                        <div key={item.path} className="training-item">
                          <div className="training-item-head">
                            <span className="name">{item.name}</span>
                            <span className="meta">{bytes(item.size)}</span>
                          </div>
                          <div className="tiny muted">{relative(new Date(item.modified_at * 1000).toISOString())}</div>
                        </div>
                      ))}
                    </div>
              }
            </Panel>
          )}

          <Panel title={<span className="row" style={{ gap: 8 }}><Lightbulb size={15} />KI-Systeme</span>}
            actions={<span className="small muted">Direkte Anthropic/OpenAI/Gemini-Zugänge bewusst ausgeblendet — das Denken läuft über OpenRouter</span>}
            flush>
            {integrations.error ? <div className="panel-body"><ErrorState error={integrations.error} /></div> :
              !integrations.data ? <div className="panel-body"><Skeleton rows={2} /></div> :
                aiSystems.length === 0 ? <div className="panel-body"><EmptyState title="Kein KI-System konfiguriert" /></div> :
                  <div className="training-items">
                    {aiSystems.map((i) => (
                      <div key={i.id} className="training-item">
                        <div className="training-item-head">
                          <span className="row" style={{ gap: 6 }}><Icon name={i.icon} size={14} /><span className="name">{i.name}</span></span>
                          <Badge status={i.status === "not_configured" ? "offline" : i.status}>
                            {i.status === "not_configured" ? "nicht eingerichtet" : i.status}
                          </Badge>
                        </div>
                        <div className="tiny muted">{i.detail || "noch nicht geprüft"}</div>
                        <div className="row" style={{ justifyContent: "space-between", marginTop: 4 }}>
                          <span className="tiny muted">{i.last_checked_at ? `geprüft ${relative(i.last_checked_at)}` : "nie geprüft"}</span>
                          {can("operator") && <button className="btn sm ghost" onClick={() => checkProvider(i.id)} disabled={checking === i.id}>
                            <RefreshCw size={12} style={checking === i.id ? { animation: "spin 1s linear infinite" } : undefined} />Jetzt prüfen
                          </button>}
                        </div>
                      </div>
                    ))}
                  </div>
            }
          </Panel>

          <Panel title={<div>
              <span className="row" style={{ gap: 8 }}><ScrollText size={15} />Trainingsprotokoll</span>
              <div className="small muted" style={{ marginTop: 2, fontWeight: 400 }}>Letzte Trainingseinträge: wer, wann, was gelernt</div>
            </div>} flush>
            {logs.error ? <ErrorState error={logs.error} /> :
              !logs.data ? <Skeleton rows={4} /> :
                logs.data.entries.length === 0 ? <EmptyState title="Noch keine Einträge" /> :
                  <div className="training-log">
                    {logs.data.entries.map((entry, i) => (
                      <div key={i} className="log-entry">
                        <div className="log-head">
                          <span className="actor">{entry.actor || "System"}</span>
                          <span className="time">{entry.timestamp ? dateTime(entry.timestamp) : "—"}</span>
                        </div>
                        <div className="log-msg">{entry.message || entry.detail || "—"}</div>
                        {entry.tags && entry.tags.length > 0 && (
                          <div className="log-tags">{entry.tags.map((t: string) => <Badge key={t}>{t}</Badge>)}</div>
                        )}
                      </div>
                    ))}
                  </div>
            }
          </Panel>
        </>
      )}
    </div>
  );
}
