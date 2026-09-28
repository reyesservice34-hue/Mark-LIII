// dashboard/static/mia-core.js — Mia's central "core": the ONE orange thing in the UI.
//
// Two parts:
//   MiaState  — the single frontend state model. Real backend/browser signals switch
//               named signals on and off; the model resolves them to ONE state by priority.
//   MiaCore   — a procedural neural-brain renderer (Canvas 2D, no dependencies) that
//               consumes MiaState. The brain is ALWAYS orange. State only changes
//               intensity: glow, energy-flow speed, neural activity, particle activity,
//               ring motion — never the hue.
//
// Performance: static layers are pre-rendered once per size; each frame draws ~2 bitmaps
// plus a few dozen additive sprites. Standby runs at ~30 fps, nothing runs while the tab is
// hidden or the canvas is off-screen, and prefers-reduced-motion (or the manual setting)
// renders single still frames that only update when the state changes.
(function (global) {
  'use strict';

  // ── State model ──────────────────────────────────────────────────────────────
  const LABELS = {
    standby: 'Bereit', listening: 'Hört zu', thinking: 'Denkt nach', speaking: 'Spricht',
    warning: 'Warnung', processing_file: 'Verarbeitet Datei', syncing: 'Synchronisiert',
    device_connected: 'Gerät verbunden', pairing: 'Kopplung',
  };
  // Highest priority first. `standby` is the fallback when no signal is active.
  const PRIORITY = ['warning', 'speaking', 'processing_file', 'thinking', 'listening',
                    'syncing', 'device_connected', 'pairing', 'standby'];

  const MiaState = (function () {
    const active = new Map();        // signal -> expiry timestamp (Infinity = until cleared)
    const subs = new Set();
    let level = 0;                   // 0..1, real audio activity (mic or Mia's speech output)
    let last = 'standby', timer = 0;

    function current() {
      const now = Date.now();
      for (const s of PRIORITY) {
        if (s === 'standby') return s;
        const exp = active.get(s);
        if (exp !== undefined && exp > now) return s;
        if (exp !== undefined) active.delete(s);
      }
      return 'standby';
    }
    function notify() {
      clearTimeout(timer);
      const now = Date.now();
      const next = Math.min(...[...active.values()].filter(e => e !== Infinity && e > now), Infinity);
      if (next !== Infinity) timer = setTimeout(notify, Math.max(20, next - now + 5));
      const cur = current();
      if (cur !== last) {
        const prev = last; last = cur;
        subs.forEach(fn => { try { fn(cur, prev); } catch (e) { console.warn('[mia-state]', e); } });
      }
    }
    return {
      LABELS, PRIORITY,
      // set(signal, true)        sticky until set(signal, false)
      // set(signal, true, 1500)  auto-clears after 1500 ms
      set(signal, on = true, ttlMs) {
        if (!PRIORITY.includes(signal)) { console.warn('[mia-state] unknown signal', signal); return; }
        if (on) active.set(signal, ttlMs ? Date.now() + ttlMs : Infinity);
        else active.delete(signal);
        notify();
      },
      current, subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
      label(state) { return LABELS[state || current()] || LABELS.standby; },
      setLevel(v) { level = Math.max(level, Math.min(1, v || 0)); },   // renderer decays it
      takeLevel() { return level; },
      decayLevel(k) { level *= k; if (level < 0.002) level = 0; },
    };
  })();

  // ── Motion preference (system setting + manual override) ──────────────────────
  const mq = global.matchMedia ? global.matchMedia('(prefers-reduced-motion: reduce)') : null;
  function motionSetting() { try { return localStorage.getItem('mia_motion') || 'auto'; } catch { return 'auto'; } }
  function reduced() { const m = motionSetting(); return m === 'reduced' || (m === 'auto' && !!(mq && mq.matches)); }

  // ── Visual profile per state: intensity only, never hue ───────────────────────
  const PROFILE = {
    standby:          { energy: .34, flow: .22, breath: .10, ring: .015, sweep: 0, focus: 0, pulse: 0 },
    listening:        { energy: .54, flow: .55, breath: .30, ring: .05,  sweep: 0, focus: 1, pulse: 0 },
    thinking:         { energy: .74, flow: 1.55, breath: .45, ring: .16, sweep: 0, focus: 0, pulse: 0 },
    speaking:         { energy: .90, flow: 1.05, breath: .90, ring: .08, sweep: 0, focus: 0, pulse: 1 },
    warning:          { energy: 1.0, flow: 1.30, breath: 1.7, ring: .10, sweep: 0, focus: 0, pulse: 1 },
    processing_file:  { energy: .80, flow: 1.90, breath: .50, ring: .20, sweep: 0, focus: 0, pulse: 0 },
    syncing:          { energy: .56, flow: .60, breath: .20, ring: .30, sweep: 1, focus: 0, pulse: 0 },
    device_connected: { energy: .72, flow: .80, breath: .50, ring: .06, sweep: 0, focus: 0, pulse: 0 },
    pairing:          { energy: .46, flow: .50, breath: .25, ring: .10, sweep: 1, focus: 0, pulse: 0 },
  };

  // Warm orange palette (RGB triplets) — the only saturated colour in the interface.
  const C = { deep: '255,90,0', mid: '255,122,24', hot: '255,157,66', warm: '255,208,163' };

  // ── Geometry ─────────────────────────────────────────────────────────────────
  // Lateral brain silhouette in a 100 × 80 unit box, frontal lobe left. Catmull-Rom closed.
  const OUTLINE = [
    [4, 42], [6, 30], [13, 18], [25, 9], [41, 5], [58, 5], [73, 10], [86, 20], [93, 32],
    [94, 43], [90, 52], [84, 57], [87, 63], [84, 70], [77, 75], [68, 74], [62, 68],
    [59, 65], [58, 69], [55, 71], [52, 69], [50, 65], [45, 62], [39, 63], [32, 65],
    [25, 61], [18, 57], [11, 52], [6, 47],
  ];
  // Major fissures / sulci (cubic Béziers, unit coords).
  const FOLDS = [
    [[40, 51], [46, 44], [56, 41], [68, 38]],   // lateral (Sylvian) fissure
    [[53, 6], [51, 16], [52, 26], [49, 36]],    // central sulcus
    [[20, 24], [28, 20], [36, 22], [43, 17]],
    [[12, 36], [20, 33], [29, 34], [38, 30]],
    [[62, 8], [66, 17], [72, 22], [80, 22]],
    [[86, 30], [79, 33], [72, 32], [66, 27]],
    [[26, 46], [32, 42], [40, 43], [46, 39]],
    [[70, 46], [76, 43], [83, 44], [88, 41]],
    [[60, 56], [66, 52], [73, 52], [80, 49]],
    [[22, 14], [30, 12], [37, 10], [45, 11]],
    [[68, 60], [72, 63], [77, 63], [80, 60]],   // cerebellum folds
    [[66, 66], [71, 68], [76, 68], [79, 65]],
  ];

  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  function catmullPath(path, pts, mapX, mapY) {
    const n = pts.length;
    path.moveTo(mapX(pts[0][0]), mapY(pts[0][1]));
    for (let i = 0; i < n; i++) {
      const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
      path.bezierCurveTo(
        mapX(p1[0] + (p2[0] - p0[0]) / 6), mapY(p1[1] + (p2[1] - p0[1]) / 6),
        mapX(p2[0] - (p3[0] - p1[0]) / 6), mapY(p2[1] - (p3[1] - p1[1]) / 6),
        mapX(p2[0]), mapY(p2[1]));
    }
    path.closePath();
  }

  // The neural graph is generated once (deterministic seed) in unit space.
  const GRAPH = (function build() {
    const rnd = mulberry32(20260921);
    const probe = document.createElement('canvas').getContext('2d');
    const unit = new Path2D();
    catmullPath(unit, OUTLINE, x => x, y => y);
    const nodes = [];
    const minD = 5.6;
    for (let i = 0; i < 6000 && nodes.length < 118; i++) {
      const x = 4 + rnd() * 91, y = 4 + rnd() * 76;
      if (!probe.isPointInPath(unit, x, y)) continue;
      if (nodes.some(n => (n.x - x) ** 2 + (n.y - y) ** 2 < minD * minD)) continue;
      nodes.push({ x, y, z: rnd(), ph: rnd() * 6.283, glow: 0, deg: 0, hub: false });
    }
    const edges = [], seen = new Set();
    const add = (a, b) => {
      const key = a < b ? a + '_' + b : b + '_' + a;
      if (seen.has(key)) return; seen.add(key);
      const A = nodes[a], B = nodes[b];
      const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
      const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
      const bend = (rnd() - 0.5) * len * 0.5;
      edges.push({ a, b, cx: mx - dy / len * bend, cy: my + dx / len * bend, len });
      A.deg++; B.deg++;
    };
    nodes.forEach((n, i) => {
      nodes.map((m, j) => ({ j, d: (m.x - n.x) ** 2 + (m.y - n.y) ** 2 }))
        .filter(o => o.j !== i && o.d < 15.5 * 15.5).sort((p, q) => p.d - q.d)
        .slice(0, 3).forEach(o => add(i, o.j));
    });
    // a few long-range "association fibres" between distant, well-connected nodes
    const byDeg = nodes.map((n, i) => i).sort((p, q) => nodes[q].deg - nodes[p].deg);
    byDeg.slice(0, 14).forEach(i => nodes[i].hub = true);
    for (let k = 0; k < 10; k++) {
      const a = byDeg[Math.floor(rnd() * 14)], b = byDeg[Math.floor(rnd() * 14)];
      if (a !== b && Math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y) > 26) add(a, b);
    }
    const incident = nodes.map(() => []);
    edges.forEach((e, i) => { incident[e.a].push(i); incident[e.b].push(i); });
    // procedural gyri: short wobbling strokes that echo the folded cortex
    const wobble = [];
    for (let i = 0; i < 26; i++) {
      const n = nodes[Math.floor(rnd() * nodes.length)];
      const ang = rnd() * 6.283, len = 8 + rnd() * 12;
      const pts = [];
      for (let s = 0; s <= 8; s++) {
        const t = s / 8;
        pts.push([n.x + Math.cos(ang) * len * t + Math.sin(t * 9 + i) * 1.6,
                  n.y + Math.sin(ang) * len * t + Math.cos(t * 7 + i) * 1.6]);
      }
      wobble.push(pts);
    }
    return { nodes, edges, incident, wobble };
  })();

  const sprite = (function () {                                  // additive glow dot
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const g = c.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, `rgba(${C.warm},1)`); r.addColorStop(.22, `rgba(${C.hot},.78)`);
    r.addColorStop(.58, `rgba(${C.deep},.20)`); r.addColorStop(1, `rgba(${C.deep},0)`);
    g.fillStyle = r; g.fillRect(0, 0, 64, 64);
    return c;
  })();

  // ── Renderer instance ─────────────────────────────────────────────────────────
  const instances = new Set();
  let raf = 0, lastNow = 0, lastDraw = 0;

  function mount(canvas, opts) {
    opts = opts || {};
    const mini = !!opts.mini;
    const ctx = canvas.getContext('2d');
    const inst = {
      canvas, mini, visible: true, params: Object.assign({}, PROFILE.standby),
      phase: 0, smoothLevel: 0, pulses: [], particles: [], stateSince: 0, lastState: 'standby',
      L: null, staticCanvas: null, glitch: 0, nextGlitch: 4, focusPhase: 0,
    };

    function layout() {
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 8 || rect.height < 8) { inst.L = null; return false; }   // hidden view: nothing to draw
      const dpr = Math.min(global.devicePixelRatio || 1, 2);
      const W = Math.max(1, Math.round(rect.width * dpr)), H = Math.max(1, Math.round(rect.height * dpr));
      if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
      const bw = mini ? W * 0.9 : Math.min(W * 0.8, (Math.min(W, H) / 2 - 4 * dpr) / 0.76);
      const s = bw / 100, cx = W / 2, cy = mini ? H * 0.5 : H * 0.5;
      const ox = cx - 50 * s, oy = cy - 40 * s;
      const L = { W, H, dpr, s, cx, cy, ox, oy, bw };
      const px = x => ox + x * s, py = y => oy + y * s;
      L.path = new Path2D(); catmullPath(L.path, OUTLINE, px, py);
      L.nodes = GRAPH.nodes.map(n => ({ x: px(n.x), y: py(n.y) }));
      L.edges = GRAPH.edges.map(e => ({ cx: px(e.cx), cy: py(e.cy) }));
      L.ticks = new Path2D();
      const rT = bw * 0.76;
      for (let i = 0; i < 90; i++) {
        const a = i / 90 * 6.2832, l = i % 5 === 0 ? 7 * dpr : 3.5 * dpr;
        L.ticks.moveTo(Math.cos(a) * rT, Math.sin(a) * rT);
        L.ticks.lineTo(Math.cos(a) * (rT + l), Math.sin(a) * (rT + l));
      }
      inst.L = L;
      inst.staticCanvas = renderStatic(L);
      inst.particles = [];
      const count = mini ? 0 : 42;
      const rnd = mulberry32(7);
      for (let i = 0; i < count; i++) {
        inst.particles.push({ a: rnd() * 6.283, r: bw * (0.55 + rnd() * 0.42), v: (0.02 + rnd() * 0.05) * (rnd() < .5 ? -1 : 1),
                              ph: rnd() * 6.283, sz: 0.6 + rnd() * 1.4, yy: 0.55 + rnd() * 0.45 });
      }
      return true;
    }

    function renderStatic(L) {
      const c = document.createElement('canvas'); c.width = L.W; c.height = L.H;
      const g = c.getContext('2d');
      const grad = g.createLinearGradient(0, L.oy, 0, L.oy + 80 * L.s);
      grad.addColorStop(0, `rgba(${C.hot},.15)`); grad.addColorStop(.6, `rgba(${C.mid},.07)`); grad.addColorStop(1, `rgba(${C.deep},.03)`);
      g.fillStyle = grad; g.fill(L.path);
      g.save(); g.clip(L.path);
      if (!mini) {
        g.lineWidth = Math.max(1, L.dpr * 0.9); g.lineCap = 'round';
        g.strokeStyle = `rgba(${C.mid},.36)`;
        FOLDS.forEach(f => {
          g.beginPath(); g.moveTo(L.ox + f[0][0] * L.s, L.oy + f[0][1] * L.s);
          g.bezierCurveTo(L.ox + f[1][0] * L.s, L.oy + f[1][1] * L.s, L.ox + f[2][0] * L.s, L.oy + f[2][1] * L.s, L.ox + f[3][0] * L.s, L.oy + f[3][1] * L.s);
          g.stroke();
        });
        g.strokeStyle = `rgba(${C.mid},.20)`;
        GRAPH.wobble.forEach(pts => {
          g.beginPath(); pts.forEach((p, i) => { const x = L.ox + p[0] * L.s, y = L.oy + p[1] * L.s; i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
        });
      }
      g.lineWidth = Math.max(0.7, L.dpr * (mini ? 0.6 : 0.8));
      g.strokeStyle = `rgba(${C.hot},${mini ? .30 : .20})`;
      GRAPH.edges.forEach((e, i) => {
        const a = L.nodes[GRAPH.edges[i].a], b = L.nodes[GRAPH.edges[i].b], q = L.edges[i];
        g.beginPath(); g.moveTo(a.x, a.y); g.quadraticCurveTo(q.cx, q.cy, b.x, b.y); g.stroke();
      });
      GRAPH.nodes.forEach((n, i) => {
        const p = L.nodes[i], r = L.s * (0.32 + n.z * 0.3) * (n.hub ? 1.6 : 1);
        g.fillStyle = `rgba(${C.warm},${0.28 + n.z * 0.3})`;
        g.beginPath(); g.arc(p.x, p.y, Math.max(0.6, r), 0, 6.2832); g.fill();
      });
      g.restore();
      g.lineWidth = Math.max(1, L.dpr * 1.1); g.strokeStyle = `rgba(${C.hot},.62)`; g.stroke(L.path);
      g.lineWidth = Math.max(1, L.dpr * 2.6); g.strokeStyle = `rgba(${C.deep},.14)`; g.stroke(L.path);
      return c;
    }

    function spawn(edge, dir) {
      if (inst.pulses.length > (mini ? 10 : 20 + Math.round(inst.params.flow * 42))) return;
      inst.pulses.push({ e: edge, t: dir > 0 ? 0 : 1, dir, v: 0.5 + Math.random() * 0.7 });
    }

    inst.frame = function (dt, time, state, still) {
      if (!inst.L && !layout()) return;
      const L = inst.L, P = inst.params, T = PROFILE[state] || PROFILE.standby;
      const k = still ? 1 : 1 - Math.exp(-dt * 2.6);
      for (const key of Object.keys(T)) P[key] += (T[key] - P[key]) * k;
      if (state !== inst.lastState) { inst.lastState = state; inst.stateSince = time; }
      inst.phase += dt * (0.09 + 0.55 * P.breath) * 6.2832;
      const breath = 0.5 + 0.5 * Math.sin(inst.phase);

      const lvIn = MiaState.takeLevel();
      inst.smoothLevel += (lvIn - inst.smoothLevel) * (lvIn > inst.smoothLevel ? 0.6 : 0.12);
      MiaState.decayLevel(0.9);
      let lvl = inst.smoothLevel;
      if (P.pulse > 0.5 && state === 'speaking') lvl = Math.max(lvl, 0.32 * (0.5 + 0.5 * Math.sin(time * 6.3)));  // rhythm when no PCM level is available
      if (still) lvl = state === 'speaking' ? 0.5 : 0;
      const glow = Math.min(1.25, P.energy * (0.68 + 0.32 * breath) + (P.pulse ? lvl * 0.55 : lvl * 0.25));

      // neural activity
      if (!still) {
        if (Math.random() < P.flow * (mini ? 2.5 : 7) * dt && GRAPH.edges.length) spawn(Math.floor(Math.random() * GRAPH.edges.length), Math.random() < .5 ? 1 : -1);
        const arrive = [];
        for (let i = inst.pulses.length - 1; i >= 0; i--) {
          const p = inst.pulses[i];
          p.t += p.dir * p.v * dt * (0.6 + P.flow * 0.9);
          if (p.t >= 1 || p.t <= 0) {
            const E = GRAPH.edges[p.e], n = p.dir > 0 ? E.b : E.a;
            GRAPH.nodes[n].glow = 1; arrive.push(n); inst.pulses.splice(i, 1);
          }
        }
        arrive.forEach(n => {                                       // branch onward
          const inc = GRAPH.incident[n];
          if (inc.length && Math.random() < 0.35 + 0.25 * P.flow) {
            const e = inc[Math.floor(Math.random() * inc.length)];
            spawn(e, GRAPH.edges[e].a === n ? 1 : -1);
          }
        });
        GRAPH.nodes.forEach(n => { n.glow *= Math.exp(-dt * 1.9); });
        inst.focusPhase += dt * (0.35 + lvl * 1.2);
      }

      // ── draw ──
      const g = ctx;
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.clearRect(0, 0, L.W, L.H);
      g.globalCompositeOperation = 'lighter';

      // halo
      const hr = L.bw * (mini ? 0.75 : 0.72);
      g.globalAlpha = Math.min(1, 0.10 + glow * 0.34);
      const hs = hr * (1.05 + glow * 0.16);
      g.drawImage(sprite, L.cx - hs, L.cy - hs * 0.92, hs * 2, hs * 1.84);

      if (!mini) drawRings(g, L, P, time, glow, still);

      // depth: faint back layer + front layer
      const drift = still ? 0 : Math.sin(time * 0.35) * L.s * 0.7;
      g.globalAlpha = (0.16 + glow * 0.14);
      g.drawImage(inst.staticCanvas, -drift * 1.4, drift * 0.5, L.W, L.H);
      const scaleP = 1 + (P.pulse ? lvl * 0.018 : 0) + (breath - 0.5) * 0.004 * P.energy;
      g.globalAlpha = Math.min(1, 0.50 + glow * 0.5);
      if (scaleP !== 1) { g.setTransform(scaleP, 0, 0, scaleP, L.cx * (1 - scaleP), L.cy * (1 - scaleP)); }
      g.drawImage(inst.staticCanvas, 0, 0);
      g.setTransform(1, 0, 0, 1, 0, 0);

      // energy pulses along fibres
      g.lineCap = 'round';
      inst.pulses.forEach(p => {
        const E = L.edges[p.e], a = L.nodes[GRAPH.edges[p.e].a], b = L.nodes[GRAPH.edges[p.e].b];
        const at = t => { const u = 1 - t; return [u * u * a.x + 2 * u * t * E.cx + t * t * b.x, u * u * a.y + 2 * u * t * E.cy + t * t * b.y]; };
        const t1 = p.t, t0 = Math.max(0, Math.min(1, p.t - p.dir * 0.22));
        const A = at(t0), B = at(t1);
        g.globalAlpha = Math.min(1, 0.3 + glow * 0.6);
        g.strokeStyle = `rgba(${C.hot},1)`; g.lineWidth = Math.max(1, L.dpr * (mini ? 0.9 : 1.4));
        g.beginPath(); g.moveTo(A[0], A[1]); g.lineTo(B[0], B[1]); g.stroke();
        const sz = L.s * (mini ? 2.6 : 3.2);
        g.globalAlpha = Math.min(1, 0.55 + glow * 0.4);
        g.drawImage(sprite, B[0] - sz, B[1] - sz, sz * 2, sz * 2);
      });

      // nodes
      const fx = L.cx + Math.cos(inst.focusPhase * 1.3) * L.bw * 0.30, fy = L.cy + Math.sin(inst.focusPhase * 0.9) * L.bw * 0.16;
      const fr2 = (L.bw * 0.22) ** 2;
      for (let i = 0; i < GRAPH.nodes.length; i++) {
        const n = GRAPH.nodes[i], p = L.nodes[i];
        let v = n.glow * 0.9 + P.energy * 0.16 * (0.6 + 0.4 * Math.sin(time * 1.2 + n.ph));
        if (P.focus > 0.05) {
          const d2 = (p.x - fx) ** 2 + (p.y - fy) ** 2;
          v += P.focus * Math.exp(-d2 / fr2) * (0.55 + lvl * 0.9);
        }
        if (P.pulse) v += lvl * 0.35;
        if (v < 0.08) continue;
        const r = L.s * (1.3 + Math.min(1.3, v) * 3.0) * (0.7 + n.z * 0.5) * (n.hub ? 1.35 : 1);
        g.globalAlpha = Math.min(1, v);
        g.drawImage(sprite, p.x - r, p.y - r, r * 2, r * 2);
      }

      if (!mini) {
        // holographic scan band, clipped to the silhouette
        g.save(); g.clip(L.path);
        const sy = L.oy + (((time * 0.11) % 1.25) - 0.12) * 80 * L.s;
        const sg = g.createLinearGradient(0, sy - 6 * L.s, 0, sy + 6 * L.s);
        sg.addColorStop(0, `rgba(${C.hot},0)`); sg.addColorStop(.5, `rgba(${C.warm},${0.06 + glow * 0.07})`); sg.addColorStop(1, `rgba(${C.hot},0)`);
        g.globalAlpha = 1; g.fillStyle = sg; g.fillRect(L.ox, sy - 6 * L.s, 100 * L.s, 12 * L.s);
        g.restore();
        // rare, restrained slice glitch
        if (!still) {
          inst.nextGlitch -= dt;
          if (inst.nextGlitch <= 0) { inst.glitch = 0.16; inst.nextGlitch = 5 + Math.random() * 5; }
          if (inst.glitch > 0) {
            inst.glitch -= dt;
            const gy = L.oy + Math.random() * 70 * L.s, gh = 3 * L.s, off = (Math.random() - 0.5) * 3 * L.dpr;
            g.globalAlpha = 0.5; g.drawImage(inst.staticCanvas, 0, gy, L.W, gh, off, gy, L.W, gh);
          }
        }
        // ambient particles
        g.fillStyle = `rgba(${C.warm},1)`;
        inst.particles.forEach(q => {
          if (!still) q.a += q.v * dt * (0.4 + P.flow * 1.3);
          const x = L.cx + Math.cos(q.a) * q.r, y = L.cy + Math.sin(q.a) * q.r * q.yy;
          g.globalAlpha = Math.min(0.7, (0.10 + glow * 0.32) * (0.5 + 0.5 * Math.sin(time * 1.4 + q.ph)));
          g.beginPath(); g.arc(x, y, q.sz * L.dpr, 0, 6.2832); g.fill();
        });
      }
      g.globalCompositeOperation = 'source-over'; g.globalAlpha = 1;
    };

    function drawRings(g, L, P, time, glow, still) {
      const rot = still ? 0 : time * P.ring;
      const a0 = 0.20 + glow * 0.22;
      g.save(); g.translate(L.cx, L.cy); g.lineWidth = Math.max(1, L.dpr);
      g.strokeStyle = `rgba(${C.mid},${a0})`;
      g.beginPath(); g.arc(0, 0, L.bw * 0.60, 0, 6.2832); g.stroke();
      g.setLineDash([L.bw * 0.05, L.bw * 0.03]); g.lineDashOffset = -rot * L.bw * 0.6;
      g.strokeStyle = `rgba(${C.hot},${a0 * 1.15})`;
      g.beginPath(); g.arc(0, 0, L.bw * 0.68, 0, 6.2832); g.stroke();
      g.setLineDash([]);
      g.save(); g.rotate(-rot * 0.7); g.strokeStyle = `rgba(${C.hot},${a0 * 0.9})`; g.stroke(L.ticks); g.restore();
      // travelling bright arcs — quicker while thinking / processing / syncing
      g.lineCap = 'round'; g.lineWidth = Math.max(1.5, L.dpr * 2);
      [[0.60, 1.0, 0.9], [0.68, -1.4, 0.6]].forEach(([r, sp, len], i) => {
        const a = rot * 6 * sp + i * 2.1;
        g.strokeStyle = `rgba(${C.hot},${Math.min(.85, a0 * 2.6)})`;
        g.beginPath(); g.arc(0, 0, L.bw * r, a, a + len * 0.5); g.stroke();
      });
      if (P.sweep > 0.05) {                                          // synchronisation sweep
        const a = time * 1.6;
        for (let i = 0; i < 7; i++) {
          g.strokeStyle = `rgba(${C.warm},${P.sweep * (0.55 - i * 0.075)})`;
          g.lineWidth = Math.max(1.5, L.dpr * (3.2 - i * 0.3));
          g.beginPath(); g.arc(0, 0, L.bw * 0.64, a - i * 0.13, a - i * 0.13 + 0.16); g.stroke();
        }
      }
      const since = time - inst.stateSince;                          // "device connected" ripple
      if (inst.lastState === 'device_connected' && since < 1.6) {
        const u = since / 1.6;
        g.strokeStyle = `rgba(${C.warm},${(1 - u) * 0.7})`; g.lineWidth = Math.max(1.5, L.dpr * 2);
        g.beginPath(); g.arc(0, 0, L.bw * (0.45 + u * 0.36), 0, 6.2832); g.stroke();
      }
      g.restore();
    }

    inst.destroy = function () { instances.delete(inst); ro && ro.disconnect(); io && io.disconnect(); };
    let ro = null, io = null;
    if (global.ResizeObserver) { ro = new ResizeObserver(() => { inst.L = null; inst.render && inst.render(); start(); }); ro.observe(canvas); }
    if (global.IntersectionObserver) { io = new IntersectionObserver(es => { inst.visible = es[es.length - 1].isIntersecting; if (inst.visible) start(); }); io.observe(canvas); }
    inst.render = function () { inst.frame(0, performance.now() / 1000, MiaState.current(), true); };
    canvas.setAttribute('role', 'img');
    const label = () => canvas.setAttribute('aria-label', `${opts.name || 'Mia'} — ${MiaState.label()}`);
    label(); MiaState.subscribe(label);
    instances.add(inst);
    start();
    return inst;
  }

  function tick(now) {
    raf = 0;
    if (document.hidden || !instances.size) return;
    const st = MiaState.current();
    if (reduced()) { instances.forEach(i => i.visible && i.render()); return; }   // still frames only
    raf = requestAnimationFrame(tick);
    if (st === 'standby' && now - lastDraw < 33) return;                          // ~30 fps at rest
    const dt = Math.min(0.05, (now - (lastNow || now)) / 1000) || 0.016;
    lastNow = now; lastDraw = now;
    instances.forEach(i => { if (i.visible) i.frame(dt, now / 1000, st, false); });
  }
  function start() { if (!raf && !document.hidden) { lastNow = 0; raf = requestAnimationFrame(tick); } }

  document.addEventListener('visibilitychange', () => { if (!document.hidden) start(); });
  MiaState.subscribe(() => { if (reduced()) instances.forEach(i => i.visible && i.render()); else start(); });
  if (mq && mq.addEventListener) mq.addEventListener('change', start);
  // Test/diagnostic hook only: lets automated checks ask which colour the renderer actually uses.
  global.MiaCore = { mount, PROFILE, PALETTE: C, reduced, setMotion(m) { try { localStorage.setItem('mia_motion', m); } catch {} start(); } };
  global.MiaState = MiaState;
})(window);
