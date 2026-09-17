import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import { events } from "./events";

export interface User { kind: string; id: string; name: string; role: "viewer" | "operator" | "admin"; actor: string; }
export interface NavModule {
  id: string; title: string; icon: string; path: string; order: number; mobile_priority: number;
  min_role: string; description: string; commands: { id: string; title: string; path: string; shortcut?: string }[];
}

interface AuthState {
  user: User | null; modules: NavModule[]; version: string; loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (role: "viewer" | "operator" | "admin") => boolean;
}

const RANK = { viewer: 1, operator: 2, admin: 3 } as const;
const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [modules, setModules] = useState<NavModule[]>([]);
  const [version, setVersion] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    // No CSRF cookie means no session cookie either: skip the /me round-trip
    // (and the 401 it would log in the console) and go straight to login.
    if (!document.cookie.includes("jcc_csrf=")) { setUser(null); setLoading(false); return; }
    try {
      const me = await api.get<{ user: User; modules: NavModule[]; version: string }>("/api/auth/me");
      setUser(me.user); setModules(me.modules); setVersion(me.version);
      events.connect();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { setUser(null); setModules([]); }
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const onUnauthorized = () => { setUser(null); events.disconnect(); };
    window.addEventListener("jarvis:unauthorized", onUnauthorized);
    return () => window.removeEventListener("jarvis:unauthorized", onUnauthorized);
  }, []);

  const value = useMemo<AuthState>(() => ({
    user, modules, version, loading,
    login: async (username, password) => { await api.post("/api/auth/login", { username, password }); await load(); },
    logout: async () => { try { await api.post("/api/auth/logout"); } finally { setUser(null); events.disconnect(); } },
    can: (role) => !!user && RANK[user.role] >= RANK[role],
  }), [user, modules, version, loading, load]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
