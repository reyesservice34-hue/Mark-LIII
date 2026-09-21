// dashboard/static/mia-ui.js — the dashboard's view router, Mia's UI-action receiver,
// the dashboard-awareness report and the visible/logged error path.
//
// Page-agnostic on purpose: a page calls MiaUI.init({...}) with the views it really has
// (the desktop control center registers ten, the phone page a handful) plus the handlers
// that know how to open a file / task / calendar entry there. Anything a page does not
// register is refused visibly ("nicht verfügbar auf diesem Gerät") instead of silently
// doing nothing.
//
// Security model for ui_action messages (server → browser):
//   * strict whitelist of actions AND targets — anything else is rejected and reported
//   * no eval, no innerHTML from message text, no navigation to arbitrary URLs
//   * open_file only ever fetches /uploads/<name> with the session token, and the server
//     already resolved <name> to a real file inside the upload folder
//   * every outcome is acknowledged back to the server (Mia is told the truth) and logged
(function (global) {
  'use strict';

  const VIEW_LABELS = {
    dashboard: 'Übersicht', chat: 'Chat', tasks: 'Aufgaben', calendar: 'Kalender',
    files: 'Dateien', notes: 'Notizen', location: 'Standort', devices: 'Geräte',
    status: 'Systemstatus', settings: 'Einstellungen',
  };
  const ACTION_NAMES = ['open_view', 'open_file', 'open_task', 'open_calendar_event', 'show_notification', 'focus_chat'];
  const LEVELS = ['info', 'success', 'warning', 'error'];
  const REASONS = {                      // human wording for the visible error
    unknown_view: 'Diese Ansicht gibt es nicht.',
    view_not_available: 'Diese Ansicht ist auf diesem Gerät nicht verfügbar.',
    unsupported_action: 'Diese Aktion wird nicht unterstützt.',
    file_not_found: 'Die Datei wurde nicht gefunden.',
    not_found: 'Der Eintrag wurde nicht gefunden.',
    invalid_target: 'Das Ziel ist ungültig.',
    preview_failed: 'Die Vorschau konnte nicht geladen werden.',
    exception: 'Unerwarteter Fehler.',
  };

  const MiaUI = {
    VIEW_LABELS, ACTION_NAMES, current: null, selected: null, log: [], counts: {},
    cfg: null,
  };

  // ── logging: ring buffer (shown in Systemstatus) + console; errors also go to the server ──
  function note(level, event, detail) {
    const e = { ts: Date.now(), level, event, detail: String(detail == null ? '' : detail).slice(0, 160) };
    MiaUI.log.push(e); if (MiaUI.log.length > 80) MiaUI.log.shift();
    (level === 'error' || level === 'warn' ? console.warn : console.debug)('[mia-ui]', event, e.detail);
    document.dispatchEvent(new CustomEvent('mia:log', { detail: e }));
  }
  function sendWs(obj) {
    try {
      if (typeof ws !== 'undefined' && ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; }
    } catch (_) { /* logged by caller */ }
    return false;
  }
  MiaUI.note = note;
  MiaUI.report = function (kind, extra) {          // browser-detected failure → server log
    sendWs(Object.assign({ type: 'ui_report', kind, view: MiaUI.current }, extra || {}));
  };

  // ── visible notifications (role=status / role=alert, textContent only) ───────
  MiaUI.notify = function (text, level) {
    level = LEVELS.includes(level) ? level : 'info';
    const stack = document.getElementById('notice-stack');
    note(level === 'error' ? 'error' : 'info', 'notification', text);
    if (!stack) { if (typeof toast === 'function') toast(String(text).slice(0, 120)); return; }
    const el = document.createElement('div');
    el.className = 'notice notice-' + level;
    el.setAttribute('role', level === 'error' || level === 'warning' ? 'alert' : 'status');
    const msg = document.createElement('span'); msg.textContent = String(text).slice(0, 240);
    const x = document.createElement('button'); x.type = 'button'; x.className = 'notice-x';
    x.setAttribute('aria-label', 'Meldung schließen'); x.textContent = '×';
    const close = () => el.remove();
    x.addEventListener('click', close);
    el.append(msg, x); stack.appendChild(el);
    while (stack.children.length > 4) stack.firstChild.remove();
    setTimeout(close, level === 'error' ? 12000 : 6000);
  };

  // ── awareness: what the dashboard itself is showing (debounced, small, no content) ──
  let reportTimer = 0;
  MiaUI.reportState = function () {
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => {
      if (!MiaUI.cfg) return;
      sendWs({
        type: 'ui_state', view: MiaUI.current, views: Object.keys(MiaUI.cfg.views),
        selected: MiaUI.selected ? { kind: MiaUI.selected.kind, name: MiaUI.selected.name } : null,
        visible: MiaUI.counts, hidden: document.hidden,
      });
    }, 300);
  };
  MiaUI.setCounts = function (obj) { MiaUI.counts = Object.assign({}, MiaUI.counts, obj); MiaUI.reportState(); };
  MiaUI.select = function (kind, name, id) {
    MiaUI.selected = kind ? { kind, name: String(name || '').slice(0, 120), id } : null;
    document.dispatchEvent(new CustomEvent('mia:selected', { detail: MiaUI.selected }));
    MiaUI.reportState();
  };

  // ── router: hash based (#/tasks), real history entries, no server route needed ──
  function hashView() {
    const m = /^#\/([a-z]+)$/.exec(location.hash);
    return m && MiaUI.cfg && MiaUI.cfg.views[m[1]] ? m[1] : null;
  }
  MiaUI.navigate = function (name, opts) {
    opts = opts || {};
    const cfg = MiaUI.cfg;
    if (!cfg || !cfg.views[name]) {
      const why = VIEW_LABELS[name] ? 'view_not_available' : 'unknown_view';
      note('error', 'navigation_failed', `${name}:${why}`);
      MiaUI.report('navigation_failed', { reason: why, action: String(name).slice(0, 24) });
      if (opts.source !== 'action') MiaUI.notify(`Navigation fehlgeschlagen: ${REASONS[why]}`, 'error');
      return false;
    }
    const prev = MiaUI.current;
    MiaUI.current = name;
    try { cfg.views[name].open && cfg.views[name].open(prev); cfg.onNavigate && cfg.onNavigate(name, prev); }
    catch (e) {
      note('error', 'navigation_failed', `${name}:${e.message}`);
      MiaUI.report('navigation_failed', { reason: 'exception', action: name });
      MiaUI.notify('Ansicht konnte nicht geöffnet werden.', 'error');
      return false;
    }
    const url = '#/' + name;
    if (opts.push !== false && location.hash !== url) history.pushState({ view: name }, '', url);
    else if (opts.replace) history.replaceState({ view: name }, '', url);
    if (MiaUI.selected && prev !== name && !opts.keepSelection) MiaUI.select(null);
    MiaUI.reportState();
    return true;
  };

  // ── UI actions ────────────────────────────────────────────────────────────────
  const ACTIONS = {
    open_view(m) {
      if (typeof m.target !== 'string' || !VIEW_LABELS[m.target]) return { ok: false, reason: 'unknown_view' };
      if (!MiaUI.cfg.views[m.target]) return { ok: false, reason: 'view_not_available' };
      return MiaUI.navigate(m.target, { source: 'action' }) ? { ok: true } : { ok: false, reason: 'exception' };
    },
    open_file: m => delegate('openFile', m),
    open_task: m => delegate('openTask', m),
    open_calendar_event: m => delegate('openEvent', m),
    show_notification(m) {
      if (typeof m.text !== 'string' || !m.text.trim()) return { ok: false, reason: 'invalid_target' };
      MiaUI.notify(m.text, LEVELS.includes(m.level) ? m.level : 'info');
      return { ok: true };
    },
    focus_chat() {
      const c = MiaUI.cfg;
      if (c.focusChat) return c.focusChat() ? { ok: true } : { ok: false, reason: 'exception' };
      return { ok: false, reason: 'view_not_available' };
    },
  };
  function delegate(fn, m) {
    const h = MiaUI.cfg && MiaUI.cfg.data && MiaUI.cfg.data[fn];
    if (typeof m.target !== 'string' || !m.target.trim() || m.target.length > 255) return { ok: false, reason: 'invalid_target' };
    if (!h) return { ok: false, reason: 'view_not_available' };
    return h(m.target.trim(), m);
  }

  MiaUI.handleAction = function (m) {
    const id = typeof m.id === 'string' ? m.id.slice(0, 24) : '';
    const done = (res) => {
      res = res || { ok: false, reason: 'exception' };
      sendWs({ type: 'ui_ack', id, ok: !!res.ok, reason: res.reason || null, detail: res.detail || null });
      if (res.ok) { note('info', 'ui_action_ok', `${m.action} ${m.target || ''}`); return; }
      const why = REASONS[res.reason] || res.detail || res.reason || 'Unbekannter Fehler.';
      note('error', 'ui_action_failed', `${m.action}:${res.reason}`);
      MiaUI.notify(`Mia wollte etwas öffnen, aber: ${why}`, 'error');
      MiaUI.report('ui_action_failed', { action: String(m.action).slice(0, 24), reason: String(res.reason).slice(0, 40) });
    };
    if (!MiaUI.cfg || !ACTION_NAMES.includes(m.action)) return done({ ok: false, reason: 'unsupported_action' });
    try { Promise.resolve(ACTIONS[m.action](m)).then(done, e => { note('error', 'ui_action_exception', e && e.message); done({ ok: false, reason: 'exception' }); }); }
    catch (e) { note('error', 'ui_action_exception', e && e.message); done({ ok: false, reason: 'exception' }); }
  };

  // ── init ─────────────────────────────────────────────────────────────────────
  MiaUI.init = function (cfg) {
    MiaUI.cfg = cfg;
    window.addEventListener('popstate', () => {
      const v = hashView() || cfg.default;
      MiaUI.navigate(v, { push: false, source: 'history' });
    });
    document.addEventListener('visibilitychange', MiaUI.reportState);
    try { if (typeof ws !== 'undefined') ws.addEventListener('open', MiaUI.reportState); } catch (_) {}
    MiaUI.navigate(hashView() || cfg.default, { push: false, replace: true, source: 'init' });
  };

  global.MiaUI = MiaUI;
})(window);
