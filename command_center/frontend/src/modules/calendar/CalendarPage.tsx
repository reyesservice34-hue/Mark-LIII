/**
 * Kalender — die Termine, mit denen er ohnehin arbeitet.
 *
 * Nach Tagen gruppiert, weil man einen Kalender so liest. Anlegen geht in der
 * Sprache, in der man es auch sagen würde („morgen", „Freitag 14:00") — die
 * Auswertung macht derselbe Dienst, den auch der Agent benutzt, damit hier
 * nicht ein zweiter Datumsparser entsteht, der anders rechnet.
 *
 * Ist kein Kalender eingerichtet, steht das da, samt fehlender Variable. Ein
 * leerer Kalender sieht sonst aus wie „keine Termine".
 */
import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Calendar, Plus, Trash2, MoveRight } from "@/lib/icons";
import { Panel, EmptyState, ErrorState, Skeleton, Modal, Badge } from "@/components/ui";

interface Event {
  uid: string; title: string; start: string; end: string;
  location: string; notes: string; backend: string;
}
interface Payload {
  available: boolean; detail: string; backend: string; events: Event[]; days: number;
}

const TAG = (iso: string) =>
  new Date(iso).toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" });
const UHR = (iso: string) =>
  new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

export default function CalendarPage() {
  const [days, setDays] = useState(14);
  const { data, error, loading, reload } = useApi<Payload>(`/api/calendar?days=${days}`, {
    refreshOn: ["calendar.changed"], interval: 120000,
  });
  const [neu, setNeu] = useState(false);
  const [move, setMove] = useState<Event | null>(null);

  const cancel = async (e: Event) => {
    if (!window.confirm(`Termin „${e.title}" absagen?`)) return;
    try {
      await api.del(`/api/calendar?query=${encodeURIComponent(e.title)}`);
      toast({ title: "Abgesagt", body: e.title, tone: "ok" });
      reload();
    } catch (err: any) { toast({ title: "Ging nicht", body: err?.message, tone: "err" }); }
  };

  if (error) return <div className="page"><ErrorState error={error} retry={reload} /></div>;

  // Nach Tagen gruppieren, Reihenfolge bleibt die des Servers (zeitlich).
  const tage = new Map<string, Event[]>();
  for (const e of data?.events || []) {
    const key = TAG(e.start);
    (tage.get(key) || tage.set(key, []).get(key)!).push(e);
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1><Calendar size={20} /> Kalender</h1>
          <p className="muted">
            {data?.available
              ? `${data.events.length} Termine in den nächsten ${data.days} Tagen · ${data.backend === "google" ? "Google Kalender" : "lokal auf diesem Server"}`
              : "Termine — lokal oder über Google"}
          </p>
        </div>
        <div className="actions">
          <select className="select" style={{ width: 150 }} value={days}
            onChange={(e) => setDays(Number(e.target.value))} aria-label="Zeitraum">
            <option value={7}>7 Tage</option>
            <option value={14}>14 Tage</option>
            <option value={30}>30 Tage</option>
            <option value={90}>90 Tage</option>
          </select>
          <button className="btn primary" onClick={() => setNeu(true)}><Plus size={14} />Termin anlegen</button>
        </div>
      </header>

      {loading && !data ? <Skeleton rows={6} height={18} />
        : !data?.available ? (
          <Panel title="Kein Kalender eingerichtet" icon={<Calendar size={15} />}>
            <div className="panel-body">
              <p>{data?.detail || "Der Kalenderdienst meldet sich nicht."}</p>
              <p className="small muted">
                Ohne Google-Zugang legt JARVIS Termine lokal auf dem Server ab — das reicht, um sie hier
                zu sehen und per Sprache anzulegen. Für den geteilten Google-Kalender braucht es die
                Zugangsdaten in <code>command_center/.env</code>.
              </p>
            </div>
          </Panel>
        ) : data.events.length === 0 ? (
          <EmptyState icon={<Calendar size={22} />} title="Nichts eingetragen">
            In den nächsten {data.days} Tagen steht nichts an. Neue Termine kannst du hier anlegen —
            oder JARVIS einfach sagen: „Trag mir Freitag um 14 Uhr die Abnahme in Bhimber ein."
          </EmptyState>
        ) : (
          [...tage.entries()].map(([tag, events]) => (
            <Panel key={tag} title={tag} icon={<Calendar size={15} />} flush>
              <table className="table">
                <tbody>
                  {events.map((e) => (
                    <tr key={e.uid || `${e.title}-${e.start}`}>
                      <td style={{ width: 130 }} className="mono small">
                        {UHR(e.start)}–{UHR(e.end)}
                      </td>
                      <td>
                        <strong>{e.title}</strong>
                        {e.location && <div className="small muted">{e.location}</div>}
                        {e.notes && <div className="small muted">{e.notes}</div>}
                      </td>
                      <td style={{ width: 90 }}>
                        <Badge status={e.backend === "google" ? "ok" : "info"}>
                          {e.backend === "google" ? "Google" : "lokal"}
                        </Badge>
                      </td>
                      <td className="row" style={{ gap: 6, justifyContent: "flex-end", width: 160 }}>
                        <button className="btn sm" onClick={() => setMove(e)} title="Verschieben">
                          <MoveRight size={13} />
                        </button>
                        <button className="btn sm danger" onClick={() => cancel(e)} title="Absagen">
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          ))
        )}

      {neu && <NeuerTermin onClose={() => setNeu(false)} onDone={() => { setNeu(false); reload(); }} />}
      {move && <Verschieben event={move} onClose={() => setMove(null)}
        onDone={() => { setMove(null); reload(); }} />}
    </div>
  );
}

function NeuerTermin({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ title: "", when: "", at: "", duration: 60, location: "", notes: "" });
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ note: string; backend: string }>("/api/calendar", f);
      toast({ title: "Eingetragen", body: r.note || (r.backend === "google" ? "In Google." : "Lokal auf dem Server."), tone: "ok" });
      onDone();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e?.message, tone: "err" });
    } finally { setBusy(false); }
  };

  return (
    <Modal title="Termin anlegen" onClose={onClose} foot={<>
      <button className="btn" onClick={onClose}>Abbrechen</button>
      <button className="btn primary" onClick={save} disabled={busy || !f.title || !f.when}>
        {busy ? "Trage ein …" : "Eintragen"}
      </button>
    </>}>
      <div className="field"><label>Was</label>
        <input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })}
          placeholder="Abnahme Bauvorhaben Müller" autoFocus />
      </div>
      <div className="field"><label>Wann</label>
        <input className="input" value={f.when} onChange={(e) => setF({ ...f, when: e.target.value })}
          placeholder="morgen · Freitag · 2026-09-25" />
        <span className="small muted">Alltagssprache reicht — dieselbe Auswertung wie beim Sprechen.</span>
      </div>
      <div className="row" style={{ gap: 10 }}>
        <div className="field" style={{ flex: 1 }}><label>Uhrzeit</label>
          <input className="input" value={f.at} onChange={(e) => setF({ ...f, at: e.target.value })}
            placeholder="14:00" />
        </div>
        <div className="field" style={{ flex: 1 }}><label>Dauer (Minuten)</label>
          <input className="input" type="number" value={f.duration}
            onChange={(e) => setF({ ...f, duration: Number(e.target.value) })} />
        </div>
      </div>
      <div className="field"><label>Ort</label>
        <input className="input" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} />
      </div>
      <div className="field"><label>Notiz</label>
        <textarea className="input" style={{ minHeight: 70 }} value={f.notes}
          onChange={(e) => setF({ ...f, notes: e.target.value })} />
      </div>
    </Modal>
  );
}

function Verschieben({ event, onClose, onDone }: { event: Event; onClose: () => void; onDone: () => void }) {
  const [when, setWhen] = useState("");
  const [at, setAt] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await api.post("/api/calendar/move", { query: event.title, when, at });
      toast({ title: "Verschoben", body: event.title, tone: "ok" });
      onDone();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e?.message, tone: "err" });
    } finally { setBusy(false); }
  };

  return (
    <Modal title={`„${event.title}" verschieben`} onClose={onClose} foot={<>
      <button className="btn" onClick={onClose}>Abbrechen</button>
      <button className="btn primary" onClick={save} disabled={busy || !when}>
        {busy ? "Verschiebe …" : "Verschieben"}
      </button>
    </>}>
      <div className="field"><label>Neuer Tag</label>
        <input className="input" value={when} onChange={(e) => setWhen(e.target.value)}
          placeholder="Montag · morgen · 2026-09-30" autoFocus />
      </div>
      <div className="field"><label>Neue Uhrzeit</label>
        <input className="input" value={at} onChange={(e) => setAt(e.target.value)} placeholder="09:30" />
      </div>
    </Modal>
  );
}
