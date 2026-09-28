// dashboard/static/shared.js — everything both the phone dashboard (app.html)
// and the desktop dashboard (desktop.html) need: auth, encryption, the
// WebSocket connection, chat, voice, confirmation banner, calendar/ETA
// companion. One copy so a fix (like the resampler bug, or the typed-command
// sync gap) lands on both surfaces at once instead of drifting between two
// near-duplicate scripts — exactly the class of bug that hit login.html
// earlier in this project's history.
//
// Both pages must provide the same element IDs this script looks up
// (header pill/brain/enc badge, #feed, #inp, #mic-btn, #wake, the companion
// box, callBanner, confirmBanner, file input) — desktop.html arranges them
// differently on screen, but the ids are identical.
//
// Mia's core (mia-core.js) is driven from here: every real signal below (WebSocket,
// audio, tools, uploads, calendar sync, warnings) switches a named signal on the shared
// MiaState model; the renderer shows whichever has priority. Nothing is simulated.

// ── Auth — bearer token ───────────────────────────────────────────────────
const _authToken  = sessionStorage.getItem('mia_token');
const _sessionKey = sessionStorage.getItem('mia_key');
const _basePath = location.pathname.startsWith('/companion/') ? '/companion' : '';
const _isDesktopDashboard = location.pathname === '/desktop' || location.pathname.endsWith('/desktop');
function _url(path) { return _basePath + path; }
function _wsPath(path) { return _basePath + path; }

if (!_authToken) {
  // Installed home-screen launches (PWA) get a fresh session token
  // automatically from a previously paired device — no QR re-scan needed.
  // `next` carries the page we were actually trying to reach (e.g. /desktop)
  // through the login round trip, so it doesn't always dump you on /.
  const _devTok = (localStorage.getItem('mia_device_token') || localStorage.getItem('jarvis_device_token'));
  const _next = encodeURIComponent(location.pathname + location.search);
  location.replace(_devTok
    ? `${_basePath}/auto-device-login?device_token=${encodeURIComponent(_devTok)}&next=${_next}`
    : `${_basePath}/login?next=${_next}`);
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register(_url('/sw.js'), { scope: _basePath ? '/companion/' : '/' }).catch(() => {});
}

function _authHeader() { return { 'Authorization': `Bearer ${_authToken}` }; }
function _authFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers, _authHeader());
  return fetch(url.startsWith('/') ? _url(url) : url, opts);
}

// ── AES-256-CBC encryption (CryptoJS) ────────────────────────────────────
const _AES_SALT = 'JARVIS-DASHBOARD-v1';
let _aesKey = null;

function _initCrypto(key) {
  if (typeof CryptoJS === 'undefined') return false;
  _aesKey = CryptoJS.SHA256(key + _AES_SALT);
  return true;
}

function _encrypt(plaintext) {
  if (!_aesKey || typeof CryptoJS === 'undefined') return null;
  try {
    const iv  = CryptoJS.lib.WordArray.random(16);
    const enc = CryptoJS.AES.encrypt(
      CryptoJS.enc.Utf8.parse(plaintext), _aesKey,
      { iv, mode: CryptoJS.mode.CBC, padding: CryptoJS.pad.Pkcs7 }
    );
    const ivHex = iv.toString(CryptoJS.enc.Hex);
    const ctHex = enc.ciphertext.toString(CryptoJS.enc.Hex);
    const hex   = ivHex + ctHex;
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < bytes.length; i++)
      bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
    return btoa(String.fromCharCode(...bytes));
  } catch { return null; }
}

const _encReady = _sessionKey ? _initCrypto(_sessionKey) : false;
const encBadge  = document.getElementById('enc');
if (_encReady) { encBadge.className = 'enc-badge on'; }
else           { encBadge.className = 'enc-badge off'; encBadge.textContent = 'NO ENC'; }

// ── Mia core state bridge (see mia-core.js) ─────────────────────────────
const _ASSISTANT = (document.querySelector('meta[name="assistant-name"]') || {}).content || 'Mia';
const _S = window.MiaState || { set() {}, setLevel() {}, current() { return 'standby'; } };
const _warnSources = new Set();          // several independent things can raise "warning"
function _warn(src, on) { on ? _warnSources.add(src) : _warnSources.delete(src); _S.set('warning', _warnSources.size > 0); }
let _uploadsActive = 0;
function _pcmLevel(pcm) {                // RMS of an Int16 chunk → 0..1 (drives the core's pulse)
  let sum = 0; const n = Math.min(pcm.length, 2400), step = Math.max(1, Math.floor(pcm.length / n));
  for (let i = 0, c = 0; i < pcm.length; i += step, c++) { const v = pcm[i] / 32768; sum += v * v; }
  return Math.min(1, Math.sqrt(sum / Math.max(1, Math.floor(pcm.length / step))) * 4.2);
}
function _miaSpoke() { _S.set('thinking', false); _S.set('speaking', true, 900); }   // real output activity
let _soundOn = true;
try { _soundOn = localStorage.getItem('mia_sound') !== 'off'; } catch (_) {}

// ── WebSocket ─────────────────────────────────────────────────────────────
const feed  = document.getElementById('feed');
const pill  = document.getElementById('pill');
const stTxt = document.getElementById('st');
const wake  = document.getElementById('wake');

const _wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
const _wsUrl   = `${_wsProto}://${location.host}${_wsPath('/ws')}?token=${encodeURIComponent(_authToken)}`;
const ws       = new WebSocket(_wsUrl);

ws.onopen    = () => { _warn('ws', false); if (!pill.classList.contains('on')) { pill.className = 'pill on'; stTxt.textContent = 'Verbunden'; } _S.set('device_connected', true, 2600); sys('Remote session active.'); toast('Connected to ' + _ASSISTANT); };
ws.onclose   = () => {
  _warn('ws', true); _S.set('speaking', false); _S.set('thinking', false);
  pill.className = 'pill'; stTxt.textContent = 'Getrennt';
  sys('Connection lost — refresh to reconnect.');
  if (window.MiaUI) MiaUI.notify('Verbindung zu ' + _ASSISTANT + ' unterbrochen. Über Einstellungen → Neu verbinden erneut anmelden.', 'error');
};
ws.onerror   = () => { sys('Connection error.'); if (window.MiaUI) MiaUI.note('error', 'ws_error', 'WebSocket error'); };
ws.onmessage = (e) => {
  let m;
  try { m = JSON.parse(e.data); }
  catch (_) { if (window.MiaUI) MiaUI.note('error', 'ws_bad_frame', 'unparseable message'); return; }
  if (m.type === 'log') {
    // A streamed reply already ends up on screen chunk by chunk (below) —
    // once the final, complete message arrives just close out that bubble
    // instead of appending the whole text again as a duplicate.
    if (m.speaker === 'jarvis' && _streamEl) _streamFinish();
    else append(m.speaker, m.text);
  }
  if (m.type === 'log_delta' && m.speaker === 'jarvis') _streamAppendJarvis(m.text);
  if (m.type === 'status')        setStatus(m.state);
  if (m.type === 'wake')          sys('Wake word detected — connecting…');
  if (m.type === 'sys')           sys(m.text);
  if (m.type === 'file_received') _onFileReceived(m);
  if (m.type === 'audio')         _playPcm16(m.data, m.rate);
  if (m.type === 'audio_stop')    { _stopPlayback(); _S.set('speaking', false); }
  if (m.type === 'incoming_call') _showIncomingCall();
  if (m.type === 'ui_action' && window.MiaUI) MiaUI.handleAction(m);
  if (m.type === 'activity')      _onActivity(m);
  if (m.type === 'confirm_pending') _showConfirm(m.title, m.detail);
  if (m.type === 'confirm_resolved') _hideConfirm();
};

function setStatus(s) {
  if (s === 'active') {
    pill.className = 'pill on'; stTxt.textContent = 'Aktiv'; wake.classList.remove('show');
  } else {
    pill.className = 'pill'; stTxt.textContent = 'Schläft'; wake.classList.add('show');
  }
}

// Tool activity reported by the server (tool name only, never arguments).
const _FILE_TOOLS = ['file_processor', 'file_controller'];
function _onActivity(m) {
  if (typeof m.tool !== 'string') return;
  const sig = _FILE_TOOLS.includes(m.tool) ? 'processing_file' : 'thinking';
  if (m.phase === 'tool_start') _S.set(sig, true, 120000);
  else if (m.phase === 'tool_end') _S.set(sig, false);
}

function append(speaker, text) {
  const d = document.createElement('div');
  if (speaker === 'jarvis') {
    d.className = 'msg msg-j';
    d.innerHTML = `<div class="lbl">${esc(_ASSISTANT)}</div>${esc(text)}`;
    _miaSpoke();
    document.dispatchEvent(new CustomEvent('mia:message', { detail: { speaker, text } }));
  } else {
    d.className = 'msg msg-u';
    d.innerHTML = `<div class="lbl">Du</div>${esc(text)}`;
    _S.set('thinking', true, 25000);            // the user finished speaking/typing → Mia is working
  }
  feed.appendChild(d);
  feed.scrollTop = feed.scrollHeight;
}

// JARVIS's reply is generated (and spoken) in real time, chunk by chunk —
// showing it only once the whole turn is done would leave the feed silent
// for as long as the full answer takes to speak. Stream each chunk into
// one growing bubble instead, closed out when the final 'log' arrives.
let _streamEl = null, _streamChunks = [];
let _thinkingEl = null, _thinkingTimer = null;

// The model responds in AUDIO mode — text can never arrive before the
// matching bit of speech has actually been generated, so there is always
// a short real gap before the first chunk. Showing something the instant
// a command is sent (rather than silence) is what actually fixes the
// "sounds broken" feeling — the streaming above only helps once the
// reply has started.
function _showThinking() {
  if (_thinkingEl || _streamEl) return;
  _thinkingEl = document.createElement('div');
  _thinkingEl.className = 'msg msg-j msg-thinking';
  _thinkingEl.innerHTML = `<div class="lbl">${esc(_ASSISTANT)}</div><span class="dots"><span>·</span><span>·</span><span>·</span></span>`;
  feed.appendChild(_thinkingEl);
  feed.scrollTop = feed.scrollHeight;
  clearTimeout(_thinkingTimer);
  _thinkingTimer = setTimeout(_hideThinking, 20000); // safety net, never stuck forever
}

function _hideThinking() {
  clearTimeout(_thinkingTimer);
  if (_thinkingEl) { _thinkingEl.remove(); _thinkingEl = null; }
}

function _streamAppendJarvis(txt) {
  _hideThinking();
  if (!_streamEl) {
    _streamEl = document.createElement('div');
    _streamEl.className = 'msg msg-j';
    _streamEl.innerHTML = `<div class="lbl">${esc(_ASSISTANT)}</div><span class="stream-text"></span>`;
    feed.appendChild(_streamEl);
    _streamChunks = [];
  }
  _streamChunks.push(txt);
  _streamEl.querySelector('.stream-text').textContent = _streamChunks.join(' ');
  feed.scrollTop = feed.scrollHeight;
  _miaSpoke();
  document.dispatchEvent(new CustomEvent('mia:message', { detail: { speaker: 'jarvis', text: _streamChunks.join(' '), partial: true } }));
}

function _streamFinish() {
  _hideThinking();
  _streamEl = null;
  _streamChunks = [];
}

function sys(text) {
  const d = document.createElement('div');
  d.className = 'msg msg-sys'; d.textContent = text;
  feed.appendChild(d); feed.scrollTop = feed.scrollHeight;
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── File helpers ──────────────────────────────────────────────────────────
function _fileIcon(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (['jpg','jpeg','png','gif','webp','heic','heif','avif','bmp','svg'].includes(ext)) return '🖼️';
  if (['mp4','mov','avi','mkv','webm','m4v','3gp'].includes(ext))                       return '🎬';
  if (['mp3','m4a','wav','flac','aac','ogg','opus'].includes(ext))                      return '🎵';
  if (['pdf'].includes(ext))                                                             return '📋';
  if (['doc','docx','txt','md','rtf','odt'].includes(ext))                              return '📝';
  if (['xls','xlsx','csv','ods'].includes(ext))                                         return '📊';
  if (['ppt','pptx','odp'].includes(ext))                                               return '📑';
  if (['zip','rar','7z','tar','gz','bz2','xz'].includes(ext))                          return '📦';
  if (['apk','exe','dmg','deb','rpm','msi'].includes(ext))                              return '⚙️';
  return '📎';
}

function _fmtSize(bytes) {
  if (bytes < 1024)             return bytes + ' B';
  if (bytes < 1024 * 1024)     return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 ** 3)       return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  return (bytes / 1024 ** 3).toFixed(2) + ' GB';
}

// ── File upload (phone → computer) ────────────────────────────────────────
function _uploadFile(file) {
  const id = 'fc_' + Date.now() + '_' + Math.random().toString(36).slice(2);

  // Create file card in feed
  const card = document.createElement('div');
  card.className = 'msg-file';
  card.id = id;
  card.innerHTML = `
    <div class="fc-icon">${_fileIcon(file.name)}</div>
    <div class="fc-meta">
      <div class="fc-lbl">Sending</div>
      <div class="fc-name">${esc(file.name)}</div>
      <div class="fc-size">${_fmtSize(file.size)}</div>
      <div class="fc-bar-wrap"><div class="fc-bar" id="${id}_bar"></div></div>
    </div>
    <div class="fc-status" id="${id}_st">0%</div>
  `;
  feed.appendChild(card);
  feed.scrollTop = feed.scrollHeight;

  const xhr = new XMLHttpRequest();
  _uploadsActive++; _S.set('processing_file', true);
  const _uploadDone = () => { _uploadsActive = Math.max(0, _uploadsActive - 1); if (!_uploadsActive) _S.set('processing_file', false); };
  xhr.open('POST', _url('/api/upload'));
  xhr.setRequestHeader('Authorization', `Bearer ${_authToken}`);

  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round(e.loaded / e.total * 100);
    const bar = document.getElementById(`${id}_bar`);
    const st  = document.getElementById(`${id}_st`);
    if (bar) bar.style.width = pct + '%';
    if (st)  st.textContent  = pct + '%';
  };

  xhr.onload = () => {
    _uploadDone();
    const bar  = document.getElementById(`${id}_bar`);
    const st   = document.getElementById(`${id}_st`);
    const meta = card.querySelector('.fc-meta');
    const lbl  = card.querySelector('.fc-lbl');
    if (xhr.status === 200) {
      let savedName = file.name;
      try { savedName = JSON.parse(xhr.responseText).name || file.name; } catch {}
      const dlUrl = `${_basePath}/uploads/${encodeURIComponent(savedName)}?token=${encodeURIComponent(_authToken)}`;
      if (bar)  { bar.style.width = '100%'; bar.style.background = 'var(--green)'; }
      if (st)   { st.textContent = '✓'; st.className = 'fc-status done'; }
      if (lbl)  lbl.textContent = 'Sent';
      if (meta) meta.insertAdjacentHTML('beforeend',
        `<a class="fc-dl" href="${dlUrl}" download="${esc(savedName)}">⬇ Download back</a>`);
      toast('File sent!');
      if (typeof mvpLoad === 'function') mvpLoad();
    } else {
      let errMsg = 'Upload failed';
      try { errMsg = JSON.parse(xhr.responseText).error || errMsg; } catch {}
      if (bar) bar.style.background = '#f87171';
      if (st)  { st.textContent = 'ERR'; st.className = 'fc-status err'; }
      if (lbl) lbl.textContent = errMsg;
      _warn('upload', true); setTimeout(() => _warn('upload', false), 4000);
      if (window.MiaUI) MiaUI.notify('Upload fehlgeschlagen: ' + errMsg, 'error');
    }
  };

  xhr.onerror = () => {
    _uploadDone();
    if (window.MiaUI) MiaUI.notify('Upload fehlgeschlagen: Verbindungsfehler', 'error');
    const st  = document.getElementById(`${id}_st`);
    const lbl = card.querySelector('.fc-lbl');
    if (st)  { st.textContent = 'ERR'; st.className = 'fc-status err'; }
    if (lbl) lbl.textContent = 'Connection error';
  };

  const fd = new FormData();
  fd.append('file', file);
  xhr.send(fd);
}

// File received by another WebSocket client (e.g., second open tab)
function _onFileReceived(m) {
  const dlUrl = `${_basePath}/uploads/${encodeURIComponent(m.name)}?token=${encodeURIComponent(_authToken)}`;
  const card  = document.createElement('div');
  card.className = 'msg-file';
  card.innerHTML = `
    <div class="fc-icon">${_fileIcon(m.name)}</div>
    <div class="fc-meta">
      <div class="fc-lbl">Received</div>
      <div class="fc-name">${esc(m.name)}</div>
      <div class="fc-size">${_fmtSize(m.size)}</div>
      <a class="fc-dl" href="${dlUrl}" download="${esc(m.name)}">⬇ Download</a>
    </div>
    <div class="fc-status done">✓</div>
  `;
  feed.appendChild(card);
  feed.scrollTop = feed.scrollHeight;
  toast('File received on computer!');
  _S.set('processing_file', true, 1800);
  if (typeof mvpLoad === 'function') mvpLoad();
}

// File input change handler
document.getElementById('file-inp').addEventListener('change', function () {
  Array.from(this.files).forEach(_uploadFile);
  this.value = ''; // reset so the same file can be re-sent
});

// ── Commands ──────────────────────────────────────────────────────────────
// No local echo here: the server broadcasts the typed text back over the
// same WebSocket every connected client already listens on (mirroring how
// spoken input is echoed via transcription). One source of truth means a
// second open tab (or the phone, at the same time) sees it too, and it
// survives a reconnect instead of only ever existing in the sender's tab.
async function doSend() {
  const inp = document.getElementById('inp');
  const txt = inp.value.trim();
  if (!txt) return;
  if (doSend.pending) return;
  doSend.pending = true;
  inp.value = '';
  const enc  = _encrypt(txt);
  const body = enc ? { enc } : { text: txt };
  try {
    const response = await _authFetch('/api/local-chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || 'Lokale MIA-Antwort fehlgeschlagen.');
    append('jarvis', result.answer || 'Ich konnte darauf lokal keine Antwort erzeugen.');
  } catch {
    sys('Senden fehlgeschlagen — Text wiederhergestellt.');
    if (!inp.value) inp.value = txt;
  } finally {
    doSend.pending = false;
  }
}

function doWake() {
  sys('Sending wake signal…');
  _authFetch('/api/wake', { method: 'POST' });
}

document.getElementById('inp').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); }
});

// ── Toast ─────────────────────────────────────────────────────────────────
let _toastTimer;
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── Playback — JARVIS's spoken replies, streamed as PCM16 @ 24 kHz ───────
let _playCtx    = null;
let _playCursor = 0;      // ctx.currentTime cursor for gapless scheduling
let _playNodes  = [];     // scheduled sources, so audio_stop can cut them off

function _ensurePlayCtx() {
  if (!_playCtx) {
    try { _playCtx = new AudioContext({ sampleRate: 24000 }); }
    catch (_) { _playCtx = new AudioContext(); }
  }
  if (_playCtx.state === 'suspended') _playCtx.resume().catch(() => {});
  return _playCtx;
}

function _playPcm16(b64, rate) {
  const raw  = atob(b64);
  const buf  = new ArrayBuffer(raw.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) view[i] = raw.charCodeAt(i);
  const pcm = new Int16Array(buf);
  if (!pcm.length) return;
  _miaSpoke(); _S.setLevel(_pcmLevel(pcm));      // core pulses with the real output level
  if (!_soundOn) return;
  const ctx  = _ensurePlayCtx();

  const audioBuf = ctx.createBuffer(1, pcm.length, rate || 24000);
  const ch = audioBuf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;

  const src = ctx.createBufferSource();
  src.buffer = audioBuf;
  src.connect(ctx.destination);

  const startAt = Math.max(ctx.currentTime, _playCursor);
  src.start(startAt);
  _playCursor = startAt + audioBuf.duration;
  _playNodes.push(src);
  src.onended = () => { _playNodes = _playNodes.filter(n => n !== src); };
}

function _stopPlayback() {
  for (const n of _playNodes) { try { n.stop(); } catch (_) {} }
  _playNodes = [];
  if (_playCtx) _playCursor = _playCtx.currentTime;
}

// ── Incoming call — JARVIS wants to talk on its own initiative and
// nobody's mic is open to hear it ─────────────────────────────────────────
const callBanner = document.getElementById('callBanner');
let _callTimer = null;

function _showIncomingCall() {
  if (_voiceWs) return;   // already on the line — nothing to announce
  callBanner.classList.add('show');
  _warn('call', true);
  toast(_ASSISTANT + ' ruft an');
  clearTimeout(_callTimer);
  _callTimer = setTimeout(_dismissCall, 30000);   // missed call after 30s
}

function _dismissCall() {
  _warn('call', false);
  callBanner.classList.remove('show');
  clearTimeout(_callTimer);
}

document.getElementById('cb-answer').addEventListener('click', () => {
  _ensurePlayCtx();   // unlock playback on this user gesture
  _dismissCall();
  doMic();
});
document.getElementById('cb-decline').addEventListener('click', _dismissCall);

// ── Confirmation — an irreversible action is waiting for approval ────────
// core/confirm.py's HUD gate has nobody to render to on a headless
// server; without this it just times out unanswered. This mirrors it
// over the dashboard so any connected device can approve or reject.
const confirmBanner = document.getElementById('confirmBanner');

function _showConfirm(title, detail) {
  document.getElementById('cf-title').textContent = title || '';
  document.getElementById('cf-detail').textContent = detail || '';
  confirmBanner.classList.add('show');
  toast('⚠ Bestätigung nötig');
}

function _hideConfirm() {
  confirmBanner.classList.remove('show');
}

async function _decideConfirm(accepted) {
  _hideConfirm();   // optimistic — confirm_resolved will also arrive via WS
  try {
    await _authFetch('/api/confirm/decide', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accepted }),
    });
  } catch { sys('Entscheidung konnte nicht gesendet werden.'); }
}

document.getElementById('cf-approve').addEventListener('click', () => _decideConfirm(true));
document.getElementById('cf-reject').addEventListener('click', () => _decideConfirm(false));

// Catch a confirmation that was already pending before this page loaded
// (e.g. it fired while nobody had the dashboard open).
_authFetch('/api/confirm/pending').then(r => r.json()).then(d => {
  if (d && d.title) _showConfirm(d.title, d.detail);
}).catch(() => {});

// ── Real-time Voice — PCM16 WebSocket → Gemini Live ──────────────────────
const micBtn  = document.getElementById('mic-btn');
let _voiceWs  = null;
let _audioCtx = null;
let _micStm   = null;
let _audioNd  = null;

function _micIdle() {
  if (_isDesktopDashboard) {
    micBtn.textContent = 'Dialog starten';
    micBtn.title = 'Dialog-Sitzung starten';
  } else {
    micBtn.innerHTML = '🎤';
    micBtn.title = 'Voice — tap to speak';
  }
  micBtn.classList.remove('recording');
  _S.set('listening', false);
}
_micIdle();

// Float32 PCM → resample to 16 kHz + convert to Int16.
// Picking every Nth sample (the old approach) aliases high-frequency
// content back into the speech band as noise — audible distortion that
// measurably hurts recognition. A box-filter (cheap anti-aliasing
// low-pass, sized to the downsample ratio) followed by linear
// interpolation is a large quality step up for negligible CPU cost.
// Both the filter's lookback and the fractional resample position are
// carried across calls: resetting them every AudioWorklet callback
// (every ~128 samples) reintroduces a periodic block-boundary artifact,
// verified via a Node test (RMSE 0.79 vs. 0.0 against an unchunked
// reference resample of the same signal).
function _makePcm16Resampler(srcRate) {
  if (srcRate === 16000) {
    return input => {
      const out = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++)
        out[i] = Math.max(-32768, Math.min(32767, Math.round(input[i] * 32768)));
      return out.buffer;
    };
  }
  const ratio = srcRate / 16000;
  const filterLen = Math.max(1, Math.round(ratio));
  let history = new Float32Array(filterLen - 1);
  let carry = new Float32Array(0);
  let position = 0;
  return input => {
    const extended = new Float32Array(history.length + input.length);
    extended.set(history); extended.set(input, history.length);
    const filtered = new Float32Array(input.length);
    let sum = 0;
    for (let i = 0; i < filterLen; i++) sum += extended[i] || 0;
    for (let i = 0; i < input.length; i++) {
      filtered[i] = sum / filterLen;
      sum += (extended[i + filterLen] || 0) - extended[i];
    }
    history = extended.slice(extended.length - (filterLen - 1));

    const samples = new Float32Array(carry.length + filtered.length);
    samples.set(carry); samples.set(filtered, carry.length);
    const output = [];
    while (position + 1 < samples.length) {
      const index = Math.floor(position), fraction = position - index;
      const value = samples[index] + fraction * (samples[index + 1] - samples[index]);
      output.push(Math.max(-32768, Math.min(32767, Math.round(value * 32768))));
      position += ratio;
    }
    const consumed = Math.min(samples.length, Math.floor(position));
    carry = samples.slice(consumed); position -= consumed;
    return new Int16Array(output).buffer;
  };
}

function _showVoiceSetup() {
  if (document.getElementById('voice-setup')) return; // show once
  const origin = location.origin;
  const card = document.createElement('div');
  card.id = 'voice-setup';
  card.style.cssText = [
    'align-self:stretch', 'background:rgba(255,255,255,0.05)',
    'border:1px solid rgba(255,255,255,0.14)', 'border-radius:16px',
    'padding:16px 18px', 'font-size:13px', 'line-height:1.8',
    'animation:pop 0.18s ease'
  ].join(';');
  card.innerHTML =
    '<div style="font-weight:700;color:#f3f4f6;letter-spacing:.5px;margin-bottom:10px">🎤 One-time voice setup</div>' +
    '<ol style="padding-left:18px;color:#9ca3af;margin:0;display:flex;flex-direction:column;gap:4px">' +
    '<li>Open a new tab → go to <span style="background:rgba(255,255,255,0.07);padding:1px 6px;border-radius:4px;font-family:monospace;font-size:12px">chrome://flags</span></li>' +
    '<li>Search: <strong style="color:#f3f2f7">Insecure origins treated as secure</strong></li>' +
    '<li>Paste this URL into the box:<br><span id="flags-url" style="background:rgba(255,255,255,0.07);padding:2px 8px;border-radius:4px;font-family:monospace;font-size:11px;user-select:all;cursor:pointer">' + esc(origin) + '</span></li>' +
    '<li>Set to <strong style="color:#f3f4f6">Enabled</strong> → tap <strong style="color:#f3f2f7">Relaunch</strong></li>' +
    '<li>Return here and tap 🎤</li>' +
    '</ol>';
  feed.appendChild(card);
  feed.scrollTop = feed.scrollHeight;
  // tap to copy
  card.querySelector('#flags-url').addEventListener('click', () => {
    navigator.clipboard?.writeText(origin).then(() => toast('URL copied!')).catch(() => {});
  });
}

async function doMic() {
  if (_voiceWs) { _stopVoice(); return; }

  if (!navigator.mediaDevices?.getUserMedia) {
    _showVoiceSetup();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    sys(e.name === 'NotAllowedError'
      ? 'Microphone denied — tap the address bar lock and allow microphone.'
      : 'Mic error: ' + e.message);
    return;
  }

  // 16 kHz context (fall back to native rate — the resampler handles it)
  let ctx;
  try { ctx = new AudioContext({ sampleRate: 16000 }); }
  catch (_) { ctx = new AudioContext(); }
  if (ctx.state === 'suspended') await ctx.resume();

  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(
    `${wsProto}://${location.host}${_wsPath('/ws/phone-audio')}?token=${encodeURIComponent(_authToken)}`
  );
  ws.binaryType = 'arraybuffer';

  ws.onopen = async () => {
    const rate = ctx.sampleRate;
    const resample = _makePcm16Resampler(rate);
    const src  = ctx.createMediaStreamSource(stream);

    // AudioWorklet (modern) — runs in dedicated audio thread
    const wCode = `class J extends AudioWorkletProcessor{process(i){const c=i[0]?.[0];if(c)this.port.postMessage(c.slice());return true;}}registerProcessor('j',J);`;
    try {
      const burl = URL.createObjectURL(new Blob([wCode], { type: 'application/javascript' }));
      await ctx.audioWorklet.addModule(burl);
      URL.revokeObjectURL(burl);
      const nd = new AudioWorkletNode(ctx, 'j');
      // Buffer to 1024 samples (64 ms at 16 kHz) before sending —
      // matches PC mic chunk size, prevents queue flooding on the server.
      let _pbuf = [], _plen = 0;
      nd.port.onmessage = e => {
        let sq = 0; for (let k = 0; k < e.data.length; k += 4) sq += e.data[k] * e.data[k];
        _S.setLevel(Math.min(1, Math.sqrt(sq / (e.data.length / 4 || 1)) * 5));   // real mic level
        const chunk = new Int16Array(resample(e.data));
        _pbuf.push(chunk); _plen += chunk.length;
        if (_plen >= 1024) {
          const out = new Int16Array(_plen); let off = 0;
          for (const c of _pbuf) { out.set(c, off); off += c.length; }
          if (ws.readyState === 1) ws.send(out.buffer);
          _pbuf = []; _plen = 0;
        }
      };
      src.connect(nd);
      _audioNd = nd;
    } catch (_) {
      // ScriptProcessor fallback (deprecated, still works everywhere)
      const sp = ctx.createScriptProcessor(4096, 1, 1);
      sp.onaudioprocess = e => {
        if (ws.readyState === 1) ws.send(resample(e.inputBuffer.getChannelData(0)));
      };
      src.connect(sp); sp.connect(ctx.destination);
      _audioNd = sp;
    }

    if (_isDesktopDashboard) {
      micBtn.textContent = 'Dialog beenden';
      micBtn.title = 'Dialog-Sitzung beenden';
    } else {
      micBtn.innerHTML = '⏹';
      micBtn.title = 'Tap to stop';
    }
    micBtn.classList.add('recording');
    _S.set('listening', true);
    sys(_isDesktopDashboard ? 'Dialog-Sitzung aktiv — du kannst frei sprechen.' : 'Voice live — speak now');
    toast(_isDesktopDashboard ? 'Dialog aktiv' : '🎤 Live');
  };

  ws.onclose = () => { _stopVoice(); };
  ws.onerror = () => { sys('Voice connection failed.'); _stopVoice(); if (window.MiaUI) MiaUI.notify('Sprachverbindung fehlgeschlagen.', 'error'); };

  _voiceWs  = ws;
  _audioCtx = ctx;
  _micStm   = stream;
}

function _stopVoice() {
  const wasOpen = !!_voiceWs;
  if (_audioNd)  { try { _audioNd.disconnect(); }  catch (_) {}; _audioNd  = null; }
  if (_audioCtx) { try { _audioCtx.close(); }      catch (_) {}; _audioCtx = null; }
  if (_micStm)   { _micStm.getTracks().forEach(t => t.stop()); _micStm = null; }
  if (_voiceWs)  {
    const w = _voiceWs; _voiceWs = null;
    if (w.readyState < 2) w.close();
  }
  _micIdle();
  if (_isDesktopDashboard && wasOpen) {
    sys('Dialog-Sitzung beendet.');
    toast('Dialog beendet');
  }
}

// ── Companion: next calendar event + optional ETA ─────────────────────────
// Silently does nothing if the server has no companion-calendar.json
// configured (companionRefresh's fetch just returns non-ok and the card
// stays hidden) — no separate feature flag needed.
let _companionEvent = null;
let _locationWatchId = null;
let _lastPosition = null;
let _locationEtaTimer = null;

async function companionRefresh() {
  const box = document.getElementById('companion');
  _S.set('syncing', true, 1500);
  try {
    const r = await _authFetch('/api/companion/calendar');
    if (!r.ok) { box.classList.remove('show'); return; }
    const data = await r.json();
    const now  = new Date();
    const next = (data.events || [])
      .filter(e => !e.allDay && new Date(e.start) > now)
      .sort((a, b) => new Date(a.start) - new Date(b.start))[0];
    if (!next) {
      box.classList.remove('show'); _companionEvent = null;
      if (_locationWatchId !== null) _companionStopLocation('Standort pausiert — kein anstehender Termin.');
      return;
    }
    _companionEvent = next;
    document.getElementById('companion-title').textContent = next.title;
    const mins = Math.round((new Date(next.start) - now) / 60000);
    document.getElementById('companion-when').textContent =
      mins >= 60 ? `in ${Math.floor(mins / 60)}h ${mins % 60}m` : `in ${mins}m`;
    const etaEl = document.getElementById('companion-eta');
    const toggleBtn = document.getElementById('companion-location-toggle');
    if (next.location) {
      if (!_locationWatchId) { etaEl.textContent = ''; etaEl.className = 'companion-eta'; }
      toggleBtn.style.display = 'inline-block';
    } else {
      etaEl.textContent = 'Kein Ort für diesen Termin hinterlegt.';
      etaEl.className = 'companion-eta';
      toggleBtn.style.display = 'none';
      if (_locationWatchId !== null) _companionStopLocation('Standort pausiert — kein Ort hinterlegt.');
    }
    box.classList.add('show');
    document.dispatchEvent(new CustomEvent('mia:companion'));
  } catch (_) { box.classList.remove('show'); }
}

function _companionRenderEta(d) {
  const el = document.getElementById('companion-eta');
  if (d.error) { el.textContent = d.error; el.className = 'companion-eta'; _warn('eta', false); document.dispatchEvent(new CustomEvent('mia:companion')); return; }
  el.textContent = `${d.destination}: ${d.durationMinutes} Min Fahrt · ${d.uncertainty}`;
  el.className = 'companion-eta' +
    (d.level === 'spaet' ? ' warn' : (d.level === 'los' || d.level === 'bald') ? ' soon' : '');
  _warn('eta', d.level === 'spaet');           // urgent appointment → warning intensity on the core
  document.dispatchEvent(new CustomEvent('mia:companion'));
}

function _companionLocStatus(text) {
  const el = document.getElementById('companion-loc-status');
  if (el) el.textContent = text;
}

// Live standort: kontinuierliches watchPosition statt einmaliger Abfrage —
// Status ist jederzeit sichtbar (Button + Statuszeile) und der Nutzer kann
// jederzeit pausieren; wird außerdem automatisch pausiert, sobald die App
// in den Hintergrund geht (siehe visibilitychange/pagehide unten).
function companionToggleLocation() {
  if (_locationWatchId !== null) { _companionStopLocation('Live-Standort pausiert.'); return; }
  if (!navigator.geolocation || !isSecureContext) {
    _companionLocStatus('Standort hier nicht verfügbar — HTTPS und Berechtigung prüfen.');
    return;
  }
  _companionLocStatus('Standortfreigabe wird angefragt …');
  _locationWatchId = navigator.geolocation.watchPosition(
    pos => {
      _lastPosition = pos;
      _companionLocStatus(
        `Live-Standort aktiv · erfasst ${new Date(pos.timestamp).toLocaleTimeString('de-DE')} · Genauigkeit ca. ${Math.round(pos.coords.accuracy)} m`
      );
      _companionCheckEta();
    },
    () => _companionStopLocation('Standort nicht verfügbar oder nicht freigegeben.'),
    { enableHighAccuracy: false, maximumAge: 60000, timeout: 15000 }
  );
  const btn = document.getElementById('companion-location-toggle');
  btn.textContent = '📍 Live-Standort pausieren';
  btn.classList.add('live');
  if (_locationEtaTimer) clearInterval(_locationEtaTimer);
  _locationEtaTimer = setInterval(_companionCheckEta, 180000);
}

function _companionStopLocation(msg) {
  if (_locationWatchId !== null) navigator.geolocation.clearWatch(_locationWatchId);
  _locationWatchId = null;
  _lastPosition = null;
  if (_locationEtaTimer) { clearInterval(_locationEtaTimer); _locationEtaTimer = null; }
  const btn = document.getElementById('companion-location-toggle');
  if (btn) { btn.textContent = '📍 Live-Standort einschalten'; btn.classList.remove('live'); }
  _companionLocStatus(msg);
}

async function _companionCheckEta() {
  if (!_lastPosition || !_companionEvent) return;
  if (Date.now() - _lastPosition.timestamp > 180000) {
    _companionLocStatus('Standort veraltet — warte auf neue Erfassung.');
    return;
  }
  const etaEl = document.getElementById('companion-eta');
  etaEl.textContent = 'Fahrzeit wird geprüft …';
  const body = {
    eventId: _companionEvent.id,
    lat: _lastPosition.coords.latitude, lon: _lastPosition.coords.longitude,
    accuracy: _lastPosition.coords.accuracy, timestamp: _lastPosition.timestamp / 1000,
  };
  try {
    let r = await _authFetch('/api/companion/eta', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data = await r.json();
    if (data.needsConfirmation && data.destinations && data.destinations.length) {
      // Ambiguous address match — take the closest-named candidate rather
      // than showing a picker, keeping this card minimal; the full label
      // is still visible in the result if the user wants to double check.
      body.destinationId = data.destinations[0].id;
      r = await _authFetch('/api/companion/eta', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      data = await r.json();
    }
    _companionRenderEta(data);
  } catch (_) {
    etaEl.textContent = 'Prognose derzeit nicht möglich.';
  }
}

// Geräteberechtigungen bleiben sichtbar und sind jederzeit pausierbar:
// Mikrofon, Wiedergabe und Live-Standort werden beendet, sobald die App
// nicht mehr sichtbar ist oder geschlossen wird — kein stilles Tracking
// im Hintergrund.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) return;
  if (_voiceWs) _stopVoice();
  _stopPlayback();
  if (_locationWatchId !== null) _companionStopLocation('Standort pausiert (App im Hintergrund).');
});
window.addEventListener('pagehide', () => {
  if (_voiceWs) _stopVoice();
  if (_locationWatchId !== null) _companionStopLocation('Standort pausiert.');
});

companionRefresh();
setInterval(companionRefresh, 120000);


// -- Tasks / calendar / notes / files panels (phone page; desktop renders its own views) -------------------------------------------------
(function installMvpPanels(){
  if (document.getElementById('mvp-shell') || document.body.dataset.layout === 'control-center') return;
  const style = document.createElement('style');
  style.textContent = `
    .mvp-shell{flex-shrink:0;border-bottom:1px solid var(--line-1);background:rgba(255,255,255,.03);-webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);padding:10px 14px;display:grid;gap:10px}
    .mvp-tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
    .mvp-tab{border:1px solid var(--line-1);background:var(--glass-2);color:var(--muted);border-radius:10px;padding:9px 4px;font-size:11px;font-weight:700;letter-spacing:.5px;min-height:40px}
    .mvp-tab.on{color:#fff;border-color:var(--line-3);background:var(--glass-3)}
    .mvp-pane{display:none;gap:8px}.mvp-pane.on{display:grid}.mvp-row{display:grid;grid-template-columns:1fr auto;gap:7px}
    .mvp-input{min-width:0;border:1px solid var(--line-2);background:var(--glass-2);color:var(--text);border-radius:10px;padding:10px 11px;font-size:16px}
    .mvp-add{border:1px solid var(--line-3);background:var(--glass-3);color:#fff;border-radius:10px;padding:0 14px;font-weight:800;min-width:44px}
    .mvp-list{display:grid;gap:6px;max-height:130px;overflow:auto}
    .mvp-item{border:1px solid var(--line-1);background:var(--glass-1);border-radius:10px;padding:8px 9px;font-size:12px;color:var(--text);display:flex;justify-content:space-between;gap:8px;text-align:left}
    a.mvp-item{text-decoration:none}.mvp-item small{color:var(--muted)}.mvp-item.done{opacity:.55;text-decoration:line-through}.mvp-item.flash{animation:flash 1.6s ease}
    .mvp-empty{color:var(--muted);font-size:12px;padding:6px 2px}`;
  document.head.appendChild(style);
  const shell=document.createElement('section');
  shell.id='mvp-shell';
  shell.className='mvp-shell';
  shell.innerHTML=`
    <div class="mvp-tabs"><button class="mvp-tab on" data-pane="tasks">Aufgaben</button><button class="mvp-tab" data-pane="calendar">Kalender</button><button class="mvp-tab" data-pane="notes">Notizen</button><button class="mvp-tab" data-pane="files">Dateien</button></div>
    <div class="mvp-pane on" id="mvp-pane-tasks"><div class="mvp-row"><input class="mvp-input" id="mvp-task-input" placeholder="Neue Aufgabe"><button class="mvp-add" data-add="task">+</button></div><div class="mvp-list" id="mvp-tasks"></div></div>
    <div class="mvp-pane" id="mvp-pane-calendar"><div class="mvp-row"><input class="mvp-input" id="mvp-event-input" placeholder="Termin oder Erinnerung"><button class="mvp-add" data-add="event">+</button></div><div class="mvp-list" id="mvp-events"></div></div>
    <div class="mvp-pane" id="mvp-pane-notes"><div class="mvp-row"><input class="mvp-input" id="mvp-note-input" placeholder="Notiz für ${_ASSISTANT}"><button class="mvp-add" data-add="note">+</button></div><div class="mvp-list" id="mvp-notes"></div></div>
    <div class="mvp-pane" id="mvp-pane-files"><div class="mvp-list" id="mvp-files"><div class="mvp-empty">Dateien ueber die Büroklammer hochladen.</div></div></div>`;
  const companion=document.getElementById('companion');
  companion?.insertAdjacentElement('afterend', shell);
  shell.querySelectorAll('.mvp-tab').forEach(btn=>btn.addEventListener('click',()=>{
    shell.querySelectorAll('.mvp-tab').forEach(b=>b.classList.toggle('on',b===btn));
    shell.querySelectorAll('.mvp-pane').forEach(p=>p.classList.toggle('on',p.id==='mvp-pane-'+btn.dataset.pane));
  }));
  shell.querySelectorAll('[data-add]').forEach(btn=>btn.addEventListener('click',()=>mvpAdd(btn.dataset.add)));
  mvpLoad(); setInterval(mvpLoad,30000);
})();
async function mvpLoad(){
  const out = { state: null, files: null, error: false };
  try{ const r=await _authFetch('/api/app/state'); if(r.ok){ out.state=await r.json(); mvpRender(out.state); } else out.error=true; }catch{ out.error=true; }
  try{ const r=await _authFetch('/api/files'); if(r.ok){ out.files=(await r.json()).files||[]; mvpRenderFiles(out.files); } else out.error=true; }catch{ out.error=true; }
  document.dispatchEvent(new CustomEvent('mia:data', { detail: out }));
}
function mvpRender(data){
  const render=(id,items,empty,task)=>{ const box=document.getElementById(id); if(!box)return; box.innerHTML=''; if(!items.length){box.innerHTML=`<div class="mvp-empty">${empty}</div>`;return;} items.slice(0,8).forEach(it=>{const d=document.createElement('div');d.className='mvp-item'+(it.done?' done':'');d.innerHTML=`<span>${esc(it.text||it.title||'')}</span>${task?'<small>tippen</small>':''}`; if(task)d.onclick=()=>mvpToggle(it.id); box.appendChild(d);}); };
  render('mvp-tasks',data.tasks||[],'Noch keine Aufgaben.',true); render('mvp-events',data.events||[],'Noch keine Termine.',false); render('mvp-notes',data.notes||[],'Noch keine Notizen.',false);
}
function mvpRenderFiles(files){ const box=document.getElementById('mvp-files'); if(!box)return; box.innerHTML=''; if(!files.length){box.innerHTML='<div class="mvp-empty">Dateien ueber die Büroklammer hochladen.</div>';return;} files.slice(0,8).forEach(f=>{const d=document.createElement('a');d.className='mvp-item';d.dataset.name=f.name;d.href=`${_basePath}/uploads/${encodeURIComponent(f.name)}?token=${encodeURIComponent(_authToken)}`;d.download=f.name;d.innerHTML=`<span>${esc(f.name)}</span><small>${_fmtSize(f.size||0)}</small>`;box.appendChild(d);}); }
async function mvpAdd(kind){ const map={task:['mvp-task-input','/api/app/tasks'],note:['mvp-note-input','/api/app/notes'],event:['mvp-event-input','/api/app/events']}; const [id,url]=map[kind]; const input=document.getElementById(id); const text=(input?.value||'').trim(); if(!text)return; input.value=''; await _authFetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})}); mvpLoad(); }
async function mvpToggle(id){ await _authFetch(`/api/app/tasks/${id}/toggle`,{method:'POST'}); mvpLoad(); }
