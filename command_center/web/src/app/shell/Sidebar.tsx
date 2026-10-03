import { NavLink } from "@/lib/router";
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
  const nav = [{ id: "home", title: "MIA", icon: "home", path: "/", description: "Kommandozentrale" }, ...modules];
  // Leitstand-Gliederung: wer arbeitet (Betrieb), wer denkt (Intelligenz), worauf alles läuft (Systeme).
  // Die Reihenfolge innerhalb einer Gruppe bleibt die vom Backend gelieferte; unbekannte Module landen unter „Weitere“.
  const GROUPS: { label: string; ids: string[] }[] = [
    { label: "Betrieb", ids: ["home", "tasks", "agents", "agents_office", "workflows", "automations", "approvals", "calendar"] },
    { label: "Intelligenz", ids: ["chat", "memory", "mia_core", "training", "teach", "analytics"] },
    { label: "Systeme", ids: ["server", "desktop", "files", "integrations", "extensions", "logs", "notifications", "settings"] },
  ];
  const known = new Set(GROUPS.flatMap((g) => g.ids));
  const groups = [
    ...GROUPS.map((g) => ({ label: g.label, items: nav.filter((m) => g.ids.includes(m.id)) })),
    { label: "Weitere", items: nav.filter((m) => !known.has(m.id)) },
  ].filter((g) => g.items.length > 0);
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
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} aria-label="Hauptnavigation">
      <div className="brand">
        <span className="core" aria-hidden><span className={`ring ${conn.state}`} /><span className="nucleus" /></span>
        {!collapsed && <span className="brand-text"><strong>MIA</strong><span className="label">Command Center</span></span>}
      </div>
      <nav className="nav">
        {groups.map((g) => (
          <div key={g.label} style={{ display: "contents" }}>
            <div className="nav-group" aria-hidden><span>{g.label}</span></div>
            {g.items.map((m) => {
              const badge = badges[m.id];
              return (
                <NavLink key={m.id} to={m.path} end={m.path === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
                  title={collapsed ? (badge ? `${m.title} (${badge.count})` : m.title) : undefined}>
                  <Icon name={m.icon} size={16} />
                  {!collapsed && <span className="truncate grow">{m.title}</span>}
                  {badge && <span className={`nav-badge ${badge.warn ? "warn" : ""}`}>{badge.count}</span>}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        <button className="nav-item" onClick={() => paletteOpen.set(true)} title="Befehle (Strg/⌘ K)">
          <Command size={16} />{!collapsed && <span className="row between grow"><span>Befehle</span><kbd>⌘K</kbd></span>}
        </button>
        <button className="nav-item" onClick={logout} title="Abmelden">
          <LogOut size={16} />{!collapsed && <span className="truncate">{user?.name} · abmelden</span>}
        </button>
        <button className="nav-item collapse" onClick={onToggle} aria-label={collapsed ? "Seitenleiste ausklappen" : "Seitenleiste einklappen"}>
          {collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}{!collapsed && <span className="tiny muted">v{version}</span>}
        </button>
      </div>
    </aside>
  );
}
