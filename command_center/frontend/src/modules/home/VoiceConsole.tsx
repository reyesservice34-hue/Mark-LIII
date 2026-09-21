import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Mic, Square } from "@/lib/icons";
import { type LiveState } from "@/app/voice/live";
import { closeLine, openLine, useLive } from "@/app/voice/liveStore";
import "./voice-console.css";

const PHASE_LABEL: Record<LiveState, string> = {
  connecting: "Verbinde",
  listening: "Ich höre",
  thinking: "Denke nach",
  speaking: "Antworte",
  closed: "Bereit",
};

interface LiveCaps {
  available: boolean;
  detail: string;
  model: string;
  voice: string;
  tools: number;
}

/**
 * Die Sprachkonsole: Push-to-Talk. Erst auf Knopfdruck hört Jarvis zu; die
 * Leitung bleibt dann offen (man kann ihm ins Wort fallen, mehrere Sätze
 * hintereinander sagen), bis man selbst wieder auf "Beenden" drückt — sie
 * schaltet sich nie von selbst ein.
 *
 * Sie täuscht nichts vor: fehlt der Schlüssel oder läuft die Seite nicht über
 * HTTPS, steht genau das da, statt eines Knopfes, der nichts tut.
 */
export function VoiceConsole() {
  const [caps, setCaps] = useState<LiveCaps | null>(null);
  // Zustand und Leitung liegen im Speicher der Anwendung. Diese Konsole ist
  // nur das Fenster darauf — sie wird beim Seitenwechsel abgebaut, das
  // Gespräch nicht.
  const live = useLive();
  const state: LiveState = live.phase;
  const { heard, said, tools } = live;
  const alive = useRef(true);
  const shown = useRef("");

  useEffect(() => {
    alive.current = true;
    api.get<LiveCaps>("/api/voice/live/capabilities")
      .then((c) => alive.current && setCaps(c))
      .catch(() => alive.current && setCaps({ available: false, detail: "Der Server hat auf die Anfrage "
        + "nach der Live-Leitung nicht geantwortet.", model: "", voice: "", tools: 0 }));
    return () => { alive.current = false; };
  }, []);

  // Fehler kommen aus dem Speicher und sollen einmal auffallen, nicht bei
  // jedem Neuzeichnen erneut.
  useEffect(() => {
    if (live.error && live.error !== shown.current) {
      shown.current = live.error;
      toast({ title: "Live-Leitung", body: live.error, tone: "err" });
    }
    if (!live.error) shown.current = "";
  }, [live.error]);

  // Der Browser gibt das Mikrofon nur auf sicherem Ursprung frei. Das ist
  // keine Servereinstellung — also hier benannt, statt den Nutzer rätseln zu
  // lassen, warum nichts passiert.
  const insecure = typeof window !== "undefined" && !window.isSecureContext;
  // Ohne OpenAI-Schlüssel gibt es die eigene, lokale Leitung: eigene Seite,
  // Hören und Sprechen laufen auf diesem Server und kosten nichts.
  const local = !!caps && !caps.available;
  const blocked = !caps ? "Prüfe die Leitung …"
    : insecure ? "Das Mikrofon gibt der Browser nur über HTTPS frei. Ruf das Dashboard über deine Domain auf, nicht über die IP-Adresse."
      : "";

  const open = state !== "closed";

  const start = useCallback(async () => {
    if (blocked || open) return;
    try {
      await openLine();
    } catch {
      // Der Grund steht schon im Speicher und wird oben angezeigt.
    }
  }, [blocked, open]);

  const stop = useCallback(async () => { await closeLine(); }, []);

  // Lokale Leitung: eingebettet aus Mark-LIII statt aus dem separaten
  // /live/-Dienst (jarvis-live-voice), der über den aktuellen Server-Umzug
  // nicht mit freigeschaltet wurde. Andere Adresse als diese Seite hier
  // (jarvis-reyes.de-Subdomain, eigenes Zertifikat), daher eigene Anmeldung
  // im eingebetteten Fenster nötig — anders als bei /live/, das denselben
  // Ursprung hatte.
  if (local && !blocked) {
    return (
      <section className="voice-console vc-embed-wrap" aria-label="Sprachkonsole">
        <iframe className="vc-embed" src="https://dashboard.jarvis-reyes.de/" title="Jarvis Live-Gespräch"
          allow="microphone; autoplay" />
      </section>
    );
  }

  return (
    <section className={`voice-console phase-${state}`} aria-label="Sprachkonsole">
      <div className="vc-core" aria-hidden>
        <span className="vc-ring r1" />
        <span className="vc-ring r2" />
        <span className="vc-ring r3" />
        <span className="vc-nucleus" />
      </div>

      <div className="vc-body">
        <div className="vc-phase">
          <span className={`dot ${open ? "live" : ""} ${blocked ? "err" : ""}`} />
          {blocked ? "Nicht verfügbar" : local ? "Lokale Live-Leitung" : PHASE_LABEL[state]}
          {local && !blocked && <span className="vc-meta">läuft auf diesem Server · kostenlos</span>}
          {caps?.available && !blocked && (
            <span className="vc-meta">{caps.voice} · {caps.tools} Werkzeuge</span>
          )}
        </div>

        {blocked ? (
          <p className="vc-blocked">{blocked}</p>
        ) : local ? (
          <p className="vc-hint">
            Öffne die Live-Seite und sprich. Jarvis hört über die lokale Spracherkennung zu, denkt mit Claude
            und antwortet mit seiner Stimme. Fällst du ihm ins Wort, hört er sofort auf.
          </p>
        ) : (
          <>
            {heard && <p className="vc-heard">„{heard}"</p>}
            {said ? <p className="vc-answer">{said}</p>
              : !heard && (
                <p className="vc-hint">
                  {open ? "Sprich einfach los. Das Mikrofon bleibt offen, du kannst ihm jederzeit ins Wort fallen, "
                        + "bis du auf „Beenden“ drückst."
                    : "Drück auf das Mikrofon und sprich. Er hört zu, bis du wieder auf „Beenden“ drückst."}
                </p>
              )}
            {tools.length > 0 && (
              <div className="vc-tools">
                {tools.map((t, i) => (
                  <span key={`${t.name}-${i}`} className={`vc-tool ${t.ok ? "ok" : "err"}`}>
                    <span className={`dot ${t.ok ? "ok" : "err"}`} />{t.name}
                  </span>
                ))}
              </div>
            )}
          </>
        )}

        <div className="vc-actions">
          {local && !blocked ? (
            <a className="btn primary" href="/live/" target="_blank" rel="noopener">
              <Mic size={15} />Live-Gespräch öffnen
            </a>
          ) : (
            <button className={`btn ${open ? "danger" : "primary"}`} onClick={open ? stop : start}
              disabled={!!blocked || state === "connecting"} title={blocked || undefined}>
              {open ? <Square size={15} /> : <Mic size={15} />}
              {state === "connecting" ? "Verbinde …" : open ? "Beenden" : "Drücken zum Sprechen"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
