"use client";
// Minimal client-side router with the react-router-dom surface this app uses.
// The Next.js host is a single client-only catch-all route, so navigation is
// handled here on top of the History API; App.tsx does the module matching.
import {
  createContext, useCallback, useContext, useMemo, useSyncExternalStore,
  type AnchorHTMLAttributes, type CSSProperties, type MouseEvent, type ReactNode,
} from "react";

export interface Location { pathname: string; search: string; state: unknown; }
interface NavOpts { replace?: boolean; state?: unknown; }

const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function subscribe(cb: () => void) {
  listeners.add(cb);
  window.addEventListener("popstate", cb);
  return () => { listeners.delete(cb); window.removeEventListener("popstate", cb); };
}
const snapshot = () => window.location.pathname + window.location.search;
const serverSnapshot = () => "/";

function readState(): unknown {
  const s = window.history.state as { usr?: unknown } | null;
  return s && typeof s === "object" ? s.usr : undefined;
}

function go(to: string, opts: NavOpts = {}) {
  const url = new URL(to, window.location.href);
  const target = url.pathname + url.search + url.hash;
  const st = { usr: opts.state };
  if (opts.replace) window.history.replaceState(st, "", target);
  else window.history.pushState(st, "", target);
  emit();
}

export function useLocation(): Location {
  useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  return {
    pathname: typeof window === "undefined" ? "/" : window.location.pathname,
    search: typeof window === "undefined" ? "" : window.location.search,
    state: typeof window === "undefined" ? undefined : readState(),
  };
}

export function useNavigate() {
  return useCallback((to: string | number, opts?: NavOpts) => {
    if (typeof to === "number") window.history.go(to);
    else go(to, opts);
  }, []);
}

type Params = Record<string, string | undefined>;
const ParamsCtx = createContext<Params>({});
export function ParamsProvider({ params, children }: { params: Params; children: ReactNode }) {
  return <ParamsCtx.Provider value={params}>{children}</ParamsCtx.Provider>;
}
export const useParams = () => useContext(ParamsCtx);

export function useSearchParams(): [URLSearchParams, (next: Record<string, string> | URLSearchParams, opts?: { replace?: boolean }) => void] {
  const { search, pathname } = useLocation();
  const params = useMemo(() => new URLSearchParams(search), [search]);
  const set = useCallback((next: Record<string, string> | URLSearchParams, opts?: { replace?: boolean }) => {
    const qs = new URLSearchParams(next as Record<string, string>).toString();
    go(pathname + (qs ? `?${qs}` : ""), { replace: opts?.replace });
  }, [pathname]);
  return [params, set];
}

const OutletCtx = createContext<ReactNode>(null);
export function OutletProvider({ element, children }: { element: ReactNode; children: ReactNode }) {
  return <OutletCtx.Provider value={element}>{children}</OutletCtx.Provider>;
}
export const Outlet = () => <>{useContext(OutletCtx)}</>;

export function Navigate({ to, replace, state }: { to: string; replace?: boolean; state?: unknown }) {
  // Navigating during render would loop; schedule it after commit.
  useMemo(() => { queueMicrotask(() => go(to, { replace, state })); }, [to, replace, state]);
  return null;
}

function isModified(e: MouseEvent) { return e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0; }

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string; replace?: boolean; state?: unknown };

export function Link({ to, replace, state, onClick, target, ...rest }: LinkProps) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || isModified(e) || (target && target !== "_self")) return;
    e.preventDefault();
    go(to, { replace, state });
  };
  return <a {...rest} href={to} target={target} onClick={handle} />;
}

type NavLinkProps = Omit<LinkProps, "className" | "style"> & {
  end?: boolean;
  className?: string | ((s: { isActive: boolean }) => string);
  style?: CSSProperties;
};

export function NavLink({ to, end, className, ...rest }: NavLinkProps) {
  const { pathname } = useLocation();
  const base = to.split("?")[0];
  const isActive = end || base === "/" ? pathname === base : pathname === base || pathname.startsWith(base + "/");
  return <Link {...rest} to={to} className={typeof className === "function" ? className({ isActive }) : className} />;
}

/** Match `pattern` (e.g. "/chat/:id") against `pathname`; returns params or null. */
export function matchPath(pattern: string, pathname: string): Params | null {
  const p = pattern.split("/").filter(Boolean);
  const s = pathname.split("/").filter(Boolean);
  if (p.length !== s.length) return null;
  const out: Params = {};
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(":")) out[p[i].slice(1)] = decodeURIComponent(s[i]);
    else if (p[i] !== s[i]) return null;
  }
  return out;
}
