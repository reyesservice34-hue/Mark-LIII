import { useEffect, type ReactNode } from "react";
import { tone } from "@/lib/format";
import { AlertTriangle, Box, X } from "@/lib/icons";
import { dismissToast, toasts } from "@/lib/toast";
import { useStore } from "@/lib/store";

export function Panel({ title, icon, actions, children, className = "", flush = false, foot }: {
  title?: ReactNode; icon?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
  flush?: boolean; foot?: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <header className="panel-head">
          <h2>{icon}{title}</h2>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={`panel-body ${flush ? "flush" : ""}`}>{children}</div>
      {foot && <footer className="panel-foot">{foot}</footer>}
    </section>
  );
}

export function Badge({ status, children, className = "" }: { status?: string; children?: ReactNode; className?: string }) {
  const t = tone(status);
  return <span className={`badge ${t} ${className}`}>{children ?? (status || "").replace(/_/g, " ")}</span>;
}

export function StatusIndicator({ status, label, live = false, size = 8 }: { status?: string; label?: ReactNode; live?: boolean; size?: number }) {
  const t = tone(status);
  return (
    <span className="row" style={{ gap: 7 }}>
      <span className={`dot ${t} ${live ? "live" : ""}`} style={{ width: size, height: size }} aria-hidden />
      {label !== undefined && <span className="label" style={{ color: "var(--text-2)" }}>{label}</span>}
    </span>
  );
}

export function Meter({ value, warnAt = 75, errAt = 90 }: { value?: number | null; warnAt?: number; errAt?: number }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  const cls = v >= errAt ? "err" : v >= warnAt ? "warn" : "";
  return <div className={`meter ${cls}`} role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${v}%` }} /></div>;
}

export function Stat({ label, value, unit, sub, meter, tone: t }: { label: ReactNode; value: ReactNode; unit?: string; sub?: ReactNode; meter?: number | null; tone?: string }) {
  return (
    <div className="panel stat">
      <span className="label">{label}</span>
      <span className="value" style={t ? { color: `var(--${t})` } : undefined}>{value}{unit && <small>{unit}</small>}</span>
      {meter !== undefined && <Meter value={meter} />}
      {sub && <span className="small muted">{sub}</span>}
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <Box size={28} />}
      <strong>{title}</strong>
      {children && <span className="small">{children}</span>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: { message?: string; status?: number } | string | null; retry?: () => void }) {
  if (!error) return null;
  const msg = typeof error === "string" ? error : error.message || "Something went wrong";
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={16} style={{ flex: "none", marginTop: 2 }} />
      <div className="grow">{msg}</div>
      {retry && <button className="btn sm" onClick={retry}>Retry</button>}
    </div>
  );
}

export function Skeleton({ rows = 3, height = 14 }: { rows?: number; height?: number }) {
  return <div className="stack" aria-busy>{Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton" style={{ height, width: `${90 - i * 12}%` }} />)}</div>;
}

export function Modal({ title, onClose, children, foot, wide = false }: { title: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" style={wide ? { width: "min(960px, 100%)" } : undefined}>
        <div className="modal-head"><h2>{title}</h2><button className="btn icon ghost sm" onClick={onClose} aria-label="Close"><X /></button></div>
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="toggle" onClick={() => onChange(!checked)} />;
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 16px", margin: 0 }}>
      {items.map(([k, v], i) => (<div key={i} style={{ display: "contents" }}><dt className="small muted">{k}</dt><dd style={{ margin: 0 }} className="small">{v}</dd></div>))}
    </dl>
  );
}

export function Toaster() {
  const list = useStore(toasts);
  return (
    <div className="toaster" aria-live="polite">
      {list.map((t) => (
        <div key={t.id} className={`toast ${t.tone || ""}`} onClick={() => dismissToast(t.id)} role="status">
          <div className="title">{t.title}</div>
          {t.body && <div className="dim small">{t.body}</div>}
        </div>
      ))}
    </div>
  );
}

export function Sparkline({ points, width = 120, height = 32, max = 100, color = "var(--accent)" }: { points: number[]; width?: number; height?: number; max?: number; color?: string }) {
  if (!points.length) return <svg width={width} height={height} aria-hidden />;
  const step = width / Math.max(points.length - 1, 1);
  const y = (v: number) => height - 2 - (Math.max(0, Math.min(max, v)) / max) * (height - 4);
  const d = points.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${d} L${((points.length - 1) * step).toFixed(1)},${height} L0,${height} Z`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden style={{ display: "block", width: "100%" }}>
      <path d={area} fill={color} opacity={0.12} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
