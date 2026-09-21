/**
 * Der Beleg, dass die Leitung noch steht — auf jeder Seite.
 *
 * Eine offene Leitung, die man nicht sieht, ist genauso falsch wie eine, die
 * ungefragt abbricht: Das Mikrofon ist an, es kostet Geld, und wer das
 * vergisst, redet irgendwann ungewollt hinein. Also steht sie sichtbar da,
 * überall, mit dem einen Knopf, der sie beendet.
 */
import { useNavigate } from "react-router-dom";
import { Mic, Square } from "@/lib/icons";
import { closeLine, useLive } from "@/app/voice/liveStore";
import type { LiveState } from "@/app/voice/live";

const LABEL: Record<LiveState, string> = {
  connecting: "Verbinde …",
  listening: "Hört zu",
  thinking: "Denkt nach",
  speaking: "Antwortet",
  closed: "",
};

export function LiveBar() {
  const live = useLive();
  const nav = useNavigate();
  if (!live.open) return null;

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
      <button className="btn sm danger" onClick={() => void closeLine()}>
        <Square size={13} />Beenden
      </button>
    </div>
  );
}
