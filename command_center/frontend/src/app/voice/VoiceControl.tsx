import { useEffect, useState } from "react";
import { Mic } from "@/lib/icons";
import { resolveVoiceBackend, type VoiceBackend } from "./voice";

/** Microphone button: disabled with a truthful tooltip until a backend exists. */
export function VoiceControl({ onTranscript }: { onTranscript?: (text: string) => void }) {
  const [backend, setBackend] = useState<VoiceBackend | null>(null);
  const [listening, setListening] = useState(false);
  useEffect(() => { resolveVoiceBackend().then(setBackend); }, []);
  const disabled = !backend?.available;
  const title = backend ? (backend.available ? "Talk to JARVIS" : `Voice not available: ${backend.reason}`) : "Checking voice…";
  return (
    <button type="button" className={`btn icon ghost ${listening ? "primary" : ""}`} disabled={disabled} title={title} aria-label={title}
      onClick={async () => {
        if (!backend?.available) return;
        if (listening) { backend.stop(); setListening(false); return; }
        setListening(true);
        try { const text = await backend.listen(); if (text) onTranscript?.(text); } finally { setListening(false); }
      }}>
      <Mic />
    </button>
  );
}
