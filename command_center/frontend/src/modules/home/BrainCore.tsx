/**
 * Der Kern: ein orangenes Gehirn aus Nervenzellen. Impulse laufen über die Verbindungen, und wenn JARVIS
 * spricht, pulsiert es im Rhythmus seiner echten Stimme — die Live-Konsole meldet Zustand und Lautstärke.
 * Ohne Meldung ruht es nur leise. Bewegung ist Zierde: mit „reduzierte Bewegung" steht es still.
 */
import { useEffect, useRef } from "react";
import "./braincore.css";

type Mode = "idle" | "listening" | "thinking" | "speaking";
interface Node { x: number; y: number; g: number; h: number; nb: number[] }
interface Edge { a: number; b: number; len: number }
interface Pulse { e: number; p: number; dir: number; v: number }

export function BrainCore({ thinking = false }: { thinking?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef<{ state: Mode; level: number; at: number }>({ state: "idle", level: 0, at: 0 });
  const think = useRef(thinking);
  think.current = thinking;

  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      const d = e.data;
      if (d && d.jarvis === "live") {
        const st = ["idle", "listening", "thinking", "speaking"].includes(d.state) ? d.state : "idle";
        live.current = { state: st, level: Math.max(0, Math.min(1, Number(d.level) || 0)), at: performance.now() };
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const ctx = cv.getContext("2d"); if (!ctx) return;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rand = (a: number, b: number) => a + Math.random() * (b - a);
    let W = 0, H = 0, sc = 1, cx = 0, cy = 0, raf = 0;
    let polys: number[][][] = [], nodes: Node[] = [], edges: Edge[] = [], sulci: { h: number; line: number[][] }[] = [];
    const pulses: Pulse[] = [], rings: { t: number }[] = [];
    let level = 0.15, flash = 0, lastBeat = 0, acc = 0, last = performance.now(), prevLv = 0;

    const hemi = (sign: number, seed: number) => {
      const pts: number[][] = [], N = 96;
      for (let i = 0; i < N; i++) {
        const th = (i / N) * Math.PI * 2;
        const p = 1 + 0.05 * Math.sin(4 * th + seed) + 0.032 * Math.sin(7 * th + seed * 1.7) + 0.02 * Math.sin(12 * th + seed * 0.6);
        let x = sign * 74 + 172 * p * Math.cos(th);
        const y = 198 * p * Math.sin(th) * (1 - 0.07 * Math.cos(th) * sign);
        x = sign < 0 ? Math.min(x, -5) : Math.max(x, 5);
        pts.push([cx + x * sc, cy + y * sc]);
      }
      return pts;
    };
    const inside = (poly: number[][], x: number, y: number) => {
      let c = false;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const [xi, yi] = poly[i], [xj, yj] = poly[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
      }
      return c;
    };
    const build = () => {
      sc = Math.min(W / 780, (H - 40) / 470); cx = W / 2; cy = (H - 40) * 0.5;
      polys = [hemi(-1, 1.3), hemi(1, 4.1)];
      nodes = []; edges = []; sulci = []; pulses.length = 0;
      const minD = 19 * sc;
      polys.forEach((poly, hi) => {
        poly.forEach((p, i) => { if (i % 3 === 0) nodes.push({ x: p[0], y: p[1], g: 0, h: hi, nb: [] }); });
        const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
        const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
        let tries = 0, added = 0;
        while (added < 110 && tries < 5000) {
          tries++;
          const x = rand(x0, x1), y = rand(y0, y1);
          if (!inside(poly, x, y) || nodes.some((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < minD * minD)) continue;
          nodes.push({ x, y, g: 0, h: hi, nb: [] }); added++;
        }
        for (let k = 0; k < 14; k++) {
          let x = 0, y = 0, tr = 0;
          do { x = rand(x0, x1); y = rand(y0, y1); tr++; } while (!inside(poly, x, y) && tr < 200);
          let a = rand(0, Math.PI * 2); const line = [[x, y]];
          for (let s = 0; s < 22; s++) { a += rand(-0.55, 0.55); x += Math.cos(a) * 7 * sc; y += Math.sin(a) * 7 * sc; if (!inside(poly, x, y)) break; line.push([x, y]); }
          if (line.length > 5) sulci.push({ h: hi, line });
        }
      });
      const seen = new Set<string>(), maxD = 58 * sc;
      nodes.forEach((n, i) => {
        nodes.map((m, j) => ({ j, d: Math.hypot(n.x - m.x, n.y - m.y) }))
          .filter((o) => o.j !== i && o.d < maxD && nodes[o.j].h === n.h).sort((a, b) => a.d - b.d).slice(0, 3)
          .forEach((o) => { const k = i < o.j ? `${i}-${o.j}` : `${o.j}-${i}`; if (!seen.has(k)) { seen.add(k); edges.push({ a: i, b: o.j, len: o.d }); } });
      });
      const L = nodes.map((n, i) => [n, i] as const).filter(([n]) => n.h === 0 && n.x > cx - 60 * sc);
      const R = nodes.map((n, i) => [n, i] as const).filter(([n]) => n.h === 1 && n.x < cx + 60 * sc);
      for (let k = 0; k < 14 && L.length && R.length; k++) {
        const A = L[Math.floor(Math.random() * L.length)], B = R[Math.floor(Math.random() * R.length)];
        edges.push({ a: A[1], b: B[1], len: Math.hypot(A[0].x - B[0].x, A[0].y - B[0].y) });
      }
      edges.forEach((e, i) => { nodes[e.a].nb.push(i); nodes[e.b].nb.push(i); });
    };
    const resize = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1), r = cv.getBoundingClientRect();
      W = r.width; H = r.height; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      build();
    };
    const spawn = (e: number, dir: number, v?: number) => { if (pulses.length < 300) pulses.push({ e, p: dir > 0 ? 0 : 1, dir, v: v || rand(150, 300) }); };
    const pos = (e: Edge, p: number) => { const a = nodes[e.a], b = nodes[e.b]; return [a.x + (b.x - a.x) * p, a.y + (b.y - a.y) * p]; };

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now; const t = now / 1000;
      const lv = live.current; const fresh = now - lv.at < 1500;              // ohne aktuelle Meldung: ruhig
      const state: Mode = fresh ? lv.state : think.current ? "thinking" : "idle";
      const amp = state === "speaking" ? lv.level : state === "listening" ? lv.level * 0.6 : 0;
      const target = state === "idle" ? 0.15 : state === "listening" ? 0.3 + 0.4 * amp : state === "thinking" ? 0.5 + 0.12 * Math.sin(t * 7) : 0.45 + 0.55 * amp;
      level += (target - level) * Math.min(1, dt * 8);

      // Schlag: steigt die Lautstärke über die Schwelle, feuern viele Nervenzellen auf einmal
      if (state === "speaking" && lv.level > 0.28 && prevLv <= 0.28 && now - lastBeat > 180) {
        lastBeat = now; flash = 1; rings.push({ t: 0 });
        for (let i = 0; i < 24 && edges.length; i++) spawn(Math.floor(Math.random() * edges.length), Math.random() < 0.5 ? 1 : -1, rand(220, 420));
      }
      prevLv = lv.level; flash *= Math.exp(-dt * 4.5);
      acc += dt * (4 + 68 * level);
      while (acc > 1 && edges.length) { acc -= 1; spawn(Math.floor(Math.random() * edges.length), Math.random() < 0.5 ? 1 : -1); }

      ctx.clearRect(0, 0, W, H);
      const R = 250 * sc, pulse = 1 + 0.02 * amp + 0.012 * flash + (state === "idle" ? 0.006 * Math.sin(t * 1.6) : 0);
      ctx.save(); ctx.translate(cx, cy);
      [[R * 1.3, 1, 0.1], [R * 1.46, -0.6, 0.06]].forEach(([r, sp, al], i) => {
        ctx.save(); ctx.rotate(t * 0.12 * sp); ctx.strokeStyle = `rgba(255,138,31,${al + 0.08 * level})`; ctx.lineWidth = 1; ctx.setLineDash(i ? [2, 10] : [70, 18, 4, 18]);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      });
      for (let i = rings.length - 1; i >= 0; i--) {
        const g = rings[i]; g.t += dt / 1.5; if (g.t >= 1) { rings.splice(i, 1); continue; }
        ctx.strokeStyle = `rgba(255,150,50,${(1 - g.t) * 0.4})`; ctx.lineWidth = 2 * (1 - g.t) + 0.5; ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(0, 0, R * (0.55 + 0.95 * g.t), 0, Math.PI * 2); ctx.stroke();
      }
      ctx.restore();

      ctx.save(); ctx.translate(cx, cy); ctx.scale(pulse, pulse); ctx.translate(-cx, -cy);
      polys.forEach((poly, hi) => {
        ctx.beginPath(); poly.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.closePath();
        const g = ctx.createRadialGradient(cx + (hi ? 60 : -60) * sc, cy, 10, cx + (hi ? 60 : -60) * sc, cy, 230 * sc);
        g.addColorStop(0, `rgba(255,120,20,${0.1 + 0.12 * level + 0.1 * flash})`); g.addColorStop(1, "rgba(255,120,20,.01)");
        ctx.fillStyle = g; ctx.fill();
        ctx.save(); ctx.shadowColor = "rgba(255,138,31,.9)"; ctx.shadowBlur = 14 + 26 * level + 20 * flash;
        ctx.strokeStyle = `rgba(255,150,50,${0.5 + 0.35 * level + 0.15 * flash})`; ctx.lineWidth = 1.6; ctx.stroke(); ctx.restore();
        ctx.save(); ctx.clip(); ctx.lineCap = "round";
        sulci.filter((s) => s.h === hi).forEach((s) => {
          ctx.beginPath(); s.line.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
          ctx.strokeStyle = `rgba(255,140,40,${0.14 + 0.1 * level})`; ctx.lineWidth = 1.2; ctx.stroke();
        });
        ctx.restore();
      });
      ctx.lineWidth = 1;
      edges.forEach((e) => { const a = nodes[e.a], b = nodes[e.b]; ctx.strokeStyle = `rgba(255,140,40,${0.1 + 0.1 * level})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); });
      ctx.save(); ctx.shadowColor = "rgba(255,170,70,1)"; ctx.shadowBlur = 10;
      for (let i = pulses.length - 1; i >= 0; i--) {
        const q = pulses[i], e = edges[q.e]; if (!e) { pulses.splice(i, 1); continue; }
        q.p += (q.dir * dt * q.v * (0.7 + 1.1 * level)) / Math.max(20, e.len);
        if (q.p >= 1 || q.p <= 0) {
          const end = q.dir > 0 ? e.b : e.a, n = nodes[end]; n.g = 1;
          if (Math.random() < 0.55 * level + 0.1) { const nb = n.nb.filter((k) => k !== q.e); if (nb.length) { const k = nb[Math.floor(Math.random() * nb.length)]; spawn(k, edges[k].a === end ? 1 : -1, q.v); } }
          pulses.splice(i, 1); continue;
        }
        const [x, y] = pos(e, q.p), [x2, y2] = pos(e, Math.min(1, Math.max(0, q.p - q.dir * 0.16)));
        ctx.strokeStyle = "rgba(255,205,130,.95)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = "#fff2dc"; ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill();
      }
      ctx.restore();
      nodes.forEach((n) => {
        n.g *= Math.exp(-dt * 3);
        if (n.g > 0.25) { ctx.save(); ctx.shadowColor = "rgba(255,160,60,1)"; ctx.shadowBlur = 14 * n.g; }
        ctx.fillStyle = `rgba(255,${(170 + 60 * n.g) | 0},${(80 + 90 * n.g) | 0},${0.4 + 0.6 * n.g})`;
        ctx.beginPath(); ctx.arc(n.x, n.y, 1.5 + n.g * 2.6 + flash * 0.8, 0, Math.PI * 2); ctx.fill();
        if (n.g > 0.25) ctx.restore();
      });
      ctx.restore();

      // Hirnströme unter dem Gehirn
      for (let k = 0; k < 3; k++) {
        const y0 = H - 30 + k * 9, A = 2 + 11 * level * (1 - k * 0.22);
        ctx.beginPath();
        for (let x = 0; x <= W; x += 3) {
          const u = x / W, env = Math.sin(Math.PI * u), s = t * (1.6 + k * 0.5);
          const y = y0 + env * A * (Math.sin(x * 0.034 - s * 3) + 0.55 * Math.sin(x * 0.087 + s * 4.6 + k) + 0.25 * Math.sin(x * 0.19 - s * 9));
          if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        ctx.strokeStyle = `rgba(255,150,50,${0.55 - k * 0.14})`; ctx.lineWidth = 1.3 - k * 0.2; ctx.stroke();
      }
      if (!reduce) raf = requestAnimationFrame(frame);
    };

    const ro = new ResizeObserver(resize); ro.observe(cv); resize();
    if (reduce) frame(performance.now()); else raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  return <canvas ref={ref} className="brain-core" aria-label="JARVIS Kern" role="img" />;
}
