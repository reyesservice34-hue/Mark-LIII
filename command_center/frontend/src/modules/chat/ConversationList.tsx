import { useState } from "react";
import { Link } from "react-router-dom";
import { relative } from "@/lib/format";
import { Plus, Search, Trash2, Pencil } from "@/lib/icons";
import { EmptyState } from "@/components/ui";
import type { Conversation } from "./types";

export function ConversationList({ items, activeId, onNew, onSearch, onRename, onDelete, query }: {
  items: Conversation[]; activeId?: string; onNew: () => void; onSearch: (q: string) => void; query: string;
  onRename: (c: Conversation) => void; onDelete: (c: Conversation) => void;
}) {
  const [menu, setMenu] = useState<string | null>(null);
  return (
    <div className="panel chat-col" style={{ height: "100%" }}>
      <div className="panel-head" style={{ padding: "10px 12px" }}>
        <div className="row grow" style={{ gap: 6 }}>
          <Search size={14} style={{ color: "var(--text-3)", flex: "none" }} />
          <input className="input" style={{ height: 28, border: 0, background: "transparent", padding: 0, boxShadow: "none" }} placeholder="Sitzungsverläufe durchsuchen" value={query} onChange={(e) => onSearch(e.target.value)} aria-label="Sitzungsverläufe durchsuchen" />
        </div>
        <button className="btn icon sm primary" onClick={onNew} title="Neue Sitzung" aria-label="Neue Sitzung"><Plus /></button>
      </div>
      <div className="conv-list">
        {items.length === 0 && <EmptyState title={query ? "Keine Treffer" : "Keine Sitzungen"}>{query ? "Versuche einen anderen Suchbegriff." : "Starte eine Sitzung. MIA speichert den Verlauf auf dem Server."}</EmptyState>}
        {items.map((c) => (
          <Link key={c.id} to={`/chat/${c.id}`} className={`conv-item ${c.id === activeId ? "active" : ""}`} onMouseLeave={() => setMenu(null)}>
            <div className="row between"><span className="title truncate">{c.title === "New conversation" ? "Neue Sitzung" : c.title}</span>
              <span className="row" style={{ gap: 2 }} onClick={(e) => e.preventDefault()}>
                {menu === c.id ? <>
                  <button className="btn icon ghost sm" title="Umbenennen" onClick={() => onRename(c)}><Pencil /></button>
                  <button className="btn icon ghost sm" title="Löschen" onClick={() => onDelete(c)}><Trash2 /></button>
                </> : <button className="btn ghost sm" style={{ height: 22 }} onClick={() => setMenu(c.id)} aria-label="Weitere Aktionen">…</button>}
              </span>
            </div>
            <span className="preview truncate">{c.preview || "—"}</span>
            <span className="tiny muted">{relative(c.updated_at)} · {c.message_count ?? 0} Nachrichten</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
