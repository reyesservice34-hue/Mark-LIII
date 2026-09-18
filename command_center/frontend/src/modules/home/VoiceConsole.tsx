import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, streamPost } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Mic, Square, Volume2, AudioLines, VolumeX } from "@/lib/icons";
import { useStore } from "@/lib/store";
import { setSpeechMode, speakable, speechMode } from "@/app/voice/spoken";
import { resolveVoiceBackend, type VoiceBackend } from "@/app/voice/voice";
import "./voice-console.css";

type Phase = "idle" | "listening" | "thinking" | "speaking";

const PHASE_LABEL: Record<Phase, string> = {
  idle: "Bereit",
  listening: "Ich höre",
  thinking: "Denke nach",
  speaking: "Antworte",
};

/**
 * Die Sprachkonsole auf der Startseite.
 *
 * Kein zweiter, abgekürzter Weg neben dem Chat: sie legt eine echte
 * Unterhaltung an und schickt die Nachricht durch dieselbe Strecke — Master
 * Agent, Werkzeuge, Freigaben, Prüfspur. Was hier gesprochen wird, steht
 * danach im Chatverlauf und lässt sich dort weiterlesen.
 *
 * Sie täuscht auch nichts vor: fehlt ein Sprach-Backend oder ist die Seite
 * nicht über HTTPS aufgerufen, sagt sie genau das, statt einen Knopf
 * anzubieten, der nichts tut.
 */
export function VoiceConsole({ masterOnline }: { masterOnline: boolean }) {
  const nav = useNavigate();
  const mode = useStore(speechMode);
  const [backend, setBackend] = useState<VoiceBackend | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [heard, setHeard] = useState("");
  const [answer, setAnswer] = useState("");
  const [convId, setConvId] = useState("");
  const alive = useRef(true);
  const phaseRef = useRef<Phase>("idle");
  const loop = useRef(false);
  phaseRef.current = phase;

  useEffect(() => {
    alive.current = true;
    resolveVoiceBackend().then((b) => alive.current && setBackend(b));
    return () => { alive.current = false; loop.current = false; };
  }, []);

  // getUserMedia gibt es nur auf sicherem Ursprung. Das ist keine Einstellung
  // des Servers, sondern eine Regel des Browsers — also hier benannt, statt
  // den Nutzer raten zu lassen, warum das Mikrofon nichts tut.
  const insecure = typeof window !== "undefined" && !window.isSecureContext;

  const blocked = !backend ? "Prüfe Sprachfunktion …"
    : insecure ? "Das Mikrofon gibt der Browser nur über HTTPS frei. Ruf das Dashboard über deine Domain auf."
      : !backend.available ? backend.reason
        : !masterOnline ? "Der Master Agent ist offline — ohne AI-Schlüssel gibt es keine Antwort."
          : "";

  const ensureConversation = useCallback(async () => {
    if (convId) return convId;
    const r = await api.post<{ conversation: { id: string } }>("/api/chat/conversations",
      { title: "Sprachdialog" });
    setConvId(r.conversation.id);
    return r.conversation.id;
  }, [convId]);

  /** Eine Runde: zuhören, fragen, antworten, vorlesen. */
  const round = useCallback(async () => {
    if (blocked || !backend?.available || phaseRef.current !== "idle") return;
    let text = "";
    try {
      setAnswer("");
      text = await backend.listen((s) => alive.current && setPhase(s === "recording" ? "listening" : "thinking"));
    } catch (e: any) {
      if (alive.current) setPhase("idle");
      toast({ title: "Mikrofon", body: e?.message, tone: "err" });
      loop.current = false;
      return;
    }
    if (!alive.current) return;
    if (!text.trim()) {
      setPhase("idle");
      toast({ title: "Nichts verstanden", tone: "warn" });
      loop.current = false;
      return;
    }
    setHeard(text);
    setPhase("thinking");

    const id = await ensureConversation().catch(() => "");
    if (!id) { setPhase("idle"); loop.current = false; return; }

    let full = "";
    await new Promise<void>((resolve) => {
      streamPost(`/api/chat/conversations/${id}/messages`, { content: text, attachments: [] },
        (ev) => {
          const d: any = ev.data;
          if (ev.type === "chat.delta" && d?.text) {
            full += d.text;
            if (alive.current) setAnswer(full);
          } else if (ev.type === "message.updated" && d?.role === "assistant" && d?.content) {
            // Die fertige Nachricht ist maßgeblich: sie enthält auch, was
            // nach dem letzten Delta noch dazukam.
            full = d.content;
            if (alive.current) setAnswer(full);
          }
        },
        (err) => {
          if (err) toast({ title: "JARVIS konnte nicht antworten", body: err.message, tone: "err" });
          resolve();
        });
    });
    if (!alive.current) return;

    const body = speakable(full);
    if (body && backend.canSpeak && speechMode.get() !== "off") {
      setPhase("speaking");
      try { await backend.speak(body); }
      catch (e: any) { toast({ title: "Vorlesen fehlgeschlagen", body: e?.message, tone: "err" }); }
    }
    if (!alive.current) return;
    setPhase("idle");
    // Freihand: gleich wieder zuhören, damit ein Gespräch daraus wird.
    if (loop.current && speechMode.get() === "handsfree") setTimeout(() => void round(), 400);
    else loop.current = false;
  }, [backend, blocked, ensureConversation]);

  const start = () => {
    if (phase === "listening") { backend?.stop(); return; }
    if (phase !== "idle") return;
    loop.current = speechMode.get() === "handsfree";
    void round();
  };

  const cycleMode = () => {
    const next = mode === "off" ? "speak" : mode === "speak" ? "handsfree" : "off";
    setSpeechMode(next);
  };

  const busy = phase !== "idle";
  return (
    <section className={`voice-console phase-${phase}`} aria-label="Sprachkonsole">
      <div className="vc-core" aria-hidden>
        <span className="vc-ring r1" />
        <span className="vc-ring r2" />
        <span className="vc-ring r3" />
        <span className="vc-nucleus" />
      </div>

      <div className="vc-body">
        <div className="vc-phase">
          <span className={`dot ${phase === "idle" ? "" : "live"} ${blocked ? "err" : "info"}`} />
          {blocked ? "Nicht verfügbar" : PHASE_LABEL[phase]}
        </div>

        {blocked ? (
          <p className="vc-blocked">{blocked}</p>
        ) : (
          <>
            {heard && <p className="vc-heard">„{heard}"</p>}
            {answer ? <p className="vc-answer">{answer}</p>
              : !heard && <p className="vc-hint">Drück auf das Mikrofon und sprich. Frag ihn, was auf dem
                Server läuft, lass ihn einen Termin eintragen oder eine Aufgabe anlegen.</p>}
          </>
        )}

        <div className="vc-actions">
          <button className={`btn ${phase === "listening" ? "danger" : "primary"}`} onClick={start}
            disabled={!!blocked || phase === "thinking" || phase === "speaking"}
            title={blocked || (phase === "listening" ? "Aufnahme beenden" : "Sprechen")}>
            {phase === "listening" ? <Square size={15} /> : <Mic size={15} />}
            {phase === "listening" ? "Fertig" : phase === "thinking" ? "Denkt nach …"
              : phase === "speaking" ? "Spricht …" : "Sprechen"}
          </button>

          <button className={`btn icon ${mode === "off" ? "ghost" : ""}`} onClick={cycleMode}
            disabled={!backend?.canSpeak}
            title={!backend?.canSpeak ? `Vorlesen nicht möglich: ${backend?.speakReason || "nicht eingerichtet"}`
              : mode === "off" ? "Antworten werden nicht vorgelesen"
                : mode === "speak" ? "Antworten werden vorgelesen"
                  : "Freihand: antworten und gleich wieder zuhören"}
            aria-pressed={mode !== "off"}>
            {mode === "handsfree" ? <AudioLines size={15} /> : mode === "speak" ? <Volume2 size={15} /> : <VolumeX size={15} />}
          </button>

          {convId && !busy && (
            <button className="btn ghost sm" onClick={() => nav(`/chat/${convId}`)}>Im Chat weiterlesen</button>
          )}
        </div>
      </div>
    </section>
  );
}
