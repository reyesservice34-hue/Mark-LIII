import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import "./login.css";

export default function LoginPage() {
  const { user, login, loading } = useAuth();
  const loc = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to={(loc.state as any)?.from || "/"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    try { await login(username, password); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Login failed"); }
    finally { setBusy(false); }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit} aria-busy={busy || loading}>
        <div className="login-core" aria-hidden><span className="ring r1" /><span className="ring r2" /><span className="nucleus" /></div>
        <h1>JARVIS</h1>
        <p className="label" style={{ textAlign: "center" }}>Command Center · Authentication required</p>
        <div className="field"><label htmlFor="u">Username</label><input id="u" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></div>
        <div className="field"><label htmlFor="p">Password</label><input id="p" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
        {error && <div className="error-state" role="alert">{error}</div>}
        <button className="btn primary" type="submit" disabled={busy || !username || !password} style={{ height: 38 }}>{busy ? "Authenticating…" : "Enter"}</button>
        <p className="tiny muted" style={{ textAlign: "center" }}>Sessions are cookie-based and expire automatically. No credentials are stored in this browser.</p>
      </form>
    </div>
  );
}
