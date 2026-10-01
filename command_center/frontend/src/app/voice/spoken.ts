/**
 * Spoken replies.
 *
 * The microphone only ever wrote into the text box — half a conversation.
 * This is the other half: a finished answer is read out, and in hands-free
 * mode the microphone opens again on its own once he has stopped speaking,
 * so it becomes a back-and-forth instead of dictation.
 *
 * Nothing here pretends either. With no text-to-speech endpoint configured
 * the toggle stays off and says why, and one failure switches it off rather
 * than failing silently on every answer.
 */
import { createStore } from "@/lib/store";
import type { VoiceBackend } from "./voice";

export type SpeechMode = "off" | "speak" | "handsfree";

const KEY = "jarvis.voice.mode";

function initial(): SpeechMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "speak" || v === "handsfree") return v;
  } catch { /* private window, blocked storage — the default is fine */ }
  return "off";
}

export const speechMode = createStore<SpeechMode>(initial());

export function setSpeechMode(mode: SpeechMode) {
  speechMode.set(mode);
  try { localStorage.setItem(KEY, mode); } catch { /* not worth failing over */ }
}

/** What to read out: the prose, without the markup that would be read aloud. */
export function speakable(text: string): string {
  return (text || "")
    .replace(/```[\s\S]*?```/g, " … Codeblock … ")     // never read code letter by letter
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|\s)[*_]([^*_]+)[*_]/g, "$1$2")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/^\s*\|.*\|\s*$/gm, "")                    // tables read as noise
    .replace(/\n{2,}/g, ". ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Bumped to ask the microphone button to start listening again. */
export const micRequest = createStore(0);

export function requestMic() {
  micRequest.set((n) => n + 1);
}

let speaking: Promise<void> | null = null;

/**
 * Read one answer out. Returns true when it actually spoke, so the caller can
 * decide whether to reopen the microphone.
 */
export async function speakReply(backend: VoiceBackend | null, text: string,
                                 onError?: (reason: string) => void): Promise<boolean> {
  const mode = speechMode.get();
  if (mode === "off" || !backend?.canSpeak) return false;
  const body = speakable(text);
  if (!body) return false;
  try {
    speaking = backend.speak(body);
    await speaking;
    return true;
  } catch (e) {
    // One failure turns it off: an endpoint that is down would otherwise
    // produce the same error after every single answer.
    setSpeechMode("off");
    onError?.(e instanceof Error ? e.message : String(e));
    return false;
  } finally {
    speaking = null;
  }
}

export function isSpeaking(): boolean {
  return speaking !== null;
}
