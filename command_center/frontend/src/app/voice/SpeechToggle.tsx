import { useEffect, useState } from "react";
import { AudioLines, Volume2, VolumeX } from "@/lib/icons";
import { useStore } from "@/lib/store";
import { toast } from "@/lib/toast";
import { setSpeechMode, speechMode, type SpeechMode } from "./spoken";
import { resolveVoiceBackend, type VoiceBackend } from "./voice";

const NEXT: Record<SpeechMode, SpeechMode> = { off: "speak", speak: "handsfree", handsfree: "off" };
const LABEL: Record<SpeechMode, string> = {
  off: "Antworten werden nicht vorgelesen",
  speak: "Antworten werden vorgelesen",
  handsfree: "Freihand: Antwort vorlesen, dann gleich wieder zuhören",
};

/**
 * Drei Zustände statt eines Schalters: still, vorlesen, Freihand. Der dritte
 * ist der, in dem ein Gespräch entsteht — er antwortet und hört dann von
 * selbst wieder zu, ohne dass man etwas anklickt.
 *
 * Ohne eingerichtete Sprachausgabe bleibt der Knopf gesperrt und nennt den
 * Grund, statt einen Zustand anzubieten, der nichts täte.
 */
export function SpeechToggle() {
  const mode = useStore(speechMode);
  const [backend, setBackend] = useState<VoiceBackend | null>(null);

  useEffect(() => {
    let alive = true;
    resolveVoiceBackend().then((b) => { if (alive) setBackend(b); });
    return () => { alive = false; };
  }, []);

  // Eine gespeicherte Einstellung darf nicht aktiv aussehen, wenn der Server
  // gar nicht sprechen kann.
  useEffect(() => {
    if (backend && !backend.canSpeak && speechMode.get() !== "off") setSpeechMode("off");
  }, [backend]);

  const usable = !!backend?.canSpeak;
  const title = !backend ? "Prüfe Sprachausgabe …"
    : !usable ? `Vorlesen nicht möglich: ${backend.speakReason || "JARVIS_CC_TTS_URL ist nicht gesetzt"}`
      : `${LABEL[mode]} — klicken zum Umschalten`;

  const click = () => {
    if (!usable) return;
    const next = NEXT[mode];
    setSpeechMode(next);
    toast({ title: LABEL[next], tone: next === "off" ? "info" : "ok" });
  };

  return (
    <button type="button" className={`btn icon sm ${mode === "off" ? "ghost" : ""}`} disabled={!usable}
      title={title} aria-label={title} aria-pressed={mode !== "off"} onClick={click}>
      {mode === "handsfree" ? <AudioLines /> : mode === "speak" ? <Volume2 /> : <VolumeX />}
    </button>
  );
}
