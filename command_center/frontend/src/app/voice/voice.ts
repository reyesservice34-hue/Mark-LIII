/**
 * Voice abstraction — speech to text and text to speech behind one interface.
 *
 * Nothing here pretends. `resolveVoiceBackend()` asks the server what it can
 * actually do; when no STT backend is configured the returned backend reports
 * `available: false` with the server's own reason, and the microphone control
 * stays disabled. When one *is* configured, the browser records with
 * MediaRecorder and the server transcribes it — no audio is stored.
 */
import { api } from "@/lib/api";

export interface VoiceCapabilities {
  speech_to_text: { available: boolean; configured: boolean; detail: string; model?: string; max_bytes?: number };
  text_to_speech: { available: boolean; configured: boolean; detail: string; model?: string; voice?: string };
  note?: string;
}

export interface VoiceBackend {
  id: string;
  available: boolean;
  reason: string;
  canSpeak: boolean;
  /** Why speaking is unavailable — the server's own wording, not a guess. */
  speakReason: string;
  /** Start recording; resolves with the transcript once stop() is called. */
  listen(onState?: (state: "recording" | "transcribing") => void): Promise<string>;
  stop(): void;
  speak(text: string): Promise<void>;
}

class UnavailableBackend implements VoiceBackend {
  id = "none";
  available = false;
  canSpeak = false;
  speakReason: string;
  constructor(public reason: string, speakReason = "") {
    this.speakReason = speakReason || reason;
  }
  async listen(): Promise<string> { throw new Error(this.reason); }
  stop() { /* nothing to stop */ }
  async speak() { throw new Error(this.reason); }
}

class ServerVoiceBackend implements VoiceBackend {
  id = "server";
  available = true;
  reason = "";
  canSpeak: boolean;
  speakReason: string;
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private audio: HTMLAudioElement | null = null;

  constructor(caps: VoiceCapabilities) {
    this.canSpeak = caps.text_to_speech.available;
    this.speakReason = caps.text_to_speech.available ? "" : caps.text_to_speech.detail;
  }

  private static mimeType(): string {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    for (const t of candidates) {
      if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(t)) return t;
    }
    return "";
  }

  async listen(onState?: (s: "recording" | "transcribing") => void): Promise<string> {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      throw new Error("This browser cannot record audio (a secure origin — https or localhost — is required).");
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      throw new Error("Microphone access was denied.");
    }
    this.stream = stream;
    const mimeType = ServerVoiceBackend.mimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    this.recorder = recorder;
    const chunks: BlobPart[] = [];
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };

    const finished = new Promise<Blob>((resolve) => {
      recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
    });
    recorder.start();
    onState?.("recording");

    const blob = await finished;
    this.cleanup();
    if (!blob.size) throw new Error("The recording was empty.");
    onState?.("transcribing");

    const form = new FormData();
    const ext = (blob.type.includes("ogg") && "ogg") || (blob.type.includes("mp4") && "m4a") || "webm";
    form.append("file", blob, `speech.${ext}`);
    const res = await api.upload<{ text: string }>("/api/voice/transcribe", form);
    return (res.text || "").trim();
  }

  stop() {
    try {
      if (this.recorder?.state === "recording") this.recorder.stop();
    } catch { /* already stopped */ }
  }

  private cleanup() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.recorder = null;
  }

  async speak(text: string): Promise<void> {
    if (!this.canSpeak) throw new Error("No text-to-speech backend is configured on the server.");
    const res = await fetch(`${api.base}/api/voice/speak`, {
      method: "POST", credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": (document.cookie.match(/(?:^|;\s*)jcc_csrf=([^;]+)/) || ["", ""])[1],
      },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      let detail = `speech failed (HTTP ${res.status})`;
      try { detail = (await res.json()).detail || detail; } catch { /* keep default */ }
      throw new Error(detail);
    }
    const url = URL.createObjectURL(await res.blob());
    this.audio?.pause();
    this.audio = new Audio(url);
    this.audio.onended = () => URL.revokeObjectURL(url);
    await this.audio.play();
  }
}

let cached: Promise<VoiceBackend> | null = null;

export function resolveVoiceBackend(): Promise<VoiceBackend> {
  if (!cached) {
    cached = api.get<VoiceCapabilities>("/api/voice/capabilities")
      .then((caps) => (caps.speech_to_text.available
        ? new ServerVoiceBackend(caps)
        : new UnavailableBackend(caps.speech_to_text.detail, caps.text_to_speech.detail)))
      .catch(() => new UnavailableBackend("voice capabilities could not be loaded"));
  }
  return cached;
}

/** Forget the cached answer, e.g. after the server configuration changed. */
export function resetVoiceBackend() { cached = null; }
