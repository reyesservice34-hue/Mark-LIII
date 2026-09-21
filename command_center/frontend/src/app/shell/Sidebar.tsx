import { NavLink } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { Icon, ChevronLeft, ChevronRight, LogOut, Command } from "@/lib/icons";
import { paletteOpen } from "@/app/commands/commands";
import { useConnection } from "@/lib/events";
import { useApi } from "@/lib/useApi";
import "./shell.css";

interface StatusForBadges { tasks: Record<string, number>; approvals_pending: number }

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { modules, user, logout, version } = useAuth();
  const conn = useConnection();
  const nav = [{ id: "home", title: "JARVIS Home", icon: "home", path: "/", description: "Command center" }, ...modules];
  // Damit man von der Seitenleiste aus sieht, wo gerade etwas los ist, ohne
  // erst hineinzuklicken: Aufgaben, die laufen oder warten, und Freigaben,
  // die eine Entscheidung brauchen — letzteres in Warnfarbe, weil dort
  // wirklich jemand hin muss, nicht nur informativ ist.
  const status = useApi<StatusForBadges>("/api/status", { refreshOn: ["task.*", "approval.*"], interval: 30000 });
  const t = status.data?.tasks;
  const badges: Record<string, { count: number; warn?: boolean }> = {};
  if (t) {
    const active = (t.RUNNING || 0) + (t.PLANNING || 0) + (t.QUEUED || 0);
    if (active > 0) badges.tasks = { count: active };
  }
  if (status.data?.approvals_pending) badges.approvals = { count: status.data.approvals_pending, warn: true };
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} aria-label="Primary navigation">
      <div className="brand">
        <span className="core" aria-hidden><span className={`ring ${conn.state}`} /><span className="nucleus" /></span>
        {!collapsed && <span className="brand-text"><strong>JARVIS</strong><span className="label">Command Center</span></span>}
      </div>
      <nav className="nav">
        {nav.map((m) => {
          const badge = badges[m.id];
          return (
            <NavLink key={m.id} to={m.path} end={m.path === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
              title={collapsed ? (badge ? `${m.title} (${badge.count})` : m.title) : undefined}>
              <Icon name={m.icon} size={17} />
              {!collapsed && <span className="truncate grow">{m.title}</span>}
              {badge && <span className={`nav-badge ${badge.warn ? "warn" : ""}`}>{badge.count}</span>}
            </NavLink>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <button className="nav-item" onClick={() => paletteOpen.set(true)} title="Command palette (Ctrl/⌘ K)">
          <Command size={17} />{!collapsed && <span className="row between grow"><span>Commands</span><kbd>⌘K</kbd></span>}
        </button>
        <button className="nav-item" onClick={logout} title="Sign out">
          <LogOut size={17} />{!collapsed && <span className="truncate">{user?.name} · sign out</span>}
        </button>
        <button className="nav-item collapse" onClick={onToggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}{!collapsed && <span className="tiny muted">v{version}</span>}
        </button>
      </div>
    </aside>
  );
}
