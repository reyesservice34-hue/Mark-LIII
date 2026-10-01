import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useStore } from "@/lib/store";
import { Command as CommandIcon, Search } from "@/lib/icons";
import { commandStore, paletteOpen, type Command } from "./commands";

function score(cmd: Command, q: string): number {
  if (!q) return 1;
  const hay = `${cmd.title} ${cmd.hint || ""} ${cmd.keywords || ""} ${cmd.group || ""}`.toLowerCase();
  const needle = q.toLowerCase();
  if (hay.includes(needle)) return 2 + (cmd.title.toLowerCase().startsWith(needle) ? 2 : 0);
  // subsequence match
  let i = 0;
  for (const ch of hay) if (ch === needle[i]) i++;
  return i === needle.length ? 1 : 0;
}

export function CommandPalette() {
  const open = useStore(paletteOpen);
  const commands = useStore(commandStore);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); paletteOpen.set((v) => !v); }
      if (e.key === "Escape" && paletteOpen.get()) paletteOpen.set(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => { if (open) { setQ(""); setIdx(0); setTimeout(() => input.current?.focus(), 10); } }, [open]);

  const results = useMemo(() => commands.map((c) => ({ c, s: score(c, q) })).filter((r) => r.s > 0)
    .sort((a, b) => b.s - a.s || a.c.title.localeCompare(b.c.title)).slice(0, 12).map((r) => r.c), [commands, q]);

  const run = (c: Command) => { paletteOpen.set(false); if (c.run) c.run(); else if (c.path) nav(c.path); };

  if (!open) return null;
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) paletteOpen.set(false); }}>
      <div className="modal" role="dialog" aria-label="Command palette" style={{ width: "min(560px, 100%)" }}>
        <div className="row" style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)" }}>
          <Search size={16} style={{ color: "var(--text-3)" }} />
          <input ref={input} className="input" style={{ border: 0, background: "transparent", height: 30, padding: 0, boxShadow: "none" }}
            placeholder="Type a command or search…" value={q} onChange={(e) => { setQ(e.target.value); setIdx(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(results.length - 1, i + 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
              if (e.key === "Enter" && results[idx]) run(results[idx]);
            }} aria-label="Command" />
          <kbd>esc</kbd>
        </div>
        <ul style={{ listStyle: "none", margin: 0, padding: 6, maxHeight: "50vh", overflow: "auto" }} role="listbox">
          {results.length === 0 && <li className="empty" style={{ padding: 20 }}>No matching command</li>}
          {results.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === idx} onMouseEnter={() => setIdx(i)} onClick={() => run(c)}
              className="row between" style={{ padding: "9px 10px", borderRadius: 8, cursor: "pointer", background: i === idx ? "var(--accent-soft)" : "transparent" }}>
              <span className="row"><CommandIcon size={14} style={{ color: "var(--text-3)" }} /><span>{c.title}</span>{c.hint && <span className="small muted">{c.hint}</span>}</span>
              <span className="row" style={{ gap: 6 }}>{c.group && <span className="tiny muted">{c.group}</span>}{c.shortcut && <kbd>{c.shortcut}</kbd>}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
