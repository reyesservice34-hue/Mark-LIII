/**
 * Der Beleg, dass die Leitung noch steht — auf jeder Seite.
 *
 * Eine offene Leitung, die man nicht sieht, ist genauso falsch wie eine, die
 * ungefragt abbricht: Das Mikrofon ist an, es kostet Geld, und wer das
 * vergisst, redet irgendwann ungewollt hinein. Also steht sie sichtbar da,
 * überall, mit dem einen Knopf, der sie beendet.
 */
import { useNavigate } from "@/lib/router";
import { Mic, Square, Volume2, VolumeX } from "@/lib/icons";
import { closeLine, openLine, setListenEnabled, toggleLineMuted, useLive } from "@/app/voice/liveStore";
import type { LiveState } from "@/app/voice/live";

const LABEL: Record<LiveState, string> = {
  connecting: "Verbinde …",
  reconnecting: "Verbinde neu …",
  standby: "Standby – sag „Hey Mia“",
  listening: "Hört zu",
  thinking: "Denkt nach",
  speaking: "Antwortet",
  closed: "",
};

export function LiveBar() {
  const live = useLive();
  const nav = useNavigate();
  if (!live.open) {
    // Ohne offene Leitung reagiert „Hey Mia“ nicht – das muss man sehen.
    return (
      <div className="live-bar phase-closed" role="status">
        <span className="live-bar-dot" aria-hidden />
        <button className="live-bar-label" onClick={() => { setListenEnabled(true); void openLine().catch(() => undefined); }}
          title="Mikrofon an: MIA wartet im Standby auf „Hey Mia“">
          <Mic size={14} />
          MIA hört nicht zu – tippen zum Aktivieren
        </button>
        {live.error && <span className="live-bar-text">{live.error}</span>}
      </div>
    );
  }

  const spoken = live.said || live.heard;
  return (
    <div className={`live-bar phase-${live.phase}`} role="status" aria-live="polite">
      <span className="live-bar-dot" aria-hidden />
      <button className="live-bar-label" onClick={() => nav("/")}
        title="Zur Sprachkonsole">
        <Mic size={14} />
        {LABEL[live.phase]}
      </button>
      {spoken && <span className="live-bar-text">{spoken}</span>}
      <button className={`btn sm ${live.muted ? "primary" : "ghost"}`} onClick={toggleLineMuted}
        title={live.muted ? "Mikrofon einschalten" : "Mikrofon stummschalten"}
        aria-label={live.muted ? "Mikrofon einschalten" : "Mikrofon stummschalten"}>
        {live.muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
        {live.muted ? "Stumm" : "Mikrofon an"}
      </button>
      <button className="btn sm danger" onClick={() => { setListenEnabled(false); void closeLine(); }}
        title="Mikrofon aus, bis du wieder aktivierst">
        <Square size={13} />Zuhören beenden
      </button>
    </div>
  );
}
