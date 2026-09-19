/**
 * Zwei Kacheln, die im Bild fehlten: woher das Denken kommt, und was man mit
 * einem Klick anfängt.
 *
 * Beide zeigen echten Zustand. „LLM STATUS" mit sechs Kästchen, von denen
 * fünf erfunden sind, wäre genau die Sorte Oberfläche, die gut aussieht und
 * nichts sagt: Hier steht neben jedem Anbieter, ob sein Schlüssel wirklich
 * hinterlegt ist, und beim aktiven zusätzlich, was die letzte Prüfung ergab.
 *
 * Die Schnellbefehle sind keine Zierleiste: Jeder führt an eine Stelle, die
 * es gibt, und der wichtigste — die Sprachleitung — öffnet sie direkt, statt
 * nur dorthin zu zeigen.
 */
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { toast } from "@/lib/toast";
import { Cpu, Zap, Mic, Plus, Calendar, Workflow, BookOpen } from "@/lib/icons";
import { Panel, Skeleton } from "@/components/ui";
import { openLine, useLive } from "@/app/voice/liveStore";

interface SettingsPayload {
  master: { mode: string; provider: any; label: string };
  providers: Record<string, boolean>;
}
interface HealthPayload {
  components?: { agent_gateway?: { status?: string; detail?: string; mode?: string } };
}

/** Anzeigename und Reihenfolge — die Reihenfolge ist die der Vorauswahl im Server. */
const ANBIETER: [string, string][] = [
  ["anthropic", "Anthropic · Claude"],
  ["openai", "OpenAI"],
  ["gemini", "Google Gemini"],
  ["local", "Lokales Modell"],
  ["remote_control_plane", "Fernsteuerung"],
];

export function CoreDeck() {
  const settings = useApi<SettingsPayload>("/api/settings", { interval: 120000 });
  const health = useApi<HealthPayload>("/api/health", { interval: 60000, refreshOn: ["master.status"] });
  const nav = useNavigate();
  const live = useLive();

  const gw = health.data?.components?.agent_gateway;
  const aktiv = (settings.data?.master?.provider || {})?.id || "";
  const verbunden = Object.values(settings.data?.providers || {}).filter(Boolean).length;

  const sprechen = async () => {
    if (live.open) { nav("/"); return; }
    try { await openLine(); } catch { /* der Grund steht in der Konsole */ }
  };

  return (
    <>
      <Panel title="Denkapparat" icon={<Cpu size={15} />}
        actions={<Link className="btn sm ghost" to="/settings">Verwalten</Link>}
        foot={gw ? `${gw.detail || ""}` : undefined}>
        {!settings.data ? <div className="panel-body"><Skeleton rows={3} /></div> : (
          <div className="deck-grid">
            {ANBIETER.filter(([key]) => !!settings.data!.providers?.[key]).map(([key, name]) => {
              const ist = aktiv === key || (key === "local" && aktiv === "local");
              return (
                <div className={`deck-cell ${ist ? "live" : "ok"}`} key={key}>
                  <span className="dot" />
                  <span className="deck-name">{name}</span>
                  <span className="deck-state">{ist ? "denkt gerade" : "Schlüssel liegt vor"}</span>
                </div>
              );
            })}
            <div className={`deck-cell ${verbunden ? "ok" : "off"}`}>
              <span className="dot" />
              <span className="deck-name">Betriebsart</span>
              <span className="deck-state">
                {settings.data.master.mode === "local" ? "denkt auf diesem Server" : settings.data.master.mode}
              </span>
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Schnellbefehle" icon={<Zap size={15} />}>
        <div className="quick-grid">
          <button className="quick" onClick={sprechen}>
            <Mic size={16} /><span>{live.open ? "Zur laufenden Leitung" : "Sprechen"}</span>
          </button>
          <button className="quick" onClick={() => nav("/tasks?new=1")}>
            <Plus size={16} /><span>Neue Aufgabe</span>
          </button>
          <button className="quick" onClick={() => nav("/calendar")}>
            <Calendar size={16} /><span>Kalender</span>
          </button>
          <button className="quick" onClick={() => nav("/memory")}>
            <BookOpen size={16} /><span>Anweisungen</span>
          </button>
          <button className="quick" onClick={() => nav("/workflows")}>
            <Workflow size={16} /><span>Workflow starten</span>
          </button>
          <button className="quick" onClick={() => {
            navigator.clipboard?.writeText(location.origin).then(
              () => toast({ title: "Kopiert", body: location.origin, tone: "ok" }),
              () => toast({ title: "Ging nicht", body: "Die Zwischenablage ist gesperrt.", tone: "warn" }));
          }}>
            <Zap size={16} /><span>Adresse kopieren</span>
          </button>
        </div>
      </Panel>
    </>
  );
}
