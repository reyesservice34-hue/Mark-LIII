/**
 * Die offene Leitung.
 *
 * Ein WebSocket zum eigenen Server, der ihn zur Realtime-Schnittstelle
 * durchreicht. Das Mikrofon bleibt offen; wann eine Äußerung zu Ende ist,
 * entscheidet das Modell, nicht ein Knopf. Deshalb kann man ihm auch ins
 * Wort fallen: sobald der Server ein `input_audio_buffer.speech_started`
 * meldet, bricht die Wiedergabe hier sofort ab.
 *
 * Audio ist PCM16 mono mit 24 kHz in beide Richtungen. Der Browser nimmt in
 * seiner eigenen Rate auf, also wird hier umgerechnet — falsch gerechnet
 * klingt das wie ein kaputtes Mikrofon und nicht wie ein Fehler.
 */

const RATE = 24000;

export type LiveState = "connecting" | "listening" | "thinking" | "speaking" | "closed";

export interface LiveHandlers {
  onState?: (s: LiveState) => void;
  /** Was der Nutzer gesagt hat, sobald es erkannt wurde. */
  onHeard?: (text: string) => void;
  /** Die Antwort, während sie entsteht. */
  onSaid?: (text: string, done: boolean) => void;
  onTool?: (name: string, ok: boolean) => void;
  onError?: (detail: string) => void;
  onClose?: () => void;
}

function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function downsample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    // Mittelwert über das Quellfenster statt einfachem Wegwerfen: sonst
    // entstehen Alias-Artefakte, die die Erkennung merklich verschlechtern.
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

function toBase64(bytes: Uint8Array): string {
  let s = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + chunk)) as any);
  }
  return btoa(s);
}

function fromBase64(b64: string): Int16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

export class LiveLine {
  private ws: WebSocket | null = null;
  private ctxIn: AudioContext | null = null;
  private ctxOut: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private queue: AudioBufferSourceNode[] = [];
  private playHead = 0;
  private said = "";
  private open = false;

  constructor(private h: LiveHandlers) {}

  get isOpen() { return this.open; }

  async start(): Promise<void> {
    if (!window.isSecureContext) {
      throw new Error("Das Mikrofon gibt der Browser nur über HTTPS frei. Ruf das Dashboard "
        + "über deine Domain auf, nicht über die IP-Adresse.");
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Dieser Browser kann kein Audio aufnehmen.");
    }
    this.h.onState?.("connecting");

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      throw new Error("Zugriff auf das Mikrofon wurde abgelehnt.");
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/api/voice/live`);
    this.ws = ws;

    await new Promise<void>((resolve, reject) => {
      const fail = () => reject(new Error("Die Leitung zum Server kam nicht zustande."));
      ws.onopen = () => resolve();
      ws.onerror = fail;
      setTimeout(() => { if (ws.readyState !== WebSocket.OPEN) fail(); }, 12000);
    });

    ws.onmessage = (e) => this.downstream(e.data);
    ws.onclose = () => { this.open = false; this.h.onState?.("closed"); this.h.onClose?.(); };

    this.ctxOut = new AudioContext({ sampleRate: RATE });
    this.playHead = this.ctxOut.currentTime;

    const ctxIn = new AudioContext();
    this.ctxIn = ctxIn;
    this.source = ctxIn.createMediaStreamSource(this.stream);
    // ScriptProcessor ist veraltet, aber überall vorhanden; ein AudioWorklet
    // bräuchte eine eigene Datei und brächte hier keinen hörbaren Vorteil.
    this.node = ctxIn.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = (ev) => {
      if (!this.open || ws.readyState !== WebSocket.OPEN) return;
      const pcm = floatToPcm16(downsample(ev.inputBuffer.getChannelData(0), ctxIn.sampleRate, RATE));
      ws.send(JSON.stringify({
        type: "input_audio_buffer.append",
        audio: toBase64(new Uint8Array(pcm.buffer)),
      }));
    };
    this.source.connect(this.node);
    this.node.connect(ctxIn.destination);
    this.open = true;
    this.h.onState?.("listening");
  }

  private downstream(raw: string) {
    let ev: any;
    try { ev = JSON.parse(raw); } catch { return; }
    switch (ev.type) {
      case "jarvis.unavailable":
        this.h.onError?.(ev.detail || "Die Live-Leitung ist nicht verfügbar.");
        void this.stop();
        break;
      case "jarvis.tool":
        this.h.onTool?.(ev.name, !!ev.ok);
        break;
      case "input_audio_buffer.speech_started":
        // Dazwischenreden: alles Ungespielte sofort verwerfen.
        this.flush();
        this.h.onState?.("listening");
        break;
      case "conversation.item.input_audio_transcription.completed":
        if (ev.transcript) this.h.onHeard?.(String(ev.transcript).trim());
        break;
      case "response.created":
        this.said = "";
        this.h.onState?.("thinking");
        break;
      case "response.output_audio_transcript.delta":
        this.said += ev.delta || "";
        this.h.onSaid?.(this.said, false);
        break;
      case "response.output_audio.delta":
        if (ev.delta) { this.h.onState?.("speaking"); this.play(fromBase64(ev.delta)); }
        break;
      case "response.done":
        this.h.onSaid?.(this.said, true);
        this.h.onState?.("listening");
        break;
      case "error":
        this.h.onError?.(ev.error?.message || "Fehler auf der Leitung.");
        break;
      default:
        break;
    }
  }

  private play(pcm: Int16Array) {
    const ctx = this.ctxOut;
    if (!ctx) return;
    const buf = ctx.createBuffer(1, pcm.length, RATE);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
    const node = ctx.createBufferSource();
    node.buffer = buf;
    node.connect(ctx.destination);
    // Stück an Stück hängen, sonst entstehen hörbare Lücken zwischen den
    // Paketen.
    const at = Math.max(ctx.currentTime, this.playHead);
    node.start(at);
    this.playHead = at + buf.duration;
    this.queue.push(node);
    node.onended = () => { this.queue = this.queue.filter((n) => n !== node); };
  }

  private flush() {
    this.queue.forEach((n) => { try { n.stop(); } catch { /* lief schon aus */ } });
    this.queue = [];
    if (this.ctxOut) this.playHead = this.ctxOut.currentTime;
  }

  /** Von Hand eine Antwort auslösen, z. B. für einen getippten Einwurf. */
  say(text: string) {
    if (!this.open || this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    this.ws.send(JSON.stringify({ type: "response.create" }));
  }

  async stop(): Promise<void> {
    this.open = false;
    this.flush();
    try { this.node?.disconnect(); this.source?.disconnect(); } catch { /* schon zu */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctxIn?.close().catch(() => undefined);
    await this.ctxOut?.close().catch(() => undefined);
    this.node = null; this.source = null; this.stream = null;
    this.ctxIn = null; this.ctxOut = null;
    try { this.ws?.close(); } catch { /* schon zu */ }
    this.ws = null;
    this.h.onState?.("closed");
  }
}
