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

export type LiveState = "connecting" | "reconnecting" | "standby" | "listening" | "thinking" | "speaking" | "closed";

/** Wartezeiten bis zum nächsten Verbindungsversuch; der letzte Wert gilt danach dauerhaft. */
const RETRY_MS = [1000, 2000, 5000, 10000];
const PING_MS = 20000;
/** Abweisungen des Servers (Anmeldung, Rolle, Gespräch fehlt): Neuversuch wäre zwecklos. */
const FINAL_CODES = new Set([4401, 4403, 4404]);

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
  private muted = false;
  // Server meldet Standby („Hey Mia“ nötig); Ruhezustand der Anzeige richtet sich danach.
  private standby = false;
  /** Nach einer Nutzergeste: Browser geben Ton erst danach frei. */
  resumeAudio(): void {
    void this.ctxOut?.resume().catch(() => undefined);
    void this.ctxIn?.resume().catch(() => undefined);
  }
  private idleState(): LiveState { return this.standby ? "standby" : "listening"; }
  private playbackGeneration = 0;
  private stopped = false;
  private retries = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(private h: LiveHandlers, private conversationId = "") {}

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
    this.ctxOut = new AudioContext({ sampleRate: RATE });
    this.ctxIn = new AudioContext();
    await Promise.all([this.ctxOut.resume(), this.ctxIn.resume()]);

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      throw new Error("Zugriff auf das Mikrofon wurde abgelehnt.");
    }

    this.stopped = false;
    await this.connect();

    this.playHead = this.ctxOut!.currentTime;

    const ctxIn = this.ctxIn!;
    this.ctxIn = ctxIn;
    this.source = ctxIn.createMediaStreamSource(this.stream);
    // ScriptProcessor ist veraltet, aber überall vorhanden; ein AudioWorklet
    // bräuchte eine eigene Datei und brächte hier keinen hörbaren Vorteil.
    this.node = ctxIn.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = (ev) => {
      const ws = this.ws;
      if (!this.open || this.muted || !ws || ws.readyState !== WebSocket.OPEN) return;
      const pcm = floatToPcm16(downsample(ev.inputBuffer.getChannelData(0), ctxIn.sampleRate, RATE));
      ws.send(JSON.stringify({
        type: "input_audio_buffer.append",
        audio: toBase64(new Uint8Array(pcm.buffer)),
      }));
    };
    this.source.connect(this.node);
    this.node.connect(ctxIn.destination);
    this.open = true;
    this.muted = false;
    this.h.onState?.(this.idleState());
  }

  /**
   * Nur den WebSocket aufbauen. Mikrofon und Wiedergabe bleiben unberührt, damit
   * ein Neuaufbau nach Trennung (Server-Neustart, Netzwackler) ohne Klick klappt.
   */
  private async connect(): Promise<void> {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const q = this.conversationId ? `?conversation_id=${encodeURIComponent(this.conversationId)}` : "";
    const ws = new WebSocket(`${proto}//${location.host}/api/voice/live${q}`);
    this.ws = ws;
    ws.onmessage = (e) => this.downstream(e.data);
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      this.clearPing();
      if (this.stopped) return;
      if (FINAL_CODES.has(e.code)) {
        this.h.onError?.("Der Server hat die Sprachleitung abgewiesen. Bitte neu anmelden oder Gespräch neu öffnen.");
        void this.stop().then(() => this.h.onClose?.());
        return;
      }
      this.scheduleReconnect();
    };

    await new Promise<void>((resolve, reject) => {
      const fail = () => reject(new Error("Die Leitung zum Server kam nicht zustande."));
      ws.onopen = () => resolve();
      ws.onerror = fail;
      setTimeout(() => { if (ws.readyState !== WebSocket.OPEN) fail(); }, 12000);
    });
    ws.onerror = null;
    this.retries = 0;
    this.pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, PING_MS);
  }

  private scheduleReconnect() {
    if (this.stopped || this.retryTimer) return;
    this.flush();
    this.h.onState?.("reconnecting");
    const wait = RETRY_MS[Math.min(this.retries, RETRY_MS.length - 1)];
    this.retries++;
    this.retryTimer = setTimeout(async () => {
      this.retryTimer = null;
      if (this.stopped) return;
      try {
        await this.connect();
        if (!this.stopped) this.h.onState?.(this.idleState());
      } catch {
        // onclose des fehlgeschlagenen Sockets plant den nächsten Versuch.
        if (this.ws?.readyState !== WebSocket.CONNECTING) this.scheduleReconnect();
      }
    }, wait);
  }

  private clearPing() {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  /** Mikrofon anhalten, ohne die Sprachverbindung oder die Antwort zu beenden. */
  setMuted(muted: boolean): void {
    this.muted = muted;
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = !muted; });
  }

  private downstream(raw: string) {
    let ev: any;
    try { ev = JSON.parse(raw); } catch { return; }
    switch (ev.type) {
      case "jarvis.unavailable":
        this.h.onError?.(ev.detail || "Die Live-Leitung ist nicht verfügbar.");
        void this.stop();
        break;
      case "jarvis.standby":
        // Server wartet auf „Hey Mia“; nichts Ungespieltes abbrechen, nur die Anzeige.
        this.standby = true;
        this.h.onState?.("standby");
        break;
      case "jarvis.awake":
        this.standby = false;
        this.h.onState?.("listening");
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
      case "response.audio.file":
        if (ev.audio) void this.playFile(ev.audio);
        break;
      case "response.output_audio.delta":
        if (ev.delta) { this.h.onState?.("speaking"); this.play(fromBase64(ev.delta)); }
        break;
      case "response.done":
        this.h.onSaid?.(this.said, true);
        // Satzteile können noch laufen; dann setzt onended den Zustand.
        if (!this.queue.length) this.h.onState?.(this.idleState());
        break;
      case "error":
        this.h.onError?.(ev.error?.message || "Fehler auf der Leitung.");
        break;
      default:
        break;
    }
  }

  // Der Server schickt die Antwort satzweise; Teile in Reihenfolge dekodieren
  // und lückenlos hintereinander einplanen statt gleichzeitig abzuspielen.
  private fileChain: Promise<void> = Promise.resolve();

  private playFile(encoded: string) {
    const generation = this.playbackGeneration;
    this.fileChain = this.fileChain.then(() => this.playFilePart(encoded, generation));
  }

  private async playFilePart(encoded: string, generation: number) {
    const ctx = this.ctxOut;
    if (!ctx || generation !== this.playbackGeneration) return;
    try {
      await ctx.resume();
      const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
      const buffer = await ctx.decodeAudioData(bytes.buffer);
      if (generation !== this.playbackGeneration) return;
      const node = ctx.createBufferSource();
      node.buffer = buffer; node.connect(ctx.destination);
      const at = Math.max(ctx.currentTime, this.playHead);
      this.playHead = at + buffer.duration;
      this.queue.push(node);
      this.h.onState?.("speaking");
      node.onended = () => { this.queue = this.queue.filter((n) => n !== node); if (!this.queue.length && generation === this.playbackGeneration) this.h.onState?.(this.idleState()); };
      node.start(at);
    } catch { this.h.onError?.("Die Audioantwort konnte nicht wiedergegeben werden. Bitte Live Voice erneut starten."); }
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
    this.playbackGeneration++;
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

  /** Wichtige Meldung von MIA ansprechen lassen; wartet kurz, falls die Leitung noch aufgebaut wird. */
  announce(notificationId: string, tries = 0) {
    const ws = this.ws;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "jarvis.announce", notification_id: notificationId }));
      return;
    }
    if (tries < 40 && !this.stopped) setTimeout(() => this.announce(notificationId, tries + 1), 250);
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.clearPing();
    this.open = false;
    this.muted = false;
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
