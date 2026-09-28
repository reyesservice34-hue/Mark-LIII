// dashboard/static/mia-desktop.js — the desktop control center: views, data lists, the core,
// and the handlers Mia's UI actions (open_file / open_task / open_calendar_event) end up in.
//
// Runs after shared.js (auth, WebSocket, chat, voice, uploads, companion) and reuses its globals:
// _authFetch, _basePath, _authToken, _uploadFile, mvpLoad/mvpAdd/mvpToggle, _companionEvent,
// _encReady, _voiceWs, ws. All text from the server is inserted with textContent — never innerHTML.
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const NAME = _ASSISTANT;
  const TZ = 'Europe/Berlin';
  const fmtDay = new Intl.DateTimeFormat('sv-SE', { timeZone: TZ });
  const fmtWhen = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const fmtDate = new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit' });

  function h(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function emptyLi(text, err) { return h('li', 'empty' + (err ? ' err' : ''), text); }
  function fill(list, nodes, emptyText) { list.replaceChildren(...(nodes.length ? nodes : [emptyLi(emptyText)])); }
  function li(child) { const n = document.createElement('li'); n.appendChild(child); return n; }
  function whenText(ev) { return ev.allDay ? 'Ganztägig' : fmtWhen.format(new Date(ev.start)); }

  // ── data ─────────────────────────────────────────────────────────────────────
  const D = { tasks: [], notes: [], events: [], files: [], gcal: null, gcalError: '', status: null, loadFailed: false };

  function taskRow(t, compact) {
    const b = h('button', 'row' + (t.done ? ' done' : ''));
    b.type = 'button'; b.dataset.id = t.id; b.setAttribute('role', 'checkbox'); b.setAttribute('aria-checked', String(!!t.done));
    b.append(h('span', 'check', t.done ? '✓' : ''), h('span', 'grow', t.text || t.title || ''));
    if (!compact && t.priority && t.priority !== 'normal') b.append(h('small', '', t.priority));
    b.addEventListener('click', () => { MiaUI.select('task', t.text || t.title, t.id); mvpToggle(t.id); });
    return li(b);
  }
  function simpleRow(main, meta, id) {
    const d = h('div', 'row'); d.dataset.id = id || '';
    d.append(h('span', 'grow', main)); if (meta) d.append(h('small', '', meta));
    return li(d);
  }
  function fileRow(f) {
    const b = h('button', 'row'); b.type = 'button'; b.dataset.name = f.name;
    b.append(h('span', '', fileEmoji(f.name)), h('span', 'grow', f.name), h('small', '', fmtBytes(f.size)));
    b.addEventListener('click', () => openFile(f.name));
    return li(b);
  }
  function fileEmoji(name) { return typeof _fileIcon === 'function' ? _fileIcon(name) : '📎'; }
  function fmtBytes(n) { return typeof _fmtSize === 'function' ? _fmtSize(n || 0) : (n || 0) + ' B'; }

  function gcalToday() { const today = fmtDay.format(new Date()); return (D.gcal || []).filter(e => e.allDay ? e.start <= today && (!e.end || e.end > today) : fmtDay.format(new Date(e.start)) === today); }
  function gcalUpcoming() { const now = new Date(); return (D.gcal || []).filter(e => e.allDay || new Date(e.start) >= now); }

  function renderAll() {
    const open = D.tasks.filter(t => !t.done), done = D.tasks.filter(t => t.done);
    fill($('dash-tasks'), open.slice(0, 5).map(t => taskRow(t, true)), D.loadFailed ? 'Nicht erreichbar.' : 'Keine offenen Aufgaben.');
    fill($('list-tasks'), [...open, ...done].map(t => taskRow(t)), 'Noch keine Aufgaben.');
    fill($('dash-notes'), D.notes.slice(0, 3).map(n => simpleRow(n.text || n.title || '', '', n.id)), D.loadFailed ? 'Nicht erreichbar.' : 'Noch keine Notizen.');
    fill($('list-notes'), D.notes.map(n => simpleRow(n.text || n.title || '', n.created_at ? fmtDate.format(new Date(n.created_at * 1000)) : '', n.id)), 'Noch keine Notizen.');
    fill($('list-events'), D.events.map(e => simpleRow(e.text || e.title || '', e.when || '', e.id)), 'Noch keine eigenen Termine.');
    fill($('dash-files'), D.files.slice(0, 5).map(fileRow), D.loadFailed ? 'Nicht erreichbar.' : 'Noch keine Dateien.');
    fill($('list-files'), D.files.map(fileRow), 'Noch keine Dateien hochgeladen.');
    const setBadge = (id, n) => { const b = $(id); b.textContent = n; b.classList.toggle('has', n > 0); };
    setBadge('badge-tasks', open.length); setBadge('badge-files', D.files.length);
    renderCalendar();
    markSelected();
    MiaUI.setCounts({ tasks_open: open.length, tasks_done: done.length, notes: D.notes.length, own_events: D.events.length, files: D.files.length, calendar_events: (D.gcal || []).length });
  }

  function renderCalendar() {
    const mk = e => {
      const d = h('div', 'row'); d.dataset.id = e.id;
      const main = h('div', 'grow'); main.append(h('div', '', e.title), h('small', '', whenText(e) + (e.location ? ' · ' + e.location : '')));
      d.append(main);
      if (e.location) {
        const a = h('a', 'link', 'Karte'); a.href = 'https://www.openstreetmap.org/search?query=' + encodeURIComponent(e.location);
        a.target = '_blank'; a.rel = 'noopener noreferrer'; a.setAttribute('aria-label', 'Ort auf der Karte öffnen (externe Seite)'); d.append(a);
      }
      return li(d);
    };
    const fail = D.gcal === null;
    fill($('dash-today'), gcalToday().slice(0, 5).map(mk), fail ? (D.gcalError || 'Wird geladen …') : 'Heute keine Termine.');
    fill($('dash-cal'), gcalUpcoming().slice(0, 5).map(mk), fail ? (D.gcalError || 'Wird geladen …') : 'Keine Termine im Zeitraum.');
    fill($('list-gcal'), (D.gcal || []).map(mk), fail ? '' : 'Keine Termine im abgerufenen Zeitraum.');
    $('gcal-status').textContent = fail ? (D.gcalError || 'Wird geladen …') : `Google-Kalender · ${D.gcal.length} Termine (nächste 7 Tage)`;
  }

  function renderNext() {
    const box = $('next-box'), ev = typeof _companionEvent !== 'undefined' ? _companionEvent : null;
    if (!ev) { box.replaceChildren(h('div', 'empty', D.gcal === null ? (D.gcalError || 'Wird geladen …') : 'Kein anstehender Termin.')); return; }
    const eta = $('companion-eta').textContent || '';
    const nodes = [h('div', 'companion-title', ev.title), h('div', 'companion-when', $('companion-when').textContent + (ev.location ? ' · ' + ev.location : ''))];
    if (eta) { const e = h('div', 'companion-eta ' + ($('companion-eta').className.replace('companion-eta', '').trim()), eta); nodes.push(e); }
    box.replaceChildren(...nodes);
  }
  document.addEventListener('mia:companion', renderNext);

  document.addEventListener('mia:data', e => {
    const d = e.detail || {};
    D.loadFailed = !!d.error && !d.state && !d.files;
    if (d.state) { D.tasks = d.state.tasks || []; D.notes = d.state.notes || []; D.events = d.state.events || []; }
    if (d.files) D.files = d.files;
    renderAll();
  });

  async function loadGcal() {
    try {
      const r = await _authFetch('/api/companion/calendar', { cache: 'no-store' });
      if (r.status === 404) throw new Error('Kalender ist auf diesem Server nicht eingerichtet.');
      const data = await r.json();
      if (!r.ok || !Array.isArray(data.events)) throw new Error(data.detail || 'Kalender derzeit nicht erreichbar.');
      D.gcal = data.events; D.gcalError = '';
    } catch (err) { D.gcal = null; D.gcalError = err.message || 'Kalender nicht erreichbar.'; MiaUI.note('warn', 'calendar_unavailable', D.gcalError); }
    renderCalendar(); renderNext();
    MiaUI.setCounts({ calendar_events: (D.gcal || []).length });
  }

  async function loadFiles() {
    try {
      const r = await _authFetch('/api/files');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      D.files = (await r.json()).files || []; D.loadFailed = false; renderAll();
    } catch (err) { MiaUI.note('error', 'files_load_failed', err.message); }
  }

  // ── status, devices, system ──────────────────────────────────────────────────
  function kv(dl, rows) { dl.replaceChildren(...rows.flatMap(([k, v]) => [h('dt', '', k), h('dd', '', String(v))])); }
  function wsText() { return typeof ws !== 'undefined' && ws.readyState === 1 ? 'verbunden' : 'getrennt'; }
  function renderStatus() {
    const s = D.status, st = MiaState.current();
    kv($('dash-dev'), s ? [['Gekoppelt', s.paired_devices], ['Verbunden', s.connected_clients], ['Verbindung', wsText()]] : [['Status', 'nicht verfügbar']]);
    kv($('dev-kv'), s ? [['Gekoppelte Geräte', s.paired_devices], ['Aktive Sitzungen', s.active_sessions], ['Jetzt verbundene Oberflächen', s.connected_clients], ['Dieses Gerät', wsText()]] : [['Status', 'nicht verfügbar']]);
    kv($('status-kv'), [
      ['Assistentin', s ? s.assistant : NAME], ['Zustand', MiaState.label(st)], ['WebSocket', wsText()],
      ['Verschlüsselung', (typeof _encReady !== 'undefined' && _encReady) ? 'AES-256-CBC aktiv' : 'nicht aktiv'],
      ['Mikrofon', (typeof _voiceWs !== 'undefined' && _voiceWs) ? 'live' : 'aus'],
      ...(s ? [['TLS', s.tls ? 'ja' : 'nein'], ['Uploads', `${s.uploads.count} · ${fmtBytes(s.uploads.bytes)} (max. ${s.uploads.max_mb} MB je Datei)`]] : []),
    ]);
    $('quick-status').textContent = `${NAME} · ${MiaState.label(st)} · Verbindung ${wsText()}`;
  }
  async function loadStatus() {
    try {
      const r = await _authFetch('/api/status');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      D.status = await r.json();
    } catch (err) { D.status = null; MiaUI.note('warn', 'status_unavailable', err.message); }
    renderStatus();
  }

  function metric(label, pct, extra) {
    const cls = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';
    const wrap = document.createElement('div');
    const row = h('div', 'metric-row'); row.append(h('span', '', label), h('span', 'val', extra || pct.toFixed(0) + '%'));
    const bar = h('div', 'metric-bar-wrap'); const fillEl = h('div', 'metric-bar ' + cls); fillEl.style.width = Math.min(100, pct) + '%'; bar.append(fillEl);
    wrap.append(row, bar); return wrap;
  }
  async function refreshSystem() {
    const targets = [$('sys-metrics'), $('sys-metrics-full')];
    try {
      const r = await _authFetch('/api/system/status');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const nodes = [metric('CPU', d.cpu_percent), metric('RAM', d.ram_percent, `${d.ram_used_gb}/${d.ram_total_gb} GB`)];
      if (d.gpu_percent != null) nodes.push(metric('GPU', d.gpu_percent));
      if (d.cpu_temp_c != null) nodes.push(metric('Temp', d.cpu_temp_c, d.cpu_temp_c + '°C'));
      const up = h('div', 'metric-row'); up.append(h('span', '', 'Uptime'), h('span', 'val', d.uptime)); nodes.push(up);
      targets.forEach(t => t.replaceChildren(...nodes.map(n => n.cloneNode(true))));
    } catch (err) { targets.forEach(t => t.replaceChildren(h('div', 'empty err', 'System-Metriken nicht verfügbar.'))); MiaUI.note('warn', 'system_status_unavailable', err.message); }
  }

  // ── selection highlight ──────────────────────────────────────────────────────
  function markSelected() {
    const sel = MiaUI.selected;
    document.querySelectorAll('.is-selected').forEach(n => n.classList.remove('is-selected'));
    if (!sel) return;
    const q = sel.kind === 'file' ? `#list-files [data-name="${CSS.escape(sel.name)}"]` : sel.id ? `[data-id="${CSS.escape(String(sel.id))}"]` : null;
    if (q) document.querySelectorAll(q).forEach(n => n.classList.add('is-selected'));
  }
  document.addEventListener('mia:selected', markSelected);
  function flash(node) { if (!node) return; node.classList.add('flash'); node.scrollIntoView({ block: 'center', behavior: MiaCore.reduced() ? 'auto' : 'smooth' }); setTimeout(() => node.classList.remove('flash'), 1800); }

  // ── file open / preview ──────────────────────────────────────────────────────
  const IMG = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'], TXT = ['txt', 'md', 'csv', 'json', 'log', 'xml', 'yml', 'yaml', 'ini', 'py', 'js', 'css', 'html'];
  function fileUrl(name) { return `${_basePath}/uploads/${encodeURIComponent(name)}?token=${encodeURIComponent(_authToken)}`; }

  async function showFilePreview(f) {
    const box = $('file-preview'); const ext = (f.name.split('.').pop() || '').toLowerCase(), url = fileUrl(f.name);
    const meta = h('dl', 'meta');
    [['Name', f.name], ['Größe', fmtBytes(f.size)], ['Geändert', f.mtime ? fmtWhen.format(new Date(f.mtime * 1000)) : '–']].forEach(([k, v]) => meta.append(h('dt', '', k), h('dd', '', v)));
    const actions = h('div', 'add-row');
    const dl = h('a', 'btn btn-primary', 'Herunterladen'); dl.href = url; dl.download = f.name; actions.append(dl);
    const nodes = [h('h2', '', 'Vorschau'), meta, actions];
    let result = { ok: true };
    if (IMG.includes(ext)) {
      const img = document.createElement('img'); img.alt = f.name;
      result = await new Promise(res => { const t = setTimeout(() => res({ ok: false, reason: 'preview_failed' }), 6000); img.onload = () => { clearTimeout(t); res({ ok: true }); }; img.onerror = () => { clearTimeout(t); res({ ok: false, reason: 'preview_failed' }); }; img.src = url; });
      nodes.push(result.ok ? img : h('p', 'empty err', 'Vorschau konnte nicht geladen werden.'));
    } else if (TXT.includes(ext) && f.size <= 2 * 1024 * 1024) {
      try {
        const r = await fetch(url); if (!r.ok) throw new Error('HTTP ' + r.status);
        const pre = h('pre'); pre.textContent = (await r.text()).slice(0, 60000); nodes.push(pre);
      } catch (err) { result = { ok: false, reason: 'preview_failed' }; nodes.push(h('p', 'empty err', 'Vorschau konnte nicht geladen werden.')); }
    } else {
      const open = h('a', 'btn', 'In neuem Tab öffnen'); open.href = url; open.target = '_blank'; open.rel = 'noopener noreferrer'; actions.append(open);
      nodes.push(h('p', 'hint', 'Für diesen Dateityp gibt es keine Vorschau. Du kannst die Datei herunterladen oder in einem neuen Tab öffnen.'));
    }
    box.replaceChildren(...nodes);
    return result;
  }

  async function openFile(name) {
    if (!MiaUI.navigate('files', { source: 'action', keepSelection: true })) return { ok: false, reason: 'view_not_available' };
    await loadFiles();
    const f = D.files.find(x => x.name === name) || D.files.find(x => x.name.toLowerCase() === String(name).toLowerCase());
    if (!f) { MiaUI.report('file_open_failed', { reason: 'file_not_found' }); return { ok: false, reason: 'file_not_found' }; }
    MiaUI.select('file', f.name);
    flash(document.querySelector(`#list-files [data-name="${CSS.escape(f.name)}"]`));
    const res = await showFilePreview(f);
    if (!res.ok) MiaUI.report('file_open_failed', { reason: res.reason });
    return res;
  }

  // ── task / calendar-event open ───────────────────────────────────────────────
  function findBy(list, q, textOf) {
    const lc = q.toLowerCase();
    return list.find(x => String(x.id) === q) || list.find(x => textOf(x).toLowerCase() === lc) || list.find(x => lc && textOf(x).toLowerCase().includes(lc));
  }
  async function openTask(q) {
    if (!MiaUI.navigate('tasks', { source: 'action', keepSelection: true })) return { ok: false, reason: 'view_not_available' };
    await mvpLoad();
    const t = findBy(D.tasks, q, x => x.text || x.title || '');
    if (!t) return { ok: false, reason: 'not_found' };
    MiaUI.select('task', t.text || t.title, t.id);
    flash(document.querySelector(`#list-tasks [data-id="${CSS.escape(t.id)}"]`));
    return { ok: true };
  }
  async function openEvent(q) {
    if (!MiaUI.navigate('calendar', { source: 'action', keepSelection: true })) return { ok: false, reason: 'view_not_available' };
    await Promise.all([mvpLoad(), loadGcal()]);
    const g = findBy(D.gcal || [], q, x => x.title || '');
    if (g) { MiaUI.select('calendar_event', g.title, g.id); flash(document.querySelector(`#list-gcal [data-id="${CSS.escape(g.id)}"]`)); return { ok: true }; }
    const own = findBy(D.events, q, x => x.text || x.title || '');
    if (own) { MiaUI.select('calendar_event', own.text || own.title, own.id); flash(document.querySelector(`#list-events [data-id="${CSS.escape(own.id)}"]`)); return { ok: true }; }
    return { ok: false, reason: 'not_found' };
  }

  // ── views ────────────────────────────────────────────────────────────────────
  const VIEWS = {};
  Object.keys(MiaUI.VIEW_LABELS).forEach(name => {
    VIEWS[name] = {
      open() {
        document.querySelectorAll('.view').forEach(v => { v.hidden = v.dataset.view !== name; });
        document.querySelectorAll('.nav-item').forEach(b => { if (b.dataset.view === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
        $('view-title').textContent = MiaUI.VIEW_LABELS[name];
        document.title = `${MiaUI.VIEW_LABELS[name]} · ${NAME}`;
        if (name === 'chat') { $('chat-composer-slot').appendChild($('composer')); const f = $('feed'); f.scrollTop = f.scrollHeight; }
        else if (name === 'dashboard') $('dock-composer').appendChild($('composer'));
        if (['tasks', 'notes', 'files', 'dashboard'].includes(name)) mvpLoad();
        if (['calendar', 'dashboard'].includes(name)) loadGcal();
        if (['devices', 'status', 'dashboard'].includes(name)) loadStatus();
        if (['status', 'dashboard'].includes(name)) refreshSystem();
        if (name === 'status') renderLog();
      },
    };
  });

  function focusChat() { if (!MiaUI.navigate('chat', { source: 'action' })) return false; $('inp').focus(); return true; }

  // ── log list ─────────────────────────────────────────────────────────────────
  function renderLog() {
    const box = $('log-list'); if (!box) return;
    const t = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    box.replaceChildren(...MiaUI.log.slice(-40).reverse().map(e => h('div', 'e-' + (e.level === 'info' ? 'info' : 'error'), `${t.format(new Date(e.ts))}  ${e.event}  ${e.detail}`)));
    if (!MiaUI.log.length) box.append(h('div', 'e-info', 'Noch keine Einträge.'));
  }
  document.addEventListener('mia:log', () => { if (MiaUI.current === 'status') renderLog(); });

  // ── core + state chip ────────────────────────────────────────────────────────
  MiaCore.mount($('core-canvas'), { name: NAME });
  MiaCore.mount($('rail-core'), { mini: true, name: NAME });
  function stateUI() {
    const s = MiaState.current();
    $('core-label').textContent = MiaState.label(s); const chip = $('core-state'); chip.textContent = MiaState.label(s); chip.dataset.state = s;
    renderStatus();
  }
  MiaState.subscribe(stateUI); stateUI();
  document.addEventListener('mia:message', e => { if (e.detail.speaker === 'jarvis') $('core-caption').textContent = e.detail.text; });

  // ── wiring ───────────────────────────────────────────────────────────────────
  document.querySelectorAll('.nav-item').forEach(b => b.addEventListener('click', () => MiaUI.navigate(b.dataset.view)));
  document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => MiaUI.navigate(b.dataset.goto)));
  document.querySelectorAll('[data-add]').forEach(b => b.addEventListener('click', () => mvpAdd(b.dataset.add)));
  ['task', 'note', 'event'].forEach(k => { const i = $('mvp-' + k + '-input'); i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); mvpAdd(k); } }); });
  $('attach-btn').addEventListener('click', () => $('file-inp').click());
  $('cal-refresh').addEventListener('click', loadGcal);
  document.querySelectorAll('[data-quick]').forEach(b => b.addEventListener('click', () => {
    const q = b.dataset.quick;
    if (q === 'ask-today') { $('inp').value = 'Was steht heute noch an?'; doSend(); }
    else if (q === 'new-task') { MiaUI.navigate('tasks'); $('mvp-task-input').focus(); }
    else if (q === 'send-file') $('file-inp').click();
    else if (q === 'location') MiaUI.navigate('location');
  }));

  const dz = $('dropzone'), fv = $('view-files');
  ['dragenter', 'dragover'].forEach(t => fv.addEventListener(t, e => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => fv.addEventListener(t, e => { e.preventDefault(); dz.classList.remove('over'); }));
  fv.addEventListener('drop', e => { Array.from(e.dataTransfer.files || []).forEach(_uploadFile); });

  // settings
  function segState(sel, attr, value) { document.querySelectorAll(sel).forEach(b => b.setAttribute('aria-pressed', String(b.dataset[attr] === value))); }
  const motion = (() => { try { return localStorage.getItem('mia_motion') || 'auto'; } catch { return 'auto'; } })();
  segState('[data-motion]', 'motion', motion);
  document.querySelectorAll('[data-motion]').forEach(b => b.addEventListener('click', () => { MiaCore.setMotion(b.dataset.motion); segState('[data-motion]', 'motion', b.dataset.motion); }));
  segState('[data-sound]', 'sound', _soundOn ? 'on' : 'off');
  document.querySelectorAll('[data-sound]').forEach(b => b.addEventListener('click', () => {
    _soundOn = b.dataset.sound === 'on'; try { localStorage.setItem('mia_sound', _soundOn ? 'on' : 'off'); } catch (_) {}
    if (!_soundOn) _stopPlayback(); segState('[data-sound]', 'sound', b.dataset.sound);
  }));
  $('reconnect-btn').addEventListener('click', () => { sessionStorage.removeItem('mia_token'); location.reload(); });
  $('logout-btn').addEventListener('click', () => { sessionStorage.removeItem('mia_token'); sessionStorage.removeItem('mia_key'); localStorage.removeItem('mia_device_token'); localStorage.removeItem('jarvis_device_token'); location.replace(_basePath + '/login'); });
  let revokeArmed = 0;
  $('revoke-btn').addEventListener('click', async e => {
    const btn = e.currentTarget;
    if (!revokeArmed) { revokeArmed = setTimeout(() => { revokeArmed = 0; btn.textContent = 'Widerrufen'; }, 5000); btn.textContent = 'Wirklich widerrufen?'; return; }
    clearTimeout(revokeArmed); revokeArmed = 0; btn.textContent = 'Widerrufen';
    try {
      const r = await _authFetch('/api/revoke-devices', { method: 'POST' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      MiaUI.notify(`${(await r.json()).revoked} gespeicherte Kopplung(en) widerrufen.`, 'success'); loadStatus();
    } catch (err) { MiaUI.notify('Widerrufen fehlgeschlagen.', 'error'); }
  });

  // command palette (Ctrl/⌘+K) — same commands as before, plus navigation
  const po = $('palette-overlay'), pi = $('palette-input'), pa = $('palette-actions');
  Object.entries(MiaUI.VIEW_LABELS).forEach(([v, label]) => { const b = h('button', 'palette-action', label); b.type = 'button'; b.dataset.cmd = '__goto:' + v; pa.appendChild(b); });
  let lastFocus = null;
  function openPalette() { lastFocus = document.activeElement; po.classList.add('show'); pi.value = ''; pi.focus(); }
  function closePalette() { po.classList.remove('show'); if (lastFocus && lastFocus.focus) lastFocus.focus(); }
  function paletteRun(cmd) {
    closePalette();
    if (cmd === '__wake') return doWake();
    if (cmd === '__companion-toggle-location') return companionToggleLocation();
    if (cmd.startsWith('__goto:')) return MiaUI.navigate(cmd.slice(7));
    $('inp').value = cmd; doSend();
  }
  $('palette-hint').addEventListener('click', openPalette);
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); po.classList.contains('show') ? closePalette() : openPalette(); }
    else if (e.key === 'Escape' && po.classList.contains('show')) closePalette();
  });
  po.addEventListener('click', e => { if (e.target === po) closePalette(); });
  pi.addEventListener('keydown', e => { if (e.key === 'Enter') { const v = pi.value.trim(); if (v) paletteRun(v); } });
  pa.addEventListener('click', e => { const b = e.target.closest('.palette-action'); if (b) paletteRun(b.dataset.cmd); });

  // ── go ───────────────────────────────────────────────────────────────────────
  MiaUI.init({ default: 'dashboard', views: VIEWS, focusChat, data: { openFile, openTask, openEvent } });
  mvpLoad(); loadGcal(); loadStatus(); refreshSystem();
  const every = (fn, ms, when) => setInterval(() => { if (!document.hidden && (!when || when())) fn(); }, ms);
  every(mvpLoad, 30000); every(loadGcal, 120000);
  every(loadStatus, 15000, () => ['dashboard', 'devices', 'status'].includes(MiaUI.current));
  every(refreshSystem, 8000, () => ['dashboard', 'status'].includes(MiaUI.current));
  every(renderStatus, 3000);
})();
