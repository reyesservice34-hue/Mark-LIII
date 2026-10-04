/**
 * Der Kern: ein Hologramm in Gehirnform im Aurora-Verlauf (Cyan → Violett → Rosa) — eine Wolke aus Lichtpunkten
 * mit feinen Längen- und Breitenlinien, Nervenimpulsen und zwei kreisenden Bahnen. Wenn MIA spricht, pulsiert alles im Rhythmus seiner
 * echten Stimme: die Live-Konsole meldet Zustand und Lautstärke. Ohne Meldung atmet es nur leise.
 * Bewegung ist Zierde: mit „reduzierte Bewegung" steht es still.
 */
import { useEffect, useRef } from "react";
import "./braincore.css";

type Mode = "idle" | "listening" | "thinking" | "speaking";
interface P { x: number; y: number; z: number; s: number; b: number; c: [number, number, number] }
interface Link { a: number; b: number }
interface Imp { l: number; t: number; d: number }

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
    const c2 = cv.getContext("2d"); if (!c2) return;
    const ctx: CanvasRenderingContext2D = c2;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const TAU = Math.PI * 2;
    let W = 0, H = 0, R = 100, cx = 0, cy = 0, raf = 0;
    let level = 0.12, flash = 0, prevLv = 0, lastBeat = 0, ang = 0, tt = 0, last = performance.now();
    const shock: number[] = [];
    const imps: Imp[] = [];

    // Gehirnform: Kugel, oben durch eine Furche geteilt, mit weichen Windungen.
    const shape = (u: number, v: number): [number, number, number] => {
      let x = Math.sin(v) * Math.cos(u), y = Math.cos(v), z = Math.sin(v) * Math.sin(u);
      const gy = 1 + 0.06 * Math.sin(9 * u + 3 * v) + 0.05 * Math.sin(6 * v - 4 * u) + 0.03 * Math.sin(15 * u + 5 * v);
      const fis = Math.exp(-Math.pow(x / 0.11, 2)) * Math.max(0, y) * 0.24;
      const k = gy * (1 - fis);
      x *= 1.2 * k; y *= 0.84 * k; z *= 0.98 * k;
      if (y < -0.3) { const s = 1 + (y + 0.3) * 0.3; x *= s; z *= s; }
      return [x, y, z];
    };

    // Aurora-Verlauf: Cyan → Violett → Rosa.
    const STOPS: [number, number, number][] = [[34, 211, 238], [139, 123, 255], [244, 114, 182]];
    const aurora = (t: number): [number, number, number] => {
      const u = Math.max(0, Math.min(1, t)) * 2, i = Math.min(1, Math.floor(u)), f = u - i, a = STOPS[i], b = STOPS[i + 1];
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    };
    const pts: P[] = [], links: Link[] = [];
    const build = () => {
      pts.length = 0; links.length = 0; imps.length = 0;
      const N = 3600, ga = Math.PI * (3 - Math.sqrt(5));
      for (let i = 0; i < N; i++) {
        const v = Math.acos(1 - (2 * (i + 0.5)) / N), u = i * ga;
        const [x, y, z] = shape(u, v);
        const j = 1 - Math.random() * 0.05;
        pts.push({ x: x * j, y: y * j, z: z * j, s: 0.5 + Math.random() * 1.1, b: Math.random(), c: aurora((x + 1.2) / 2.4 + (Math.random() - 0.5) * 0.25) });
      }
      for (let i = 0; i < 260; i++) {
        const a = Math.floor(Math.random() * N); let best = -1, bd = 9;
        for (let k = 0; k < 24; k++) {
          const b = Math.floor(Math.random() * N); if (b === a) continue;
          const d = Math.hypot(pts[a].x - pts[b].x, pts[a].y - pts[b].y, pts[a].z - pts[b].z);
          if (d > 0.12 && d < 0.5 && d < bd) { bd = d; best = b; }
        }
        if (best >= 0) links.push({ a, b: best });
      }
    };
    build();

    const resize = () => {
      const r = cv.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      W = Math.max(200, r.width); H = Math.max(200, r.height);
      cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cx = W / 2; cy = H / 2 - 6; R = Math.min(W * 0.9, H) * 0.34;
      if (reduce) frame(performance.now());
    };

    const proj = (x: number, y: number, z: number, ca: number, sa: number, ct: number, st: number, sc: number) => {
      const x1 = x * ca + z * sa, z1 = -x * sa + z * ca;
      const y2 = y * ct - z1 * st, z2 = y * st + z1 * ct;
      const f = 3.2 / (3.2 - z2 * 0.9);
      return [cx + x1 * sc * f, cy + y2 * sc * f, z2, f];
    };

    function addImp(n: number) { for (let i = 0; i < n && links.length; i++) imps.push({ l: Math.floor(Math.random() * links.length), t: 0, d: Math.random() < 0.5 ? 1.3 : 1.9 }); }

    function frame(now: number) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now; tt += dt;
      const m = live.current, fresh = now - m.at < 700;
      const speaking = fresh && m.state === "speaking", listening = fresh && m.state === "listening";
      const busy = (fresh && m.state === "thinking") || think.current;
      let target = speaking ? Math.max(0.35, m.level) : listening ? 0.25 + m.level * 0.5 : busy ? 0.5 : 0.1 + 0.05 * Math.sin(tt * 1.2);
      if (reduce) target = 0.15;
      level += (target - level) * Math.min(1, dt * (target > level ? 16 : 5));
      if (speaking && m.level > 0.28 && prevLv <= 0.28 && now - lastBeat > 140) { flash = 1; lastBeat = now; shock.push(0); addImp(18); }
      prevLv = speaking ? m.level : 0;
      flash *= Math.exp(-dt * 6);
      if (!speaking && Math.random() < dt * (busy ? 9 : 1.6)) addImp(busy ? 3 : 1);
      ang += dt * (0.22 + level * 0.5 + (busy ? 0.25 : 0));
      const pulse = 1 + 0.05 * level + 0.05 * flash;
      const sc = R * pulse, ca = Math.cos(ang), sa = Math.sin(ang), tilt = 0.28 + 0.04 * Math.sin(tt * 0.4), ct = Math.cos(tilt), st = Math.sin(tilt);

      ctx.clearRect(0, 0, W, H);
      const g = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * (2.1 + level * 0.5));
      g.addColorStop(0, `rgba(139,123,255,${0.26 + 0.28 * level + 0.2 * flash})`); g.addColorStop(0.45, `rgba(34,140,238,${0.08 + 0.08 * level})`); g.addColorStop(1, "rgba(34,140,238,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

      ctx.globalCompositeOperation = "lighter";
      // Zwei schräge Bahnen mit Verlauf, auf denen Lichtpunkte kreisen.
      const orbit = (rx: number, ry: number, rot: number, alpha: number, speed: number, dots: number) => {
        ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot);
        const og = ctx.createLinearGradient(-rx, 0, rx, 0);
        og.addColorStop(0, `rgba(34,211,238,${alpha})`); og.addColorStop(0.5, `rgba(139,123,255,${alpha * 1.2})`); og.addColorStop(1, `rgba(244,114,182,${alpha})`);
        ctx.strokeStyle = og; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, TAU); ctx.stroke();
        for (let i = 0; i < dots; i++) {
          const a = tt * speed + (i / dots) * TAU, x = Math.cos(a) * rx, y = Math.sin(a) * ry;
          const [r, gg, b] = aurora((x / rx + 1) / 2);
          ctx.fillStyle = `rgba(${r | 0},${gg | 0},${b | 0},.95)`; ctx.shadowColor = `rgb(${r | 0},${gg | 0},${b | 0})`; ctx.shadowBlur = 12;
          ctx.beginPath(); ctx.arc(x, y, 2.8, 0, TAU); ctx.fill();
        }
        ctx.shadowBlur = 0; ctx.restore();
      };
      orbit(R * 1.62, R * 0.46, -0.32, 0.32 + 0.3 * level, 0.45, 2);
      orbit(R * 1.48, R * 0.6, 0.42, 0.2 + 0.25 * level, -0.32, 1);
      for (let i = shock.length - 1; i >= 0; i--) {
        shock[i] += dt * 1.1; const s = shock[i];
        if (s > 1) { shock.splice(i, 1); continue; }
        ctx.strokeStyle = `rgba(167,139,250,${0.55 * (1 - s)})`; ctx.lineWidth = 2 * (1 - s) + 0.5;
        ctx.beginPath(); ctx.arc(cx, cy, R * (1.0 + s * 0.9), 0, TAU); ctx.stroke();
      }

      ctx.lineWidth = 0.8;
      for (let k = 0; k < 14; k++) {
        const u = (k / 14) * TAU; ctx.beginPath();
        for (let j = 0; j <= 28; j++) { const [x, y, z] = shape(u, 0.05 + (j / 28) * (Math.PI - 0.1)); const q = proj(x, y, z, ca, sa, ct, st, sc); if (j) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); }
        ctx.strokeStyle = `rgba(139,123,255,${0.06 + 0.06 * level})`; ctx.stroke();
      }
      for (let k = 1; k < 9; k++) {
        const v = (k / 9) * Math.PI; ctx.beginPath();
        for (let j = 0; j <= 48; j++) { const [x, y, z] = shape((j / 48) * TAU, v); const q = proj(x, y, z, ca, sa, ct, st, sc); if (j) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); }
        ctx.strokeStyle = `rgba(34,211,238,${0.06 + 0.06 * level})`; ctx.stroke();
      }

      const lp = (i: number) => proj(pts[i].x, pts[i].y, pts[i].z, ca, sa, ct, st, sc);
      ctx.strokeStyle = `rgba(180,170,255,${0.08 + 0.1 * level})`;
      ctx.beginPath();
      for (const l of links) { const a = lp(l.a), b = lp(l.b); if (a[2] < -0.1 && b[2] < -0.1) continue; ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); }
      ctx.stroke();

      const scan = Math.sin(tt * 0.7) * 0.95;
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i], q = proj(p.x, p.y, p.z, ca, sa, ct, st, sc), depth = (q[2] + 1) / 2;
        const sw = Math.exp(-Math.pow((p.y - scan) / 0.09, 2));
        const tw = 0.55 + 0.45 * Math.sin(tt * (1 + p.b * 2) + p.b * 40);
        const a = (0.3 + 0.7 * depth) * tw * (0.75 + level * 0.7) + sw * 0.7;
        const sz = (0.6 + p.s * 0.75) * q[3] * (0.75 + depth * 0.6) + sw * 1.3 + flash * 0.5;
        const lift = 0.35 * depth;
        ctx.fillStyle = sw > 0.3 ? `rgba(236,233,255,${Math.min(1, a)})` : `rgba(${(p.c[0] + (255 - p.c[0]) * lift) | 0},${(p.c[1] + (255 - p.c[1]) * lift) | 0},${(p.c[2] + (255 - p.c[2]) * lift) | 0},${Math.min(1, a)})`;
        ctx.fillRect(q[0] - sz / 2, q[1] - sz / 2, sz, sz);
      }

      ctx.lineWidth = 1.8;
      for (let i = imps.length - 1; i >= 0; i--) {
        const im = imps[i]; im.t += dt * im.d * (0.9 + 1.4 * level);
        if (im.t >= 1) { imps.splice(i, 1); continue; }
        const l = links[im.l], a = lp(l.a), b = lp(l.b), x = a[0] + (b[0] - a[0]) * im.t, y = a[1] + (b[1] - a[1]) * im.t;
        const t0 = Math.max(0, im.t - 0.2), x0 = a[0] + (b[0] - a[0]) * t0, y0 = a[1] + (b[1] - a[1]) * t0;
        ctx.strokeStyle = "rgba(216,206,255,.9)"; ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = "#f5f3ff"; ctx.beginPath(); ctx.arc(x, y, 2.2, 0, TAU); ctx.fill();
      }
      const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * (0.35 + 0.25 * level + 0.2 * flash));
      cg.addColorStop(0, `rgba(230,225,255,${0.32 + 0.4 * level})`); cg.addColorStop(0.5, `rgba(139,123,255,${0.12 + 0.2 * level})`); cg.addColorStop(1, "rgba(139,123,255,0)");
      ctx.fillStyle = cg; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);

      for (let k = 0; k < 3; k++) {
        const y0 = H - 16 + k * 5, A = 1.5 + 10 * level * (1 - k * 0.22);
        ctx.beginPath();
        for (let x = 0; x <= W; x += 3) {
          const u = x / W, env = Math.sin(Math.PI * u), s = tt * (1.6 + k * 0.5);
          const y = y0 + env * A * (Math.sin(x * 0.034 - s * 3) + 0.55 * Math.sin(x * 0.087 + s * 4.6 + k) + 0.25 * Math.sin(x * 0.19 - s * 9));
          if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        const wg = ctx.createLinearGradient(0, 0, W, 0);
        wg.addColorStop(0, "rgba(34,211,238,0)"); wg.addColorStop(0.3, `rgba(34,211,238,${0.6 - k * 0.15})`);
        wg.addColorStop(0.6, `rgba(139,123,255,${0.6 - k * 0.15})`); wg.addColorStop(1, "rgba(244,114,182,0)");
        ctx.strokeStyle = wg; ctx.lineWidth = 1.4 - k * 0.2; ctx.stroke();
      }
      ctx.globalCompositeOperation = "source-over";
      if (!reduce) raf = requestAnimationFrame(frame);
    }

    const ro = new ResizeObserver(resize); ro.observe(cv); resize();
    if (reduce) frame(performance.now()); else raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  return <canvas ref={ref} className="brain-core" aria-label="MIA Kern" role="img" />;
}
