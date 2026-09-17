import { NavLink } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { Icon, ChevronLeft, ChevronRight, LogOut, Command } from "@/lib/icons";
import { paletteOpen } from "@/app/commands/commands";
import { useConnection } from "@/lib/events";
import "./shell.css";

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { modules, user, logout, version } = useAuth();
  const conn = useConnection();
  const nav = [{ id: "home", title: "JARVIS Home", icon: "home", path: "/", description: "Command center" }, ...modules];
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} aria-label="Primary navigation">
      <div className="brand">
        <span className="core" aria-hidden><span className={`ring ${conn.state}`} /><span className="nucleus" /></span>
        {!collapsed && <span className="brand-text"><strong>JARVIS</strong><span className="label">Command Center</span></span>}
      </div>
      <nav className="nav">
        {nav.map((m) => (
          <NavLink key={m.id} to={m.path} end={m.path === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title={collapsed ? m.title : undefined}>
            <Icon name={m.icon} size={17} />
            {!collapsed && <span className="truncate">{m.title}</span>}
          </NavLink>
        ))}
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
