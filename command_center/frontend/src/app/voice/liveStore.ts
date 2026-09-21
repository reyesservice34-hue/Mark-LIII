/**
 * Die Leitung gehört der Anwendung, nicht einer Seite.
 *
 * Vorher hing der WebSocket samt Mikrofon an der Sprachkonsole auf der
 * Startseite. Wer während des Gesprächs auf „Aufgaben" klickte, riss sie damit
 * ab — mitten im Satz, ohne dass es jemand gesagt hätte. Ein Gespräch, das
 * endet, weil man eine andere Seite ansieht, ist kein Gespräch.
 *
 * Deshalb steht die Leitung hier: ein einziges Objekt neben der React-Welt,
 * das Seitenwechsel gar nicht mitbekommt. Sie endet, wenn der Nutzer sie
 * schließt, der Server sie schließt oder die Seite wirklich verlassen wird.
 */
import { useSyncExternalStore } from "react";
import { LiveLine, type LiveState } from "./live";

export interface LiveSnapshot {
  phase: LiveState;
  /** Zuletzt Gehörtes, so wie der Server es verstanden hat. */
  heard: string;
  /** Die Antwort, während sie entsteht. */
  said: string;
  tools: { name: string; ok: boolean }[];
  /** Warum die Leitung nicht zustande kam — leer, solange alles geht. */
  error: string;
  open: boolean;
}

const EMPTY: LiveSnapshot = { phase: "closed", heard: "", said: "", tools: [], error: "", open: false };

let snapshot: LiveSnapshot = EMPTY;
let line: LiveLine | null = null;
const listeners = new Set<() => void>();

function set(patch: Partial<LiveSnapshot>) {
  snapshot = { ...snapshot, ...patch };
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

/** Der aktuelle Zustand der Leitung, überall gleich. */
export function useLive(): LiveSnapshot {
  return useSyncExternalStore(subscribe, () => snapshot, () => EMPTY);
}

export function isLineOpen(): boolean {
  return snapshot.open;
}

/**
 * Leitung öffnen. Ein zweiter Aufruf, während sie schon steht, tut nichts —
 * zwei offene Mikrofone auf dieselbe Sitzung wären nur Rückkopplung.
 */
export async function openLine(): Promise<void> {
  if (snapshot.open || snapshot.phase === "connecting") return;
  set({ heard: "", said: "", tools: [], error: "", phase: "connecting", open: true });
  const l = new LiveLine({
    onState: (phase) => set({ phase, open: phase !== "closed" }),
    onHeard: (heard) => set({ heard, said: "" }),
    onSaid: (said) => set({ said }),
    onTool: (name, ok) => set({ tools: [...snapshot.tools.slice(-4), { name, ok }] }),
    onError: (error) => set({ error }),
    onClose: () => { line = null; set({ phase: "closed", open: false }); },
  });
  line = l;
  try {
    await l.start();
  } catch (e: any) {
    line = null;
    set({ phase: "closed", open: false, error: e?.message || "Die Leitung kam nicht zustande." });
    throw e;
  }
}

export async function closeLine(): Promise<void> {
  const l = line;
  line = null;
  await l?.stop();
  set({ phase: "closed", open: false });
}

/** Etwas Getipptes einwerfen, ohne zu sprechen. */
export function sayOnLine(text: string): void {
  line?.say(text);
}

// Ein Seitenwechsel innerhalb des Dashboards lässt die Leitung stehen; das
// Schließen des Tabs nicht. Ohne das bliebe serverseitig eine Sitzung offen,
// die niemand mehr hört — und die kostet Geld, solange sie läuft.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => { void closeLine(); });
}
