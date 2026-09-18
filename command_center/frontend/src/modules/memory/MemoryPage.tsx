/**
 * Gedächtnis — was er weiß, und was du ihm sagst.
 *
 * Oben die stehenden Anweisungen: der Text, der in jedem Gespräch mitläuft,
 * im Chat wie auf der Sprachleitung. Darunter die einzelnen Merksätze, jeder
 * für sich löschbar. Beides lag bisher nur im Quelltext beziehungsweise in
 * der Datenbank — man kam nicht heran, und ein falscher Merksatz vergiftet
 * still jedes weitere Gespräch.
 */
import { useEffect, useState } from "react";
import { useApi } from "@/lib/useApi";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { BookOpen, Sparkles, Trash2, Plus, Check, Zap, X, Pencil } from "@/lib/icons";
import { Panel, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { relative } from "@/lib/format";
import "@/modules/home/architecture.css";

interface Fact { id: string; text: string; actor: string; created_at: string; pinned: number }
interface Payload { instructions: string; facts: Fact[]; core: Fact[]; max_core: number; total: number }

const BEISPIEL = `Sprich mich mit „Chef" an.
Angebote immer mit 14 Tagen Bindefrist.
Bei allem, was an einen Kunden rausgeht, vorher fragen.
Preise nie ohne meinen Aufschlag nennen.`;

export default function MemoryPage() {
  const { data, error, loading, reload } = useApi<Payload>("/api/memory");
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [neu, setNeu] = useState("");
  const [kern, setKern] = useState("");
  const [q, setQ] = useState("");
  // Welcher Eintrag gerade bearbeitet wird, und mit welchem Wortlaut.
  const [edit, setEdit] = useState<{ id: string; text: string } | null>(null);

  // Den Serverstand übernehmen, solange niemand tippt — sonst überschreibt
  // ein Hintergrund-Neuladen die halbfertige Eingabe.
  useEffect(() => {
    if (data && !dirty) setText(data.instructions);
  }, [data, dirty]);

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/api/memory/instructions", { text });
      setDirty(false);
      toast({ title: "Gespeichert", body: "Gilt ab dem nächsten Satz — ohne Neustart.", tone: "ok" });
      reload();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e?.message, tone: "err" });
    } finally { setSaving(false); }
  };

  const addFact = async () => {
    if (!neu.trim()) return;
    try { await api.post("/api/memory/facts", { text: neu.trim() }); setNeu(""); reload(); }
    catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };

  /** Direkt ins Hauptgedächtnis — der Umweg über „Gemerktes" und das
   *  Blitzsymbol war zwar möglich, aber niemand kommt darauf. */
  const addCore = async () => {
    if (!kern.trim()) return;
    try {
      await api.post("/api/memory/facts", { text: kern.trim(), pinned: true });
      setKern("");
      toast({ title: "Ins Hauptgedächtnis gelegt", body: "Gilt ab sofort in jedem Gespräch.", tone: "ok" });
      reload();
    } catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };

  const delFact = async (f: Fact) => {
    try { await api.del(`/api/memory/facts/${f.id}`); reload(); }
    catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };

  const clearAll = async () => {
    if (!window.confirm("Wirklich ALLES Gemerkte löschen? Das lässt sich nicht rückgängig machen.")) return;
    try {
      const r = await api.del<{ removed: number }>("/api/memory/facts?confirm=ALLES");
      toast({ title: "Gelöscht", body: `${r.removed} Einträge entfernt.`, tone: "ok" });
      reload();
    } catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };

  const pin = async (f: Fact, pinned: boolean) => {
    try { await api.post(`/api/memory/facts/${f.id}/pin?pinned=${pinned}`); reload(); }
    catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };

  const saveEdit = async () => {
    if (!edit || !edit.text.trim()) return;
    try {
      await api.patch(`/api/memory/facts/${edit.id}`, { text: edit.text.trim() });
      setEdit(null);
      reload();
    } catch (e: any) { toast({ title: "Ging nicht", body: e?.message, tone: "err" }); }
  };

  /** Ein Eintrag, der sich auf Klick in ein Eingabefeld verwandelt. Löschen
   *  und neu anlegen wäre der Umweg — und im Hauptgedächtnis kostet er den
   *  Platz, den man danach wieder suchen muss. */
  const editable = (f: Fact, className = "") => (
    edit?.id === f.id ? (
      <span className="row" style={{ gap: 6, width: "100%" }}>
        <input className="input sm" autoFocus value={edit.text} style={{ flex: 1 }}
          onChange={(e) => setEdit({ id: f.id, text: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter") void saveEdit();
            if (e.key === "Escape") setEdit(null);
          }} />
        <button className="btn sm primary" onClick={saveEdit}><Check size={13} /></button>
        <button className="btn sm ghost" onClick={() => setEdit(null)}><X size={13} /></button>
      </span>
    ) : (
      <span className={className} onDoubleClick={() => setEdit({ id: f.id, text: f.text })}
        title="Doppelklick zum Bearbeiten">{f.text}</span>
    )
  );

  const facts = (data?.facts || []).filter((f) => !q || f.text.toLowerCase().includes(q.toLowerCase()));
  const core = data?.core || [];

  if (error) return <div className="page"><ErrorState error={error} retry={reload} /></div>;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1><BookOpen size={20} /> Gedächtnis</h1>
          <p className="muted">Anweisungen gelten immer. Gemerktes sind einzelne Tatsachen — beides
            hier änderbar, ohne Neustart.</p>
        </div>
      </header>

      <Panel title={`Hauptgedächtnis${data ? ` · ${core.length}/${data.max_core}` : ""}`}
        icon={<Zap size={15} />}
        foot="Diese Sätze stehen in JEDEM Systemtext — vor jeder Antwort und vor jeder Handlung, ohne dass er etwas aufrufen muss. Deshalb ist der Platz begrenzt: Was hier steht, wird bei jeder Anfrage mitgeschickt.">
        <div className="panel-body row" style={{ gap: 8 }}>
          <input className="input" placeholder="Satz, der immer gelten soll — z. B. Firmenwagen ist ein Sprinter, HH-RS 412"
            value={kern} onChange={(e) => setKern(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void addCore(); }}
            disabled={!!data && core.length >= data.max_core} />
          <button className="btn primary" onClick={addCore}
            disabled={!kern.trim() || (!!data && core.length >= data.max_core)}>
            <Zap size={14} />Eintragen
          </button>
        </div>
        {data && core.length >= data.max_core && (
          <div className="panel-body small muted" style={{ paddingTop: 0 }}>
            Alle {data.max_core} Plätze belegt. Nimm erst einen heraus — was hier steht, wird bei jeder
            Anfrage mitgeschickt.
          </div>
        )}
        {loading && !data ? <div className="panel-body"><Skeleton rows={2} /></div>
          : core.length === 0 ? (
            <div className="panel-body small muted">
              Noch nichts eingetragen. Oben einen Satz hineinschreiben — oder unten bei „Gemerktes"
              auf das Blitzsymbol klicken, um einen bestehenden hierher zu heben.
            </div>
          ) : (
            <ul className="core-list">
              {core.map((f) => (
                <li key={f.id}>
                  <span className="core-mark" aria-hidden />
                  {editable(f, "core-text")}
                  {edit?.id !== f.id && (
                    <>
                      <button className="btn sm ghost" onClick={() => setEdit({ id: f.id, text: f.text })}
                        title="Bearbeiten"><Pencil size={13} /></button>
                      <button className="btn sm ghost" onClick={() => pin(f, false)}
                        title="Aus dem Hauptgedächtnis nehmen"><X size={13} /></button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
      </Panel>

      <Panel title="Stehende Anweisungen" icon={<Sparkles size={15} />}
        actions={<button className="btn sm primary" onClick={save} disabled={saving || !dirty}>
          <Check size={14} />{saving ? "Speichere …" : dirty ? "Speichern" : "Gespeichert"}
        </button>}
        foot="Dieser Text läuft in jedem Gespräch mit — im Chat und auf der Sprachleitung. Er geht seinen eigenen Gewohnheiten vor.">
        {loading && !data ? <div className="panel-body"><Skeleton rows={4} /></div> : (
          <div className="panel-body">
            <textarea className="input" style={{ minHeight: 220, lineHeight: 1.6 }}
              value={text} placeholder={BEISPIEL}
              onChange={(e) => { setText(e.target.value); setDirty(true); }} />
            <p className="small muted" style={{ marginTop: 8 }}>
              Kurze, klare Sätze wirken besser als Absätze. Ein Satz pro Regel.
              {text.length > 0 && ` · ${text.length} Zeichen`}
            </p>
          </div>
        )}
      </Panel>

      <Panel title={`Gemerktes${data ? ` (${data.total})` : ""}`} icon={<BookOpen size={15} />}
        actions={<>
          <input className="input sm" placeholder="suchen …" value={q} onChange={(e) => setQ(e.target.value)}
            style={{ width: 160 }} />
          {(data?.total ?? 0) > 0 && (
            <button className="btn sm danger" onClick={clearAll}><Trash2 size={13} />Alles löschen</button>
          )}
        </>}
        foot="Was er sich selbst notiert hat oder was du ihm einträgst. Ein falscher Eintrag hier wirkt in jedem Gespräch — deshalb steht er zum Löschen da.">
        <div className="panel-body row" style={{ gap: 8 }}>
          <input className="input" placeholder="Etwas eintragen, das er sich merken soll …"
            value={neu} onChange={(e) => setNeu(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void addFact(); }} />
          <button className="btn primary" onClick={addFact} disabled={!neu.trim()}><Plus size={14} />Merken</button>
        </div>
        {loading && !data ? <div className="panel-body"><Skeleton rows={3} /></div>
          : facts.length === 0 ? (
            <EmptyState icon={<BookOpen size={22} />} title={q ? "Nichts gefunden" : "Er hat sich noch nichts gemerkt"}>
              {q ? "Andere Suche versuchen." : "Im Gespräch merkt er sich von selbst, was wichtig aussieht. Du kannst hier auch direkt etwas eintragen."}
            </EmptyState>
          ) : (
            <table className="table">
              <thead><tr><th>Eintrag</th><th>von</th><th>wann</th><th /></tr></thead>
              <tbody>
                {facts.map((f) => (
                  <tr key={f.id}>
                    <td>
                      {f.pinned ? <span className="core-badge" title="Im Hauptgedächtnis"><Zap size={11} /></span> : null}
                      {editable(f)}
                    </td>
                    <td className="small muted">{f.actor || "—"}</td>
                    <td className="small muted">{relative(f.created_at)}</td>
                    <td className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                      <button className="btn sm" onClick={() => setEdit({ id: f.id, text: f.text })}
                        title="Bearbeiten"><Pencil size={13} /></button>
                      <button className={`btn sm ${f.pinned ? "primary" : ""}`} onClick={() => pin(f, !f.pinned)}
                        title={f.pinned ? "Aus dem Hauptgedächtnis nehmen" : "Ins Hauptgedächtnis heben — gilt dann immer"}>
                        <Zap size={13} />
                      </button>
                      <button className="btn sm danger" onClick={() => delFact(f)} title="Diesen Eintrag löschen">
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Panel>
    </div>
  );
}
