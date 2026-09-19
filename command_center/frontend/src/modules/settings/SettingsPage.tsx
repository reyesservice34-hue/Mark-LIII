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

  const changePw = async () => { try { await api.post("/api/auth/password", pw); toast({ title: "Password changed", tone: "ok" }); setPw({ current_password: "", new_password: "" }); } catch (e: any) { toast({ title: "Failed", body: e.message, tone: "err" }); } };
  const createUser = async () => { if (!newUser) return; try { await api.post("/api/auth/users", newUser); toast({ title: "User created", tone: "ok" }); setNewUser(null); users.reload(); } catch (e: any) { toast({ title: "Failed", body: e.message, tone: "err" }); } };
  const patchUser = async (id: string, body: any) => { try { await api.patch(`/api/auth/users/${id}`, body); users.reload(); } catch (e: any) { toast({ title: "Failed", body: e.message, tone: "err" }); } };
  const createToken = async () => { if (!newToken) return; try { const r = await api.post("/api/auth/tokens", newToken); setSecret(r.secret); setNewToken(null); tokens.reload(); } catch (e: any) { toast({ title: "Failed", body: e.message, tone: "err" }); } };
  const revoke = async (id: string) => { if (window.confirm("Revoke this token? Clients using it will be disconnected.")) { await api.del(`/api/auth/tokens/${id}`); tokens.reload(); } };
  const s = settings.data;

  return (
    <div className="page">
      <div className="page-head"><div><div className="eyebrow">Configuration is environment-driven · secrets never leave the server</div><h1>Settings</h1></div></div>
      <ErrorState error={settings.error} retry={() => settings.reload(false)} />
      <div className="grid cols-2">
        <Panel title="Master agent & providers" icon={<Settings size={15} />}>
          {!s ? <Skeleton /> : <div className="stack">
            <KeyValue items={[["Mode", <Badge status={s.master.mode === "none" ? "offline" : "ok"}>{s.master.mode}</Badge>], ["Provider", s.master.provider?.label || "—"], ["State", s.master.label], ["Version", s.version]]} />
            <div className="label">Configured providers</div>
            <div className="row wrap">{Object.entries(s.providers).map(([k, v]: any) => <span key={k} className="row small" style={{ gap: 6 }}><StatusIndicator status={v ? "ok" : "offline"} />{k}</span>)}</div>
            <p className="small muted">Select with <code>JARVIS_AI_PROVIDER</code> (anthropic · openai · gemini · local) and <code>JARVIS_AI_MODEL</code>; or set <code>JARVIS_MASTER_AGENT_MODE=remote</code> with <code>JARVIS_GATEWAY_URL</code>/<code>JARVIS_GATEWAY_TOKEN</code> to relay to the upstream control plane.</p>
          </div>}
        </Panel>
        <Panel title="Capabilities & safety" icon={<KeyRound size={15} />}>
          {!s ? <Skeleton /> : <KeyValue items={[["Terminal tool", <Badge status={s.capabilities.terminal ? "ok" : "offline"}>{s.capabilities.terminal ? "enabled (approval-gated)" : "disabled"}</Badge>], ["Docker actions", <Badge status={s.capabilities.docker_actions ? "ok" : "offline"}>{s.capabilities.docker_actions ? "enabled" : "disabled"}</Badge>], ["Service restarts", <Badge status={s.capabilities.service_restart ? "ok" : "offline"}>{s.capabilities.service_restart ? "enabled" : "disabled"}</Badge>], ["Monitored services", s.capabilities.monitored_services.join(", ") || "—"], ["Approval from risk", s.capabilities.approval_threshold], ["Approval timeout", `${s.capabilities.approval_timeout_minutes} min`], ["Secure cookies", s.security.secure_cookies], ["Session TTL", `${s.security.session_ttl_hours} h`], ["Trust proxy headers", s.security.trust_proxy ? "yes" : "no"], ["Login rate limit", `${s.security.login_rate_limit_per_minute}/min`], ["Data dir", <code>{s.paths.data_dir}</code>], ["Workspace", <code>{s.paths.workspace_dir}</code>], ["Agent roster", s.paths.agent_roster || "built-in"]]} />}
        </Panel>
      </div>
      <Panel title="Desktop pairing (Mark-LIII remote)" icon={<Radio size={15} />}>
        {!s ? <Skeleton /> : <div className="stack small">
          <p>{s.desktop_pairing.instructions}</p>
          <KeyValue items={[["Gateway URL", <code>{s.desktop_pairing.gateway_url}</code>], ["Endpoints", s.desktop_pairing.endpoints.map((e: string) => <code key={e} style={{ marginRight: 6 }}>{e}</code>)]]} />
          <p className="muted">Create a machine token below (role operator) with the actor name the desktop uses, e.g. <code>mark-liii-windows</code>. The token is shown once.</p>
        </div>}
      </Panel>
      <div className="grid cols-2">
        <Panel title="Your account" icon={<UserRound size={15} />}>
          <div className="stack">
            <KeyValue items={[["Name", user?.name], ["Username", user?.actor], ["Role", user?.role]]} />
            <div className="label">Change password</div>
            <input className="input" type="password" placeholder="Current password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
            <input className="input" type="password" placeholder="New password (min 8)" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
            <button className="btn" onClick={changePw} disabled={!pw.current_password || pw.new_password.length < 8}>Update password</button>
          </div>
        </Panel>
        {can("admin") && (
          <Panel title="Users" icon={<UserRound size={15} />} actions={<button className="btn sm primary" onClick={() => setNewUser({ username: "", password: "", role: "viewer" })}>Add user</button>} flush>
            {!users.data ? <div className="panel-body"><Skeleton /></div> : <table className="table"><thead><tr><th>User</th><th>Role</th><th>Last login</th><th>Active</th></tr></thead><tbody>{users.data.users.map((u) => (
              <tr key={u.id}><td>{u.display_name}<div className="tiny muted">{u.username}</div></td><td><select className="select" style={{ height: 28, width: 120 }} value={u.role} disabled={u.id === user?.id} onChange={(e) => patchUser(u.id, { role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></td><td className="small muted">{u.last_login_at ? relative(u.last_login_at) : "never"}</td><td><Toggle checked={!u.disabled} onChange={(v) => u.id !== user?.id && patchUser(u.id, { disabled: !v })} label={`Enable ${u.username}`} /></td></tr>))}</tbody></table>}
          </Panel>
        )}
      </div>
      {can("admin") && (
        <Panel title="Machine tokens" icon={<KeyRound size={15} />} actions={<button className="btn sm primary" onClick={() => setNewToken({ name: "desktop", actor: "mark-liii-windows", role: "operator" })}>Create token</button>} flush foot="Tokens authenticate the desktop client and automations via the X-Jarvis-Token header. Only a hash is stored.">
          {!tokens.data ? <div className="panel-body"><Skeleton /></div> : tokens.data.tokens.length === 0 ? <div className="panel-body small muted">No tokens yet.</div> : <table className="table"><thead><tr><th>Name</th><th>Actor</th><th>Role</th><th>Created</th><th>Last used</th><th /></tr></thead><tbody>{tokens.data.tokens.map((t) => (
            <tr key={t.id} style={{ opacity: t.revoked ? 0.5 : 1 }}><td>{t.name}</td><td><code>{t.actor}</code></td><td className="muted">{t.role}</td><td className="small muted">{relative(t.created_at)} by {t.created_by}</td><td className="small muted">{t.last_used_at ? relative(t.last_used_at) : "never"}</td><td style={{ textAlign: "right" }}>{t.revoked ? <Badge status="offline">revoked</Badge> : <button className="btn sm danger" onClick={() => revoke(t.id)}>Revoke</button>}</td></tr>))}</tbody></table>}
        </Panel>
      )}
      {newUser && <Modal title="Add user" onClose={() => setNewUser(null)} foot={<><button className="btn" onClick={() => setNewUser(null)}>Cancel</button><button className="btn primary" onClick={createUser} disabled={!newUser.username || newUser.password.length < 8}>Create</button></>}>
        <div className="stack"><div className="field"><label>Username</label><input className="input" value={newUser.username} onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} autoFocus /></div><div className="field"><label>Password (min 8)</label><input className="input" type="password" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} /></div><div className="field"><label>Role</label><select className="select" value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></div></div>
      </Modal>}
      {newToken && <Modal title="Create machine token" onClose={() => setNewToken(null)} foot={<><button className="btn" onClick={() => setNewToken(null)}>Cancel</button><button className="btn primary" onClick={createToken} disabled={!newToken.name || !newToken.actor}>Create</button></>}>
        <div className="stack"><div className="field"><label>Name</label><input className="input" value={newToken.name} onChange={(e) => setNewToken({ ...newToken, name: e.target.value })} /></div><div className="field"><label>Actor (identity the client reports)</label><input className="input" value={newToken.actor} onChange={(e) => setNewToken({ ...newToken, actor: e.target.value })} /></div><div className="field"><label>Role</label><select className="select" value={newToken.role} onChange={(e) => setNewToken({ ...newToken, role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></div></div>
      </Modal>}
      {secret && <Modal title="Token created — copy it now" onClose={() => setSecret(null)} foot={<button className="btn primary" onClick={() => setSecret(null)}>Done</button>}>
        <div className="stack"><p className="small">This secret is shown once. Put it into the desktop's <code>JARVIS_GATEWAY_TOKEN</code> environment variable.</p><div className="row"><code className="input" style={{ height: "auto", padding: 10, wordBreak: "break-all" }}>{secret}</code><button className="btn icon" onClick={() => navigator.clipboard?.writeText(secret).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })} aria-label="Copy token">{copied ? <Check /> : <Copy />}</button></div></div>
      </Modal>}
    </div>
  );
}
