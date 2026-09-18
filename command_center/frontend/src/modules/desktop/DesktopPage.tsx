/**
 * Geräte — was mit diesem JARVIS verbunden ist, und was man damit tun kann.
 *
 * Vorher war das eine Statusanzeige mit einem Rohformular für JSON-Parameter:
 * richtig, aber nur für jemanden brauchbar, der die Aktionsnamen auswendig
 * kennt. Jetzt steht je Gerät eine Karte da — Zustand, Fähigkeiten, und die
 * Handgriffe, die man wirklich braucht: nachsehen, was auf dem Bildschirm
 * ist, eine App öffnen, etwas tippen, umbenennen, entfernen.
 *
 * Der Server selbst steht mit in der Liste. Er ist schließlich auch ein
 * Gerät, und wer „welche Geräte hängen dran" fragt, meint ihn mit.
 */
import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import { Monitor, Play, RefreshCw, Terminal, Eye, Pencil, Trash2, Server, Cpu, Keyboard } from "@/lib/icons";
import { Badge, EmptyState, ErrorState, KeyValue, Modal, Panel, Skeleton, StatusIndicator } from "@/components/ui";
import { toast } from "@/lib/toast";

interface Device {
  id: string; name: string; platform: string; version: string; online: boolean; queued: number;
  last_seen_at: string; registered_at: string; actor: string;
  actions: { name: string; description?: string; parameters?: any }[];
}

/** Die Handgriffe, die man ohne Handbuch braucht. Alles Übrige steht weiter
 *  unter „Aktion ausführen" — dort mit der echten Liste des Geräts. */
const SCHNELL = [
  { id: "open", label: "App öffnen", icon: Play, action: "open_app",
    field: "app_name", placeholder: "Chrome, Excel, WhatsApp …", needs: "open_app" },
  { id: "type", label: "Text tippen", icon: Keyboard, action: "computer_control",
    field: "text", placeholder: "Was getippt werden soll", needs: "computer_control" },
];

export default function DesktopPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<{ devices: Device[]; stats: any; history: any[] }>(
    "/api/desktop/devices", { refreshOn: ["desktop.device", "desktop.command", "desktop.removed"],
      interval: 20000 });
  const status = useApi<any>("/api/status", { interval: 60000 });
  const [form, setForm] = useState<{ device: Device; action: string; params: string } | null>(null);
  const [quick, setQuick] = useState<{ device: Device; kind: typeof SCHNELL[number]; value: string } | null>(null);
  const [rename, setRename] = useState<{ device: Device; name: string } | null>(null);
  const [shot, setShot] = useState<{ device: Device; path: string } | null>(null);
  const [busy, setBusy] = useState("");

  const devices = data?.devices || [];

  const run = async (device: Device, action: string, params: any, label: string) => {
    setBusy(device.id);
    try {
      const r = await api.post(`/api/desktop/devices/${device.id}/command`, { action, params, timeout: 90 });
      const cmd = r.command;
      toast({ title: cmd.status === "done" ? label : "Der PC meldet ein Problem",
        body: cmd.result || cmd.error, tone: cmd.status === "done" ? "ok" : "err" });
      reload();
    } catch (e: any) {
      toast({ title: "Ging nicht", body: e.message, tone: "err" });
    } finally { setBusy(""); }
  };

  const screen = async (device: Device) => {
    setBusy(device.id);
    try {
      const r = await api.post<{ path: string }>(`/api/desktop/devices/${device.id}/screen`);
      setShot({ device, path: r.path });
    } catch (e: any) {
      toast({ title: "Kein Bild", body: e.message, tone: "err" });
    } finally { setBusy(""); }
  };

  const forget = async (device: Device) => {
    if (!window.confirm(`„${device.name}" aus der Liste entfernen? Meldet er sich wieder, taucht er neu auf.`)) return;
    try { await api.del(`/api/desktop/devices/${device.id}`); reload(); }
    catch (e: any) { toast({ title: "Ging nicht", body: e.message, tone: "err" }); }
  };

  const saveName = async () => {
    if (!rename?.name.trim()) return;
    try {
      await api.patch(`/api/desktop/devices/${rename.device.id}`, { name: rename.name.trim() });
      setRename(null); reload();
    } catch (e: any) { toast({ title: "Ging nicht", body: e.message, tone: "err" }); }
  };

  const sendQuick = async () => {
    if (!quick || !quick.value.trim()) return;
    const params = quick.kind.action === "computer_control"
      ? { action: "type", text: quick.value }
      : { [quick.kind.field]: quick.value };
    await run(quick.device, quick.kind.action, params, quick.kind.label + " — erledigt");
    setQuick(null);
  };

  const sendRaw = async () => {
    if (!form) return;
    let params: any = {};
    try { params = form.params.trim() ? JSON.parse(form.params) : {}; }
    catch { toast({ title: "Die Parameter sind kein gültiges JSON", tone: "err" }); return; }
    await run(form.device, form.action, params, "Auf dem PC erledigt");
    setForm(null);
  };

  const srv = status.data?.server;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1><Monitor size={20} /> Geräte</h1>
          <p className="muted">
            {data ? `${data.stats.online} von ${data.stats.devices} Rechnern online`
              : "Was mit diesem JARVIS verbunden ist"}
            {data?.stats.queued ? ` · ${data.stats.queued} Aufträge warten` : ""}
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => reload()}><RefreshCw size={14} />Aktualisieren</button>
        </div>
      </header>

      <ErrorState error={error} retry={reload} />

      {/* Der Server ist auch ein Gerät — und das einzige, das immer da ist. */}
      <Panel title="Dieser Server" icon={<Server size={15} />}
        actions={<Badge status="ok">läuft</Badge>}>
        <div className="panel-body">
          <KeyValue items={[
            ["Name", srv?.hostname || "—"],
            ["Auslastung", srv ? `CPU ${Math.round(srv.cpu)} % · RAM ${Math.round(srv.ram)} % · Platte ${Math.round(srv.disk)} %` : "—"],
            ["Docker", <Badge status={srv?.docker?.status}>{srv?.docker?.status?.replace("_", " ") || "—"}</Badge>],
            ["Laufzeit", status.data ? relative(new Date(Date.now() - status.data.jarvis.uptime_seconds * 1000).toISOString()) : "—"],
          ]} />
        </div>
      </Panel>

      {loading && !data ? <Skeleton rows={5} height={18} />
        : devices.length === 0 ? (
          <EmptyState icon={<Monitor size={22} />} title="Noch kein Rechner gekoppelt">
            Auf dem PC einmal <code>python install_desktop.py</code> laufen lassen — das legt das
            Maschinen-Token an und trägt es ein. Danach meldet sich der Rechner hier von selbst.
          </EmptyState>
        ) : devices.map((d) => (
          <Panel key={d.id} title={d.name} icon={<Monitor size={15} />}
            actions={<>
              <StatusIndicator status={d.online ? "ok" : "err"} label={d.online ? "online" : "offline"}
                live={d.online} />
              {can("operator") && <>
                <button className="btn sm" onClick={() => setRename({ device: d, name: d.name })}
                  title="Umbenennen"><Pencil size={13} /></button>
                <button className="btn sm danger" onClick={() => forget(d)}
                  title="Aus der Liste entfernen"><Trash2 size={13} /></button>
              </>}
            </>}
            foot={`${d.actions.length} Fähigkeiten · zuletzt gesehen ${relative(d.last_seen_at)} · gekoppelt seit ${dateTime(d.registered_at)}`}>
            <div className="panel-body stack">
              <KeyValue items={[
                ["System", `${d.platform || "unbekannt"}${d.version ? ` · ${d.version}` : ""}`],
                ["Kennung", <code>{d.actor}</code>],
                ["Wartende Aufträge", String(d.queued)],
              ]} />

              {can("operator") && (
                <div className="row wrap" style={{ gap: 8 }}>
                  <button className="btn sm" disabled={!d.online || busy === d.id}
                    onClick={() => screen(d)}
                    title={d.online ? "Ein Bild des Bildschirms holen" : "Der Rechner ist offline"}>
                    <Eye size={13} />{busy === d.id ? "hole …" : "Bildschirm ansehen"}
                  </button>
                  {SCHNELL.map((k) => (
                    <button key={k.id} className="btn sm" disabled={!d.online || busy === d.id}
                      onClick={() => setQuick({ device: d, kind: k, value: "" })}
                      title={d.actions.some((a) => a.name === k.needs)
                        ? k.label : `Dieser Rechner kann ${k.needs} nicht`}>
                      <k.icon size={13} />{k.label}
                    </button>
                  ))}
                  <button className="btn sm" disabled={!d.online}
                    onClick={() => setForm({ device: d, action: d.actions[0]?.name || "", params: "{}" })}>
                    <Terminal size={13} />Aktion ausführen
                  </button>
                </div>
              )}

              <details>
                <summary className="small muted" style={{ cursor: "pointer" }}>
                  Was dieser Rechner kann ({d.actions.length})
                </summary>
                <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
                  {d.actions.map((a) => (
                    <span key={a.name} className="badge" title={a.description}>{a.name}</span>
                  ))}
                </div>
              </details>
            </div>
          </Panel>
        ))}

      {data && data.history.length > 0 && (
        <Panel title="Zuletzt ausgeführt" icon={<Cpu size={15} />} flush>
          <table className="table">
            <thead><tr><th>Wann</th><th>Gerät</th><th>Aktion</th><th>Ergebnis</th></tr></thead>
            <tbody>
              {data.history.slice(0, 15).map((h: any) => (
                <tr key={h.id}>
                  <td className="small muted">{relative(h.created_at)}</td>
                  <td className="small">{devices.find((d) => d.id === h.device_id)?.name || h.device_id}</td>
                  <td className="small mono">{h.action}</td>
                  <td className="small">
                    <Badge status={h.status === "done" ? "ok" : h.status === "failed" ? "err" : "warn"}>
                      {h.status}
                    </Badge>{" "}
                    <span className="muted">{(h.result || h.error || "").slice(0, 80)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {quick && (
        <Modal title={`${quick.kind.label} — ${quick.device.name}`} onClose={() => setQuick(null)} foot={<>
          <button className="btn" onClick={() => setQuick(null)}>Abbrechen</button>
          <button className="btn primary" onClick={sendQuick} disabled={!quick.value.trim() || !!busy}>
            {busy ? "Läuft …" : "Ausführen"}
          </button>
        </>}>
          <div className="field">
            <label>{quick.kind.label}</label>
            <input className="input" autoFocus value={quick.value}
              placeholder={quick.kind.placeholder}
              onChange={(e) => setQuick({ ...quick, value: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") void sendQuick(); }} />
            <span className="small muted">
              {quick.kind.action === "computer_control"
                ? "Getippt wird in das Fenster, das auf dem PC gerade vorn ist."
                : "Der Name der Anwendung, so wie du sie kennst."}
            </span>
          </div>
        </Modal>
      )}

      {rename && (
        <Modal title="Gerät umbenennen" onClose={() => setRename(null)} foot={<>
          <button className="btn" onClick={() => setRename(null)}>Abbrechen</button>
          <button className="btn primary" onClick={saveName} disabled={!rename.name.trim()}>Speichern</button>
        </>}>
          <div className="field"><label>Name</label>
            <input className="input" autoFocus value={rename.name}
              onChange={(e) => setRename({ ...rename, name: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") void saveName(); }} />
            <span className="small muted">Nur die Anzeige. Die Kopplung bleibt bestehen.</span>
          </div>
        </Modal>
      )}

      {shot && (
        <Modal wide title={`Bildschirm — ${shot.device.name}`} onClose={() => setShot(null)}
          foot={<span className="small muted">Liegt als {shot.path} unter Dateien.</span>}>
          <img src={`${api.base}/api/files/download?path=${encodeURIComponent(shot.path)}`}
            alt={`Bildschirm von ${shot.device.name}`}
            style={{ width: "100%", borderRadius: 10, border: "1px solid var(--line)" }} />
        </Modal>
      )}

      {form && (
        <Modal title={`Aktion auf ${form.device.name}`} onClose={() => setForm(null)} foot={<>
          <button className="btn" onClick={() => setForm(null)}>Abbrechen</button>
          <button className="btn primary" onClick={sendRaw} disabled={!form.action || !!busy}>
            {busy ? "Läuft …" : "Ausführen"}
          </button>
        </>}>
          <div className="field"><label>Aktion</label>
            <select className="select" value={form.action}
              onChange={(e) => setForm({ ...form, action: e.target.value })}>
              {form.device.actions.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
            </select>
            <span className="small muted">
              {form.device.actions.find((a) => a.name === form.action)?.description || ""}
            </span>
          </div>
          <div className="field"><label>Parameter (JSON)</label>
            <textarea className="input" style={{ minHeight: 120, fontFamily: "var(--mono, monospace)" }}
              value={form.params} onChange={(e) => setForm({ ...form, params: e.target.value })} />
            <span className="small muted">
              Welche Felder eine Aktion nimmt, steht in ihrer Beschreibung oben.
            </span>
          </div>
        </Modal>
      )}
    </div>
  );
}
