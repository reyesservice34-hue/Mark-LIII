import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Mic, Square } from "@/lib/icons";
import { LiveLine, type LiveState } from "@/app/voice/live";
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
 * Die Sprachkonsole: eine offene Leitung, kein Knopfdruck-Betrieb.
 *
 * Einmal auf „Leitung öffnen", danach bleibt das Mikrofon offen. Wann eine
 * Äußerung zu Ende ist, entscheidet das Modell; man kann ihm ins Wort fallen
 * und er hört sofort auf zu reden.
 *
 * Sie täuscht nichts vor: fehlt der Schlüssel oder läuft die Seite nicht über
 * HTTPS, steht genau das da, statt eines Knopfes, der nichts tut.
 */
export function VoiceConsole() {
  const [caps, setCaps] = useState<LiveCaps | null>(null);
  const [state, setState] = useState<LiveState>("closed");
  const [heard, setHeard] = useState("");
  const [said, setSaid] = useState("");
  const [tools, setTools] = useState<{ name: string; ok: boolean }[]>([]);
  const line = useRef<LiveLine | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    api.get<LiveCaps>("/api/voice/live/capabilities")
      .then((c) => alive.current && setCaps(c))
      .catch(() => alive.current && setCaps({ available: false, detail: "Der Server hat auf die Anfrage "
        + "nach der Live-Leitung nicht geantwortet.", model: "", voice: "", tools: 0 }));
    return () => { alive.current = false; void line.current?.stop(); };
  }, []);

  // Der Browser gibt das Mikrofon nur auf sicherem Ursprung frei. Das ist
  // keine Servereinstellung — also hier benannt, statt den Nutzer rätseln zu
  // lassen, warum nichts passiert.
  const insecure = typeof window !== "undefined" && !window.isSecureContext;
  const blocked = !caps ? "Prüfe die Leitung …"
    : insecure ? "Das Mikrofon gibt der Browser nur über HTTPS frei. Ruf das Dashboard über deine Domain auf, nicht über die IP-Adresse."
      : !caps.available ? caps.detail
        : "";

  const open = state !== "closed";

  const start = useCallback(async () => {
    if (blocked || open) return;
    setHeard(""); setSaid(""); setTools([]);
    const l = new LiveLine({
      onState: (s) => alive.current && setState(s),
      onHeard: (t) => alive.current && (setHeard(t), setSaid("")),
      onSaid: (t) => alive.current && setSaid(t),
      onTool: (name, ok) => alive.current && setTools((x) => [...x.slice(-4), { name, ok }]),
      onError: (d) => toast({ title: "Live-Leitung", body: d, tone: "err" }),
      onClose: () => alive.current && setState("closed"),
    });
    line.current = l;
    try {
      await l.start();
    } catch (e: any) {
      setState("closed");
      toast({ title: "Leitung nicht geöffnet", body: e?.message, tone: "err" });
    }
  }, [blocked, open]);

  const stop = useCallback(async () => {
    await line.current?.stop();
    line.current = null;
    setState("closed");
  }, []);

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
          <span className={`dot ${open ? "live" : ""} ${blocked ? "err" : open ? "info" : ""}`} />
          {blocked ? "Nicht verfügbar" : PHASE_LABEL[state]}
          {caps?.available && !blocked && (
            <span className="vc-meta">{caps.voice} · {caps.tools} Werkzeuge</span>
          )}
        </div>

        {blocked ? (
          <p className="vc-blocked">{blocked}</p>
        ) : (
          <>
            {heard && <p className="vc-heard">„{heard}"</p>}
            {said ? <p className="vc-answer">{said}</p>
              : !heard && (
                <p className="vc-hint">
                  {open ? "Sprich einfach los. Das Mikrofon bleibt offen, und du kannst ihm jederzeit "
                        + "ins Wort fallen."
                    : "Öffne die Leitung und sprich. Er hört durchgehend zu, antwortet mit Stimme und "
                      + "greift dabei auf seine echten Werkzeuge zu."}
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
          <button className={`btn ${open ? "danger" : "primary"}`} onClick={open ? stop : start}
            disabled={!!blocked || state === "connecting"} title={blocked || undefined}>
            {open ? <Square size={15} /> : <Mic size={15} />}
            {state === "connecting" ? "Verbinde …" : open ? "Leitung schließen" : "Leitung öffnen"}
          </button>
        </div>
      </div>
    </section>
  );
}
