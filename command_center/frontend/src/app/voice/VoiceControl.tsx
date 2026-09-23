import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "@/lib/icons";
import { toast } from "@/lib/toast";
import { useStore } from "@/lib/store";
import { micRequest, speechMode } from "./spoken";
import { resolveVoiceBackend, type VoiceBackend } from "./voice";

type Phase = "idle" | "recording" | "transcribing";

/**
 * Mikrofonknopf. Bleibt gesperrt, solange kein echtes Spracherkennungs-
 * Backend eingerichtet ist, und trägt dann den Grund des Servers als
 * Beschriftung — er tut nie so, als würde er zuhören.
 *
 * Im Freihandmodus startet er von selbst, sobald die Antwort gesprochen ist.
 */
export function VoiceControl({ onTranscript }: { onTranscript?: (text: string, spoken: boolean) => void }) {
  const [backend, setBackend] = useState<VoiceBackend | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const alive = useRef(true);
  const wanted = useStore(micRequest);
  const seen = useRef(wanted);
  const phaseRef = useRef<Phase>("idle");
  phaseRef.current = phase;

  useEffect(() => {
    alive.current = true;
    resolveVoiceBackend().then((b) => alive.current && setBackend(b));
    return () => { alive.current = false; };
  }, []);

  const disabled = !backend?.available || phase === "transcribing";
  const title = !backend ? "Prüfe Sprachfunktion …"
    : !backend.available ? `Sprache nicht verfügbar: ${backend.reason}`
      : phase === "recording" ? "Aufnahme beenden und übertragen"
        : phase === "transcribing" ? "Übertrage …"
          : "Sprich mit MIA";

  const click = async () => {
    if (!backend?.available || phaseRef.current !== "idle") {
      if (phaseRef.current === "recording") backend?.stop();
      return;
    }
    try {
      const text = await backend.listen((s) => alive.current && setPhase(s));
      if (text) onTranscript?.(text, speechMode.get() === "handsfree");
      else toast({ title: "Nichts verstanden", tone: "warn" });
    } catch (e: any) {
      toast({ title: "Spracheingabe fehlgeschlagen", body: e?.message, tone: "err" });
    } finally {
      if (alive.current) setPhase("idle");
    }
  };

  // Der Freihandmodus bittet nach der gesprochenen Antwort ums Wort.
  useEffect(() => {
    if (wanted === seen.current) return;
    seen.current = wanted;
    if (backend?.available && phaseRef.current === "idle") void click();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, backend]);

  return (
    <button type="button" className={`btn icon ${phase === "recording" ? "danger" : "ghost"}`} disabled={disabled}
      title={title} aria-label={title} onClick={click}>
      {phase === "transcribing" ? <span className="spinner" /> : phase === "recording" ? <Square /> : <Mic />}
    </button>
  );
}
