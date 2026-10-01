/**
 * Kalender — Monat, Woche und Tag wie im Google Kalender.
 *
 * Farbe = Kategorie (Touren fürs Team, eigene Termine, Privates, Google-Firmenkalender), damit man Touren
 * für die Jungs von den eigenen und privaten Terminen trennen kann. Zeitgleiche Termine eines Tages liegen
 * nebeneinander und tragen einen roten Rand: eine Kollision sieht man, bevor man sie erlebt.
 * Google-Termine sind nur lesbar; alles Eigene legt Jarvis lokal auf dem Server ab.
 */
import { useMemo, useState } from "react";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Calendar, Plus, ChevronLeft, ChevronRight } from "@/lib/icons";
import { ErrorState, Skeleton, Modal } from "@/components/ui";
import "./calendar.css";

interface Ev { uid: string; title: string; start: string; end: string; location: string; notes: string; backend: string; category?: string }
interface Payload { available: boolean; detail: string; backend: string; events: Ev[] }

const CATS = [
  { id: "tour", label: "Touren (Team)", color: "#ff9f43" },
  { id: "ich", label: "Meine Termine", color: "#4dabf7" },
  { id: "privat", label: "Privat", color: "#b197fc" },
  { id: "google", label: "Google Firma", color: "#3ddc97" },
];
const colorOf = (id: string) => CATS.find((c) => c.id === id)?.color || "#4dabf7";
const catOf = (e: Ev) => e.category || (e.backend === "google" || e.backend === "n8n" ? "google" : "ich");
const key = (e: Ev) => e.uid || e.title + e.start;

const HOUR0 = 6, HOUR1 = 22, PX = 48;
const pad = (n: number) => String(n).padStart(2, "0");
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const mondayOf = (d: Date) => { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
const hm = (iso: string) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const minutesOf = (e: Ev) => (+new Date(e.end) - +new Date(e.start)) / 60000;
const isAllDay = (e: Ev) => minutesOf(e) >= 20 * 60;
const DOW = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

/** Zeitgleiche Termine nebeneinander anordnen; jede Überschneidung merkt sich, dass sie eine ist. */
function layout(evs: Ev[]) {
  const sorted = [...evs].sort((a, b) => +new Date(a.start) - +new Date(b.start));
  const res = new Map<string, { col: number; cols: number; clash: boolean }>();
  let cluster: Ev[] = []; let end = 0;
  const flush = () => {
    if (!cluster.length) return;
    const ends: number[] = []; const col = new Map<string, number>();
    for (const e of cluster) {
      const s = +new Date(e.start); let c = ends.findIndex((x) => x <= s);
      if (c < 0) { c = ends.length; ends.push(+new Date(e.end)); } else ends[c] = +new Date(e.end);
      col.set(key(e), c);
    }
    for (const e of cluster) res.set(key(e), { col: col.get(key(e))!, cols: ends.length, clash: cluster.length > 1 });
    cluster = [];
  };
  for (const e of sorted) { const s = +new Date(e.start); if (cluster.length && s >= end) flush(); cluster.push(e); end = Math.max(end, +new Date(e.end)); }
  flush();
  return res;
}

export default function CalendarPage() {
  const [view, setView] = useState<"month" | "week" | "day">("week");
  const [cursor, setCursor] = useState(new Date());
  const [hidden, setHidden] = useState<string[]>([]);
  const [edit, setEdit] = useState<{ ev?: Ev; date?: string; from?: string } | null>(null);

  const range = useMemo(() => {
    if (view === "month") { const first = mondayOf(new Date(cursor.getFullYear(), cursor.getMonth(), 1)); return { a: first, b: addDays(first, 42) }; }
    if (view === "week") { const m = mondayOf(cursor); return { a: m, b: addDays(m, 7) }; }
    const d = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate()); return { a: d, b: addDays(d, 1) };
  }, [view, cursor]);

  const { data, error, loading, reload } = useApi<Payload>(
    `/api/calendar?start=${ymd(range.a)}T00:00:00&end=${ymd(range.b)}T00:00:00`, { refreshOn: ["calendar.changed"], interval: 120000 });

  const events = useMemo(() => (data?.events || []).filter((e) => !hidden.includes(catOf(e))), [data, hidden]);
  const byDay = useMemo(() => {
    const m = new Map<string, Ev[]>();
    for (const e of events) { const k = ymd(new Date(e.start)); (m.get(k) || m.set(k, []).get(k)!).push(e); }
    return m;
  }, [events]);
  const clashes = useMemo(() => {
    const out: string[] = [];
    for (const [day, evs] of byDay) {
      const timed = evs.filter((e) => !isAllDay(e)); const lay = layout(timed); const seen = new Set<string>();
      for (const e of timed) {
        if (!lay.get(key(e))?.clash || seen.has(key(e))) continue;
        const group = timed.filter((o) => +new Date(o.start) < +new Date(e.end) && +new Date(o.end) > +new Date(e.start));
        group.forEach((g) => seen.add(key(g)));
        if (group.length > 1) out.push(`${new Date(day).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" })} ${hm(e.start)}: ${group.map((g) => g.title).join(" ⇄ ")}`);
      }
    }
    return out;
  }, [byDay]);

  const step = (dir: number) => {
    const d = new Date(cursor);
    if (view === "month") d.setMonth(d.getMonth() + dir); else d.setDate(d.getDate() + dir * (view === "week" ? 7 : 1));
    setCursor(d);
  };
  const title = view === "month" ? cursor.toLocaleDateString("de-DE", { month: "long", year: "numeric" })
    : view === "week" ? `${range.a.toLocaleDateString("de-DE", { day: "numeric", month: "short" })} – ${addDays(range.b, -1).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "numeric" })}`
    : cursor.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const today = ymd(new Date());

  if (error) return <div className="page"><ErrorState error={error} retry={reload} /></div>;

  const days = view === "week" ? Array.from({ length: 7 }, (_, i) => addDays(range.a, i)) : [range.a];
  const open = (e: Ev) => setEdit({ ev: e });

  return (
    <div className="page">
      <header className="page-head">
        <div><h1><Calendar size={20} /> Kalender</h1>
          <p className="muted">{data?.backend === "n8n" ? "Google Firmenkalender (nur lesen) + eigene Termine auf dem Server" : "Eigene Termine auf dem Server"}</p></div>
        <div className="actions"><button className="btn primary" onClick={() => setEdit({ date: ymd(cursor) })}><Plus size={14} />Termin</button></div>
      </header>

      <div className="cal-bar">
        <button className="btn icon" onClick={() => step(-1)} aria-label="zurück"><ChevronLeft size={16} /></button>
        <button className="btn" onClick={() => setCursor(new Date())}>Heute</button>
        <button className="btn icon" onClick={() => step(1)} aria-label="weiter"><ChevronRight size={16} /></button>
        <span className="title">{title}</span>
        <span className="cal-tabs">{(["month", "week", "day"] as const).map((v) => (
          <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{v === "month" ? "Monat" : v === "week" ? "Woche" : "Tag"}</button>))}</span>
        <span className="cal-cats">{CATS.map((c) => (
          <button key={c.id} className={`cal-cat ${hidden.includes(c.id) ? "off" : ""}`}
            onClick={() => setHidden(hidden.includes(c.id) ? hidden.filter((x) => x !== c.id) : [...hidden, c.id])}>
            <span className="sw" style={{ background: c.color }} />{c.label}</button>))}</span>
      </div>

      {clashes.length > 0 && <div className="cal-alert">⚠ {clashes.length} Zeitüberschneidung{clashes.length > 1 ? "en" : ""} in dieser Ansicht:
        <ul>{clashes.slice(0, 4).map((c) => <li key={c}>{c}</li>)}</ul></div>}

      {loading && !data ? <Skeleton rows={8} height={22} /> : view === "month" ? (
        <div className="cal-month">
          {DOW.map((d) => <div key={d} className="cal-dow">{d}</div>)}
          {Array.from({ length: 42 }, (_, i) => addDays(range.a, i)).map((d) => {
            const k = ymd(d); const evs = byDay.get(k) || []; const lay = layout(evs.filter((e) => !isAllDay(e)));
            const clash = [...lay.values()].some((v) => v.clash);
            return (
              <div key={k} className={`cal-cell ${d.getMonth() !== cursor.getMonth() ? "other" : ""} ${k === today ? "today" : ""}`}
                onClick={() => setEdit({ date: k })}>
                <div className="num"><b>{d.getDate()}</b>{clash && <span className="cal-warn" title="Zeitüberschneidung">⚠</span>}</div>
                {evs.slice(0, 3).map((e) => (
                  <div key={key(e)} className={`cal-chip ${lay.get(key(e))?.clash ? "clash" : ""}`} style={{ ["--c" as any]: colorOf(catOf(e)) }}
                    onClick={(ev) => { ev.stopPropagation(); open(e); }} title={e.title}>
                    {!isAllDay(e) && <span className="t">{hm(e.start)}</span>}<span className="n">{e.title}</span></div>))}
                {evs.length > 3 && <div className="cal-more" onClick={(ev) => { ev.stopPropagation(); setCursor(d); setView("day"); }}>+{evs.length - 3} weitere</div>}
              </div>);
          })}
        </div>
      ) : (
        <div className="cal-week"><div className="cal-week-inner">
          <div className="cal-head" style={{ gridTemplateColumns: `52px repeat(${days.length}, 1fr)` }}>
            <div />{days.map((d) => <div key={ymd(d)} className={ymd(d) === today ? "today" : ""}>{DOW[(d.getDay() + 6) % 7]} {d.getDate()}.{d.getMonth() + 1}.</div>)}</div>
          <div className="cal-allday" style={{ gridTemplateColumns: `52px repeat(${days.length}, 1fr)` }}>
            <div />{days.map((d) => (
              <div key={ymd(d)}>{(byDay.get(ymd(d)) || []).filter(isAllDay).map((e) => (
                <div key={key(e)} className="cal-chip" style={{ ["--c" as any]: colorOf(catOf(e)) }} onClick={() => open(e)}><span className="n">{e.title}</span></div>))}</div>))}</div>
          <div className="cal-body" style={{ gridTemplateColumns: `52px repeat(${days.length}, 1fr)` }}>
            <div className="cal-hours">{Array.from({ length: HOUR1 - HOUR0 }, (_, i) => <div key={i}>{pad(HOUR0 + i)}:00</div>)}</div>
            {days.map((d) => {
              const k = ymd(d); const timed = (byDay.get(k) || []).filter((e) => !isAllDay(e)); const lay = layout(timed);
              const now = new Date(); const nowY = (now.getHours() + now.getMinutes() / 60 - HOUR0) * PX;
              return (
                <div key={k} className={`cal-col ${k === today ? "today" : ""}`}
                  onClick={(ev) => { const r = ev.currentTarget.getBoundingClientRect(); const h = Math.min(HOUR1 - 1, Math.max(HOUR0, Math.floor((ev.clientY - r.top) / PX) + HOUR0)); setEdit({ date: k, from: `${pad(h)}:00` }); }}>
                  {Array.from({ length: HOUR1 - HOUR0 }, (_, i) => <div key={i} className="hl" />)}
                  {k === today && nowY > 0 && nowY < (HOUR1 - HOUR0) * PX && <div className="cal-now" style={{ top: nowY }} />}
                  {timed.map((e) => {
                    const s = new Date(e.start); const top = Math.max(0, (s.getHours() + s.getMinutes() / 60 - HOUR0) * PX);
                    const h = Math.max(22, (minutesOf(e) / 60) * PX - 2); const l = lay.get(key(e)) || { col: 0, cols: 1, clash: false };
                    return (
                      <div key={key(e)} className={`cal-block ${l.clash ? "clash" : ""}`}
                        style={{ top, height: h, left: `calc(${(l.col / l.cols) * 100}% + 2px)`, width: `calc(${100 / l.cols}% - 4px)`, ["--c" as any]: colorOf(catOf(e)) }}
                        onClick={(ev) => { ev.stopPropagation(); open(e); }} title={e.title}>
                        <div><span className="t">{hm(e.start)}–{hm(e.end)}</span> {l.clash && <span className="x">⚠</span>}</div><div>{e.title}</div></div>);
                  })}
                </div>);
            })}
          </div>
        </div></div>
      )}

      {edit && <Editor init={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload(); }} />}
    </div>
  );
}

function Editor({ init, onClose, onDone }: { init: { ev?: Ev; date?: string; from?: string }; onClose: () => void; onDone: () => void }) {
  const ev = init.ev; const readOnly = !!ev && (ev.backend === "google" || ev.backend === "n8n");
  const s0 = ev ? new Date(ev.start) : null; const e0 = ev ? new Date(ev.end) : null;
  const t0 = (d: Date | null, fb: string) => (d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : fb);
  const [f, setF] = useState({
    title: ev?.title || "", category: ev ? catOf(ev) : "", date: s0 ? ymd(s0) : init.date || ymd(new Date()),
    from: t0(s0, init.from || "08:00"), to: t0(e0, init.from ? `${pad(Math.min(23, Number(init.from.slice(0, 2)) + 1))}:00` : "09:00"),
    location: ev?.location || "", notes: ev?.notes || "", repeat: "once", until: "",
  });
  const [busy, setBusy] = useState(false);
  const mins = () => { const [a, b] = [f.from, f.to].map((x) => Number(x.slice(0, 2)) * 60 + Number(x.slice(3, 5))); return b > a ? b - a : 60; };
  const dates = () => {
    if (f.repeat === "once" || !f.until) return [f.date];
    const out: string[] = []; let d = new Date(f.date);
    for (let i = 0; i < 90 && ymd(d) <= f.until; i++, d = addDays(d, 1)) if (f.repeat === "daily" || (d.getDay() !== 0 && d.getDay() !== 6)) out.push(ymd(d));
    return out;
  };
  const save = async () => {
    setBusy(true);
    try {
      if (ev) {
        await api.put(`/api/calendar/${encodeURIComponent(ev.uid)}`, { title: f.title, when: f.date, at: f.from, duration: mins(), location: f.location, notes: f.notes, category: f.category });
        toast({ title: "Gespeichert", body: f.title, tone: "ok" });
      } else {
        const ds = dates(); let cat = f.category; let loc = f.location; let note = "";
        for (const d of ds) {
          const r = await api.post<any>("/api/calendar", { title: f.title, when: d, at: f.from, duration: mins(), location: loc, notes: f.notes, category: cat });
          if (!cat) cat = r.event?.category || ""; if (!loc) loc = r.event?.location || ""; note = note || r.note || "";
        }
        toast({ title: ds.length > 1 ? `${ds.length} Termine eingetragen` : "Eingetragen", body: `${f.title}${note.includes("Erkannt") ? " — " + note.slice(note.indexOf("Erkannt")) : ""}`, tone: "ok" });
      }
      onDone();
    } catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); } finally { setBusy(false); }
  };
  const del = async () => {
    if (!ev || !window.confirm(`Termin „${ev.title}" löschen?`)) return;
    try { await api.del(`/api/calendar/${encodeURIComponent(ev.uid)}`); toast({ title: "Gelöscht", body: ev.title, tone: "ok" }); onDone(); }
    catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };
  return (
    <Modal title={readOnly ? "Google-Termin (nur lesbar)" : ev ? "Termin bearbeiten" : "Neuer Termin"} onClose={onClose} foot={<>
      {ev && !readOnly && <button className="btn danger" style={{ marginRight: "auto" }} onClick={del}>Löschen</button>}
      <button className="btn" onClick={onClose}>{readOnly ? "Schließen" : "Abbrechen"}</button>
      {!readOnly && <button className="btn primary" onClick={save} disabled={busy || !f.title || !f.date}>{busy ? "Speichere …" : ev ? "Speichern" : "Eintragen"}</button>}
    </>}>
      {readOnly && <p className="small muted">Dieser Termin kommt aus deinem Google Firmenkalender. MIA kann Google-Termine nur lesen, bitte direkt in Google ändern.</p>}
      <div className="field"><label>Was</label><input className="input" value={f.title} disabled={readOnly} autoFocus onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Tour Karben: Christoph, Jürgen" /></div>
      <div className="field"><label>Kategorie</label><select className="select" value={f.category} disabled={readOnly} onChange={(e) => setF({ ...f, category: e.target.value })}>
        {!ev && <option value="">Automatisch (MIA erkennt es)</option>}{CATS.filter((c) => c.id !== "google").map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}{readOnly && <option value="google">Google Firma</option>}</select></div>
      <div className="row" style={{ gap: 10 }}>
        <div className="field" style={{ flex: 2 }}><label>Tag</label><input className="input" type="date" value={f.date} disabled={readOnly} onChange={(e) => setF({ ...f, date: e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Von</label><input className="input" type="time" value={f.from} disabled={readOnly} onChange={(e) => setF({ ...f, from: e.target.value })} /></div>
        <div className="field" style={{ flex: 1 }}><label>Bis</label><input className="input" type="time" value={f.to} disabled={readOnly} onChange={(e) => setF({ ...f, to: e.target.value })} /></div>
      </div>
      {!ev && <div className="row" style={{ gap: 10 }}>
        <div className="field" style={{ flex: 1 }}><label>Wiederholen</label><select className="select" value={f.repeat} onChange={(e) => setF({ ...f, repeat: e.target.value })}>
          <option value="once">Einmalig</option><option value="weekdays">Mehrere Tage: Mo bis Fr</option><option value="daily">Mehrere Tage: jeden Tag</option></select></div>
        {f.repeat !== "once" && <div className="field" style={{ flex: 1 }}><label>Bis einschließlich</label><input className="input" type="date" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} /></div>}
      </div>}
      {!ev && f.repeat !== "once" && f.until && <p className="small muted">Es werden {dates().length} Termine angelegt.</p>}
      <div className="field"><label>Ort</label><input className="input" value={f.location} disabled={readOnly} onChange={(e) => setF({ ...f, location: e.target.value })} /></div>
      <div className="field"><label>Notiz</label><textarea className="input" style={{ minHeight: 64 }} value={f.notes} disabled={readOnly} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
    </Modal>
  );
}
