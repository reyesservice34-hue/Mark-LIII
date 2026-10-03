import { Suspense, useMemo, type ComponentType, type ReactElement } from "react";
import { Navigate, OutletProvider, ParamsProvider, matchPath, useLocation } from "@/lib/router";
import { useAuth } from "@/lib/auth";
import { allModules } from "@/app/modules";
import { JarvisShell } from "@/app/shell/JarvisShell";
import LoginPage from "@/modules/login/LoginPage";
import { Skeleton } from "@/components/ui";

function Protected({ children }: { children: ReactElement }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div style={{ padding: 40 }}><Skeleton rows={5} height={18} /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return children;
}

const NotFound = () => (
  <div className="page"><div className="empty"><strong>Nicht gefunden</strong><span>Diese Adresse gehört zu keinem Modul.</span></div></div>
);

interface Entry { pattern: string; Comp: ComponentType }

export default function App() {
  const { modules } = useAuth();
  const { pathname } = useLocation();

  // Static patterns sort before ":param" ones so "/agents/office" wins over "/agents/:agentId".
  const entries = useMemo<Entry[]>(() => {
    const paths = new Map<string, string>(modules.map((m) => [m.id, m.path]));
    paths.set("home", "/");
    paths.set("approvals", "/approvals");
    const out: Entry[] = [];
    for (const m of allModules()) {
      const base = paths.get(m.id);
      if (!base) continue;
      const root = base === "/" ? "" : base;
      out.push({ pattern: base, Comp: m.component });
      for (const r of m.routes || []) out.push({ pattern: `${root}/${r}`, Comp: m.component });
    }
    return out.sort((a, b) => Number(a.pattern.includes(":")) - Number(b.pattern.includes(":")));
  }, [modules]);

  if (pathname === "/login") return <LoginPage />;

  let element: ReactElement = <NotFound />;
  let params: Record<string, string | undefined> = {};
  for (const { pattern, Comp } of entries) {
    const hit = matchPath(pattern, pathname);
    if (hit) { element = <Comp />; params = hit; break; }
  }

  return (
    <Protected>
      <OutletProvider element={<ParamsProvider params={params}><Suspense fallback={null}>{element}</Suspense></ParamsProvider>}>
        <JarvisShell />
      </OutletProvider>
    </Protected>
  );
}
