import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { events } from "./events";

interface Options { refreshOn?: string[]; interval?: number; enabled?: boolean; debounce?: number; }

/** Fetch JSON with loading/error state, optional live refresh on events. */
export function useApi<T = any>(path: string | null, opts: Options = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(!!path);
  const timer = useRef<number | null>(null);
  const alive = useRef(true);

  const reload = useCallback(async (silent = true) => {
    if (!path || opts.enabled === false) return;
    if (!silent) setLoading(true);
    try {
      const res = await api.get<T>(path);
      if (alive.current) { setData(res); setError(null); }
    } catch (e) {
      if (alive.current) setError(e as ApiError);
    } finally { if (alive.current) setLoading(false); }
  }, [path, opts.enabled]);

  useEffect(() => { alive.current = true; reload(false); return () => { alive.current = false; }; }, [reload]);

  useEffect(() => {
    if (!opts.refreshOn?.length) return;
    const offs = opts.refreshOn.map((t) => events.on(t, () => {
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => reload(true), opts.debounce ?? 250);
    }));
    return () => { offs.forEach((o) => o()); if (timer.current) window.clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, (opts.refreshOn || []).join("|")]);

  useEffect(() => {
    if (!opts.interval) return;
    const id = window.setInterval(() => reload(true), opts.interval);
    return () => window.clearInterval(id);
  }, [reload, opts.interval]);

  return { data, error, loading, reload, setData };
}
