import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "@/lib/icons";
import { toast } from "@/lib/toast";
import { resolveVoiceBackend, type VoiceBackend } from "./voice";

type Phase = "idle" | "recording" | "transcribing";

/**
 * Microphone button. Disabled with the server's own reason until a real
 * speech-to-text backend is configured — it never pretends to listen.
 */
export function VoiceControl({ onTranscript }: { onTranscript?: (text: string) => void }) {
  const [backend, setBackend] = useState<VoiceBackend | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    resolveVoiceBackend().then((b) => alive.current && setBackend(b));
    return () => { alive.current = false; };
  }, []);

  const disabled = !backend?.available || phase === "transcribing";
  const title = !backend ? "Checking voice…"
    : !backend.available ? `Voice not available: ${backend.reason}`
      : phase === "recording" ? "Stop and transcribe"
        : phase === "transcribing" ? "Transcribing…"
          : "Speak to JARVIS";

  const click = async () => {
    if (!backend?.available) return;
    if (phase === "recording") { backend.stop(); return; }
    try {
      const text = await backend.listen((s) => alive.current && setPhase(s));
      if (text && onTranscript) onTranscript(text);
      else if (!text) toast({ title: "Nothing recognised", tone: "warn" });
    } catch (e: any) {
      toast({ title: "Voice input failed", body: e?.message, tone: "err" });
    } finally {
      if (alive.current) setPhase("idle");
    }
  };

  return (
    <button type="button" className={`btn icon ${phase === "recording" ? "danger" : "ghost"}`} disabled={disabled}
      title={title} aria-label={title} onClick={click}>
      {phase === "transcribing" ? <span className="spinner" /> : phase === "recording" ? <Square /> : <Mic />}
    </button>
  );
}
