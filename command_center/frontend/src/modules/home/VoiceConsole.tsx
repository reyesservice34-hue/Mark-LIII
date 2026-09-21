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
  ambient?: boolean;
  wake_word?: string;
}

/**
 * Die Sprachkonsole: eine offene Leitung, kein Knopfdruck-Betrieb.
 *
 * Die Leitung öffnet sich von selbst, sobald man angemeldet ist (siehe
 * JarvisShell), und bleibt über jeden Seitenwechsel im Dashboard hinweg
 * bestehen — kein Knopf, der erst gedrückt werden muss, bevor irgendetwas
 * geht. Bei einer Gemini-Leitung (ambient: true) hört das Mikrofon zwar
 * durchgehend zu, Jarvis antwortet aber erst, sobald sein Name fällt — bis
 * dahin bleibt die Fläche für das Gespräch selbst reserviert, statt für einen
 * Zustand, der sowieso schon vorbei ist, bevor man ihn sieht.
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
  const { heard, said, tools, awake } = live;
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
          <span className={`dot ${open ? (awake ? "live" : "info") : ""} ${blocked ? "err" : ""}`} />
          {blocked ? "Nicht verfügbar" : local ? "Lokale Live-Leitung"
            : state === "listening" && !awake ? `Hört zu — sag „${caps?.wake_word || "Jarvis"}“`
            : PHASE_LABEL[state]}
          {local && !blocked && <span className="vc-meta">läuft auf diesem Server · kostenlos</span>}
          {caps?.available && !blocked && (
            <span className="vc-meta">{caps.voice} · {caps.tools} Werkzeuge</span>
          )}
          {caps?.available && !blocked && !local && (
            <button className="btn sm ghost vc-mute" onClick={open ? stop : start}
              disabled={state === "connecting"} title={open ? "Stummschalten" : "Wieder zuhören"}>
              {open ? <Square size={13} /> : <Mic size={13} />}
            </button>
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
            {heard && awake && <p className="vc-heard">„{heard}"</p>}
            {said ? <p className="vc-answer">{said}</p>
              : !heard && (
                <p className="vc-hint">
                  {!open ? "Stummgeschaltet — klick auf das Mikrofon oben, um wieder zuzuhören."
                    : caps?.ambient && !awake
                      ? `Er hört durchgehend mit, reagiert aber erst, wenn du „${caps.wake_word || "Jarvis"}“ sagst. `
                        + "Die Leitung bleibt bestehen, auch wenn du im Dashboard woanders hingehst oder den Tab wechselst."
                      : "Sprich einfach los. Das Mikrofon bleibt offen, du kannst ihm jederzeit ins Wort fallen, "
                        + "und die Leitung bleibt bestehen, auch wenn du im Dashboard woanders hingehst oder den "
                        + "Tab wechselst."}
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
        {local && !blocked && (
          <div className="vc-actions">
            <a className="btn primary" href="/live/" target="_blank" rel="noopener">
              <Mic size={15} />Live-Gespräch öffnen
            </a>
          </div>
        )}
      </div>
    </section>
  );
}
