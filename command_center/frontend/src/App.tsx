import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { allModules } from "@/app/modules";
import { JarvisShell } from "@/app/shell/JarvisShell";
import LoginPage from "@/modules/login/LoginPage";
import { Skeleton } from "@/components/ui";

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div style={{ padding: 40 }}><Skeleton rows={5} height={18} /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return children;
}

export default function App() {
  const { modules } = useAuth();
  const paths = new Map(modules.map((m) => [m.id, m.path]));
  paths.set("home", "/");
  paths.set("approvals", "/approvals");
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<Protected><JarvisShell /></Protected>}>
        {allModules().map((m) => {
          const base = paths.get(m.id);
          if (!base) return null;
          const Comp = m.component;
          const rel = base === "/" ? "" : base.replace(/^\//, "");
          return [
            <Route key={m.id} index={base === "/"} path={base === "/" ? undefined : rel} element={<Comp />} />,
            ...(m.routes || []).map((r) => <Route key={`${m.id}/${r}`} path={`${rel}/${r}`} element={<Comp />} />),
          ];
        })}
        <Route path="*" element={<div className="page"><div className="empty"><strong>Not found</strong><span>This route has no module.</span></div></div>} />
      </Route>
    </Routes>
  );
}
