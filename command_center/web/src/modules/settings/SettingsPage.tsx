import { useState } from "react";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { Settings, KeyRound, UserRound, Radio, Copy, Check } from "@/lib/icons";
import { Badge, ErrorState, KeyValue, Modal, Panel, Skeleton, StatusIndicator, Toggle } from "@/components/ui";
import { toast } from "@/lib/toast";

export default function SettingsPage() {
  const { user, can } = useAuth();
  const settings = useApi<any>("/api/settings");
  const users = useApi<{ users: any[] }>(can("admin") ? "/api/auth/users" : null);
  const tokens = useApi<{ tokens: any[] }>(can("admin") ? "/api/auth/tokens" : null);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [newUser, setNewUser] = useState<{ username: string; password: string; role: string } | null>(null);
  const [newToken, setNewToken] = useState<{ name: string; actor: string; role: string } | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const changePw = async () => { try { await api.post("/api/auth/password", pw); toast({ title: "Passwort geändert", tone: "ok" }); setPw({ current_password: "", new_password: "" }); } catch (e: any) { toast({ title: "Fehlgeschlagen", body: e.message, tone: "err" }); } };
  const createUser = async () => { if (!newUser) return; try { await api.post("/api/auth/users", newUser); toast({ title: "Benutzer angelegt", tone: "ok" }); setNewUser(null); users.reload(); } catch (e: any) { toast({ title: "Fehlgeschlagen", body: e.message, tone: "err" }); } };
  const patchUser = async (id: string, body: any) => { try { await api.patch(`/api/auth/users/${id}`, body); users.reload(); } catch (e: any) { toast({ title: "Fehlgeschlagen", body: e.message, tone: "err" }); } };
  const createToken = async () => { if (!newToken) return; try { const r = await api.post("/api/auth/tokens", newToken); setSecret(r.secret); setNewToken(null); tokens.reload(); } catch (e: any) { toast({ title: "Fehlgeschlagen", body: e.message, tone: "err" }); } };
  const deleteUser = async (u: any) => { if (window.confirm(`Benutzer „${u.username}“ endgültig löschen? Laufende Anmeldungen enden sofort.`)) { try { await api.del(`/api/auth/users/${u.id}`); users.reload(); } catch (e: any) { toast({ title: "Löschen fehlgeschlagen", body: e.message, tone: "err" }); } } };
  const purgeToken = async (t: any) => { if (window.confirm(`Token „${t.name}“ endgültig löschen? Er verschwindet aus der Liste und kann nie wieder benutzt werden.`)) { try { await api.del(`/api/auth/tokens/${t.id}?purge=true`); tokens.reload(); } catch (e: any) { toast({ title: "Löschen fehlgeschlagen", body: e.message, tone: "err" }); } } };
  const revoke = async (id: string) => { if (window.confirm("Diesen Token widerrufen? Geräte, die ihn nutzen, werden getrennt.")) { await api.del(`/api/auth/tokens/${id}`); tokens.reload(); } };
  const s = settings.data;

  return (
    <div className="page">
      <div className="page-head"><div><div className="eyebrow">Konfiguration kommt aus der Umgebung · Geheimnisse verlassen den Server nie</div><h1>Einstellungen</h1></div></div>
      <ErrorState error={settings.error} retry={() => settings.reload(false)} />
      <div className="grid cols-2">
        <Panel title="Hauptagent & Anbieter" icon={<Settings size={15} />}>
          {!s ? <Skeleton /> : <div className="stack">
            <KeyValue items={[["Modus", <Badge status={s.master.mode === "none" ? "offline" : "ok"}>{s.master.mode}</Badge>], ["Anbieter", s.master.provider?.label || "—"], ["Zustand", s.master.label], ["Version", s.version]]} />
            <div className="label">Eingerichtete Anbieter</div>
            <div className="row wrap">{Object.entries(s.providers).map(([k, v]: any) => <span key={k} className="row small" style={{ gap: 6 }}><StatusIndicator status={v ? "ok" : "offline"} />{k}</span>)}</div>
            <p className="small muted">Auswahl über <code>JARVIS_AI_PROVIDER</code> (anthropic · openai · gemini · local) und <code>JARVIS_AI_MODEL</code>. Oder <code>JARVIS_MASTER_AGENT_MODE=remote</code> zusammen mit <code>JARVIS_GATEWAY_URL</code>/<code>JARVIS_GATEWAY_TOKEN</code> setzen, um an die übergeordnete Steuerzentrale weiterzuleiten.</p>
          </div>}
        </Panel>
        <Panel title="Fähigkeiten & Sicherheit" icon={<KeyRound size={15} />}>
          {!s ? <Skeleton /> : <KeyValue items={[["Terminal-Werkzeug", <Badge status={s.capabilities.terminal ? "ok" : "offline"}>{s.capabilities.terminal ? "an (nur mit Freigabe)" : "aus"}</Badge>], ["Docker-Aktionen", <Badge status={s.capabilities.docker_actions ? "ok" : "offline"}>{s.capabilities.docker_actions ? "an" : "aus"}</Badge>], ["Dienst-Neustarts", <Badge status={s.capabilities.service_restart ? "ok" : "offline"}>{s.capabilities.service_restart ? "an" : "aus"}</Badge>], ["Überwachte Dienste", s.capabilities.monitored_services.join(", ") || "—"], ["Freigabe ab Risiko", s.capabilities.approval_threshold], ["Freigabe-Frist", `${s.capabilities.approval_timeout_minutes} min`], ["Sichere Cookies", s.security.secure_cookies ? "ja" : "nein"], ["Sitzungsdauer", `${s.security.session_ttl_hours} h`], ["Proxy-Headern vertrauen", s.security.trust_proxy ? "ja" : "nein"], ["Login-Limit", `${s.security.login_rate_limit_per_minute}/min`], ["Datenordner", <code>{s.paths.data_dir}</code>], ["Arbeitsbereich", <code>{s.paths.workspace_dir}</code>], ["Agentenliste", s.paths.agent_roster || "eingebaut"]]} />}
        </Panel>
      </div>
      <p className="small muted">
        Handy koppeln (QR-Code) und PC koppeln stehen jetzt gemeinsam unter <a href="/desktop">Geräte</a>.
      </p>

      <Panel title="Geräte koppeln (Mark-LIII Fernsteuerung)" icon={<Radio size={15} />}>
        {!s ? <Skeleton /> : <div className="stack small">
          <p>{s.desktop_pairing.instructions}</p>
          <KeyValue items={[["Gateway-Adresse", <code>{s.desktop_pairing.gateway_url}</code>], ["Endpunkte", s.desktop_pairing.endpoints.map((e: string) => <code key={e} style={{ marginRight: 6 }}>{e}</code>)]]} />
          <p className="muted">Unten einen Maschinen-Token (Rolle Operator) mit dem Akteursnamen anlegen, den der Desktop verwendet, z. B. <code>mark-liii-windows</code>. Der Token wird nur einmal angezeigt.</p>
        </div>}
      </Panel>
      <div className="grid cols-2">
        <Panel title="Dein Konto" icon={<UserRound size={15} />}>
          <div className="stack">
            <KeyValue items={[["Name", user?.name], ["Benutzername", user?.actor], ["Rolle", user?.role]]} />
            <div className="label">Passwort ändern</div>
            <input className="input" type="password" placeholder="Aktuelles Passwort" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
            <input className="input" type="password" placeholder="Neues Passwort (mind. 8 Zeichen)" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
            <button className="btn" onClick={changePw} disabled={!pw.current_password || pw.new_password.length < 8}>Passwort ändern</button>
          </div>
        </Panel>
        {can("admin") && (
          <Panel title="Benutzer" icon={<UserRound size={15} />} actions={<button className="btn sm primary" onClick={() => setNewUser({ username: "", password: "", role: "viewer" })}>Benutzer anlegen</button>} flush>
            {!users.data ? <div className="panel-body"><Skeleton /></div> : <table className="table"><thead><tr><th>Benutzer</th><th>Rolle</th><th>Letzter Login</th><th>Aktiv</th><th /></tr></thead><tbody>{users.data.users.map((u) => (
              <tr key={u.id}><td>{u.display_name}<div className="tiny muted">{u.username}</div></td><td><select className="select" style={{ height: 28, width: 120 }} value={u.role} disabled={u.id === user?.id} onChange={(e) => patchUser(u.id, { role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></td><td className="small muted">{u.last_login_at ? relative(u.last_login_at) : "nie"}</td><td><Toggle checked={!u.disabled} onChange={(v) => u.id !== user?.id && patchUser(u.id, { disabled: !v })} label={`Enable ${u.username}`} /></td><td style={{ textAlign: "right" }}>{u.id !== user?.id && <button className="btn sm danger" onClick={() => deleteUser(u)}>Löschen</button>}</td></tr>))}</tbody></table>}
          </Panel>
        )}
      </div>
      {can("admin") && (
        <Panel title="Maschinen-Token" icon={<KeyRound size={15} />} actions={<button className="btn sm primary" onClick={() => setNewToken({ name: "desktop", actor: "mark-liii-windows", role: "operator" })}>Token erstellen</button>} flush foot="Token weisen den Desktop-Client und Automationen über den Header X-Jarvis-Token aus. Gespeichert wird nur ein Hash.">
          {!tokens.data ? <div className="panel-body"><Skeleton /></div> : tokens.data.tokens.length === 0 ? <div className="panel-body small muted">Noch keine Token.</div> : <table className="table"><thead><tr><th>Name</th><th>Akteur</th><th>Rolle</th><th>Erstellt</th><th>Zuletzt genutzt</th><th /></tr></thead><tbody>{tokens.data.tokens.map((t) => (
            <tr key={t.id} style={{ opacity: t.revoked ? 0.5 : 1 }}><td>{t.name}</td><td><code>{t.actor}</code></td><td className="muted">{t.role}</td><td className="small muted">{relative(t.created_at)} von {t.created_by}</td><td className="small muted">{t.last_used_at ? relative(t.last_used_at) : "nie"}</td><td style={{ textAlign: "right" }}><span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>{t.revoked ? <Badge status="offline">widerrufen</Badge> : <button className="btn sm" onClick={() => revoke(t.id)}>Widerrufen</button>}<button className="btn sm danger" onClick={() => purgeToken(t)}>Löschen</button></span></td></tr>))}</tbody></table>}
        </Panel>
      )}
      {newUser && <Modal title="Benutzer anlegen" onClose={() => setNewUser(null)} foot={<><button className="btn" onClick={() => setNewUser(null)}>Abbrechen</button><button className="btn primary" onClick={createUser} disabled={!newUser.username || newUser.password.length < 8}>Anlegen</button></>}>
        <div className="stack"><div className="field"><label>Benutzername</label><input className="input" value={newUser.username} onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} autoFocus /></div><div className="field"><label>Passwort (mind. 8 Zeichen)</label><input className="input" type="password" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} /></div><div className="field"><label>Rolle</label><select className="select" value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></div></div>
      </Modal>}
      {newToken && <Modal title="Maschinen-Token erstellen" onClose={() => setNewToken(null)} foot={<><button className="btn" onClick={() => setNewToken(null)}>Abbrechen</button><button className="btn primary" onClick={createToken} disabled={!newToken.name || !newToken.actor}>Erstellen</button></>}>
        <div className="stack"><div className="field"><label>Name</label><input className="input" value={newToken.name} onChange={(e) => setNewToken({ ...newToken, name: e.target.value })} /></div><div className="field"><label>Akteur (Name, den der Client meldet)</label><input className="input" value={newToken.actor} onChange={(e) => setNewToken({ ...newToken, actor: e.target.value })} /></div><div className="field"><label>Rolle</label><select className="select" value={newToken.role} onChange={(e) => setNewToken({ ...newToken, role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></div></div>
      </Modal>}
      {secret && <Modal title="Token erstellt — jetzt kopieren" onClose={() => setSecret(null)} foot={<button className="btn primary" onClick={() => setSecret(null)}>Fertig</button>}>
        <div className="stack"><p className="small">Dieses Geheimnis wird nur einmal angezeigt. Trage es in die Umgebungsvariable <code>JARVIS_GATEWAY_TOKEN</code> des Desktops ein.</p><div className="row"><code className="input" style={{ height: "auto", padding: 10, wordBreak: "break-all" }}>{secret}</code><button className="btn icon" onClick={() => navigator.clipboard?.writeText(secret).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })} aria-label="Token kopieren">{copied ? <Check /> : <Copy />}</button></div></div>
      </Modal>}
    </div>
  );
}
