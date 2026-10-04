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
import { api } from "@/lib/api";

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
  muted: boolean;
  conversationId: string;
  /**
   * Das Gespräch ist wirklich da: MIA wurde mit „Hey Mia“ geweckt oder jemand hat auf „Sprechen“
   * gedrückt. Eine Leitung im Standby zählt nicht – die wartet nur still auf das Weckwort.
   */
  engaged: boolean;
}

const EMPTY: LiveSnapshot = { phase: "closed", heard: "", said: "", tools: [], error: "", open: false, muted: false, conversationId: "", engaged: false };
const AWAKE: LiveState[] = ["listening", "thinking", "speaking"];
/** Von Hand geöffnet (Knopf), nicht still im Hintergrund für „Hey Mia“. */
let explicit = false;

let snapshot: LiveSnapshot = EMPTY;
let line: LiveLine | null = null;
const listeners = new Set<() => void>();

function set(patch: Partial<LiveSnapshot>) {
  snapshot = { ...snapshot, ...patch };
  listeners.forEach((l) => l());
  if (typeof window !== "undefined") {
    const level = snapshot.phase === "speaking" ? 0.72 : snapshot.phase === "thinking" ? 0.48 : snapshot.phase === "listening" ? 0.28 : 0.08;
    window.postMessage({ jarvis: "live", state: snapshot.phase === "closed" ? "idle" : snapshot.phase, level }, window.location.origin);
  }
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

/** Der aktuelle Zustand der Leitung, überall gleich. */
export function useLive(): LiveSnapshot {
  return useSyncExternalStore(subscribe, () => snapshot, () => EMPTY);
}

export function selectConversation(conversationId: string): void {
  if (!snapshot.open) set({ conversationId });
}

export function isLineOpen(): boolean {
  return snapshot.open;
}

/**
 * Leitung öffnen. Ein zweiter Aufruf, während sie schon steht, tut nichts —
 * zwei offene Mikrofone auf dieselbe Sitzung wären nur Rückkopplung.
 */
let opening: Promise<void> | null = null;

export async function openLine(conversationId = "", opts: { auto?: boolean } = {}): Promise<void> {
  // Läuft schon ein Aufbau, nicht parallel einen zweiten starten (zwei Mikrofone, zwei Sitzungen).
  if (opening) {
    if (opts.auto) return opening;
    await opening.catch(() => undefined);
  }
  const run = openNow(conversationId, opts);
  opening = run;
  try { await run; } finally { if (opening === run) opening = null; }
}

async function openNow(conversationId: string, opts: { auto?: boolean }): Promise<void> {
  if (!opts.auto) {
    // Ein Knopfdruck macht auch eine schon wartende Leitung zum Gespräch.
    explicit = true;
    if (snapshot.open && conversationId && conversationId !== snapshot.conversationId) {
      // Wartende Leitung gehört zu einem anderen Gespräch: neu aufbauen, nicht umbiegen.
      await closeLine();
      explicit = true;
    } else if (snapshot.open) { set({ engaged: true }); line?.wake(); return; }
  }
  if (snapshot.open || snapshot.phase === "connecting") return;
  conversationId = conversationId || snapshot.conversationId;
  if (!conversationId) {
    const result = await api.get<{ conversation: { id: string } }>("/api/chat/main");
    conversationId = result.conversation.id;
  }
  set({ heard: "", said: "", tools: [], error: "", phase: "connecting", open: true, muted: false, conversationId, engaged: explicit });
  const l = new LiveLine({
    onState: (phase) => set({ phase, open: phase !== "closed", engaged: explicit || AWAKE.includes(phase) || (snapshot.engaged && phase !== "standby" && phase !== "closed") }),
    onReady: (wakeword) => {
      // Knopfdruck: MIA soll sofort zuhören, nicht erst auf „Hey Mia“ warten.
      if (wakeword && explicit) l.wake();
      // Ohne Weckwort-Erkennung auf dem Server wäre jede still geöffnete Leitung sofort ein offenes
      // Gespräch. Dann nur auf Knopfdruck – die Hintergrund-Leitung wird wieder geschlossen.
      if (!wakeword && opts.auto && !explicit) {
        void closeLine().then(() => set({ error: "„Hey Mia“ ist auf dem Server nicht verfügbar – zum Sprechen „Mit MIA sprechen“ drücken." }));
      }
    },
    onHeard: (heard) => set({ heard, said: "" }),
    onSaid: (said) => set({ said }),
    onTool: (name, ok) => set({ tools: [...snapshot.tools.slice(-4), { name, ok }] }),
    onError: (error) => set({ error }),
    onClose: () => { line = null; explicit = false; set({ phase: "closed", open: false, muted: false, engaged: false }); },
  }, conversationId);
  line = l;
  try {
    await l.start();
  } catch (e: any) {
    await l.stop();
    line = null;
    explicit = false;
    set({ phase: "closed", open: false, muted: false, engaged: false, error: e?.message || "Die Leitung kam nicht zustande." });
    throw e;
  }
}

/**
 * Wichtige Meldung: Sprachchat von selbst öffnen und MIA sprechen lassen.
 * Mehrere offene Tabs: nur der erste spricht (Merker in localStorage).
 */
export async function announce(notificationId: string): Promise<void> {
  try {
    if (localStorage.getItem("mia-announced") === notificationId) return;
    localStorage.setItem("mia-announced", notificationId);
  } catch { /* ohne Speicher trotzdem ansagen */ }
  if (!snapshot.open) {
    try { await openLine(); } catch { return; }
  }
  line?.announce(notificationId);
}

export function resumeLine(): void {
  line?.resumeAudio();
}

/** Dauerhaftes Zuhören (Standby mit „Hey Mia“) an- oder abschalten; gilt für diesen Browser. */
export function listenEnabled(): boolean {
  try { return localStorage.getItem("mia.listen") !== "0"; } catch { return true; }
}

export function setListenEnabled(on: boolean): void {
  try { localStorage.setItem("mia.listen", on ? "1" : "0"); } catch { /* ohne Speicher: nur diese Sitzung */ }
}

export async function closeLine(): Promise<void> {
  const l = line;
  line = null;
  explicit = false;
  await l?.stop();
  set({ phase: "closed", open: false, muted: false, engaged: false });
}

/**
 * Gespräch beenden. Ist Zuhören erlaubt, wartet MIA danach wieder still im Standby auf „Hey Mia“;
 * die Leitung wird dafür neu aufgebaut, damit der Server sicher nicht mehr wach mithört.
 */
export async function endConversation(): Promise<void> {
  await closeLine();
  if (listenEnabled()) void openLine("", { auto: true }).catch(() => undefined);
}

export function setLineMuted(muted: boolean): void {
  if (!line || !snapshot.open) return;
  line.setMuted(muted);
  set({ muted });
}

export function toggleLineMuted(): void {
  setLineMuted(!snapshot.muted);
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
