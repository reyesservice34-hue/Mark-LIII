/**
 * Voice abstraction — speech-to-text and text-to-speech behind one interface.
 *
 * Nothing here pretends. `resolveVoiceBackend()` asks the server what it can
 * do; until a real STT/TTS backend exists the returned backend reports
 * `available: false` and the microphone control stays disabled with the
 * server's own reason. When a backend is added (e.g. a Whisper endpoint),
 * implement `VoiceBackend` and return it from `resolveVoiceBackend()`.
 */
import { api } from "@/lib/api";

export interface VoiceCapabilities {
  speech_to_text: { available: boolean; configured: boolean; detail: string };
  text_to_speech: { available: boolean; configured: boolean; detail: string };
}

export interface VoiceBackend {
  id: string;
  available: boolean;
  reason: string;
  /** Start capturing; resolves with a transcript when the user stops. */
  listen(onPartial?: (text: string) => void): Promise<string>;
  stop(): void;
  speak(text: string): Promise<void>;
}

class UnavailableBackend implements VoiceBackend {
  id = "none";
  available = false;
  constructor(public reason: string) {}
  async listen(): Promise<string> { throw new Error(this.reason); }
  stop() { /* nothing to stop */ }
  async speak() { throw new Error(this.reason); }
}

let cached: Promise<VoiceBackend> | null = null;

export function resolveVoiceBackend(): Promise<VoiceBackend> {
  if (!cached) {
    cached = api.get<VoiceCapabilities>("/api/voice/capabilities").then((caps) => {
      if (caps.speech_to_text.available) {
        // Placeholder for a future server-backed implementation.
        return new UnavailableBackend("server voice backend declared but no client adapter is bundled yet");
      }
      return new UnavailableBackend(caps.speech_to_text.detail);
    }).catch(() => new UnavailableBackend("voice capabilities could not be loaded"));
  }
  return cached;
}
