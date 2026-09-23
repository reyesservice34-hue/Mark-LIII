export type WakeStatus = "unsupported" | "idle" | "listening" | "error";

type Listener = (status: WakeStatus) => void;
let recognition: any = null;
let status: WakeStatus = "idle";
const listeners = new Set<Listener>();
let armed = false;

function publish(next: WakeStatus) {
  status = next;
  listeners.forEach((l) => l(next));
}

export function onWakeStatus(fn: Listener) {
  listeners.add(fn);
  fn(status);
  return () => listeners.delete(fn);
}

export function wakeSupported(): boolean {
  if (typeof window === "undefined") return false;
  return !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

export function armHeyMia(): void {
  if (armed || !wakeSupported()) {
    if (!wakeSupported()) publish("unsupported");
    return;
  }
  const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  recognition = new Ctor();
  recognition.lang = "de-DE";
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.onstart = () => { armed = true; publish("listening"); };
  recognition.onerror = () => { publish("error"); };
  recognition.onend = () => {
    armed = false;
    publish("idle");
    // Browser dürfen nach einem echten Nutzerstart weiterlaufen; bei einem
    // Tab-Wechsel/Permission-Entzug scheitert restart still und wartet auf
    // die nächste Nutzerinteraktion.
    window.setTimeout(() => { try { recognition?.start(); } catch { /* browser gate */ } }, 600);
  };
  recognition.onresult = (event: any) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const text = String(event.results[i]?.[0]?.transcript || "").toLowerCase();
      if (/\b(hey|hei|hi)\s+mia\b/.test(text)) {
        window.dispatchEvent(new CustomEvent("mia:wake", { detail: { transcript: text } }));
        try { recognition.abort(); } catch { /* ignore */ }
        break;
      }
    }
  };
  try { recognition.start(); } catch { publish("error"); }
}

export function disarmHeyMia(): void {
  armed = false;
  try { recognition?.abort(); } catch { /* ignore */ }
  recognition = null;
  publish("idle");
}
