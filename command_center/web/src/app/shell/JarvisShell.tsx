import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "@/lib/router";
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
import { LiveBar } from "./LiveBar";
import { announce, isLineOpen, listenEnabled, openLine, resumeLine } from "@/app/voice/liveStore";

export function JarvisShell() {
  const { modules, logout } = useAuth();
  const conn = useConnection();
  const nav = useNavigate();
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("jcc.sidebar") === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("jcc.sidebar", collapsed ? "1" : "0"); } catch { /* ignore */ } }, [collapsed]);

  // „Hey Mia“ überall: Nach einer echten Nutzergeste (Browser-Pflicht für Mikrofon und Ton) öffnet sich die
  // Sprachleitung von selbst. Der Server hält MIA im Standby und weckt sie beim Weckwort. Ist die Leitung zu
  // (beendet, Fehler, Netz), öffnet die nächste Geste sie wieder. Aus: localStorage „mia.listen“ = "0".
  useEffect(() => {
    let lastTry = 0;
    const listen = () => {
      resumeLine();
      if (!listenEnabled() || isLineOpen() || Date.now() - lastTry < 10000) return;
      lastTry = Date.now();
      void openLine().catch(() => { /* Mikrofon verweigert oder Server weg: nächste Geste versucht es neu */ });
    };
    // Mikrofon schon erlaubt: gleich beim Laden verbinden; den Ton gibt der Browser mit der ersten Geste frei.
    void navigator.permissions?.query({ name: "microphone" as PermissionName })
      .then((p) => { if (p.state === "granted") listen(); }).catch(() => undefined);
    window.addEventListener("pointerdown", listen);
    window.addEventListener("keydown", listen);
    return () => { window.removeEventListener("pointerdown", listen); window.removeEventListener("keydown", listen); };
  }, []);

  // Commands from backend modules + shell-level ones.
  useEffect(() => registerCommands([
    { id: "home", title: "MIA Start", path: "/", group: "Navigation", shortcut: "g h" },
    ...modules.flatMap((m) => [
      { id: `nav.${m.id}`, title: `${m.title} öffnen`, path: m.path, group: "Navigation", keywords: m.description },
      ...m.commands.map((c) => ({ id: c.id, title: c.title, path: c.path, shortcut: c.shortcut, group: m.title })),
    ]),
    { id: "logout", title: "Abmelden", group: "Sitzung", run: () => logout() },
    { id: "reconnect", title: "Live-Stream neu verbinden", group: "Sitzung", run: () => events.reconnectNow() },
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

  // MIA kann zeigen, wovon er redet: dashboard.open schickt einen Hinweis
  // über den Ereignisbus, und hier wird er zur Navigation. Mit einer Meldung
  // dazu — eine Seite, die von selbst wechselt, ohne dass jemand sagt warum,
  // ist gruselig, keine Hilfe.
  useEvent("ui.open", (ev) => {
    const path = String(ev.data?.path || "");
    if (!path.startsWith("/")) return;
    toast({ title: "MIA zeigt dir etwas", body: ev.data?.reason || path, tone: "info" });
    nav(path);
  });

  useEvent("notification.created", (ev) => {
    const n = ev.data;
    toast({ title: n.title, body: n.body, tone: n.severity === "error" || n.severity === "critical" ? "err" : n.severity === "warning" ? "warn" : n.severity === "success" ? "ok" : "info" });
    // Wichtiges sagt MIA von selbst: Sprachchat öffnet sich und sie spricht (Server entscheidet über speak).
    if (n.speak && n.id) void announce(String(n.id));
  });

  const mobile = [{ id: "home", title: "Home", icon: "home", path: "/", mobile_priority: 1000 },
    ...modules.filter((m) => m.mobile_priority > 0)].sort((a, b) => b.mobile_priority - a.mobile_priority).slice(0, 5);

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""}`}>
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
      <TopStatusBar />
      <main className="main" id="main">
        <LiveBar />
        {conn.state !== "online" && conn.state !== "connecting" && (
          <div className={`conn-banner ${conn.state === "offline" ? "err" : ""}`} role="status">
            {conn.state === "offline" ? "Live-Verbindung unterbrochen — Daten sind möglicherweise veraltet." : "Verbindung zum Live-Stream wird neu aufgebaut …"}
            <button className="btn sm" onClick={() => events.reconnectNow()}>Jetzt verbinden</button>
          </div>
        )}
        <Suspense fallback={<div className="page"><Skeleton rows={6} height={18} /></div>}>
          <Outlet />
        </Suspense>
      </main>
      <nav className="mobile-nav mobile-only" aria-label="Mobile Navigation">
        {mobile.map((m) => <NavLink key={m.id} to={m.path} end={m.path === "/"} className={({ isActive }) => isActive ? "active" : ""}>{m.id === "home" ? <Home size={18} /> : <Icon name={(m as any).icon} size={18} />}<span>{m.title}</span></NavLink>)}
      </nav>
      <CommandPalette />
      <ApprovalDialog />
      <Toaster />
    </div>
  );
}
