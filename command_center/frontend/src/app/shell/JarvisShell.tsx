import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { useConnection, useEvent, events } from "@/lib/events";
import { Icon, Home } from "@/lib/icons";
import { toast } from "@/lib/toast";
import { Skeleton, Toaster } from "@/components/ui";
import { CommandPalette } from "@/app/commands/CommandPalette";
import { registerCommands } from "@/app/commands/commands";
import { ApprovalDialog } from "@/modules/approvals/ApprovalDialog";
import { Sidebar } from "./Sidebar";
import { TopStatusBar } from "./TopStatusBar";

export function JarvisShell() {
  const { modules, logout } = useAuth();
  const conn = useConnection();
  const nav = useNavigate();
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("jcc.sidebar") === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("jcc.sidebar", collapsed ? "1" : "0"); } catch { /* ignore */ } }, [collapsed]);

  // Commands from backend modules + shell-level ones.
  useEffect(() => registerCommands([
    { id: "home", title: "JARVIS Home", path: "/", group: "Navigate", shortcut: "g h" },
    ...modules.flatMap((m) => [
      { id: `nav.${m.id}`, title: `Open ${m.title}`, path: m.path, group: "Navigate", keywords: m.description },
      ...m.commands.map((c) => ({ id: c.id, title: c.title, path: c.path, shortcut: c.shortcut, group: m.title })),
    ]),
    { id: "logout", title: "Sign out", group: "Session", run: () => logout() },
    { id: "reconnect", title: "Reconnect live stream", group: "Session", run: () => events.reconnectNow() },
  ]), [modules, logout]);

  // "g x" style shortcuts.
  useEffect(() => {
    let pending = "";
    let timer = 0;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (pending === "g") {
        const map: Record<string, string> = { h: "/", c: "/chat", a: "/agents", t: "/tasks", w: "/workflows", s: "/server", f: "/files", l: "/logs", n: "/notifications" };
        if (map[e.key]) { e.preventDefault(); nav(map[e.key]); }
        pending = "";
        return;
      }
      if (e.key === "g") { pending = "g"; window.clearTimeout(timer); timer = window.setTimeout(() => { pending = ""; }, 900); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nav]);

  useEvent("notification.created", (ev) => {
    const n = ev.data;
    toast({ title: n.title, body: n.body, tone: n.severity === "error" || n.severity === "critical" ? "err" : n.severity === "warning" ? "warn" : n.severity === "success" ? "ok" : "info" });
  });

  const mobile = [{ id: "home", title: "Home", icon: "home", path: "/", mobile_priority: 1000 },
    ...modules.filter((m) => m.mobile_priority > 0)].sort((a, b) => b.mobile_priority - a.mobile_priority).slice(0, 5);

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""}`}>
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
      <TopStatusBar />
      <main className="main" id="main">
        {conn.state !== "online" && conn.state !== "connecting" && (
          <div className={`conn-banner ${conn.state === "offline" ? "err" : ""}`} role="status">
            {conn.state === "offline" ? "Live connection lost — data may be stale." : "Reconnecting to the live stream…"}
            <button className="btn sm" onClick={() => events.reconnectNow()}>Reconnect now</button>
          </div>
        )}
        <Suspense fallback={<div className="page"><Skeleton rows={6} height={18} /></div>}>
          <Outlet />
        </Suspense>
      </main>
      <nav className="mobile-nav mobile-only" aria-label="Mobile navigation">
        {mobile.map((m) => <NavLink key={m.id} to={m.path} end={m.path === "/"} className={({ isActive }) => isActive ? "active" : ""}>{m.id === "home" ? <Home size={18} /> : <Icon name={(m as any).icon} size={18} />}<span>{m.title}</span></NavLink>)}
      </nav>
      <CommandPalette />
      <ApprovalDialog />
      <Toaster />
    </div>
  );
}
