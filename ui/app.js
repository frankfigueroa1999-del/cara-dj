// Non Stop Pop DJ for Windows: the window. Everything it shows comes from Python (window.pywebview.api).
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const api = new Proxy({}, { get: (_, name) => (...args) => window.pywebview.api[name](...args) });
const sleep = ms => new Promise(r => setTimeout(r, ms));

const S = {
  boot: null, config: {}, state: null, home: null,
  route: { name: 'home', tab: 'home' }, history: [], future: [],
  tracks: new Map(),       // uri -> track (for menus and playing)
  lists: new Map(),        // list id -> [uris] (songs with no playlist behind them)
  liked: new Map(),        // uri -> liked?
  log: [], logNext: 0,
  np: { open: false, tab: 'lyrics', lyrics: null, lyricsKey: '', about: null, aboutKey: '', queue: null, queueKey: '' },
  search: { q: '', scope: 'all', results: null, loading: false, offset: 0, done: false, timer: 0 },
  lib: 'playlists',
  djSig: '', nowUri: '', artKey: '', connected: false,
  ui: loadUi(),            // panes, zoom and library filters, remembered on this PC
  nv: { key: '', mode: 'now', d: {}, lyricIdx: -2, video: null, failed: new Set() },
  libQuery: '', homeKind: 'all', lyricsCache: new Map(),
  feed: null,              // Home's mixes, stations, new releases... (made by Python from your listening)
  mixes: new Map(),        // mix id -> {at, value}
  mixNow: null,            // the mix playing now: {id, name, uris}
  listMix: new Map(),      // song list id -> the mix it shows
};

// ---------------------------------------------------------------- the layout you left it in
function loadUi() {
  const d = { libMini: false, libW: 300, nvOpen: true, nvW: 340, zoom: 100, libKind: '', libSort: 'recent', npOnPlay: true, bg: 'song' };
  try { return Object.assign(d, JSON.parse(localStorage.getItem('nsp-ui') || '{}')); } catch (e) { return d; }
}
function saveUi() { try { localStorage.setItem('nsp-ui', JSON.stringify(S.ui)); } catch (e) { /* a private window: fine */ } }
function applyUi() {
  const u = S.ui, b = document.body;
  u.libW = Math.max(240, Math.min(440, u.libW || 300));
  u.nvW = Math.max(290, Math.min(480, u.nvW || 340));
  if (!['song', 'black', 'white'].includes(u.bg)) u.bg = 'song';
  b.classList.toggle('lib-mini', !!u.libMini);
  b.classList.toggle('nv-mini', !u.nvOpen);
  b.classList.toggle('bg-black', u.bg === 'black');
  b.classList.toggle('light', u.bg === 'white');
  b.classList.toggle('bg-static', u.bg !== 'song');
  document.documentElement.style.setProperty('--lib-w', u.libW + 'px');
  document.documentElement.style.setProperty('--nv-w', u.nvW + 'px');
  document.documentElement.style.zoom = (u.zoom || 100) / 100;
  const lt = $('#lib-toggle'); if (lt) lt.title = u.libMini ? 'Expand Your Library' : 'Collapse Your Library';
  if (S.titleBg !== u.bg && window.pywebview && window.pywebview.api) { S.titleBg = u.bg; api.titlebar(u.bg); }
}
// Preferences > Background: the song's colours behind everything, or plain black, or white
function setBg(v) {
  S.ui.bg = v; saveUi(); applyUi();
  api.set_config({ ui_bg: v });                       // so the window opens in it next time
  if (S.route.name === 'prefs') refresh();
  toast({ song: "Background: your song's colours", black: 'Background: black', white: 'Background: white' }[v] || 'Saved', 'sparkles');
}
const zf = () => (S.ui.zoom || 100) / 100;          // page zoom: screen coordinates divide by this
function setZoom(z) { S.ui.zoom = Math.max(70, Math.min(130, Math.round(z / 10) * 10)); saveUi(); applyUi(); toast(`Zoom ${S.ui.zoom}%`, 'expand'); if (S.route.name === 'prefs') refresh(); }
function zoomBy(d) { setZoom((S.ui.zoom || 100) + d); }

// ---------------------------------------------------------------- little helpers
function fmtTime(ms) { const s = Math.max(0, Math.floor((ms || 0) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }
function fmtLength(ms) { const m = Math.max(0, Math.floor((ms || 0) / 60000)); return m >= 60 ? `${Math.floor(m / 60)} hr ${m % 60} min` : `${m} min`; }
function prettyDate(s) { if (!s || s.length < 10) return s || ''; const d = new Date(s + 'T12:00:00'); return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }); }
function plural(n, w) { return `${n.toLocaleString()} ${w}${n === 1 ? '' : 's'}`; }
function art(url, size, o = {}) {
  const st = size ? `style="width:${size}px;height:${size}px"` : '';
  const ph = `<div class="ph">${icon(o.icon || 'note', Math.max(16, Math.round((size || 120) * 0.36)))}</div>`;
  return `<div class="art ${o.circle ? 'circle' : ''} ${o.cls || ''}" ${st}>${url ? `<img src="${esc(url)}" loading="lazy" alt="" onload="this.classList.add('ok')" onerror="this.remove()">` : ''}${url ? '' : ph}</div>`;
}
function likedArt(size, radius = 8) { return `<div class="art liked-art" style="width:${size}px;height:${size}px;border-radius:${radius}px">${icon('heart', Math.round(size * 0.38))}</div>`; }
function remember(tracks) { for (const t of tracks || []) if (t && t.uri) S.tracks.set(t.uri, t); return tracks || []; }
let listSeq = 0;
function listOf(tracks) { const id = 'l' + (++listSeq); S.lists.set(id, (tracks || []).map(t => t.uri).filter(Boolean)); return id; }
function now() { return S.state && S.state.now; }
function nowTrack() { return now() && now().track; }
function nowUri() { const t = nowTrack(); return t ? t.uri : ''; }
function progressNow() {
  const n = now(); if (!n || !n.track) return 0;
  let p = n.progress || 0;
  if (n.playing) p += Date.now() - (n.stamp || Date.now());
  return Math.min(Math.max(0, p), n.track.duration || 0);
}
function greet() { const h = new Date().getHours(); return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'; }
function firstName() { const m = S.state && S.state.me; const n = (m && m.name) || (S.boot && S.boot.first) || ''; return n.split(' ')[0]; }
function myId() { const m = S.state && S.state.me; return m && m.id; }

let toastTimer = 0;
function toast(text, ic = 'check') {
  const t = $('#toast'); t.innerHTML = `${icon(ic, 18)}<span>${esc(text)}</span>`; t.classList.add('on');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

// ---------------------------------------------------------------- moving around
const TABS = ['home', 'cara', 'library', 'search'];
function go(route) {
  route.tab = route.tab || S.route.tab;
  S.route.scroll = $('#scroller').scrollTop;
  S.history.push(S.route); S.future = [];
  S.route = route; render(true);
}
function goBack() { if (!S.history.length) return; S.route.scroll = $('#scroller').scrollTop; S.future.push(S.route); S.route = S.history.pop(); render(true, true); }
function goForward() { if (!S.future.length) return; S.route.scroll = $('#scroller').scrollTop; S.history.push(S.route); S.route = S.future.pop(); render(true, true); }
function tab(name) {
  closeNP();
  if (S.route.name === name && !S.history.length) { $('#scroller').scrollTo({ top: 0, behavior: 'smooth' }); return; }
  S.route.scroll = $('#scroller').scrollTop;
  S.history.push(S.route); S.future = [];
  S.route = { name, tab: name }; render(true);
}

function render(fresh = false, restoring = false) {
  const r = S.route, v = VIEWS[r.name] || VIEWS.home;
  const page = $('#page');
  page.innerHTML = v.html(r);
  $('#main-head .title').textContent = v.title ? v.title(r) : '';
  $('#nav-back').disabled = !S.history.length;
  $('#nav-fwd').disabled = !S.future.length;
  $('#tb-home').classList.toggle('on', r.name === 'home');
  $('.tb-browse').classList.toggle('on', r.name === 'search' && !S.search.q.trim());
  const q = $('#search-input');
  if (document.activeElement !== q) q.value = r.name === 'search' ? S.search.q : '';
  $('#tb-clear').hidden = !(r.name === 'search' && S.search.q);
  markLibrary();
  if (fresh) {
    page.style.animation = 'none'; void page.offsetWidth; page.style.animation = '';
    $('#scroller').scrollTop = restoring ? (r.scroll || 0) : 0;
  }
  afterRender();
  if (v.load && !r.loading && !r.loaded) {
    r.loading = true;
    v.load(r).catch(e => console.error(e)).finally(() => { r.loading = false; r.loaded = true; if (S.route === r) refresh(); });
  }
}
function refresh() {
  const sc = $('#scroller').scrollTop;
  const v = VIEWS[S.route.name] || VIEWS.home;
  $('#page').innerHTML = v.html(S.route);
  $('#scroller').scrollTop = sc;
  afterRender();
}
function routeKey(r) { return r.name + ':' + (r.id || ''); }
function afterRender() {
  markNow();
  const log = $('#log'); if (log) { log.textContent = S.log.join('\n'); log.scrollTop = log.scrollHeight; }
  $$('input[type=range]').forEach(paintRange);
  const q = $('#search-input'); if (q && S.route.name === 'search' && document.activeElement !== q && S.route.focus) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); S.route.focus = false; }
  if (S.route.name === 'cara' && S.route.focusLog) { const l = $('#log'); if (l) l.scrollIntoView({ block: 'center' }); S.route.focusLog = false; }
}
function paintRange(el) { const p = ((el.value - (el.min || 0)) / ((el.max || 100) - (el.min || 0))) * 100; el.style.setProperty('--p', p + '%'); }

// the songs playing now get the accent colour and the little equaliser
function markNow() {
  const uri = nowUri(), playing = !!(now() && now().playing);
  $$('[data-row]').forEach(el => {
    const on = !!uri && el.dataset.uri === uri;
    el.classList.toggle('now', on);
    const n = el.querySelector('.n span');
    if (n) n.innerHTML = on ? `<span class="eq ${playing ? 'on' : ''}"><i></i><i></i><i></i></span>` : esc(n.dataset.n || '');
  });
  const ctx = now() && now().context;
  $$('.card [data-ctx]').forEach(b => {
    const on = !!ctx && b.dataset.ctx === ctx && playing;
    b.classList.toggle('playing', on); b.innerHTML = icon(on ? 'pause' : 'play', 22);
  });
  const mx = S.mixNow && S.mixNow.uris.has(uri) ? S.mixNow.id : '';
  $$('[data-mix].hover-play, .big-play[data-mix]').forEach(b => {
    const on = !!mx && b.dataset.mix === mx && playing;
    b.classList.toggle('playing', on); b.innerHTML = icon(on ? 'pause' : 'play', b.classList.contains('big-play') ? 28 : 22);
  });
  $$('.shortcut').forEach(el => {
    const b = el.querySelector('.sp'), c = el.dataset.ctx, here = !!ctx && !!c && c === ctx, on = here && playing;
    el.classList.toggle('now', here);
    if (b) { b.classList.toggle('playing', on); b.innerHTML = icon(on ? 'pause' : 'play', 18); }
  });
}

// ---------------------------------------------------------------- shared pieces
function rowOf(items, cardSize = 184) {
  return `<div class="row-wrap" style="--card:${cardSize}px"><button class="row-arrow l" data-act="row-scroll" data-dir="-1">${icon('back')}</button>
    <div class="row hide-scroll">${items.join('')}</div>
    <button class="row-arrow r" data-act="row-scroll" data-dir="1">${icon('forward')}</button></div>`;
}
function head(title, sub = '', moreAct = '') {
  return `<div class="section-head"><div><h2>${esc(title)}</h2>${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</div>${moreAct ? `<button class="more" ${moreAct}>Show all</button>` : ''}</div>`;
}
function albumCard(a, sub) {
  return `<div class="card" data-act="open-album" data-id="${esc(a.id)}"><div class="cover">${art(a.artMid || a.art)}
    <button class="hover-play" data-act="play-ctx" data-ctx="${esc(a.uri)}" title="Play">${icon('play', 22)}</button></div>
    <div><b class="ell">${esc(a.name)}</b><small class="ell">${esc(sub ?? a.artist)}</small></div></div>`;
}
function playlistCard(p) {
  return `<div class="card" data-act="open-playlist" data-id="${esc(p.id)}"><div class="cover">${art(p.imageMid || p.image, 0, { icon: 'queue' })}
    <button class="hover-play" data-act="play-ctx" data-ctx="${esc(p.uri)}" title="Play">${icon('play', 22)}</button></div>
    <div><b class="ell">${esc(p.name)}</b><small class="ell">${esc(p.owner ? 'By ' + p.owner : 'Playlist')}</small></div></div>`;
}
function artistCard(a) {
  return `<div class="card circle" data-act="open-artist" data-id="${esc(a.id)}"><div class="cover">${art(a.imageMid || a.image, 0, { circle: true, icon: 'person' })}
    <button class="hover-play" data-act="play-ctx" data-ctx="${esc(a.uri)}" title="Play">${icon('play', 22)}</button></div>
    <div style="text-align:center"><b class="ell">${esc(a.name)}</b><small>Artist</small></div></div>`;
}
function likedCard() {
  const h = S.home;
  return `<div class="card" data-act="open-liked"><div class="cover"><div class="art liked-art" style="width:100%;height:100%;border-radius:12px">${icon('heart', 60)}</div>
    <button class="hover-play" data-act="play-liked" title="Play">${icon('play', 22)}</button></div>
    <div><b>Liked Songs</b><small>${h ? plural(h.likedTotal || 0, 'song') : 'Your favourites'}</small></div></div>`;
}
function artistLinks(t) {
  const list = (t.artists && t.artists.length) ? t.artists : [{ id: '', name: t.artist }];
  return list.map(a => a.id ? `<button data-act="open-artist" data-id="${esc(a.id)}">${esc(a.name)}</button>` : esc(a.name)).join(', ');
}
// a list of songs, like the desktop Spotify / Apple Music. Double-click (or the play button) plays.
function trackTable(tracks, o = {}) {
  remember(tracks);
  const list = o.list || (o.ctx ? '' : listOf(tracks));
  const albumCol = o.album !== false;
  const rows = tracks.map((t, i) => {
    const liked = S.liked.get(t.uri);
    const n = o.numbers ? (t.number || i + 1) : i + 1;
    return `<div class="tr ${albumCol ? '' : 'no-album'}" data-row data-uri="${esc(t.uri)}" data-ctx="${esc(o.ctx || '')}" data-list="${list}" data-i="${i}" data-act="select-row">
      <div class="n"><span data-n="${n}">${n}</span><button class="play" data-act="play-row" title="Play">${icon('play', 16)}</button></div>
      <div class="t">${o.numbers ? '' : art(t.artMid || t.art, 42)}<div class="ell"><b class="ell">${esc(t.title)}</b><small class="ell">${t.explicit ? '<span class="badge-e">E</span>' : ''}<span class="ell">${artistLinks(t)}</span></small></div></div>
      ${albumCol ? `<div class="ell"><button class="al ell" data-act="open-album" data-id="${esc(t.albumId)}">${esc(t.album)}</button></div>` : ''}
      <div class="x"><button class="icon-btn ${liked ? 'liked' : ''}" data-act="like" data-uri="${esc(t.uri)}" title="${liked ? 'Remove from Liked Songs' : 'Add to Liked Songs'}">${icon(liked ? 'heart' : 'heartOutline', 18)}</button></div>
      <div class="d"><span>${fmtTime(t.duration)}</span><button class="icon-btn" data-act="track-menu" data-uri="${esc(t.uri)}" title="More">${icon('more', 18)}</button></div>
    </div>`;
  }).join('');
  return `<div class="tracks">${o.header === false ? '' : `<div class="tr head ${albumCol ? '' : 'no-album'}"><div class="n">#</div><div>Title</div>${albumCol ? '<div>Album</div>' : ''}<div></div><div class="d">${icon('moon', 0) && ''}Time</div></div>`}${rows}</div>`;
}
function songGrid(tracks, station) {
  remember(tracks);
  const list = listOf(tracks);
  return `<div class="song-grid">${tracks.map((t, i) => `<div class="mini-song" data-row data-uri="${esc(t.uri)}" data-list="${list}" data-i="${i}" data-name="${esc(station || '')}" data-act="play-row" role="button">
    ${art(t.artMid || t.art, 48)}<div class="ell"><b class="ell">${esc(t.title)}</b><small class="ell">${esc(t.artistLine || t.artist)}</small></div>
    <button class="icon-btn" data-act="track-menu" data-uri="${esc(t.uri)}">${icon('more', 18)}</button></div>`).join('')}</div>`;
}
function loading() { return `<div class="loading"><div class="spinner"></div></div>`; }
function empty(ic, title, text, btn = '') { return `<div class="empty">${icon(ic, 46)}<b>${esc(title)}</b><div>${esc(text)}</div>${btn}</div>`; }
async function checkLiked(tracks) {
  const uris = (tracks || []).map(t => t.uri).filter(u => u && !S.liked.has(u));
  for (let i = 0; i < uris.length; i += 40) {
    const chunk = uris.slice(i, i + 40);
    const got = await api.contains(chunk);
    if (Array.isArray(got)) chunk.forEach((u, k) => S.liked.set(u, !!got[k]));
  }
}

// ---------------------------------------------------------------- Cara's card (Home and her page)
function quoteHTML(d) {
  if (d && d.kind === 'duo' && d.duo && d.duo.length) {
    return `<div class="duo">${d.duo.map(r => `<div><b>${esc(r.who)}</b>${esc(r.text)}</div>`).join('')}</div>`;
  }
  if (d && d.line) return `&ldquo;${esc(d.line.replace(/\[[^\]]*\]/g, '').trim())}&rdquo;`;
  return `<span style="font-style:normal" class="muted">Your own radio host. She talks between your songs about your town, the news, the music, and you.</span>`;
}
function heroHTML() {
  const d = (S.state && S.state.dj) || {};
  const live = !!d.running;
  return `<div class="hero ${live ? 'live' : ''}" data-live="hero">
    <div class="tile">${bars(live)}</div>
    <div class="who"><div class="caps">${esc(d.stationFull || 'Non Stop Pop FM')}</div><h2>Cara</h2><div class="status">${esc(d.status || 'Off air')}</div></div>
    <div>${live ? `<span class="live-badge"><i></i>${d.speaking ? 'ON AIR' : d.stopping ? 'SIGNING OFF' : 'LIVE'}</span>` : ''}</div>
    <div class="quote">${quoteHTML(d)}</div>
    <div class="actions">
      <button class="pill ${live ? 'secondary' : 'primary'}" data-act="dj-toggle">${icon(live ? 'stop' : 'radio', 18)}${live ? 'End Show' : 'Go Live'}</button>
      ${S.route.name === 'cara' ? '' : `<button class="pill secondary" data-act="tab" data-tab="cara">Her Settings</button>`}
      <button class="pill secondary" data-act="vis-open">${icon('sparkles', 18)}Visualizer</button>
    </div></div>`;
}
function updateHero() {
  const d = (S.state && S.state.dj) || {};
  const sig = JSON.stringify([d.running, d.speaking, d.stopping, d.status, d.line, d.kind, d.stationFull, d.queued, S.route.name]);
  if (sig === S.djSig) return;
  S.djSig = sig;
  $$('[data-live=hero]').forEach(el => { el.outerHTML = heroHTML(); });
  $$('[data-live=queue]').forEach(el => { el.outerHTML = queueSeg(); });
  $$('[data-live=dj]').forEach(el => { el.outerHTML = djCard(); });
  const live = !!d.running, dot = $('#tb-onair');
  dot.hidden = !live; dot.classList.toggle('speaking', !!d.speaking);
  $('#tb-menu').title = live ? (d.speaking ? 'Menu (Cara is on the mic)' : `Menu (Cara is live: ${d.status || 'on air'})`) : 'Menu';
}
function banners() {
  const s = S.state || {};
  if (!s.keys) return `<div class="banner glass">${'<div class="ic">' + icon('radio') + '</div>'}<div class="txt"><b>Connect Spotify</b><span>Add your Spotify app's Client ID and Secret in Settings. Your music plays through the Spotify app; Cara talks over it from here.</span></div><button class="pill primary small" data-act="settings">Open Settings</button></div>`;
  if (s.connecting) return `<div class="banner glass"><div class="ic"><div class="spinner" style="width:20px;height:20px"></div></div><div class="txt"><b>Connecting to Spotify…</b><span>The first time, a browser tab opens: click Agree, then come back here.</span></div></div>`;
  if (!s.connected) return `<div class="banner glass warn"><div class="ic">${icon('warning')}</div><div class="txt"><b>Spotify isn't connected</b><span>${esc(s.problem || 'Press Connect to log in.')}</span></div>${s.problemKind === 'unregistered' ? '<button class="pill primary small" data-act="pref-reconnect">Sign in again</button><button class="pill secondary small" data-act="open-url" data-url="https://developer.spotify.com/dashboard">Open dashboard</button>' : '<button class="pill primary small" data-act="connect">Connect</button>'}<button class="pill secondary small" data-act="settings">Settings</button></div>`;
  if (s.slow > 0) return `<div class="banner glass warn"><div class="ic">${icon('warning')}</div><div class="txt"><b>Spotify asked the app to slow down</b><span>Too many requests reached your Spotify developer app (everyone using the same Client ID counts together). The app waits, then tries again in ${s.slow < 90 ? s.slow + ' seconds' : 'about ' + Math.ceil(s.slow / 60) + ' minutes'}. Music that's already playing in the app keeps playing.</span></div></div>`;
  const pl = s.player || {}, nothing = !(s.now && s.now.track);
  if (pl.mode === 'app' && nothing && pl.status === 'starting') return `<div class="banner glass"><div class="ic"><div class="spinner" style="width:20px;height:20px"></div></div><div class="txt"><b>Starting the built-in player…</b><span>${esc(pl.problem || 'Your music plays right here in the app, no Spotify app needed.')}</span></div></div>`;
  if (pl.mode === 'app' && (pl.status === 'error' || (nothing && pl.status === 'premium'))) return `<div class="banner glass warn"><div class="ic">${icon('warning')}</div><div class="txt"><b>The built-in player couldn't start</b><span>${esc(pl.problem)}</span></div><button class="pill secondary small" data-act="player-mode" data-mode="spotify">Use the Spotify app</button>${pl.status === 'error' ? '<button class="pill primary small" data-act="player-retry">Try again</button>' : ''}</div>`;
  if (s.hint === 'no-device' && !(pl.mode === 'app' && pl.ready)) return `<div class="banner glass warn"><div class="ic">${icon('speaker')}</div><div class="txt"><b>Spotify isn't open</b><span>Open the Spotify app on this PC and play any song once. Then everything here works.</span></div><button class="pill primary small" data-act="open-spotify">Open Spotify</button></div>`;
  return '';
}

// ---------------------------------------------------------------- the pages
const VIEWS = {};

VIEWS.home = {
  title: () => 'Home',
  html: () => {
    const h = S.home, f = S.feed, k = S.homeKind || 'all';
    const chips = [['all', 'All'], ['playlists', 'Playlists'], ['albums', 'Albums'], ['artists', 'Artists']]
      .map(([v, l]) => `<button class="chip ${k === v ? 'on' : ''}" data-act="home-kind" data-kind="${v}">${l}</button>`).join('');
    let out = `${banners()}<div class="home-chips">${chips}</div>`;
    if (!h) return out + (S.state && S.state.connected ? loading() : '');
    const all = k === 'all', pl = all || k === 'playlists', al = all || k === 'albums', ar = all || k === 'artists';
    const want = x => all || x.kind + 's' === k || (k === 'playlists' && x.kind === 'liked');
    const sc = (h.shortcuts || []).filter(want);
    if (sc.length) out += `<div class="shortcuts">${sc.map(shortcutTile).join('')}</div>`;
    const block = (title, body, o = {}) => `<section class="block">${head(title, o.sub || '', o.more || '')}${body}</section>`;
    const me = (S.state && S.state.me && S.state.me.name) || firstName() || 'you';
    if (pl) {
      if (f && f.made && f.made.length) out += block(`Made For ${me}`, rowOf([djCard(), ...f.made.map(mixCard)]));
      else if (!f) out += block(`Made For ${me}`, skeletonRow());
      if (f && f.timed && f.timed.length) out += block(`Soundtrack your ${f.day} ${f.part}`, rowOf(f.timed.map(mixCard)));
    }
    const rec = ((f && f.recents) || []).filter(want);
    if (rec.length) out += block('Recents', rowOf(rec.map(ctxCard)));
    if (al && f && f.newReleases && f.newReleases.length) out += block('New releases for you', rowOf(f.newReleases.map(a => albumCard(a, `${a.type} • ${a.artist}`))));
    if (al) {
      const shown = new Set(rec.filter(x => x.kind === 'album').map(x => x.id));
      const back = (h.recentAlbums || []).filter(a => !shown.has(a.id));
      if (back.length) out += block('Jump back in', rowOf(back.map(a => albumCard(a))));
    }
    if (pl && f && f.topMixes && f.topMixes.length) out += block('Your top mixes', rowOf(f.topMixes.map(mixCard)));
    if (pl && h.playlists && h.playlists.length) out += block('Your playlists', rowOf([likedCard(), ...h.playlists.slice(0, 20).map(playlistCard)]), { more: 'data-act="lib" data-kind="playlists"' });
    const ml = (f && f.moreLike) || [];
    if (ar && ml[0]) out += moreLike(ml[0]);
    if ((pl || ar) && f && f.stations && f.stations.length) out += block('Recommended Stations', rowOf(f.stations.map(mixCard)));
    if (ar && ml[1]) out += moreLike(ml[1]);
    if (pl && f && f.big && f.big.length) out += `<section class="block"><div class="row-wrap big-row"><button class="row-arrow l" data-act="row-scroll" data-dir="-1">${icon('back')}</button><div class="row big-grid hide-scroll">${f.big.map(bigCard).join('')}</div><button class="row-arrow r" data-act="row-scroll" data-dir="1">${icon('forward')}</button></div></section>`;
    if (all && h.topTracks && h.topTracks.length) out += block('Your top songs', songGrid(h.topTracks.slice(0, 12), 'Your top songs'), { sub: 'Your most played lately' });
    if (ar && h.topArtists && h.topArtists.length) out += block('Your top artists', rowOf(h.topArtists.map(artistCard)));
    if (al && h.albums && h.albums.length) out += block('Your albums', rowOf(h.albums.slice(0, 20).map(a => albumCard(a))), { more: 'data-act="lib" data-kind="albums"' });
    if (k === 'artists' && h.artists && h.artists.length) out += block('Artists you follow', rowOf(h.artists.slice(0, 20).map(artistCard)));
    if (f && all) out += `<p class="feed-note">Your mixes, stations and new releases are made by this app from your listening. Spotify keeps its own Daily Mixes, Discover Weekly and radios to itself.</p>`;
    return out;
  },
  load: async () => { if (S.state && S.state.connected) { await loadHome(); loadFeed(); } },
};
function shortcutTile(x) {
  const liked = x.kind === 'liked', id = myId();
  const ctx = liked ? (id ? `spotify:user:${id}:collection` : '') : x.uri;
  const open = liked ? 'data-act="open-liked"' : `data-act="open-${x.kind}" data-id="${esc(x.id)}"`;
  const playBtn = liked ? `data-act="play-liked"` : `data-act="play-ctx" data-ctx="${esc(ctx)}"`;
  const pic = liked ? likedArt(56, 0) : art(x.art, 56, { circle: x.kind === 'artist', icon: x.kind === 'artist' ? 'person' : x.kind === 'album' ? 'album' : 'queue' });
  return `<div class="shortcut" role="button" ${open} data-ctx="${esc(ctx)}">${pic}<b>${esc(x.name)}</b><button class="sp" ${playBtn} title="Play ${esc(x.name)}">${icon('play', 18)}</button></div>`;
}
// something you played lately: a playlist, an album, an artist or Liked Songs
function ctxCard(x) {
  if (x.kind === 'liked') return likedCard();
  if (x.kind === 'artist') return artistCard({ id: x.id, uri: x.uri, name: x.name, imageMid: x.art });
  if (x.kind === 'album') return albumCard({ id: x.id, uri: x.uri, name: x.name, artMid: x.art }, 'Album');
  return playlistCard({ id: x.id, uri: x.uri, name: x.name, imageMid: x.art, owner: '' });
}
// Cara is the DJ: her card leads Made For you
function djCard() {
  const d = (S.state && S.state.dj) || {}, live = !!d.running;
  return `<div class="card" data-live="dj" data-act="open-cara"><div class="cover"><div class="art dj-art ${live ? 'live' : ''}">${bars(live)}<b>DJ</b><span>Cara</span></div>
    <button class="hover-play ${live ? 'playing' : ''}" data-act="dj-start" title="${live ? "Cara's Studio" : 'Start the show'}">${icon(live ? 'radio' : 'play', 22)}</button></div>
    <div><small class="two">${live ? esc(d.status || 'Cara is live') : 'Cara, live between your songs with your music, news and weather'}</small></div></div>`;
}
function moreLike(x) {
  const a = x.artist;
  const items = [...(x.station ? [mixCard(x.station)] : []), ...(x.albums || []).map(al => albumCard(al, `${al.year} • ${al.type}`)), ...(x.artists || []).map(artistCard)];
  if (!items.length) return '';
  return `<section class="block"><div class="section-head ml">${art(a.image, 48, { circle: true, icon: 'person' })}<div><span class="sub">More like</span><h2><button data-act="open-artist" data-id="${esc(a.id)}">${esc(a.name)}</button></h2></div></div>${rowOf(items)}</section>`;
}
function skeletonRow(n = 7) {
  return `<div class="row-wrap" style="--card:184px"><div class="row hide-scroll">${Array.from({ length: n }, () => `<div class="card sk"><div class="cover"><div class="art"></div></div><div><i></i><i></i></div></div>`).join('')}</div></div>`;
}
async function loadHome(force = false) {
  const h = await api.home(!!force);
  if (h && !h.error) { S.home = h; remember(h.topTracks); remember(h.liked); renderLibrary(); }
}
async function loadFeed(force = false) {
  if (loadFeed.busy) return;
  loadFeed.busy = true;
  try {
    const f = await api.home_feed(!!force);
    if (f && !f.error) S.feed = f;
    else if (!S.feed) S.feed = {};                     // nothing came back: no endless placeholders (Home asks again next time)
    if (S.route.name === 'home') refresh();
  } finally { loadFeed.busy = false; }
}

VIEWS.cara = {
  title: () => "Cara's Studio",
  html: () => {
    const c = S.config, d = (S.state && S.state.dj) || {};
    const seg = (key, opts) => `<div class="seg">${opts.map(([v, l]) => `<button class="${String(c[key]) === String(v) ? 'on' : ''}" data-act="cfg" data-key="${key}" data-val="${v}">${l}</button>`).join('')}</div>`;
    const tog = key => `<button class="toggle ${c[key] ? 'on' : ''}" data-act="cfg-toggle" data-key="${key}" aria-pressed="${!!c[key]}"></button>`;
    const step = (key, stepBy, min, max) => `<div class="stepper"><button data-act="cfg-step" data-key="${key}" data-step="${-stepBy}" data-min="${min}" data-max="${max}">${icon('minus', 16)}</button><button data-act="cfg-step" data-key="${key}" data-step="${stepBy}" data-min="${min}" data-max="${max}">${icon('plus', 16)}</button></div>`;
    const slider = key => `<input type="range" min="0" max="100" value="${c[key]}" data-key="${key}">`;
    const moods = { chill: 'Laid-back, warm and smooth, with dry wit.', normal: 'Her usual bubbly, cheeky, quick self.', unhinged: 'Maximum playful chaos and mock outrage.', mixed: 'A different mood every break.' };
    const chats = { quick: 'A line or two, then the music.', normal: 'A short story or bit, then the music.', chatty: 'Proper segments: stories, games, news.' };
    return `<h1 class="page-title">Cara's Studio</h1>${banners()}${heroHTML()}
    <div class="cara-grid">
      <div class="card-box"><h3>${icon('forward', 20)}Next Transition</h3>${queueSeg()}<div class="foot">How her next break starts, at the end of this song. Otherwise she picks one of the styles she's allowed (Transitions, below).</div></div>
      <div class="card-box"><h3>${icon('bolt', 20)}Right Now</h3><div class="tiles4">
        <button class="action-tile" data-act="dj-test" data-what="popin">${icon('sparkles', 24)}Pop In</button>
        <button class="action-tile" data-act="dj-test" data-what="duo">${icon('people', 24)}With Scratch</button>
        <button class="action-tile" data-act="dj-test" data-what="stinger">${icon('bolt', 24)}Stinger</button>
        <button class="action-tile" data-act="dj-test" data-what="break">${icon('news', 24)}Breaking</button></div>
        <div class="foot">She has to be live, with a song playing.</div></div>
      <div class="card-box"><h3>${icon('sparkles', 20)}Mood</h3>${seg('mood', [['chill', 'Chill'], ['normal', 'Normal'], ['unhinged', 'Unhinged'], ['mixed', 'Mixed']])}<div class="foot">${esc(moods[c.mood] || '')}</div></div>
      <div class="card-box"><h3>${icon('quote', 20)}How Much She Says</h3>${seg('chattiness', [['quick', 'Quick'], ['normal', 'Normal'], ['chatty', 'Chatty']])}<div class="foot">${esc(chats[c.chattiness] || '')}</div></div>
      <div class="card-box"><h3>${icon('radio', 20)}How Often</h3>
        <div class="set-row"><span>At least every</span><span class="val">${plural(c.break_min, 'song')}</span>${step('break_min', 1, 1, 10)}</div><div class="hair"></div>
        <div class="set-row"><span>At most every</span><span class="val">${plural(c.break_max, 'song')}</span>${step('break_max', 1, 1, 10)}</div>
        <div class="foot">She talks after a random number of songs in between.</div></div>
      <div class="card-box"><h3>${icon('mic', 20)}Pop-Ins</h3>
        <div class="set-row"><span>Pop back in a few seconds into the song</span>${tog('popin_enabled')}</div><div class="hair"></div>
        <div class="set-row"><span>How often</span><span class="val">${c.popin_chance}% of talk-overs</span>${step('popin_chance', 5, 0, 100)}</div><div class="hair"></div>
        <div class="set-row"><span>When</span><span class="val">about ${c.popin_secs} seconds in</span>${step('popin_secs', 5, 5, 120)}</div><div class="hair"></div>
        <div class="set-row"><span>Test mode (after every break)</span>${tog('popin_test')}</div></div>
      <div class="card-box"><h3>${icon('people', 20)}Co-Host</h3>
        <div class="set-row"><span>MC Scratch</span>${tog('cohost_enabled')}</div>
        ${c.cohost_enabled ? `<div class="hair"></div><div class="set-row"><span>Together</span><span class="val">${c.cohost_chance}% of breaks</span>${step('cohost_chance', 10, 10, 100)}</div><div class="hair"></div>
        <div class="set-row"><span>Scratch can curse</span>${tog('cohost_swears')}</div>` : ''}
        <div class="foot">${c.cohost_enabled ? `MC Scratch, Cara's West Coast co-host, joins this share of her breaks for a back-and-forth. ${c.cohost_swears ? 'He curses when it lands; Cara keeps it clean.' : 'He keeps it clean.'} His voice is in Settings.` : "Turn on MC Scratch, Cara's West Coast co-host, for back-and-forth breaks."}</div></div>
      <div class="card-box"><h3>${icon('volume', 20)}Sound</h3>
        <div class="set-row"><span>Cara's volume</span><span class="val">${c.dj_volume}%</span></div>${slider('dj_volume')}
        <div class="set-row"><span>Stinger volume</span><span class="val">${c.stinger_volume}%</span></div>${slider('stinger_volume')}
        <div class="set-row"><span>Music under her voice: automatic</span>${tog('duck_auto')}</div>
        ${c.duck_auto ? '' : `<div class="set-row"><span>Music level while she talks</span><span class="val">${c.duck_percent}%</span></div>${slider('duck_percent')}`}
        <div class="hair"></div>
        <div class="set-row"><span>Stingers before silent breaks</span><span class="val">${c.stinger_chance}%</span>${step('stinger_chance', 5, 0, 100)}</div><div class="hair"></div>
        <div class="set-row"><span>Stingers</span>${tog('stingers')}</div>
        ${c.stingers ? `<div class="hair"></div><div class="set-row"><span>Station stingers</span>${tog('station_stingers')}</div>
        ${c.station_stingers ? `<button class="link" data-act="restinger">${icon('refresh', 16)}Re-record stingers</button>` : ''}` : ''}
        <button class="link" data-act="open-stingers">${icon('folder', 16)}Open my stingers folder</button>
        <div class="foot">${!c.stingers ? 'Turn on stingers to start silent breaks with one now and then.' : c.station_stingers
          ? "Silent breaks start with a stinger this often. Station stingers are your stingers word for word, with the name of whatever's playing in place of Non-Stop-Pop, read by the station voice (Settings). On plain Non Stop Pop, your originals play."
          : 'Silent breaks start with one of your stingers this often.'}</div></div>
      <div class="card-box"><h3>${icon('news', 20)}Breaking News</h3>
        <div class="set-row"><span>Now and then, a breaking-news interruption</span>${tog('breaking_enabled')}</div><div class="hair"></div>
        <div class="set-row"><span>Test mode (every song)</span>${tog('breaking_test')}</div></div>
      <div class="card-box"><h3>${icon('shuffle', 20)}Transitions</h3>
        <div class="set-row"><span>Talk over the end of the song</span>${tog('t_talkover')}</div><div class="hair"></div>
        <div class="set-row"><span>Talk over the next song's intro</span>${tog('t_intro')}</div><div class="hair"></div>
        <div class="set-row"><span>Silent: the music stops while she talks</span>${tog('t_silent')}</div><div class="hair"></div>
        <div class="set-row"><span>Fade the song down under her</span>${tog('t_fadeout')}</div></div>
      <div class="card-box"><h3>${icon('place', 20)}Her Town</h3>
        <div class="field"><div class="box"><input id="town" value="${esc(c.city)}" placeholder="Yakima, Washington" spellcheck="false"><button class="pill secondary small" data-act="set-town">Change</button></div><div class="hint">Town, State. She talks about the weather and the news there.</div></div></div>
      <div class="card-box wide"><h3>${icon('queue', 20)}Activity</h3><div class="log scroll" id="log"></div><div class="foot">Newest at the bottom. If something goes wrong, a screenshot of this helps.</div></div>
    </div>`;
  },
};
function queueSeg() {
  const q = S.state && S.state.dj && S.state.dj.queued;
  const opts = [['talkover', 'Talk Over'], ['intro', 'Over the Intro'], ['silent', 'Silent'], ['fadeout', 'Fade Out']];
  return `<div class="seg" data-live="queue">${opts.map(([v, l]) => `<button class="${q === v ? 'on' : ''}" data-act="dj-queue" data-style="${v}">${l}</button>`).join('')}</div>`;
}

VIEWS.library = {
  title: () => 'Your Library',
  html: () => {
    const h = S.home, k = S.lib;
    const chips = [['playlists', 'Playlists'], ['albums', 'Albums'], ['artists', 'Artists']].map(([v, l]) => `<button class="chip ${k === v ? 'on' : ''}" data-act="lib" data-kind="${v}">${l}</button>`).join('');
    let out = `<div class="section-head" style="margin-top:12px"><h1 class="page-title" style="margin:0">Your Library</h1><button class="pill secondary small" data-act="new-playlist">${icon('plus', 18)}New Playlist</button></div>
      <div class="chips" style="margin:0 0 24px">${chips}</div>`;
    if (!S.state || !S.state.connected) return out + banners();
    if (!h) return out + loading();
    if (k === 'playlists') {
      const peek = (h.liked || []).slice(0, 6).map(t => `${esc(t.artist)} <span style="opacity:.6">${esc(t.title)}</span>`).join(' • ');
      out += `<div class="lib-tiles"><button class="liked-card" data-act="open-liked"><div class="peek">${peek}</div><b>Liked Songs</b><span>${plural(h.likedTotal || 0, 'liked song')}</span></button>${h.playlists.map(playlistCard).join('')}</div>`;
      if (h.playlists.length < h.playlistsTotal) out += `<div class="loading" data-more="playlists"><div class="spinner"></div></div>`;
    } else if (k === 'albums') {
      out += h.albums.length ? `<div class="lib-tiles">${h.albums.map(a => albumCard(a)).join('')}</div>` : empty('album', 'No saved albums', 'Albums you save on Spotify (or with the + on an album page) show up here.');
      if (h.albums.length < h.albumsTotal) out += `<div class="loading" data-more="albums"><div class="spinner"></div></div>`;
    } else {
      out += h.artists.length ? `<div class="lib-tiles">${h.artists.map(artistCard).join('')}</div>` : empty('mic', 'No artists yet', 'Artists you follow on Spotify show up here.');
      if (h.artistsAfter) out += `<div class="loading" data-more="artists"><div class="spinner"></div></div>`;
    }
    return out;
  },
  load: async () => { if (!S.home && S.state && S.state.connected) await loadHome(); },
};

VIEWS.liked = {
  title: () => 'Liked Songs',
  html: r => {
    const h = S.home;
    const tracks = (r.tracks || (h && h.liked) || []);
    tracks.forEach(t => S.liked.set(t.uri, true));
    const total = (h && h.likedTotal) || tracks.length, v = listView(r, tracks);
    return `<div class="page-hero">${likedArt(236, 14)}<div class="meta"><div class="caps">Playlist</div><h1>Liked Songs</h1>
      <div class="line">${firstName() ? `<b>${esc(firstName())}</b> •` : ''} ${plural(total, 'song')}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-liked" title="Play">${icon('play', 28)}</button>${shuffleBtn()}${tracks.length ? listTools(r, 'Liked Songs') : ''}</div>
      ${tracks.length ? (v.active && !v.list.length ? (r.loadingAll ? loading() : noMatches(v.q)) : trackTable(v.list, v.active ? { list: v.id } : { ctx: myId() ? `spotify:user:${myId()}:collection` : '' })) : (h ? empty('heart', 'No liked songs yet', 'Tap the heart on any song to save it here.') : loading())}
      ${tracks.length < total && !v.active ? `<div class="loading" data-more="liked"><div class="spinner"></div></div>` : ''}`;
  },
  load: async r => { if (!S.home) await loadHome(); r.tracks = (S.home && S.home.liked || []).slice(); },
};

function sortTracks(list, by, desc) {
  const key = { title: t => (t.title || '').toLowerCase(), artist: t => (t.artistLine || t.artist || '').toLowerCase(), album: t => (t.album || '').toLowerCase(),
    added: t => t.added || '', duration: t => t.duration || 0 }[by];
  if (!key) return desc ? list.slice().reverse() : list;
  const out = list.slice().sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
  return desc ? out.reverse() : out;
}
// the songs as you've asked to see them: searched, sorted. Played from here, they play in this order.
function listView(r, tracks) {
  const q = (r.find || '').trim().toLowerCase(), by = r.sort || 'custom';
  if (!q && by === 'custom' && !r.desc) return { active: false, list: tracks };
  let list = q ? tracks.filter(t => `${t.title} ${t.artistLine || t.artist} ${t.album}`.toLowerCase().includes(q)) : tracks;
  list = sortTracks(list, by, r.desc);
  const key = `${q}|${by}|${r.desc}|${list.length}|${tracks.length}`;
  if (r.vkey !== key) { r.vkey = key; r.vid = listOf(list); }
  return { active: true, list, id: r.vid, q };
}
const SORTS = [['custom', 'Custom order'], ['title', 'Title'], ['artist', 'Artist'], ['album', 'Album'], ['added', 'Date added'], ['duration', 'Duration']];
function listTools(r, noun) {
  const by = r.sort || 'custom', lab = (SORTS.find(x => x[0] === by) || SORTS[0])[1];
  return `<div class="list-tools">${r.loadingAll ? '<div class="spinner" style="width:16px;height:16px;border-width:2px"></div>' : ''}
    <div class="list-find ${r.findOpen || r.find ? 'open' : ''}"><button class="icon-btn" data-act="list-find" title="Search in ${esc(noun)}">${icon('search', 20)}</button><input class="list-q" placeholder="Search in ${esc(noun)}" value="${esc(r.find || '')}" spellcheck="false" autocomplete="off"></div>
    <button class="list-sort" data-act="list-sort" title="Sort">${esc(lab)}${by !== 'custom' || r.desc ? icon(r.desc ? 'down' : 'up', 16) : ''}${icon('list', 18)}</button></div>`;
}
function noMatches(q) { return empty('search', `Couldn't find “${q}”`, 'Try different words, or check the spelling.'); }
// searching or sorting needs every song, not just the first pages
async function ensureAll(r) {
  if (r.complete || r.loadingAll) return;
  const liked = r.name === 'liked', d = r.data;
  const have = liked ? (r.tracks || []).length : d ? d.tracks.length : 0;
  const total = liked ? ((S.home && S.home.likedTotal) || have) : d ? d.total : 0;
  if (!liked && !(d && d.canList)) return;
  if (have >= total) { r.complete = true; return; }
  r.loadingAll = true; if (S.route === r) refreshKeep('.list-q');
  const all = await api.all_tracks(liked ? 'liked' : 'playlist', liked ? '' : r.id);
  r.loadingAll = false;
  if (Array.isArray(all) && all.length) {
    remember(all); r.complete = true;
    if (liked) { r.tracks = all; all.forEach(t => S.liked.set(t.uri, true)); } else { d.tracks = all; d.rows = d.total = all.length; }
  }
  if (S.route === r) refreshKeep('.list-q');
}
function refreshKeep(sel) {
  const a = document.activeElement, keep = a && a.matches && a.matches(sel), pos = keep ? a.selectionStart : null;
  refresh();
  if (keep) { const n = $(sel); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }
}
function sortMenu(x, y) {
  const r = S.route;
  const opt = ([k, l]) => ({ label: l, check: (r.sort || 'custom') === k, run: () => {
    if ((r.sort || 'custom') === k) r.desc = !r.desc; else { r.sort = k; r.desc = false; }
    if (k !== 'custom') ensureAll(r);
    refresh();
  } });
  showMenu([{ head: 'Sort by' }, ...SORTS.map(opt)], x, y, { plain: true });
}

VIEWS.playlist = {
  title: r => (r.data && r.data.playlist.name) || 'Playlist',
  html: r => {
    const d = r.data;
    if (!d) return r.loaded ? empty('warning', "Couldn't load this playlist", 'Spotify didn\'t answer. Try again in a moment.') : loading();
    const p = d.playlist, mine = p.ownerId && p.ownerId === myId();
    const length = d.tracks.reduce((a, t) => a + (t.duration || 0), 0), v = listView(r, d.tracks);
    return `<div class="page-hero">${art(p.image || p.imageMid, 236, { icon: 'queue' })}<div class="meta"><div class="caps">${p.collaborative ? 'Collaborative playlist' : 'Playlist'}</div><h1>${esc(p.name)}</h1>
      ${p.about ? `<div class="about">${esc(p.about)}</div>` : ''}
      <div class="line"><b>${esc(p.owner)}</b> • ${plural(d.total, 'song')}${d.canList && d.tracks.length >= d.total ? ', ' + fmtLength(length) : ''}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-ctx" data-ctx="${esc(p.uri)}" title="Play">${icon('play', 28)}</button>
        ${shuffleBtn()}
        ${mine ? '' : `<button class="icon-btn ${d.saved ? 'on' : ''}" style="width:46px;height:46px" data-act="save-page" data-uri="${esc(p.uri)}" title="${d.saved ? 'Remove from Your Library' : 'Save to Your Library'}">${icon(d.saved ? 'checkCircle' : 'addCircle', 30)}</button>`}
        <button class="icon-btn" style="width:46px;height:46px" data-act="share" data-url="https://open.spotify.com/playlist/${esc(p.id)}" title="Copy link">${icon('share', 22)}</button>
        ${moreBtn('playlist', p.id, p.name)}${d.canList && d.tracks.length ? listTools(r, 'playlist') : ''}</div>
      ${!d.canList ? `<div class="locked">${icon('lock', 26)}<p>Spotify only lets apps like this one list the songs in playlists you made or collaborate on. You can still play this one.</p></div>`
        : d.tracks.length ? (v.active && !v.list.length ? (r.loadingAll ? loading() : noMatches(v.q)) : trackTable(v.list, v.active ? { list: v.id } : { ctx: p.uri })) : empty('queue', 'Empty playlist', "Add songs from any song's ••• menu.")}
      ${d.canList && d.tracks.length < d.total && !v.active ? `<div class="loading" data-more="playlist"><div class="spinner"></div></div>` : ''}`;
  },
  load: async r => { const d = await api.playlist(r.id); if (d && !d.error) { r.data = d; remember(d.tracks); checkLiked(d.tracks.slice(0, 80)).then(() => S.route === r && refresh()); } },
};

VIEWS.album = {
  title: r => (r.data && r.data.album.name) || 'Album',
  html: r => {
    const d = r.data;
    if (!d) return r.loaded ? empty('warning', "Couldn't load this album", "Spotify didn't answer. Try again in a moment.") : loading();
    const a = d.album, length = d.tracks.reduce((x, t) => x + (t.duration || 0), 0);
    Object.entries(d.liked || {}).forEach(([u, v]) => S.liked.set(u, !!v));
    return `<div class="page-hero">${art(a.art || a.artMid, 236, { icon: 'album' })}<div class="meta"><div class="caps">${esc(a.type)}</div><h1>${esc(a.name)}</h1>
      <div class="line">${a.artistId ? `<button data-act="open-artist" data-id="${esc(a.artistId)}">${esc(a.artist)}</button>` : `<b>${esc(a.artist)}</b>`} • ${esc(a.year)} • ${plural(d.tracks.length, 'song')}, ${fmtLength(length)}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-ctx" data-ctx="${esc(a.uri)}" title="Play">${icon('play', 28)}</button>
        ${shuffleBtn()}
        <button class="icon-btn ${d.saved ? 'on' : ''}" style="width:46px;height:46px" data-act="save-page" data-uri="${esc(a.uri)}" title="${d.saved ? 'Remove from Your Library' : 'Save to Your Library'}">${icon(d.saved ? 'checkCircle' : 'addCircle', 30)}</button>
        <button class="icon-btn" style="width:46px;height:46px" data-act="share" data-url="https://open.spotify.com/album/${esc(a.id)}" title="Copy link">${icon('share', 22)}</button>
        ${moreBtn('album', a.id, a.name)}</div>
      ${trackTable(d.tracks, { ctx: a.uri, album: false, numbers: true })}
      <div class="foot-note">${a.release && a.release.length > 4 ? `<span>${esc(prettyDate(a.release))}</span>` : ''}${d.copyright ? `<span>${esc(d.copyright)}</span>` : ''}</div>`;
  },
  load: async r => { const d = await api.album(r.id); if (d && !d.error) { r.data = d; remember(d.tracks); } },
};

VIEWS.artist = {
  title: r => (r.data && r.data.artist.name) || 'Artist',
  html: r => {
    const d = r.data;
    if (!d) return r.loaded ? empty('warning', "Couldn't load this artist", "Spotify didn't answer. Try again in a moment.") : loading();
    const a = d.artist, top = r.allTop ? d.top : d.top.slice(0, 5);
    return `<div class="artist-hero"><div class="bg" style="background-image:url('${esc(a.image || a.imageMid)}')"></div><div class="meta">
        <div class="caps" style="color:rgba(255,255,255,.85)">${a.genres && a.genres.length ? esc(a.genres.slice(0, 3).join(' • ')) : 'Artist'}</div><h1>${esc(a.name)}</h1>
        <div style="font-weight:600;color:rgba(255,255,255,.85)">${a.followers ? plural(a.followers, 'follower') : ''}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-ctx" data-ctx="${esc(a.uri)}" title="Play">${icon('play', 28)}</button>
        <button class="pill secondary small" data-act="save-page" data-uri="${esc(a.uri)}">${d.following ? 'Following' : 'Follow'}</button>
        <button class="icon-btn" style="width:46px;height:46px" data-act="share" data-url="https://open.spotify.com/artist/${esc(a.id)}" title="Copy link">${icon('share', 22)}</button>
        ${moreBtn('artist', a.id, a.name)}</div>
      ${d.top.length ? `<section class="block">${head('Popular')}${trackTable(top, { header: false, list: r.topList || (r.topList = listOf(d.top)) })}${d.top.length > 5 ? `<button class="more link" style="margin:10px 12px" data-act="artist-more">${r.allTop ? 'Show less' : 'See more'}</button>` : ''}</section>` : ''}
      ${d.albums.length ? `<section class="block">${head('Albums')}${rowOf(d.albums.map(x => albumCard(x, x.year)))}</section>` : ''}
      ${d.singles.length ? `<section class="block">${head('Singles & EPs')}${rowOf(d.singles.map(x => albumCard(x, x.year + ' • ' + x.type)), 164)}</section>` : ''}
      ${d.bio ? `<section class="block"><div class="about-box glass" style="max-width:980px"><h3>About</h3><p>${esc(d.bio)}</p></div></section>` : ''}`;
  },
  load: async r => { const d = await api.artist(r.id); if (d && !d.error) { r.data = d; remember(d.top); checkLiked(d.top).then(() => S.route === r && refresh()); } },
};

VIEWS.genre = {
  title: r => (r.data && r.data.genre.title) || 'Browse',
  html: r => {
    const g = (S.boot.genres || []).find(x => x.id === r.id) || { title: '', hue: .9 };
    const d = r.data;
    return `<div class="genre-hero" style="background:${tile(g.hue)}">${icon(GENRE_ICONS[g.id], 150)}<h1>${esc(g.title)}</h1></div>
      ${!d ? (r.loaded ? empty('warning', 'Nothing here yet', "Spotify didn't send anything back for this one. Try again in a bit.") : loading()) : `
      ${d.tracks.length ? `<div class="actions-row"><button class="big-play" data-act="play-list" data-list="${r.list || (r.list = listOf(d.tracks))}" data-name="${esc(g.title)}" title="Play">${icon('play', 28)}</button>
        ${shuffleBtn()}</div>
        <section class="block">${head('Songs')}${trackTable(d.tracks, { list: r.list })}</section>` : ''}
      ${d.playlists.length ? `<section class="block">${head('Playlists')}${rowOf(d.playlists.map(playlistCard))}</section>` : ''}`}`;
  },
  load: async r => { const d = await api.genre(r.id); if (d && !d.error) { r.data = d; remember(d.tracks); } },
};
VIEWS.radio = {
  title: r => (r.data && r.data.title) || 'Song radio',
  html: r => {
    const d = r.data;
    if (!d) return r.loaded ? empty('warning', "Couldn't make this radio", "Spotify didn't answer. Try again in a moment.") : loading();
    const s = d.seed;
    const from = [d.artists.join(' and '), d.friends.length ? 'the artists they work with' : '', d.genres.length ? d.genres.slice(0, 2).join(' and ') : ''].filter(Boolean);
    return `<div class="page-hero">${art(s.art || s.artMid, 236, { icon: 'radio' })}<div class="meta"><div class="caps">Song radio</div><h1>${esc(d.title)}</h1>
      <div class="about">Songs that go with ${esc(s.title)}: ${esc(from.join(', '))}.</div>
      <div class="line"><b>${esc(s.artistLine)}</b> • ${plural(d.tracks.length, 'song')}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-list" data-list="${r.list || (r.list = listOf(d.tracks))}" data-name="${esc(d.title)}" title="Play">${icon('play', 28)}</button>
        ${shuffleBtn()}</div>
      ${trackTable(d.tracks, { list: r.list })}`;
  },
  load: async r => { const d = await api.song_radio(r.id); if (d && !d.error) { r.data = d; remember(d.tracks); checkLiked(d.tracks).then(() => S.route === r && refresh()); } },
};
// ---------------------------------------------------------------- mixes and stations (made by this app from your listening)
function mixSub(m) {
  const names = (m.artists || []).slice(0, 3), and = names.length ? names.join(', ') + ' and more' : '';
  switch (m.kind) {
    case 'station': return m.with && m.with.length ? `With ${m.with.join(', ')} and more` : `${names[0] || 'Songs'} and artists like them`;
    case 'discover': return 'New to you, from the sound of your favourites';
    case 'radar': return 'The newest songs from artists you follow and play';
    case 'onrepeat': return "The songs you can't stop playing";
    case 'rewind': return "Old favourites you haven't played lately";
    case 'time': return `Your ${m.part || 'day'} sound: ${and}`;
    default: return and;
  }
}
// a mix's cover: the lead artist's photo washed in the mix's colour, a station's circles, or type on colour
function mixArt(m, size) {
  const st = `--h:${Math.round((m.hue || 0) * 360)};${size ? `width:${size}px;height:${size}px;` : ''}`;
  const img = m.image ? `<img src="${esc(m.image)}" loading="lazy" alt="" onload="this.classList.add('ok')" onerror="this.remove()">` : '';
  if (m.kind === 'station') {
    const side = (m.withArt || []).slice(0, 2).map((u, i) => `<span class="bub b${i + 1}" style="background-image:url('${esc(u)}')"></span>`).join('');
    return `<div class="art mixart station" style="${st}"><span class="tag">Radio</span>${side}<span class="bub main" ${m.image ? `style="background-image:url('${esc(m.image)}')"` : ''}></span><b class="nm ell">${esc(m.artist ? m.artist.name : m.name)}</b></div>`;
  }
  const num = m.kind === 'daily' ? `<span class="num">${esc(m.num)}</span>` : '';
  if (['daily', 'genre', 'artist', 'time', 'recent'].includes(m.kind) && m.image) {
    return `<div class="art mixart photo ${m.kind}" style="${st}">${img}<span class="wash"></span>${num}<span class="band"><b>${esc(m.label || m.name)}</b></span></div>`;
  }
  return `<div class="art mixart type ${m.kind}" style="${st}"><span class="grain"></span>${num}<b>${esc(m.kind === 'daily' ? 'Daily Mix' : m.name)}</b>${img && m.kind !== 'daily' ? `<span class="pic">${img}</span>` : ''}</div>`;
}
function mixCard(m) {
  return `<div class="card" data-act="open-mix" data-id="${esc(m.id)}" title="${esc(m.name)}"><div class="cover">${mixArt(m)}
    <button class="hover-play" data-act="play-mix" data-mix="${esc(m.id)}" title="Play ${esc(m.name)}">${icon('play', 22)}</button></div>
    <div><small class="two">${esc(mixSub(m))}</small></div></div>`;
}
function bigCard(b) {
  const m = b.mix;
  return `<div class="big-wrap"><div class="caption">${esc(b.caption)}</div>
    <div class="big-card" data-act="open-mix" data-id="${esc(m.id)}" role="button">${mixArt(m)}
      <div class="info"><b>${esc(m.name)}</b><small>${esc(mixSub(m))}</small>
        <div class="foot"><span class="tag">${m.kind === 'station' ? 'Radio' : 'Playlist'}</span>
        <button class="hover-play" data-act="play-mix" data-mix="${esc(m.id)}" title="Play ${esc(m.name)}">${icon('play', 22)}</button></div></div></div></div>`;
}
function mixDef(id) {
  const f = S.feed; if (!f) return null;
  return [...(f.made || []), ...(f.timed || []), ...(f.topMixes || []), ...(f.stations || [])].find(m => m.id === id) || null;
}
async function getMix(id, quiet = false) {
  const hit = S.mixes.get(id);
  if (hit && Date.now() - hit.at < 25 * 60000) return hit.value;
  const slow = quiet ? 0 : setTimeout(() => toast('Gathering the songs for this mix…', 'radio'), 700);
  try {
    const m = await api.mix(id);
    if (!m || m.error) return null;
    remember(m.tracks || []);
    if (m.tracks && m.tracks.length) S.mixes.set(id, { at: Date.now(), value: m });
    return m;
  } finally { clearTimeout(slow); }
}
async function playMix(id) {
  const t = nowTrack();
  if (S.mixNow && S.mixNow.id === id && t && S.mixNow.uris.has(t.uri)) return cmd('toggle');
  const m = await getMix(id);
  if (!m || !m.tracks || !m.tracks.length) return toast("Couldn't gather songs for this mix right now. Try again in a moment.", 'warning');
  const uris = m.tracks.map(x => x.uri), on = shuffleOn();
  S.mixNow = { id, name: m.name, uris: new Set(uris) };
  await play({ uris, shuffle: on, position: on ? Math.floor(Math.random() * uris.length) : 0 });
  markNow();
}
VIEWS.mix = {
  title: r => (r.data && r.data.name) || (mixDef(r.id) || {}).name || 'Mix',
  html: r => {
    const d = r.data, def = d || mixDef(r.id);
    const caps = x => x.kind === 'station' ? 'Radio' : 'Playlist';
    if (!d) {
      if (r.loaded) return empty('warning', "Couldn't make this mix", "Spotify didn't send its songs. Try again in a moment.");
      return (def ? `<div class="page-hero">${mixArt(def, 236)}<div class="meta"><div class="caps">${caps(def)}</div><h1>${esc(def.name)}</h1><div class="about">${esc(mixSub(def))}</div></div></div>` : '') + loading();
    }
    if (!r.list) { r.list = listOf(d.tracks); S.listMix.set(r.list, { id: d.id, name: d.name }); }
    const length = d.tracks.reduce((a, t) => a + (t.duration || 0), 0);
    return `<div class="page-hero">${mixArt(d, 236)}<div class="meta"><div class="caps">${caps(d)}</div><h1>${esc(d.name)}</h1>
        <div class="about">${esc(d.description || mixSub(d))}</div>
        <div class="line"><b>Made for ${esc(firstName() || 'you')}</b> • ${plural(d.tracks.length, 'song')}${d.tracks.length ? ', ' + fmtLength(length) : ''}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-mix" data-mix="${esc(d.id)}" title="Play">${icon('play', 28)}</button>
        ${shuffleBtn()}
        ${moreBtn('mix', d.id, d.name)}
        ${d.kind === 'station' && d.artist ? `<button class="pill secondary small" data-act="open-artist" data-id="${esc(d.artist.id)}">Go to ${esc(d.artist.name)}</button>` : ''}</div>
      ${d.tracks.length ? trackTable(d.tracks, { list: r.list }) : empty('radio', 'No songs yet', "Spotify didn't send songs for this mix. Try again in a bit.")}
      <div class="foot-note"><span>Made by this app from your listening.</span></div>`;
  },
  load: async r => { const d = await getMix(r.id, true); if (d) { r.data = d; checkLiked(d.tracks || []).then(() => S.route === r && refresh()); } },
};
function tile(h) { return `linear-gradient(135deg, hsl(${h * 360} 62% 50%), hsl(${((h + .06) % 1) * 360} 78% 30%))`; }

VIEWS.search = {
  title: () => 'Search',
  html: () => {
    const s = S.search, c = S.config;
    let out = '';
    if (!s.q.trim()) {
      const recent = c.recent_searches || [];
      if (recent.length) out += `<section class="block"><div class="section-head"><h2>Recent searches</h2><button class="more" data-act="search-forget">Clear</button></div><div class="chips">${recent.map(q => `<button class="chip" data-act="search-term" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div></section>`;
      out += `<section class="block">${head('Browse all')}<div class="genre-grid">${(S.boot.genres || []).map(g => `<button class="genre" style="background:${tile(g.hue)}" data-act="open-genre" data-id="${g.id}">${icon(GENRE_ICONS[g.id], 64)}${esc(g.title)}</button>`).join('')}</div></section>`;
      return out;
    }
    const scopes = [['all', 'All'], ['track', 'Songs'], ['artist', 'Artists'], ['album', 'Albums'], ['playlist', 'Playlists']];
    out += `<div class="chips" style="margin:-8px 0 24px">${scopes.map(([v, l]) => `<button class="chip ${s.scope === v ? 'on' : ''}" data-act="search-scope" data-scope="${v}">${l}</button>`).join('')}</div>`;
    const r = s.results;
    if (!r) return out + loading();
    if (!r.tracks.length && !r.artists.length && !r.albums.length && !r.playlists.length) return out + empty('search', `No results for “${s.q}”`, 'Check the spelling, or try fewer words.');
    if (s.scope === 'all') {
      const low = s.q.trim().toLowerCase();
      const best = r.artists.find(a => a.name.toLowerCase() === low) ? { kind: 'artist', x: r.artists.find(a => a.name.toLowerCase() === low) }
        : r.albums.find(a => a.name.toLowerCase() === low) ? { kind: 'album', x: r.albums.find(a => a.name.toLowerCase() === low) }
        : r.tracks[0] ? { kind: 'track', x: r.tracks[0] } : r.artists[0] ? { kind: 'artist', x: r.artists[0] } : null;
      let bestHTML = '';
      if (best) {
        const b = best.x;
        if (best.kind === 'artist') bestHTML = `<div class="best glass circle" data-act="open-artist" data-id="${esc(b.id)}" role="button">${art(b.imageMid, 108, { circle: true, icon: 'person' })}<div><b class="ell" style="display:block">${esc(b.name)}</b><span class="muted">Artist</span></div><button class="hover-play" data-act="play-ctx" data-ctx="${esc(b.uri)}">${icon('play', 24)}</button></div>`;
        else if (best.kind === 'album') bestHTML = `<div class="best glass" data-act="open-album" data-id="${esc(b.id)}" role="button">${art(b.artMid, 108)}<div><b class="ell" style="display:block">${esc(b.name)}</b><span class="muted">${esc(b.type)} • ${esc(b.artist)}</span></div><button class="hover-play" data-act="play-ctx" data-ctx="${esc(b.uri)}">${icon('play', 24)}</button></div>`;
        else { remember([b]); bestHTML = `<div class="best glass" data-act="play-one" data-uri="${esc(b.uri)}" role="button">${art(b.artMid, 108)}<div><b class="ell" style="display:block">${esc(b.title)}</b><span class="muted">Song • ${esc(b.artistLine)}</span></div><button class="hover-play" data-act="play-one" data-uri="${esc(b.uri)}">${icon('play', 24)}</button></div>`; }
      }
      out += `<div class="top-grid block"><section><div class="section-head"><h2>Top result</h2></div>${bestHTML}</section>
        <section><div class="section-head"><h2>Songs</h2><button class="more" data-act="search-scope" data-scope="track">Show all</button></div>${trackTable(r.tracks.slice(0, 4), { header: false, album: false })}</section></div>`;
      if (r.artists.length) out += `<section class="block">${head('Artists', '', 'data-act="search-scope" data-scope="artist"')}${rowOf(r.artists.map(artistCard))}</section>`;
      if (r.albums.length) out += `<section class="block">${head('Albums', '', 'data-act="search-scope" data-scope="album"')}${rowOf(r.albums.map(a => albumCard(a, a.year + ' • ' + a.artist)))}</section>`;
      if (r.playlists.length) out += `<section class="block">${head('Playlists', '', 'data-act="search-scope" data-scope="playlist"')}${rowOf(r.playlists.map(playlistCard))}</section>`;
    } else if (s.scope === 'track') out += trackTable(r.tracks, {});
    else if (s.scope === 'artist') out += `<div class="grid">${r.artists.map(artistCard).join('')}</div>`;
    else if (s.scope === 'album') out += `<div class="grid">${r.albums.map(a => albumCard(a, a.year + ' • ' + a.artist)).join('')}</div>`;
    else out += `<div class="grid">${r.playlists.map(playlistCard).join('')}</div>`;
    if (s.scope !== 'all' && !s.done) out += `<div class="loading" data-more="search"><div class="spinner"></div></div>`;
    return out;
  },
};
const SCOPE_TYPES = { all: 'track,artist,album,playlist', track: 'track', artist: 'artist', album: 'album', playlist: 'playlist' };
async function runSearch() {
  const s = S.search, q = s.q.trim();
  if (!q) { s.results = null; if (S.route.name === 'search') refresh(); return; }
  const mine = ++runSearch.seq;
  s.results = null; s.offset = 0; s.done = false;
  if (S.route.name === 'search') refresh();
  const r = await api.search(q, SCOPE_TYPES[s.scope], 0);
  if (mine !== runSearch.seq) return;
  s.results = (r && !r.error) ? r : { tracks: [], artists: [], albums: [], playlists: [] };
  const key = { track: 'tracks', artist: 'artists', album: 'albums', playlist: 'playlists' }[s.scope];
  if (key && s.results[key].length < 10) s.done = true;
  remember(s.results.tracks);
  if (S.route.name === 'search') refresh();
}
runSearch.seq = 0;

// ---------------------------------------------------------------- loading more as you scroll
let moreBusy = false;
async function loadMore() {
  const el = $('[data-more]'); if (!el || moreBusy) return;
  const box = $('#scroller'); if (el.getBoundingClientRect().top > box.getBoundingClientRect().bottom + 600) return;
  moreBusy = true;
  try {
    const kind = el.dataset.more, h = S.home, r = S.route;
    if (kind === 'playlists' && h) { const x = await api.more('playlists', h.playlists.length); if (x && x.items) { h.playlists.push(...x.items.filter(p => !h.playlists.some(q => q.id === p.id))); if (!x.items.length) h.playlistsTotal = h.playlists.length; renderSidebar(); } }
    else if (kind === 'albums' && h) { const x = await api.more('albums', h.albums.length); if (x && x.items) { h.albums.push(...x.items); if (!x.items.length) h.albumsTotal = h.albums.length; } }
    else if (kind === 'artists' && h) { const x = await api.more('artists', 0, h.artistsAfter); if (x && x.items) { h.artists.push(...x.items); h.artistsAfter = x.after; } }
    else if (kind === 'liked' && h) { const x = await api.more('liked', r.tracks.length); if (x && x.items) { remember(x.items); r.tracks.push(...x.items); h.liked = r.tracks.slice(); if (!x.items.length) h.likedTotal = r.tracks.length; } }
    else if (kind === 'playlist' && r.data) { const x = await api.playlist_more(r.id, r.data.rows); if (x && x.tracks) { remember(x.tracks); r.data.tracks.push(...x.tracks); r.data.rows += x.rows; if (!x.rows) r.data.total = r.data.tracks.length; checkLiked(x.tracks); } }
    else if (kind === 'search') {
      const s = S.search, key = { track: 'tracks', artist: 'artists', album: 'albums', playlist: 'playlists' }[s.scope];
      const x = await api.search(s.q.trim(), SCOPE_TYPES[s.scope], s.offset + 10);
      if (x && !x.error) { s.offset += 10; s.results[key].push(...x[key]); remember(x.tracks); if (x[key].length < 10 || s.offset >= 990) s.done = true; } else s.done = true;
    }
    refresh();
  } finally { moreBusy = false; }
}

// ---------------------------------------------------------------- Your Library (the left pane)
function libItems() {
  const h = S.home; if (!h) return [];
  const id = myId();
  const items = [{ kind: 'liked', key: 'liked:', name: 'Liked Songs', sub: `Playlist • ${plural(h.likedTotal || 0, 'song')}`, pinned: true, ctx: id ? `spotify:user:${id}:collection` : '', owner: '' }];
  for (const p of h.playlists || []) items.push({ kind: 'playlist', key: 'playlist:' + p.id, id: p.id, name: p.name, sub: `Playlist • ${p.owner}`, art: p.imageMid || p.image, ctx: p.uri, owner: p.owner || '' });
  for (const a of h.albums || []) items.push({ kind: 'album', key: 'album:' + a.id, id: a.id, name: a.name, sub: `Album • ${a.artist}`, art: a.artMid || a.art, ctx: a.uri, owner: a.artist || '' });
  for (const a of h.artists || []) items.push({ kind: 'artist', key: 'artist:' + a.id, id: a.id, name: a.name, sub: 'Artist', art: a.imageMid || a.image, ctx: a.uri, owner: a.name });
  return items;
}
function renderLibrary() {
  const u = S.ui, list = $('#lib-list');
  const kinds = [['playlist', 'Playlists'], ['album', 'Albums'], ['artist', 'Artists']];
  $('#lib-chips').innerHTML = (u.libKind ? `<button class="lib-chip" data-act="lib-kind" data-kind="" title="Show everything">${icon('close', 14)}</button>` : '')
    + kinds.filter(([k]) => !u.libKind || u.libKind === k).map(([k, l]) => `<button class="lib-chip ${u.libKind === k ? 'on' : ''}" data-act="lib-kind" data-kind="${k}">${l}</button>`).join('');
  $('#lib-sort span').textContent = { recent: 'Recents', alpha: 'Alphabetical', creator: 'Creator' }[u.libSort] || 'Recents';
  let items = libItems();
  if (u.libKind) items = items.filter(x => x.kind === u.libKind || (u.libKind === 'playlist' && x.kind === 'liked'));
  const q = (S.libQuery || '').trim().toLowerCase();
  if (q) items = items.filter(x => (x.name + ' ' + x.sub).toLowerCase().includes(q));
  const pin = (a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
  if (u.libSort === 'alpha') items.sort((a, b) => pin(a, b) || a.name.localeCompare(b.name));
  else if (u.libSort === 'creator') items.sort((a, b) => pin(a, b) || a.owner.localeCompare(b.owner) || a.name.localeCompare(b.name));
  else items = byRecent(items, pin);                       // Recents: what you played lately first
  const playing = now() && now().context;
  const openOf = x => x.kind === 'liked' ? 'data-act="open-liked"' : `data-act="open-${x.kind}" data-id="${esc(x.id)}"`;
  list.innerHTML = items.length ? items.map(x => {
    const on = !!playing && !!x.ctx && playing === x.ctx;
    const pic = x.kind === 'liked' ? likedArt(48, 8) : art(x.art, 48, { circle: x.kind === 'artist', icon: x.kind === 'artist' ? 'person' : x.kind === 'album' ? 'album' : 'queue' });
    return `<button class="lib-item ${x.kind} ${on ? 'playing' : ''}" data-route="${esc(x.key)}" ${openOf(x)} title="${esc(x.name)}">${pic}<div class="ell"><b class="ell">${esc(x.name)}</b><small class="ell">${x.pinned ? icon('pin', 13) : ''}${esc(x.sub)}</small></div>${on ? `<span class="vol-ic">${icon('volume', 16)}</span>` : ''}</button>`;
  }).join('') : `<div class="lib-empty">${S.home ? (q ? `Nothing in your library matches “${esc(q)}”.` : 'Your playlists, albums and artists show up here.') : (S.state && S.state.connected ? 'Loading your library…' : 'Connect Spotify to see your library.')}</div>`;
  markLibrary();
  const me = S.state && S.state.me, av = $('#me-avatar');
  av.style.backgroundImage = me && me.image ? `url('${me.image}')` : '';
  av.innerHTML = me && me.image ? '' : icon('person', 18);
  $('#tb-me').title = me ? me.name : 'Account';
}
// what you played lately first: what's played since the app opened, then Spotify's recently played, then the rest
function byRecent(items, pin = () => 0) {
  const live = S.ui.recentCtx || [];
  const recent = ((S.home && S.home.shortcuts) || []).map(x => x.kind === 'liked' ? 'liked:' : x.kind + ':' + x.id);
  const rank = x => { const l = x.ctx ? live.indexOf(x.ctx) : -1; if (l >= 0) return l; const i = recent.indexOf(x.key); return i < 0 ? 99999 : 10000 + i; };
  return items.map((x, i) => [x, i]).sort((a, b) => pin(a[0], b[0]) || rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(a => a[0]);
}
function noteRecent(ctx) {
  if (!ctx) return;
  S.ui.recentCtx = [ctx, ...(S.ui.recentCtx || []).filter(c => c !== ctx)].slice(0, 80);
  saveUi();
}
async function loadAllPlaylists() {
  const h = S.home; if (!h) return;
  for (let i = 0; i < 40 && h.playlists.length < h.playlistsTotal; i++) {
    const x = await api.more('playlists', h.playlists.length);
    const fresh = x && x.items ? x.items.filter(p => !h.playlists.some(q => q.id === p.id)) : [];
    if (!fresh.length) { h.playlistsTotal = h.playlists.length; break; }
    h.playlists.push(...fresh);
  }
}
async function loadAllLibrary() {
  const h = S.home; if (!h || loadAllLibrary.busy) return;
  loadAllLibrary.busy = true;
  try {
    await loadAllPlaylists();
    for (let i = 0; i < 40 && h.albums.length < h.albumsTotal; i++) {
      const x = await api.more('albums', h.albums.length);
      if (!x || !x.items || !x.items.length) { h.albumsTotal = h.albums.length; break; }
      h.albums.push(...x.items);
    }
    for (let i = 0; i < 40 && h.artistsAfter; i++) {
      const x = await api.more('artists', 0, h.artistsAfter);
      if (!x || !x.items || !x.items.length) { h.artistsAfter = null; break; }
      h.artists.push(...x.items); h.artistsAfter = x.after;
    }
    if (S.home === h) renderLibrary();
  } finally { loadAllLibrary.busy = false; }
}
function markLibrary() { $$('.lib-item[data-route]').forEach(b => b.classList.toggle('on', b.dataset.route === routeKey(S.route))); }
const renderSidebar = renderLibrary;
let libBusy = false;
async function loadLibMore() {
  const h = S.home, box = $('#lib-list');
  if (!h || libBusy || box.scrollTop + box.clientHeight < box.scrollHeight - 300) return;
  libBusy = true;
  try {
    if (h.playlists.length < h.playlistsTotal) { const x = await api.more('playlists', h.playlists.length); if (x && x.items && x.items.length) h.playlists.push(...x.items.filter(p => !h.playlists.some(q => q.id === p.id))); else h.playlistsTotal = h.playlists.length; }
    else if (h.albums.length < h.albumsTotal) { const x = await api.more('albums', h.albums.length); if (x && x.items && x.items.length) h.albums.push(...x.items); else h.albumsTotal = h.albums.length; }
    else if (h.artistsAfter) { const x = await api.more('artists', 0, h.artistsAfter); if (x && x.items) { h.artists.push(...x.items); h.artistsAfter = x.after; } else h.artistsAfter = null; }
    else return;
    renderLibrary();
  } finally { libBusy = false; }
}
function toggleLibrary() { S.ui.libMini = !S.ui.libMini; saveUi(); applyUi(); }
function libSortMenu(x, y) {
  const opt = (k, l) => ({ label: l, check: S.ui.libSort === k, run: () => { S.ui.libSort = k; saveUi(); renderLibrary(); } });
  showMenu([{ head: 'Sort by' }, opt('recent', 'Recents'), opt('alpha', 'Alphabetical'), opt('creator', 'Creator')], x, y, { plain: true });
}

// ---------------------------------------------------------------- playing music
async function play(opts, name) {
  const r = await api.play(opts.context || null, opts.offset || null, opts.uris || null, opts.position ?? null, opts.shuffle ?? null);
  if (r !== 'ok') toast(typeof r === 'string' ? r : "Spotify wouldn't play that.", 'warning');
  else if (S.ui.npOnPlay && !S.ui.nvOpen) openNV('now');      // Spotify's "show the now-playing panel on click of play"
}
function playRow(el) {
  const uri = el.dataset.uri, ctx = el.dataset.ctx, list = el.dataset.list;
  if (ctx) return play({ context: ctx, offset: uri });
  const all = S.lists.get(list) || [uri];
  const i = Math.max(0, all.indexOf(uri)), uris = all.slice(i, i + 100);
  const lm = S.listMix.get(list);
  S.mixNow = lm ? { id: lm.id, name: lm.name, uris: new Set(all) } : null;
  play({ uris, offset: uri });
}
function updateLike(uri, on) {
  S.liked.set(uri, on);
  $$(`[data-act=like][data-uri="${CSS.escape(uri)}"]`).forEach(b => { b.classList.toggle('liked', on); b.innerHTML = icon(on ? 'checkCircle' : 'addCircle', 18); });
  if (S.state && S.state.now && S.state.now.track && S.state.now.track.uri === uri) { S.state.now.liked = on; updateBar(); updateNP(); nvSong(); }
}
async function toggleLike(uri) {
  const on = !S.liked.get(uri) && !(nowUri() === uri && now().liked);
  updateLike(uri, on);
  const ok = await api.set_saved(uri, on);
  if (ok === true) toast(on ? 'Added to Liked Songs' : 'Removed from Liked Songs', on ? 'checkCircle' : 'addCircle');
  else { updateLike(uri, !on); toast("Spotify wouldn't save that.", 'warning'); }
  if (S.home) loadHome(true);
}

// ---------------------------------------------------------------- the now-playing bar
function updateBar() {
  const n = now(), t = n && n.track;
  const bar = $('#bar');
  if (t) {
    if (bar.dataset.uri !== t.uri) {
      bar.dataset.uri = t.uri;
      $('#bar-art').innerHTML = art(t.artMid || t.art, 56);
      $('#nv-rail-art').innerHTML = art(t.artMid || t.art, 48);
      $('#bar-title').textContent = t.title;
      $('#bar-artist').innerHTML = artistLinks(t);
    }
    const liked = n.liked ?? S.liked.get(t.uri);
    const lb = $('#bar-like'); lb.hidden = false; lb.classList.toggle('liked', !!liked); lb.innerHTML = icon(liked ? 'checkCircle' : 'addCircle', 19);
    lb.title = liked ? 'Remove from Liked Songs' : 'Save to your Liked Songs';
  } else {
    bar.dataset.uri = '';
    $('#bar-art').innerHTML = art('', 56);
    $('#nv-rail-art').innerHTML = '';
    $('#bar-title').textContent = S.state && S.state.connected ? 'Nothing playing' : 'Not connected';
    $('#bar-artist').textContent = S.state && S.state.connected ? 'Pick something to play' : 'Connect Spotify in Settings';
    $('#bar-like').hidden = true;
  }
  const playing = !!(n && n.playing);
  $('#bar-play').innerHTML = icon(playing ? 'pause' : 'play', 22);
  $('#bar-shuffle').classList.toggle('on', !!(n && n.shuffle));
  if (n && n.device && !!n.shuffle !== !!S.ui.shuffle) { S.ui.shuffle = !!n.shuffle; saveUi(); }
  const shuf = shuffleOn();
  $$('.page-shuffle').forEach(b => { b.classList.toggle('on', shuf); b.title = shuf ? 'Turn off shuffle' : 'Shuffle'; b.setAttribute('aria-pressed', shuf); });
  const rep = (n && n.repeat) || 'off';
  $('#bar-repeat').classList.toggle('on', rep !== 'off');
  $('#bar-repeat').innerHTML = icon(rep === 'track' ? 'repeatOne' : 'repeat', 20);
  const dev = n && n.device;
  const pl = (S.state && S.state.player) || {};
  const here = !!dev && ((pl.deviceId && dev.id === pl.deviceId) || dev.name === (pl.name || 'Non Stop Pop DJ'));
  $('#bar-dev').classList.toggle('on', !!dev && !here);
  $('#bar-dev').title = dev ? (here ? 'Playing in this app' : `Playing on ${dev.name}`) : 'Connect to a device';
  const strip = $('#playing-on'), away = !!(t && dev && !here);
  strip.hidden = !away;
  if (away) strip.innerHTML = `${icon(dev.type === 'computer' ? 'computer' : dev.type === 'smartphone' ? 'phone' : 'speaker', 15)}Playing on ${esc(dev.name)}`;
  $('#bar-queue').classList.toggle('active', S.ui.nvOpen && S.nv.mode === 'queue');
  $('#bar-lyrics').classList.toggle('active', S.np.open && S.np.tab === 'lyrics');
  if (dev && dev.volume != null && Date.now() - (S.volT || 0) > 2500) syncVolume(dev.volume);
  else if (S.volume == null) syncVolume(60);
}
let seeking = null;
function tickProgress() {
  const n = now(), t = n && n.track;
  const dur = (t && t.duration) || 0;
  const p = seeking != null ? seeking : progressNow();
  const frac = dur ? p / dur : 0;
  for (const id of ['bar', 'np']) {
    const fill = $(`#${id}-fill`), knob = $(`#${id}-knob`);
    if (fill) fill.style.width = (frac * 100) + '%';
    if (knob) knob.style.left = (frac * 100) + '%';
    const a = $(`#${id}-pos`), b = $(`#${id}-dur`);
    if (a) a.textContent = fmtTime(p);
    if (b) b.textContent = id === 'np' ? '-' + fmtTime(dur - p) : fmtTime(dur);
  }
  if (S.np.open && S.np.tab === 'lyrics') syncLyrics(p);
  if (S.ui.nvOpen) nvTick(p);
  requestAnimationFrame(tickProgress);
}
function seekFrom(e, el) {
  const t = nowTrack(); if (!t) return null;
  const r = el.getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * t.duration;
}
function wireSeek(el) {
  el.addEventListener('pointerdown', e => {
    if (!nowTrack()) return;
    el.setPointerCapture(e.pointerId);
    seeking = seekFrom(e, el);
    const move = ev => { seeking = seekFrom(ev, el); };
    const up = async ev => {
      el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up);
      const ms = seekFrom(ev, el); seeking = null;
      if (ms != null) { S.state.now.progress = ms; S.state.now.stamp = Date.now(); await api.player('seek', Math.round(ms)); }
    };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
  });
}

// ---------------------------------------------------------------- the Now Playing view (the right pane)
// The song's music video (muted, in time with the music) or its cover, a lyrics preview, the artist's other music
// videos, about the artist, credits, any tour they're on, and what's next. The bar's queue button turns it into the queue.
function toggleNV(mode) {
  const u = S.ui;
  if (u.nvOpen && S.nv.mode === mode) u.nvOpen = false; else { u.nvOpen = true; S.nv.mode = mode; }
  saveUi(); applyUi(); updateBar();
  if (u.nvOpen) nvRender(true); else nvStopVideo();
}
function openNV(mode = 'now') { if (!S.ui.nvOpen || S.nv.mode !== mode) toggleNV(mode); }
function toggleQueue() {
  const u = S.ui;
  if (u.nvOpen && S.nv.mode === 'queue') {                       // pressed again: back to where you were
    S.nv.mode = 'now';
    if (S.nv.qFromRail) { u.nvOpen = false; saveUi(); applyUi(); nvStopVideo(); }
    S.nv.qFromRail = false;
    updateBar(); nvRender(true);
    return;
  }
  S.nv.qFromRail = !u.nvOpen;
  u.nvOpen = true; S.nv.mode = 'queue'; saveUi(); applyUi(); updateBar(); nvRender(true);
}
function openQueue() { if (!(S.ui.nvOpen && S.nv.mode === 'queue')) toggleQueue(); }
async function getLyrics(t) {
  if (S.lyricsCache.has(t.uri)) return S.lyricsCache.get(t.uri);
  const l = await api.lyrics(t.title, t.artist, t.album, t.duration);
  const v = l && !l.error ? l : null;
  if (v) { S.lyricsCache.set(t.uri, v); if (S.lyricsCache.size > 60) S.lyricsCache.delete(S.lyricsCache.keys().next().value); }
  return v || { lines: [], plain: '', message: "Couldn't reach the lyrics right now." };
}
function nvLoad(t) {
  const key = t.uri, d = S.nv.d = { t };
  S.nv.key = key; S.nv.lyricIdx = -2;
  const done = (k, v) => { if (S.nv.key !== key) return; d[k] = v; nvRender(); };
  getLyrics(t).then(v => done('lyrics', v));
  api.about(t).then(v => done('about', v && !v.error ? v : {}));
  api.credits(t.id).then(v => done('credits', v && !v.error ? v : null));
  api.now_extras(t).then(v => done('extras', v && !v.error ? v : {}));
  getQueue().then(v => done('queue', v));
}
// the lyrics card takes the cover's colour, like Spotify's
function nvTint(url) {
  const pane = $('#nowview');
  if (!url) return pane.style.removeProperty('--tint');
  const im = new Image(); im.crossOrigin = 'anonymous';
  im.onload = () => {
    try {
      const c = document.createElement('canvas'); c.width = c.height = 12;
      const g = c.getContext('2d'); g.drawImage(im, 0, 0, 12, 12);
      const px = g.getImageData(0, 0, 12, 12).data;
      let r = 0, gg = 0, b = 0, w = 0;
      for (let i = 0; i < px.length; i += 4) {
        const mx = Math.max(px[i], px[i + 1], px[i + 2]), mn = Math.min(px[i], px[i + 1], px[i + 2]);
        const wt = 0.15 + (mx - mn) / 255;                       // colourful pixels count more than greys
        r += px[i] * wt; gg += px[i + 1] * wt; b += px[i + 2] * wt; w += wt;
      }
      pane.style.setProperty('--tint', `rgb(${Math.round(r / w)}, ${Math.round(gg / w)}, ${Math.round(b / w)})`);
    } catch (e) { pane.style.removeProperty('--tint'); }        // the picture's server didn't allow reading it
  };
  im.onerror = () => pane.style.removeProperty('--tint');
  im.src = url;
}
function nvSong() {
  const t = nowTrack(), box = $('#nv-song'); if (!t || !box) return;
  const liked = now().liked ?? S.liked.get(t.uri);
  box.innerHTML = `<div class="ell"><h2 class="ell"><button data-act="open-album" data-id="${esc(t.albumId || '')}">${esc(t.title)}</button></h2><p class="ell">${artistLinks(t)}</p></div>
    <button class="icon-btn ${liked ? 'liked' : ''}" data-act="like" data-uri="${esc(t.uri)}" title="${liked ? 'Remove from Liked Songs' : 'Save to your Liked Songs'}">${icon(liked ? 'checkCircle' : 'addCircle', 24)}</button>`;
}
function nvRender(force = false) {
  if (!S.ui.nvOpen) return;
  const body = $('#nv-body'), n = now(), t = nowTrack();
  const mixName = S.mixNow && t && S.mixNow.uris.has(t.uri) ? S.mixNow.name : '';
  $('#nv-ctx').innerHTML = S.nv.mode === 'queue' ? 'Queue' : n && n.contextName ? esc(n.contextName) : mixName ? esc(mixName) : 'Now playing';
  $('#nowview').classList.toggle('q', S.nv.mode === 'queue');
  if (S.nv.mode === 'queue') { nvStopVideo(); return nvQueue(force); }
  delete body.dataset.q;
  if (!t) { nvStopVideo(); body.innerHTML = `<div class="nv-quiet" style="padding:30px 6px">Play something and it shows up here: its video, lyrics, the artist, credits and what's next.</div>`; S.nv.key = ''; return; }
  if (S.nv.key !== t.uri || force && !S.nv.d.t) nvLoad(t);
  const d = S.nv.d, x = d.extras || {}, a = d.about || {}, c = d.credits;
  let media = $('#nv-media');
  if (!media || media.dataset.uri !== t.uri) {
    nvStopVideo();
    body.innerHTML = `<div class="nv-media" id="nv-media" data-uri="${esc(t.uri)}">${art(t.art || t.artMid, 0, { icon: 'note' })}<div class="nv-song" id="nv-song"></div></div><div id="nv-rest"></div>`;
    media = $('#nv-media');
    nvSong(); nvTint(t.artMid || t.art);
  }
  if (x.video && !S.nv.video && !S.nv.failed.has(x.video.id)) nvStartVideo(x.video.id);
  const lines = d.lyrics && d.lyrics.lines && d.lyrics.lines.length ? d.lyrics.lines : null;
  const plain = !lines && d.lyrics && d.lyrics.plain ? d.lyrics.plain.split('\n').filter(Boolean) : null;
  const parts = [];
  if (lines || plain) parts.push(`<section class="nv-card nv-lyrics" data-act="nv-lyrics" title="Show the full lyrics"><h3>Lyrics preview</h3><div class="lines" id="nv-lines">${(plain || []).slice(0, 5).map(l => `<p>${esc(l)}</p>`).join('')}</div></section>`);
  if (x.videos && x.videos.length) parts.push(`<section class="nv-card"><h3>Related music videos</h3><div class="nv-vids">${x.videos.map(v => `<button class="nv-vid" data-act="nv-video" data-title="${esc(v.title)}" title="Play ${esc(v.title)}"><div class="thumb" style="background-image:url('https://i.ytimg.com/vi/${esc(v.id)}/mqdefault.jpg')"></div><b class="ell">${esc(v.title)}</b><small>${esc(t.artist)}${v.year ? ' • ' + esc(v.year) : ''}</small></button>`).join('')}</div></section>`);
  const aid = t.artists && t.artists[0] && t.artists[0].id;
  if (d.about) parts.push(`<section class="nv-card nv-artist" ${aid ? `data-act="open-artist" data-id="${esc(aid)}" role="button"` : ''} style="${a.artistImage ? `background-image:url('${esc(a.artistImage)}')` : ''}"><span class="label">About the artist</span>
    <div class="in"><b>${esc(t.artist)}</b><span class="meta">${a.followers ? plural(a.followers, 'follower') : ''}${a.followers && a.genres && a.genres.length ? ' • ' : ''}${a.genres && a.genres.length ? esc(a.genres.slice(0, 3).join(', ')) : ''}</span>${a.bio ? `<p>${esc(a.bio)}</p>` : ''}</div></section>`);
  if (c) {
    const rows = [...c.performers.slice(0, 3).map(nm => [nm, 'Main Artist']), ...c.writers.slice(0, 3).map(nm => [nm, 'Writer']), ...c.producers.slice(0, 2).map(nm => [nm, 'Producer'])];
    const merged = [];
    for (const [nm, role] of rows) { const m = merged.find(r => r[0] === nm); if (m) m[1] += `, ${role}`; else merged.push([nm, role]); }
    parts.push(`<section class="nv-card"><h3>Credits<button class="more" data-act="nv-credits">Show all</button></h3>${merged.slice(0, 5).map(([nm, role]) => `<div class="nv-credit"><div class="ell"><b class="ell">${esc(nm)}</b><small>${esc(role)}</small></div></div>`).join('')}</section>`);
  }
  if (x.tours && x.tours.length) parts.push(`<section class="nv-card"><h3>On tour</h3>${x.tours.map(tr => { const dt = tr.start ? new Date(tr.start + 'T12:00:00') : null; return `<button class="nv-tour" ${tr.url ? `data-act="open-url" data-url="${esc(tr.url)}"` : ''}><div class="cal">${dt ? `<small>${dt.toLocaleString(undefined, { month: 'short' }).toUpperCase()}</small>${dt.getDate()}` : icon('ticket', 20)}</div><div class="ell"><b class="ell">${esc(tr.name)}</b><span>${tr.start ? `From ${esc(prettyDate(tr.start))}` : ''}${tr.end ? ` to ${esc(prettyDate(tr.end))}` : ''}</span></div></button>`; }).join('')}</section>`);
  if (d.queue) { const q = d.queue, first = q.queued[0] || q.next[0];
    parts.push(`<section class="nv-card nv-next"><h3>Next in queue<button class="more" data-act="nv-queue">Open queue</button></h3>${first ? qRow(first, { q: q.queued[0] ? 'queued' : 'next', nth: 0 }) : '<div class="nv-quiet">Nothing queued after this song.</div>'}</section>`); }
  $('#nv-rest').innerHTML = parts.join('');
  S.nv.lyricIdx = -2; nvTick(progressNow());
}
function qRow(x, o = {}) {
  return `<div class="q-row ${o.now ? 'now' : ''}" data-row data-uri="${esc(x.uri)}" ${o.q ? `data-q="${o.q}" data-nth="${o.nth || 0}"` : ''}>${art(x.artMid, 48)}<div class="ell"><b class="ell">${esc(x.title)}</b><small class="ell">${esc(x.artistLine)}</small></div>${o.now ? '' : `<button class="icon-btn" data-act="track-menu" data-uri="${esc(x.uri)}" title="More options">${icon('more', 18)}</button>`}</div>`;
}
// a song can be in the queue twice (queued and in the playlist), so each row says which time it is
function queueHTML(q, t) {
  const seen = {};
  const row = (x, sec) => { const k = seen[x.uri] || 0; seen[x.uri] = k + 1; return qRow(x, { q: sec, nth: k }); };
  let from = q.from || '';
  if (!from && S.mixNow && t && S.mixNow.uris.has(t.uri)) from = S.mixNow.name;
  return `<div class="nv-queue">${t ? `<h4>Now playing</h4>${qRow(t, { now: true })}` : ''}
    ${q.queued.length ? `<h4 class="gap">Next in queue</h4>${q.queued.map(x => row(x, 'queued')).join('')}` : ''}
    <h4 class="gap">${from ? `Next from: ${esc(from)}` : 'Next up'}</h4>${q.next.length ? q.next.map(x => row(x, 'next')).join('') : '<div class="nv-quiet">Nothing else coming up.</div>'}</div>`;
}
async function getQueue() {
  const q = await api.queue();
  const v = q && !q.error && Array.isArray(q.next) ? q : { queued: [], next: [], from: '' };
  remember(v.queued); remember(v.next);
  return v;
}
async function removeFromQueue(uri, nth, section) {
  const r = await api.queue_remove(uri, nth || 0, section);
  if (r === true) { toast('Removed from queue', 'minus'); refreshQueues(); }
  else toast(typeof r === 'string' ? r : "Couldn't take that out of the queue.", 'warning');
}
async function addToQueue(uri) {
  const ok = await api.queue_add(uri);
  toast(ok === true ? 'Added to queue' : "Spotify wouldn't queue that.", ok === true ? 'queue' : 'warning');
  if (ok === true) setTimeout(refreshQueues, 700);           // Spotify takes a moment to show it
}
function refreshQueues() {
  if (S.ui.nvOpen && S.nv.mode === 'queue') nvQueue(false);
  if (S.ui.nvOpen && S.nv.mode === 'now' && S.nv.d && S.nv.d.t) getQueue().then(v => { S.nv.d.queue = v; nvRender(); });
  if (S.np.open && S.np.tab === 'queue') loadNPTab();
}
async function nvQueue(force) {
  const body = $('#nv-body'), t = nowTrack();
  if (force || !body.dataset.q) body.innerHTML = loading();
  body.dataset.q = '1';
  const q = await getQueue();
  if (S.nv.mode !== 'queue' || !S.ui.nvOpen) return;
  body.innerHTML = queueHTML(q, t);
}
function nvTick(p) {
  if (S.nv.mode !== 'now') return;
  const d = S.nv.d, box = $('#nv-lines');
  const lines = d && d.lyrics && d.lyrics.lines;
  if (box && lines && lines.length) {
    let lo = 0, hi = lines.length - 1, found = -1; const pos = p + 250;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (lines[mid].t <= pos) { found = mid; lo = mid + 1; } else hi = mid - 1; }
    if (found !== S.nv.lyricIdx) {
      S.nv.lyricIdx = found;
      const from = Math.max(0, found);
      box.innerHTML = lines.slice(from, from + 5).map((l, i) => `<p class="${from + i === found ? 'now' : ''}">${esc(l.text || '♪')}</p>`).join('');
    }
  }
  nvSyncVideo(p);
}
// the music video: YouTube's own player, muted (the music comes from Spotify), kept in time with the song
function nvStartVideo(id) {
  const media = $('#nv-media'); if (!media) return;
  const f = document.createElement('iframe');
  const start = Math.floor(progressNow() / 1000);
  f.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&mute=1&controls=0&disablekb=1&fs=0&iv_load_policy=3&modestbranding=1&playsinline=1&rel=0&loop=1&playlist=${encodeURIComponent(id)}&enablejsapi=1&start=${start}&origin=${encodeURIComponent(location.origin)}`;
  f.allow = 'autoplay; encrypted-media; picture-in-picture';
  f.title = 'Music video';
  f.setAttribute('tabindex', '-1');
  S.nv.video = { id, el: f, time: null, at: 0, state: -1, synced: 0 };
  f.addEventListener('load', () => { try { f.contentWindow.postMessage(JSON.stringify({ event: 'listening', id: 1, channel: 'widget' }), '*'); } catch (e) { /* gone */ } });
  media.insertBefore(f, media.querySelector('.nv-song'));
}
function nvVideoCmd(func, args = []) { const v = S.nv.video; if (v && v.el.contentWindow) v.el.contentWindow.postMessage(JSON.stringify({ event: 'command', func, args }), '*'); }
function nvStopVideo() { const v = S.nv.video; if (v) { v.el.remove(); S.nv.video = null; } const b = $('#nv-media .badge'); if (b) b.remove(); }
function nvSyncVideo(p) {
  const v = S.nv.video; if (!v || v.state < 0) return;
  const playing = !!(now() && now().playing);
  if (!playing && v.state === 1) nvVideoCmd('pauseVideo');
  if (playing && v.state === 2) nvVideoCmd('playVideo');
  if (v.time == null || Date.now() - v.synced < 4000) return;
  const vt = v.time + (v.state === 1 ? (Date.now() - v.at) / 1000 : 0);
  if (Math.abs(vt - p / 1000) > 2.5) { v.synced = Date.now(); nvVideoCmd('seekTo', [p / 1000, true]); }
}
window.addEventListener('message', e => {
  const v = S.nv.video; if (!v || e.source !== v.el.contentWindow) return;
  let m; try { m = typeof e.data === 'string' ? JSON.parse(e.data) : e.data; } catch (err) { return; }
  if (!m || !m.event) return;
  if (m.event === 'onError') { S.nv.failed.add(v.id); nvStopVideo(); return; }   // this one can't play outside YouTube
  const info = m.info;
  if (m.event === 'onStateChange' && typeof info === 'number') v.state = info;
  if (m.event === 'infoDelivery' && info) {
    if (typeof info.playerState === 'number') v.state = info.playerState;
    if (typeof info.currentTime === 'number') { v.time = info.currentTime; v.at = Date.now(); }
  }
  if (v.state === 1 && !v.el.classList.contains('on')) {
    v.el.classList.add('on');
    const media = $('#nv-media');
    if (media && !media.querySelector('.badge')) media.insertAdjacentHTML('afterbegin', `<span class="badge">${icon('video', 14)}Music video</span>`);
  }
});

// ---------------------------------------------------------------- the big player (lyrics, up next, about)
function openNP(tabName) {
  if (tabName) S.np.tab = tabName;
  S.np.open = true; $('#np').classList.add('open'); updateNP(true);
}
function closeNP() { S.np.open = false; $('#np').classList.remove('open'); }
function updateNP(force = false) {
  if (!S.np.open && !force) return;
  const n = now(), t = n && n.track, d = (S.state && S.state.dj) || {};
  const np = $('#np');
  np.classList.toggle('paused', !(n && n.playing));
  const key = t ? t.uri : '';
  if (np.dataset.uri !== key || force) {
    np.dataset.uri = key;
    $('#np-bg').style.backgroundImage = t && (t.art || t.artMid) ? `url('${t.art || t.artMid}')` : 'none';
    $('#np-art').innerHTML = art(t ? (t.art || t.artMid) : '', 0, { icon: 'note' });
    $('#np-title').textContent = t ? t.title : 'Nothing playing';
    $('#np-artist').innerHTML = t ? artistLinks(t) : '';
    $('#np-from').textContent = n && n.contextName ? `Playing from ${n.contextName}` : 'Now playing';
    loadNPTab();
  }
  const liked = t && (n.liked ?? S.liked.get(t.uri));
  const lb = $('#np-like'); lb.classList.toggle('liked', !!liked); lb.innerHTML = icon(liked ? 'checkCircle' : 'addCircle', 26);
  $('#np-play').innerHTML = icon(n && n.playing ? 'pause' : 'play', 34);
  $('#np-shuffle').classList.toggle('on', !!(n && n.shuffle));
  const rep = (n && n.repeat) || 'off';
  $('#np-repeat').classList.toggle('on', rep !== 'off'); $('#np-repeat').innerHTML = icon(rep === 'track' ? 'repeatOne' : 'repeat', 24);
  $('#np-device').innerHTML = n && n.device ? `${icon(n.device.type === 'computer' ? 'computer' : n.device.type === 'smartphone' ? 'phone' : 'speaker', 16)}${esc(n.device.name)}` : '';
  const cap = $('#np-caption');
  const line = d.kind === 'duo' && d.duo && d.duo.length ? d.duo.map(r => `${r.who}: ${r.text}`).join('  ') : (d.line || '').replace(/\[[^\]]*\]/g, '');
  cap.classList.toggle('on', !!(d.speaking && line));
  if (d.speaking && line) cap.innerHTML = `${bars(true)}<span>${esc(line)}</span>`;
  $$('#np .tabs .chip').forEach(c => c.classList.toggle('on', c.dataset.tab === S.np.tab));
}
async function loadNPTab() {
  const t = nowTrack(), panel = $('#np-panel'), tabName = S.np.tab;
  if (!t) { panel.innerHTML = `<div class="lyrics"><div class="note">Nothing playing.</div></div>`; return; }
  if (tabName === 'lyrics') {
    const key = t.uri;
    if (S.np.lyricsKey !== key) {
      S.np.lyricsKey = key; S.np.lyrics = null;
      panel.innerHTML = `<div class="lyrics"><div class="note">Finding the lyrics…</div></div>`;
      const l = await api.lyrics(t.title, t.artist, t.album, t.duration);
      if (S.np.lyricsKey !== key) return;
      S.np.lyrics = l && !l.error ? l : { lines: [], plain: '', message: "Couldn't reach the lyrics right now." };
    }
    const l = S.np.lyrics;
    if (!l) return;
    if (l.lines && l.lines.length) panel.innerHTML = `<div class="lyrics" id="lyrics">${l.lines.map((x, i) => `<p data-act="lyric" data-t="${x.t}" data-i="${i}">${esc(x.text || '♪')}</p>`).join('')}</div>`;
    else if (l.plain) panel.innerHTML = `<div class="lyrics plain">${l.plain.split('\n').map(x => `<p>${esc(x) || '&nbsp;'}</p>`).join('')}</div>`;
    else panel.innerHTML = `<div class="lyrics"><div class="note">${esc(l.message || 'No lyrics for this one.')}</div></div>`;
    S.np.lyricIdx = -1;
  } else if (tabName === 'queue') {
    panel.innerHTML = loading();
    const q = await getQueue();
    if (S.np.tab !== 'queue') return;
    panel.innerHTML = q.queued.length || q.next.length ? queueHTML(q, null) : `<div class="lyrics"><div class="note">Nothing queued after this song.</div></div>`;
  } else {
    if (S.np.aboutKey !== t.uri) {
      S.np.aboutKey = t.uri; S.np.about = null;
      panel.innerHTML = loading();
      const a = await api.about(t);
      if (S.np.aboutKey !== t.uri) return;
      S.np.about = a && !a.error ? a : {};
    }
    const a = S.np.about || {};
    const aid = t.artists && t.artists[0] && t.artists[0].id;
    panel.innerHTML = `${a.song ? `<div class="about-box"><h3>About “${esc(t.title)}”</h3><p>${esc(a.song)}</p></div>` : ''}
      <div class="about-artist" style="background-image:url('${esc(a.artistImage || '')}')"><div class="in"><span class="caps" style="color:rgba(255,255,255,.75)">Artist</span><b>${esc(t.artist)}</b>
      <span style="color:rgba(255,255,255,.75)">${a.followers ? plural(a.followers, 'follower') : ''}${a.genres && a.genres.length ? ' • ' + esc(a.genres.join(', ')) : ''}</span>
      ${aid ? `<div style="margin-top:10px"><button class="pill secondary small" data-act="open-artist" data-id="${esc(aid)}">Go to artist</button></div>` : ''}</div></div>
      ${a.bio ? `<div class="about-box"><p>${esc(a.bio)}</p></div>` : ''}
      <div class="about-box"><h3>Credits</h3><p>${esc(t.title)}\n${esc(t.artistLine)}\n${esc(t.album)}${t.year ? ' (' + esc(t.year) + ')' : ''}</p></div>`;
  }
}
function syncLyrics(p) {
  const l = S.np.lyrics; if (!l || !l.lines || !l.lines.length) return;
  let lo = 0, hi = l.lines.length - 1, found = -1; const pos = p + 250;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (l.lines[mid].t <= pos) { found = mid; lo = mid + 1; } else hi = mid - 1; }
  if (found === S.np.lyricIdx) return;
  S.np.lyricIdx = found;
  const box = $('#lyrics'); if (!box) return;
  $$('p', box).forEach((el, i) => { el.classList.toggle('now', i === found); el.classList.toggle('past', i < found); });
  const cur = box.children[found];
  if (cur && !S.np.userScrolled) {
    const panel = $('#np-panel');
    panel.scrollTo({ top: cur.offsetTop - panel.clientHeight * 0.38, behavior: 'smooth' });
  }
}

// ---------------------------------------------------------------- menus and sheets
function menuHtml(items, plain) {
  return items.map((it, i) => it === '-' ? '<hr>' : it.head ? `<div class="menu-head">${esc(it.head)}</div>`
    : it.search ? `<div class="menu-search">${icon('search', 16)}<input data-msearch placeholder="${esc(it.search)}" spellcheck="false" autocomplete="off"></div>`
    : `<button data-i="${i}" class="${it.on ? 'on' : ''}${it.sub ? ' has-sub' : ''}" ${it.disabled ? 'disabled' : ''} ${it.f ? `data-f="${esc(it.label.toLowerCase())}"` : ''}>`
      + (plain ? `<span class="chk">${it.check ? icon('check', 16) : ''}</span>` : icon(it.icon || 'note', 18))
      + `<span class="ell">${esc(it.label)}</span>${it.kbd ? `<span class="kbd">${esc(it.kbd)}</span>` : ''}`
      + `${it.sub ? `<span class="sub-arrow">${icon('forward', 16)}</span>` : ''}</button>`).join('');
}
// Menus, Spotify style: an item with `sub` (a list, or a function that makes one) opens another menu beside it,
// as deep as needed. { plain: true } draws them like Spotify's … menu: no icons, check marks and shortcuts instead.
const MENUS = [];
function closeMenusFrom(level) {
  while (MENUS.length > level) { const m = MENUS.pop(); m.el.remove(); if (m.from) m.from.classList.remove('open'); }
}
function closeSub() { closeMenusFrom(1); }
function closeMenu() { closeMenusFrom(0); const d = $('#tb-menu'); if (d) d.classList.remove('open'); }
function showMenu(items, x, y, o = {}) { closeMenu(); openMenuLevel(items, x / zf(), y / zf(), 0, null, o); }
function openMenuLevel(items, x, y, level, from, o) {
  const z = zf(), W = innerWidth / z, H = innerHeight / z;
  const m = document.createElement('div');
  m.id = level ? (level > 1 ? 'menu-sub' + level : 'menu-sub') : 'menu';
  m.className = 'menu' + (o.plain ? ' plain' : '');
  m.innerHTML = menuHtml(items, o.plain);
  document.body.appendChild(m);
  // sizes from layout (offset*), not getBoundingClientRect: menus pop in scaled down, which would skew them
  let left = x, top = y;
  if (level) {
    const p = MENUS[level - 1].el, px = parseFloat(p.style.left), pw = p.offsetWidth;
    left = px + pw - 4;
    if (left + m.offsetWidth > W - 8) left = px - m.offsetWidth + 4;          // no room on the right: open on the left
    top = parseFloat(p.style.top) + from.offsetTop - 6 - p.scrollTop;
  }
  m.style.left = Math.max(8, Math.min(left, W - m.offsetWidth - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(top, H - m.offsetHeight - 8)) + 'px';
  const entry = { el: m, from, items, timer: 0, ticket: 0 };
  MENUS[level] = entry;
  if (from) from.classList.add('open');
  const openSub = async b => {
    const next = MENUS[level + 1];
    if (next && next.from === b) return;
    closeMenusFrom(level + 1);
    const it = items[+b.dataset.i], ticket = ++entry.ticket;
    const sub = typeof it.sub === 'function' ? await it.sub() : it.sub;
    if (ticket !== entry.ticket || MENUS[level] !== entry || !sub || !sub.length) return;
    openMenuLevel(sub, 0, 0, level + 1, b, o);
  };
  m.addEventListener('mousedown', e => { if (!e.target.closest('input')) e.preventDefault(); });   // keep the cursor where it was (Edit > Copy and friends)
  const find = m.querySelector('[data-msearch]');
  if (find) {                                                   // a search box at the top: narrows the list as you type
    find.addEventListener('input', () => { const v = find.value.trim().toLowerCase(); m.querySelectorAll('button[data-f]').forEach(b => { b.hidden = !!v && !b.dataset.f.includes(v); }); });
    setTimeout(() => find.focus(), 40);
  }
  m.addEventListener('mouseover', e => {
    const b = e.target.closest('button[data-i]'); if (!b) return;
    clearTimeout(entry.timer);
    entry.timer = setTimeout(() => { if (items[+b.dataset.i].sub && !b.disabled) openSub(b); else { entry.ticket++; closeMenusFrom(level + 1); } }, 120);
  });
  m.addEventListener('click', e => {
    const b = e.target.closest('button[data-i]'); if (!b || b.disabled) return;
    e.stopPropagation();
    const it = items[+b.dataset.i];
    if (it.sub) { clearTimeout(entry.timer); openSub(b); return; }
    closeMenu(); if (it.run) it.run();
  });
  return m;
}

// ---------------------------------------------------------------- the … menu (Spotify's, plus everything Cara)
function playback() { return now() || {}; }
function prevTrack() { if (progressNow() > 3000) cmd('seek', 0); else cmd('previous'); }
function seekBy(ms) { const t = nowTrack(); if (!t) return; const p = Math.max(0, Math.min(t.duration - 1000, progressNow() + ms)); S.state.now.progress = p; S.state.now.stamp = Date.now(); cmd('seek', Math.round(p)); }
function shuffleOn() { const n = now(); return n && n.device ? !!n.shuffle : !!S.ui.shuffle; }
function toggleShuffle() {
  const on = !shuffleOn(), n = now();
  S.ui.shuffle = on; saveUi();
  if (n && n.device) { n.shuffle = on; cmd('shuffle', on); }
  updateBar();
}
function shuffleBtn() {
  const on = shuffleOn();
  return `<button class="icon-btn page-shuffle ${on ? 'on' : ''}" data-act="shuffle-toggle" title="${on ? 'Turn off shuffle' : 'Shuffle'}" aria-pressed="${on}">${icon('shuffle', 26)}</button>`;
}
function cycleRepeat() { const r = playback().repeat || 'off'; cmd('repeat', r === 'off' ? 'context' : r === 'context' ? 'track' : 'off'); }
function volumeBy(d) { const v = Math.max(0, Math.min(100, volNow() + d)); setVolume(v); toast(`Volume ${v}%`, v ? 'volume' : 'volumeOff'); }
// Volume: the bar's slider, the big player's and the visualizer's all move together, and the music follows as you drag
// (or scroll the mouse wheel over any of them). Sends are spaced out a little so Spotify isn't flooded.
let volTimer = 0, volSent = 0;
function volNow() { const dev = playback().device; return dev && dev.volume != null ? dev.volume : (S.volume ?? 60); }
function playingHere() {
  const dev = playback().device, pl = (S.state && S.state.player) || {};
  return !!dev && ((pl.deviceId && dev.id === pl.deviceId) || dev.name === (pl.name || 'Non Stop Pop DJ'));
}
function syncVolume(v) {
  S.volume = v;
  $$('.vol-range').forEach(r => { if (+r.value !== v) r.value = v; paintRange(r); });
  $$('[data-act=mute]').forEach(b => { b.innerHTML = icon(v === 0 ? 'volumeOff' : 'volume', +b.dataset.size || 20); b.title = v === 0 ? 'Unmute' : 'Mute'; });
}
function setVolume(v) {
  v = Math.max(0, Math.min(100, Math.round(v)));
  S.volT = Date.now();
  const dev = playback().device; if (dev) dev.volume = v;
  syncVolume(v);
  const gap = playingHere() ? 90 : 300, since = Date.now() - volSent;
  const send = () => { volSent = Date.now(); volTimer = 0; api.player('volume', S.volume); };
  clearTimeout(volTimer);
  if (since >= gap) send(); else volTimer = setTimeout(send, gap - since);
}
function focusSearch() { const q = $('#search-input'); q.focus(); q.select(); }
function focusFilter() { if (S.ui.libMini) toggleLibrary(); const f = $('#lib-filter'); f.hidden = false; f.focus(); f.select(); }
function editCmd(c) { try { document.execCommand(c); } catch (e) { /* nothing focused */ } }
function pasteText() { navigator.clipboard.readText().then(t => document.execCommand('insertText', false, t), () => toast('Press Ctrl+V to paste', 'warning')); }
async function logOut() { await api.logout(); S.home = null; S.feed = null; S.mixes.clear(); S.mixNow = null; renderLibrary(); render(); toast('Logged out of Spotify', 'person'); }
function djTest(what) { ACTIONS['dj-test']({ dataset: { what } }); }
function djQueue(style) { ACTIONS['dj-queue']({ dataset: { style } }); }
function mainMenu() {
  const n = playback(), d = (S.state && S.state.dj) || {}, live = !!d.running;
  const k = (label, kbd, run, o = {}) => Object.assign({ label, kbd, run }, o);
  return [
    { label: 'File', sub: () => [
      k('New Playlist', 'Ctrl+N', () => askName()), '-',
      k('Log Out', 'Ctrl+Shift+W', logOut), k('Exit', 'Ctrl+Shift+Q', () => api.quit())] },
    { label: 'Edit', sub: () => [
      k('Undo', 'Ctrl+Z', () => editCmd('undo')), k('Redo', 'Ctrl+Y', () => editCmd('redo')), '-',
      k('Cut', 'Ctrl+X', () => editCmd('cut')), k('Copy', 'Ctrl+C', () => editCmd('copy')), k('Paste', 'Ctrl+V', pasteText), k('Delete', 'Del', () => editCmd('delete')), '-',
      k('Select All', 'Ctrl+A', () => editCmd('selectAll')), '-',
      k('Search', 'Ctrl+L', focusSearch), k('Filter', 'Ctrl+F', focusFilter), '-',
      k('Preferences…', 'Ctrl+P', () => go({ name: 'prefs' }))] },
    { label: 'View', sub: () => [
      k('Zoom In', 'Ctrl+=', () => zoomBy(10)), k('Zoom Out', 'Ctrl+-', () => zoomBy(-10)), k('Reset Zoom', 'Ctrl+0', () => setZoom(100)), '-',
      k('Your Library', '', toggleLibrary, { check: !S.ui.libMini }), k('Now Playing View', '', () => toggleNV('now'), { check: S.ui.nvOpen && S.nv.mode === 'now' }),
      k('Queue', '', toggleQueue, { check: S.ui.nvOpen && S.nv.mode === 'queue' }), '-',
      { label: 'Background', sub: () => [['song', "Song's Colours"], ['black', 'Black'], ['white', 'White']].map(([v, l]) => k(l, '', () => setBg(v), { check: S.ui.bg === v })) }, '-',
      k('Full Screen Player', '', () => openNP()), k('Visualizer', 'V', () => Vis.open())] },
    { label: 'Playback', sub: () => [
      k(n.playing ? 'Pause' : 'Play', 'Space', () => cmd('toggle')), '-',
      k('Next', 'Ctrl+Right Arrow', () => cmd('next')), k('Previous', 'Ctrl+Left Arrow', prevTrack),
      k('Seek Forward', 'Shift+Right Arrow', () => seekBy(5000)), k('Seek Backward', 'Shift+Left Arrow', () => seekBy(-5000)), '-',
      k('Shuffle', 'Ctrl+S', toggleShuffle, { check: !!n.shuffle }), k('Repeat', 'Ctrl+R', cycleRepeat, { check: (n.repeat || 'off') !== 'off' }), '-',
      k('Volume Up', 'Ctrl+Up Arrow', () => volumeBy(10)), k('Volume Down', 'Ctrl+Down Arrow', () => volumeBy(-10)), '-',
      { label: 'Sleep Timer', sub: () => sleepItems().map(x => x === '-' ? x : Object.assign({}, x, { check: false })) },
      { label: 'Play On', sub: async () => (await deviceItems()).map(x => x === '-' ? x : Object.assign({}, x, { check: !!x.on })) }] },
    { label: 'Cara', sub: () => [
      k(live ? 'End Her Show' : 'Go Live', '', () => ACTIONS['dj-toggle'](), { check: live }), '-',
      k('Talk Now', '', () => djTest('talk'), { disabled: !live }), k('Pop In Now', '', () => djTest('popin'), { disabled: !live }),
      k('Cara and Scratch Now', '', () => djTest('duo'), { disabled: !live }), k('Breaking News Now', '', () => djTest('break'), { disabled: !live }),
      k('Stinger Now', '', () => djTest('stinger'), { disabled: !live }), '-',
      { label: 'Her Next Break Starts', sub: () => [['talkover', 'Talking Over the End'], ['intro', 'Over the Next Intro'], ['silent', 'Silent'], ['fadeout', 'Fading the Song Out']].map(([v, l]) => k(l, '', () => djQueue(v), { check: d.queued === v })) }, '-',
      k("Cara's Studio…", '', () => go({ name: 'cara' })), k('Visualizer', 'V', () => Vis.open())] },
    { label: 'Help', sub: () => [
      k('Spotify Help', 'F1', () => api.open_url('https://support.spotify.com/')), k('Spotify Community', '', () => api.open_url('https://community.spotify.com/')),
      k('Your Account', '', () => api.open_url('https://www.spotify.com/account/overview/')), '-',
      k('Third-party Software', '', thirdPartySheet), { label: 'Troubleshooting', sub: () => [
        k('Activity Log', '', () => go({ name: 'cara', focusLog: true })), k('Start the Built-in Player Over', '', () => ACTIONS['player-retry']()),
        k('Show the Welcome Setup', '', () => showWelcome(true))] }, '-',
      k('About Non Stop Pop DJ', '', aboutSheet)] },
  ];
}
function openMainMenu() {
  const b = $('#tb-menu');
  if ($('#menu') && b.classList.contains('open')) return closeMenu();
  const r = b.getBoundingClientRect();
  showMenu(mainMenu(), r.left, r.bottom + 6, { plain: true });
  b.classList.add('open');
}
function accountMenu() {
  const r = $('#tb-me').getBoundingClientRect(), me = S.state && S.state.me;
  const items = [{ head: me ? me.name : 'Account' },
    { label: 'Account', icon: 'open', run: () => api.open_url('https://www.spotify.com/account/overview/') },
    { label: 'Profile', icon: 'person', disabled: !(me && me.id), run: () => api.open_url(`https://open.spotify.com/user/${me.id}`) },
    { label: 'Settings', icon: 'settings', run: () => go({ name: 'prefs' }) }, '-',
    S.state && S.state.connected ? { label: 'Log out', icon: 'close', run: logOut } : { label: 'Connect Spotify', icon: 'link', run: () => ACTIONS.connect() }];
  showMenu(items, r.right - 240, r.bottom + 6);
}
function aboutSheet() {
  sheet('About Non Stop Pop DJ', `<div class="pick-row" style="cursor:default"><div class="art liked-art" style="width:56px;height:56px;border-radius:14px;background:var(--cara)">${bars(true)}</div>
    <div class="ell"><b>Non Stop Pop DJ ${esc((S.boot && S.boot.version) || '')}</b><small class="muted">Your music on Spotify, with Cara on the radio.</small></div></div>
    <p class="muted">Music comes from Spotify. Lyrics from LRCLIB, credits from MusicBrainz, music videos and tours from Wikidata (videos play from YouTube). Your keys are saved on this PC only.</p>
    <div class="sheet-buttons"><button class="pill primary" data-act="sheet-close">Done</button></div>`);
}
function thirdPartySheet() {
  const rows = [['Spotify Web API and Web Playback SDK', 'Spotify'], ['pywebview', 'BSD'], ['pygame', 'LGPL'], ['spotipy', 'MIT'], ['NumPy', 'BSD'], ['SoundCard', 'BSD'],
    ['Butterchurn', 'MIT'], ['MilkDrop presets', 'see the presets folder'], ['HLSL parser', 'MIT'], ['Material Design Icons', 'Apache 2.0'],
    ['MusicBrainz data', 'CC0'], ['Wikidata', 'CC0'], ['LRCLIB lyrics', 'LRCLIB'], ['YouTube embedded player', 'YouTube']];
  sheet('Third-party Software', `<div class="credits">${rows.map(([n, l]) => `<div class="credit"><div class="caps">${esc(l)}</div><div>${esc(n)}</div></div>`).join('')}</div>`);
}
function trackMenu(uri, x, y, o = {}) {
  const t = S.tracks.get(uri) || (nowUri() === uri ? nowTrack() : null); if (!t) return;
  const liked = S.liked.get(uri) || (nowUri() === uri && now().liked);
  const arts = (t.artists || []).filter(a => a.id);
  const link = t.id ? `https://open.spotify.com/track/${t.id}` : '';
  const out = isExcluded(t);
  const items = [];
  if (t.id) items.push({ label: 'Add to playlist', icon: 'plus', sub: () => playlistItems(t) });
  if (o.q) items.push({ label: 'Remove from queue', icon: 'minus', run: () => removeFromQueue(uri, o.nth, o.q) });
  if (t.id) items.push({ label: liked ? 'Remove from Liked Songs' : 'Save to your Liked Songs', icon: liked ? 'heart' : 'heartOutline', on: !!liked, run: () => toggleLike(uri) });
  items.push({ label: 'Add to queue', icon: 'queue', run: () => addToQueue(uri) });
  if (t.id) items.push({ label: out ? 'Include in your taste profile' : 'Exclude from your taste profile', icon: out ? 'checkCircle' : 'close', run: () => tasteToggle(t, !out) },
    { label: 'Start a Jam', icon: 'people', run: () => jamSheet(t) });
  items.push({ label: 'Sleep timer', icon: 'moon', sub: sleepItems }, '-');
  if (t.id) items.push({ label: 'Go to song radio', icon: 'radio', run: () => go({ name: 'radio', id: t.id }) });
  if (arts.length > 1) items.push({ label: 'Go to artist', icon: 'person', sub: arts.map(a => ({ label: a.name, icon: 'person', run: () => go({ name: 'artist', id: a.id }) })) });
  else if (arts.length) items.push({ label: 'Go to artist', icon: 'person', run: () => go({ name: 'artist', id: arts[0].id }) });
  if (t.albumId) items.push({ label: 'Go to album', icon: 'album', run: () => go({ name: 'album', id: t.albumId }) });
  if (t.id) items.push({ label: 'View credits', icon: 'note', run: () => creditsSheet(t) }, '-',
    { label: 'Share', icon: 'share', sub: [
      { label: 'Copy song link', icon: 'link', run: () => copy(link) },
      { label: 'Copy embed code', icon: 'note', run: () => copy(`<iframe style="border-radius:12px" src="https://open.spotify.com/embed/track/${t.id}" width="100%" height="152" frameBorder="0" allowfullscreen="" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>`, 'Embed code copied') },
      { label: 'Copy song and artist', icon: 'quote', run: () => copy(`${t.title} - ${t.artistLine}`, 'Copied') }] });
  showMenu(items, x, y);
}
async function playlistItems(t, skipId) {
  if (!S.home) await loadHome();
  await loadAllPlaylists();
  const me = myId();
  const rows = ((S.home && S.home.playlists) || []).filter(p => (p.ownerId === me || p.collaborative) && p.id !== skipId)
    .map(p => ({ kind: 'playlist', key: 'playlist:' + p.id, id: p.id, name: p.name, ctx: p.uri, owner: p.owner || '' }));
  const sorted = S.ui.libSort === 'alpha' ? rows.sort((a, b) => a.name.localeCompare(b.name)) : S.ui.libSort === 'creator' ? rows.sort((a, b) => a.owner.localeCompare(b.owner) || a.name.localeCompare(b.name)) : byRecent(rows);
  const add = async pid => {
    let ok;
    if (t.uri) ok = await api.add_to_playlist(pid, t.uri) === true;
    else { const n = await api.add_many_to_playlist(pid, t.kind, t.id); ok = typeof n === 'number' && n > 0; }
    toast(ok ? 'Added to the playlist' : "Spotify wouldn't add it.", ok ? 'check' : 'warning'); loadHome(true);
  };
  return [{ search: 'Find a playlist' }, { label: 'New playlist', icon: 'plus', run: async () => { const p = await askName(); if (p) add(p.id); } },
    ...(sorted.length ? ['-'] : []), ...sorted.map(p => ({ label: p.name, icon: 'queue', f: true, run: () => add(p.id) }))];
}
// "Exclude from your taste profile": Cara and Scratch stop reading anything into the song. Spotify's own taste
// profile can only be changed in the Spotify app (apps like this one can't), so that's offered too.
const tasteKey = t => `${t.title}|${t.artist}`.toLowerCase();
function isExcluded(t) { if (!S.excluded) S.excluded = new Set((S.boot && S.boot.excluded) || []); return S.excluded.has(tasteKey(t)); }
async function tasteToggle(t, on) {
  const r = await api.taste_exclude(t.title, t.artist, on);
  if (r !== true) return toast("Couldn't change that.", 'warning');
  isExcluded(t); S.excluded[on ? 'add' : 'delete'](tasteKey(t));
  if (!on) return toast('Back in your taste profile', 'checkCircle');
  sheet('Excluded from your taste profile', `${songRow(t)}
    <p>Cara and Scratch won't read anything into this song any more. Played it for someone else? It doesn't say a thing about you now.</p>
    <p class="muted">Spotify keeps its own taste profile (for your Discover Weekly and Wrapped), and only the Spotify app can change that one. Open the song there and pick Exclude from your taste profile in its ••• menu.</p>
    <div class="sheet-buttons"><button class="pill secondary" data-act="sheet-close">Done</button><button class="pill primary" data-act="open-spotify-song" data-uri="${esc(t.uri)}">Open in Spotify</button></div>`);
}
function moreBtn(kind, id, name) { return `<button class="icon-btn page-more" data-act="page-menu" data-kind="${kind}" data-id="${esc(id)}" title="More options for ${esc(name || '')}">${icon('more', 28)}</button>`; }
function knownAlbum(id) {
  const r = S.route, h = S.home || {}, f = S.feed || {};
  if (r.name === 'album' && r.id === id && r.data) return r.data.album;
  return [...(h.albums || []), ...(h.recentAlbums || []), ...(f.newReleases || []), ...((f.moreLike || []).flatMap(m => m.albums || []))].find(a => a.id === id) || null;
}
function knownPlaylist(id) {
  const r = S.route;
  if (r.name === 'playlist' && r.id === id && r.data) return r.data.playlist;
  return ((S.home && S.home.playlists) || []).find(p => p.id === id) || null;
}
async function itemMenu(kind, id, x, y) {
  if (!id || kind === 'liked') return;
  const items = [], uri = kind === 'mix' ? '' : `spotify:${kind}:${id}`;
  const share = noun => ({ label: 'Share', icon: 'share', sub: [{ label: `Copy link to ${noun}`, icon: 'link', run: () => copy(`https://open.spotify.com/${kind}/${id}`) }] });
  const inApp = { label: 'Open in Spotify app', icon: 'open', run: () => ACTIONS['open-spotify-song']({ dataset: { uri } }) };
  let saved = null;
  if (uri) { const got = await api.contains([uri]); saved = Array.isArray(got) ? !!got[0] : null; }
  const save = (noun, follow) => saved == null ? null : { label: follow ? (saved ? 'Unfollow' : 'Follow') : (saved ? 'Remove from Your Library' : 'Add to Your Library'),
    icon: saved ? 'checkCircle' : 'addCircle', on: saved, run: () => saveItem(uri, !saved, follow) };
  if (kind === 'playlist') {
    const p = knownPlaylist(id) || { id, uri, name: '' }, mine = p.ownerId && p.ownerId === myId();
    if (mine || p.collaborative) items.push({ label: 'Add to queue', icon: 'queue', run: () => queueMany('playlist', id) },
      { label: 'Add to other playlist', icon: 'plus', sub: () => playlistItems({ kind: 'playlist', id }, id) });
    if (mine) items.push('-', { label: 'Edit details', icon: 'pencil', run: () => renamePlaylist(p) }, { label: 'Delete', icon: 'trash', run: () => deletePlaylist(p) });
    else if (save()) items.push(save());
    items.push('-', share('playlist'), inApp);
  } else if (kind === 'album') {
    const a = knownAlbum(id);
    items.push({ label: 'Add to queue', icon: 'queue', run: () => queueMany('album', id) }, { label: 'Add to playlist', icon: 'plus', sub: () => playlistItems({ kind: 'album', id }) });
    if (save()) items.push(save());
    if (a && a.artistId) items.push('-', { label: 'Go to artist', icon: 'person', run: () => go({ name: 'artist', id: a.artistId }) });
    items.push('-', share('album'), inApp);
  } else if (kind === 'artist') {
    if (save('artist', true)) items.push(save('artist', true));
    items.push({ label: 'Go to artist radio', icon: 'radio', run: () => go({ name: 'mix', id: 'station:' + id }) }, '-', share('artist'), inApp);
  } else if (kind === 'mix') {
    items.push({ label: 'Add to queue', icon: 'queue', run: () => queueMany('mix', id) }, { label: 'Add to playlist', icon: 'plus', sub: () => playlistItems({ kind: 'mix', id }) },
      { label: 'Save as a playlist', icon: 'playlistAdd', run: async () => { const p = await api.save_mix(id); if (p && p.id) { toast(`Saved as “${p.name}”`, 'queue'); loadHome(true); } else toast("Couldn't save it as a playlist.", 'warning'); } });
  }
  if (items.length) showMenu(items, x, y);
}
async function queueMany(kind, id) {
  toast('Adding to queue…', 'queue');
  const n = await api.queue_many(kind, id);
  toast(typeof n === 'number' && n > 0 ? `Added ${plural(n, 'song')} to queue` : "Spotify wouldn't queue those.", typeof n === 'number' && n > 0 ? 'queue' : 'warning');
  setTimeout(refreshQueues, 700);
}
async function saveItem(uri, on, follow) {
  const ok = await api.set_saved(uri, on);
  if (ok === true) { toast(on ? (follow ? 'Following' : 'Added to Your Library') : (follow ? 'Unfollowed' : 'Removed from Your Library'), on ? 'checkCircle' : 'close'); await loadHome(true); const r = S.route; if (r.data && r.id && uri.endsWith(r.id)) { if (r.name === 'artist') r.data.following = on; else r.data.saved = on; refresh(); } }
  else toast("Spotify wouldn't save that.", 'warning');
}
function renamePlaylist(p) {
  const w = sheet('Edit details', `<div class="field"><label>Name</label><div class="box"><input id="pl-rename" value="${esc(p.name)}" spellcheck="false"></div></div>
    <div style="display:flex;gap:10px;justify-content:flex-end"><button class="pill secondary" data-cancel>Cancel</button><button class="pill primary" data-ok>Save</button></div>`);
  const input = $('#pl-rename', w); input.focus(); input.select();
  const done = async ok => {
    const name = input.value.trim(); closeSheet();
    if (!ok || !name || name === p.name) return;
    if (await api.edit_playlist(p.id, name) === true) { toast(`Renamed to “${name}”`, 'check'); await loadHome(true); if (S.route.name === 'playlist' && S.route.id === p.id && S.route.data) { S.route.data.playlist.name = name; render(); } }
    else toast("Spotify wouldn't rename it.", 'warning');
  };
  w.addEventListener('click', e => { if (e.target.closest('[data-ok]')) done(true); if (e.target.closest('[data-cancel]')) done(false); });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') done(true); });
}
function deletePlaylist(p) {
  const w = sheet('Delete from Your Library?', `<p class="muted" style="margin:0">This deletes <b>${esc(p.name)}</b> from Your Library.</p>
    <div style="display:flex;gap:10px;justify-content:flex-end"><button class="pill secondary" data-cancel>Cancel</button><button class="pill primary" data-ok>Delete</button></div>`);
  w.addEventListener('click', async e => {
    if (e.target.closest('[data-cancel]')) return closeSheet();
    if (!e.target.closest('[data-ok]')) return;
    closeSheet();
    if (await api.set_saved(p.uri || `spotify:playlist:${p.id}`, false) === true) { toast(`Deleted ${p.name}`, 'trash'); await loadHome(true); if (S.route.name === 'playlist' && S.route.id === p.id) tab('home'); }
    else toast("Spotify wouldn't delete it.", 'warning');
  });
}
function songRow(t) { return `<div class="pick-row" style="cursor:default">${art(t.artMid, 44)}<div class="ell"><b class="ell">${esc(t.title)}</b><small class="muted">${esc(t.artistLine)}</small></div></div>`; }
// Jams are Spotify's own: only the Spotify app can start or join one, so these hand over to it
function jamSheet(t) {
  sheet('Start a Jam', `${songRow(t)}
    <p>In a Jam, friends add songs and listen along with you. Spotify only lets its own app start one.</p>
    <ol class="steps"><li>Open this song in Spotify.</li><li>Right-click the song there (or use its ••• button) and pick <b>Start a Jam</b>, then share the Jam link.</li>
      <li>Keep this app open: while the Jam plays on your account, it shows what's playing and Cara keeps doing her breaks.</li></ol>
    <div class="sheet-buttons"><button class="pill secondary" data-act="sheet-close">Not now</button><button class="pill primary" data-act="open-spotify-song" data-uri="${esc(t.uri)}">Open in Spotify</button></div>`);
}
function joinJamSheet() {
  const w = sheet('Join a Jam', `<p>Paste the Jam link a friend sent you. Jams are joined in Spotify itself, so it opens there.</p>
    <div class="field"><label>Jam link</label><div class="box"><input id="jam-link" placeholder="https://spotify.link/…" spellcheck="false"></div></div>
    <p class="muted">Keep this app open while you're in: when the Jam plays on your account, it shows what's playing and Cara keeps doing her breaks.</p>
    <div class="sheet-buttons"><button class="pill secondary" data-act="sheet-close">Cancel</button><button class="pill primary" data-join>Open in Spotify</button></div>`);
  const send = async () => { const r = await api.join_jam($('#jam-link').value); if (r === true) { closeSheet(); toast('Opening the Jam in Spotify', 'people'); } else toast((r && r.error) || r || "That link didn't open.", 'warning'); };
  w.querySelector('[data-join]').addEventListener('click', send);
  w.querySelector('#jam-link').addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
  setTimeout(() => { const i = $('#jam-link'); if (i) i.focus(); }, 60);
}
async function creditsSheet(t) {
  const w = sheet('Credits', `${songRow(t)}<div id="credits-body">${loading()}</div>`);
  const d = await api.credits(t.id);
  const box = w.querySelector('#credits-body');
  if (!box || !document.body.contains(w)) return;
  if (!d || d.error) { box.innerHTML = empty('warning', "Couldn't load the credits", "Spotify didn't answer. Try again in a moment."); return; }
  const group = (label, names) => names && names.length ? `<div class="credit"><div class="caps">${esc(label)}</div><div>${names.map(esc).join(', ')}</div></div>` : '';
  const made = d.writers.length || d.producers.length || d.more.length;
  box.innerHTML = `<div class="credits">${group('Performed by', d.performers)}${group('Written by', d.writers)}${group('Produced by', d.producers)}
      ${d.more.map(r => group(r.role, r.names)).join('')}
      ${group('Album', d.album ? [d.album + (d.released ? ` (${d.released.slice(0, 4)})` : '')] : [])}${group('Label', d.label ? [d.label] : [])}
      ${d.copyrights.length ? `<div class="credit muted">${d.copyrights.map(esc).join('<br>')}</div>` : ''}</div>
    <div class="note">${made ? '' : "Writers and producers aren't listed for this song yet. "}From Spotify${d.musicbrainz ? ` and <button class="link" data-act="open-url" data-url="${esc(d.musicbrainz)}">MusicBrainz</button>, the open music encyclopedia` : ''}.</div>`;
}
function copy(text, done = 'Link copied') { navigator.clipboard.writeText(text).then(() => toast(done, 'link'), () => toast(text, 'link')); }
function sheet(title, body, wide) {
  closeSheet();
  const w = document.createElement('div'); w.id = 'sheet-wrap';
  w.innerHTML = `<div class="sheet" ${wide ? 'style="width:min(720px,calc(100vw - 60px))"' : ''}><header><h2>${esc(title)}</h2><button class="icon-btn" data-act="sheet-close" title="Close">${icon('close', 20)}</button></header><div class="body scroll">${body}</div></div>`;
  w.addEventListener('mousedown', e => { if (e.target === w) closeSheet(); });
  document.body.appendChild(w);
  return w;
}
function closeSheet() { const w = $('#sheet-wrap'); if (w) { w.remove(); if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur(); } }
async function addToPlaylist(t) {
  if (!S.home) await loadHome();
  const mine = ((S.home && S.home.playlists) || []).filter(p => p.ownerId === myId() || p.collaborative);
  const w = sheet('Add to a playlist', `<div class="pick-row" style="cursor:default">${art(t.artMid, 44)}<div class="ell"><b class="ell">${esc(t.title)}</b><small class="muted">${esc(t.artistLine)}</small></div></div>
    <div class="group"><button class="pick-row" data-new>${'<div class="art" style="width:44px;height:44px;display:grid;place-items:center">' + icon('plus', 22) + '</div>'}<b>New playlist…</b></button>
    ${mine.map(p => `<button class="pick-row" data-pid="${esc(p.id)}">${art(p.imageMid, 44, { icon: 'queue' })}<div class="ell"><b class="ell">${esc(p.name)}</b><small class="muted">${plural(p.total, 'song')}</small></div></button>`).join('')}</div>
    <div class="note">Only playlists you made (or collaborate on) can take new songs.</div>`);
  w.addEventListener('click', async e => {
    const b = e.target.closest('[data-pid],[data-new]'); if (!b) return;
    let pid = b.dataset.pid;
    if (b.hasAttribute('data-new')) { const p = await askName(); if (!p) return; pid = p.id; }
    closeSheet();
    const ok = await api.add_to_playlist(pid, t.uri);
    toast(ok === true ? 'Added to the playlist' : "Spotify wouldn't add it.", ok === true ? 'check' : 'warning');
    loadHome(true);
  });
}
function askName() {
  return new Promise(resolve => {
    const w = sheet('New playlist', `<div class="field"><label>Name</label><div class="box"><input id="pl-name" placeholder="My playlist" spellcheck="false"></div></div>
      <div style="display:flex;gap:10px;justify-content:flex-end"><button class="pill secondary" data-cancel>Cancel</button><button class="pill primary" data-ok>Create</button></div>`);
    const input = $('#pl-name', w); input.focus();
    const done = async ok => {
      const name = input.value.trim();
      closeSheet();
      if (!ok || !name) return resolve(null);
      const p = await api.create_playlist(name);
      if (p && p.id) { toast(`Made ${p.name}`, 'queue'); loadHome(true); resolve(p); } else { toast("Spotify wouldn't make the playlist.", 'warning'); resolve(null); }
    };
    w.addEventListener('click', e => { if (e.target.closest('[data-ok]')) done(true); if (e.target.closest('[data-cancel]')) done(false); });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') done(true); });
  });
}
async function devicesMenu(x, y) { showMenu([{ head: 'Play on' }, ...(await deviceItems())], x, y); }
async function deviceItems() {
  const list = await api.devices();
  const devs = Array.isArray(list) ? list : [];
  const st = S.state || {};
  const pl = st.player || {};
  const out = (st.output || '').split(' (')[0].trim() || 'Speakers';          // Windows' playback device ("Headphones")
  const outIcon = /head|ear|bud|pod/i.test(out) ? 'headphones' : 'speaker';
  const pc = ((S.boot && S.boot.pc) || '').toLowerCase();
  const mine = d => (pl.deviceId && d.id === pl.deviceId) || d.name === (pl.name || 'Non Stop Pop DJ');
  const playingHere = devs.some(d => mine(d) && d.active);
  const items = [];
  // this PC, through the app's own player, is always there (it plays on whatever Windows plays sound on)
  const why = playingHere ? '  (playing)' : pl.status === 'premium' ? '  (needs Premium)' : pl.mode === 'app' && pl.status === 'starting' ? '  (starting…)' : '';
  items.push({ label: `${out} · this PC${why}`, icon: outIcon, on: playingHere, run: async () => {
    if (playingHere) return;
    const r = await api.play_here();
    if (r === 'ok') toast(`Playing on ${out}`, outIcon);
    else if (r === 'starting') toast(`Starting the player. The music moves to ${out} when it's ready.`, outIcon);
    else toast((r && r.error) || (typeof r === 'string' && r) || "Couldn't play here.", 'warning');
    poke();
  } });
  for (const d of devs.filter(d => !mine(d))) {
    const onPc = pc && d.type === 'computer' && (d.name || '').toLowerCase() === pc;    // the Spotify app on this PC
    items.push({ label: (onPc ? 'Spotify app on this PC' : d.name) + (d.active ? '  (playing)' : ''), icon: d.type === 'computer' ? 'computer' : d.type === 'smartphone' ? 'phone' : 'speaker', on: d.active, run: async () => { if (!d.restricted) { await api.transfer(d.id); toast(`Playing on ${onPc ? 'the Spotify app' : d.name}`, 'speaker'); poke(); } } });
  }
  items.push('-', { label: 'Join a Jam…', icon: 'people', run: () => joinJamSheet() }, { label: `Cara's voice: ${out}`, icon: 'mic', run: () => {} });
  return items;
}
function sleepItems() {
  const s = S.state && S.state.sleep;
  const items = [];
  for (const [label, m] of [['15 minutes', 15], ['30 minutes', 30], ['45 minutes', 45], ['1 hour', 60], ['End of this song', -1]]) items.push({ label, icon: 'moon', run: async () => { await api.sleep(m); toast(m === -1 ? 'Stopping after this song' : `Stopping in ${label}`, 'moon'); } });
  if (s && (s.at || s.endOfSong)) items.push('-', { label: 'Turn off the sleep timer', icon: 'close', run: async () => { await api.sleep(0); toast('Sleep timer off', 'moon'); } });
  return items;
}
function sleepMenu(x, y) { showMenu([{ head: 'Sleep timer' }, ...sleepItems()], x, y); }

// ---------------------------------------------------------------- Settings
// Settings, laid out like Spotify's: this app's own settings, Cara's voices among them
VIEWS.prefs = {
  title: () => 'Settings',
  html: () => {
    const c = S.config, s = S.state || {}, me = s.me, u = S.ui, pl = s.player || {};
    const tog = (on, attrs) => `<button class="toggle ${on ? 'on' : ''}" ${attrs} aria-pressed="${!!on}"></button>`;
    const sel = (key, opts) => `<select data-pref="${key}">${opts.map(([v, l]) => `<option value="${v}" ${String(c[key]) === String(v) ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    const txt = (key, ph, secret) => secret
      ? `<div class="secret"><input type="password" data-pref="${key}" value="${esc(c[key] || '')}" placeholder="${esc(ph)}" spellcheck="false" autocomplete="off"><button class="icon-btn" data-act="reveal" title="Show">${icon('eye', 18)}</button></div>`
      : `<input type="text" data-pref="${key}" value="${esc(c[key] || '')}" placeholder="${esc(ph)}" spellcheck="false" autocomplete="off">`;
    const row = (label, sub, ctl) => `<div class="pref"><div class="lbl">${label}${sub ? `<small>${sub}</small>` : ''}</div><div class="ctl">${ctl}</div></div>`;
    const out = (s.output || '').split(' (')[0] || 'your speakers';
    return `<div class="prefs"><h1>Settings</h1>
      <section><h2>Account</h2>
        <div class="account"><div class="avatar" style="${me && me.image ? `background-image:url('${esc(me.image)}')` : ''}">${me && me.image ? '' : icon('person', 26)}</div>
          <div class="who"><b class="ell">${esc(me ? me.name : 'Not connected')}</b><span class="muted">${s.connected ? 'Spotify connected' : s.connecting ? 'Connecting…' : 'Spotify not connected'}</span></div>
          <button class="pill secondary small" data-act="pref-reconnect">Reconnect</button>${s.connected ? '<button class="pill secondary small" data-act="pref-logout">Log out</button>' : ''}</div>
        ${row('Edit login methods', '', `<button class="pill secondary small" data-act="open-url" data-url="https://www.spotify.com/account/overview/">Edit ${icon('open', 14)}</button>`)}
      </section>
      <section><h2>Spotify developer app</h2>
        ${row('Client ID', '', txt('spotify_client_id', 'Paste it here'))}
        ${row('Client Secret', '', txt('spotify_client_secret', 'Paste it here', true))}
        <div class="note">From <button class="link" data-act="open-url" data-url="https://developer.spotify.com/dashboard">developer.spotify.com/dashboard</button>. Its Redirect URI must be <b class="num">${esc(S.boot.redirect)}</b> <button class="link" data-act="copy-redirect">Copy</button>, and everyone who uses it has to be added under User Management.</div>
      </section>
      <section><h2>Audio</h2>
        ${row('Where your music plays', (c.player || 'app') === 'app' ? `Right here in this app, through ${esc(out)} (Spotify Premium). ${esc(playerNote(pl))}` : 'In the Spotify app, on this PC or any device, with this app as the remote.',
          `<div class="seg">${[['app', 'This app'], ['spotify', 'Spotify app']].map(([v, l]) => `<button class="${(c.player || 'app') === v ? 'on' : ''}" data-act="player-mode" data-mode="${v}">${l}</button>`).join('')}</div>`)}
      </section>
      <section><h2>Display</h2>
        ${row('Background', "Your song's colours softly behind everything, or a plain black or white window.",
          `<div class="seg">${[['song', "Song's colours"], ['black', 'Black'], ['white', 'White']].map(([v, l]) => `<button class="${u.bg === v ? 'on' : ''}" data-act="ui-bg" data-bg="${v}">${l}</button>`).join('')}</div>`)}
        ${row('Show the now-playing panel on click of play', '', tog(u.npOnPlay, 'data-act="ui-toggle" data-key="npOnPlay"'))}
        ${row('Show Your Library', 'Off folds it into a narrow strip of covers.', tog(!u.libMini, 'data-act="ui-toggle" data-key="libMini"'))}
      </section>
      <section><h2>Zoom level</h2>
        <div class="pref stack"><div class="lbl">Adjusting the zoom level can help you make the most of the app. You can also press Ctrl - or Ctrl +.</div></div>
        <div class="zoom-box"><div class="zoom-steps">${[70, 80, 90, 100, 110, 120, 130].map(z => `<button class="${(u.zoom || 100) === z ? 'on' : ''}" data-act="zoom" data-z="${z}"><i></i>${z}%</button>`).join('')}</div></div>
      </section>
      <section><h2>Cara's voice</h2>
        ${row('Voice engine', '', sel('tts_engine', [['elevenlabs', 'ElevenLabs (her real voice)'], ['gemini', 'Gemini voice'], ['edge', 'Microsoft voice (free, no key)']]))}
        ${row('ElevenLabs API key', 'The secret key that starts with sk_.', txt('elevenlabs_api_key', 'sk_…', true))}
        ${row("Cara's Voice ID", '', txt('elevenlabs_voice_id', 'Voice ID'))}
        ${row('Model', '', sel('eleven_model', [['eleven_v4', 'Eleven v4 (most expressive)'], ['eleven_v4_turbo', 'Eleven v4 Turbo (faster)'], ['eleven_multilingual_v2', 'Multilingual v2 (older)']]))}
        ${row("MC Scratch's Voice ID", 'Leave blank for a deep, warm radio voice.', txt('cohost_voice', 'Voice ID'))}
        ${row('Station voice (stingers)', 'The announcer on your station stingers. Leave blank for the default.', txt('station_voice', 'Voice ID'))}
        <div class="note">Add any voice from the ElevenLabs Voice Library to My Voices, then paste its ID here.</div>
      </section>
      <section><h2>Cara's words</h2>
        ${row('Gemini API key', `A free key from <button class="link" data-act="open-url" data-url="https://aistudio.google.com/apikey">Google AI Studio</button>. Without one she still talks, with simpler lines.`, txt('gemini_api_key', 'Paste it here', true))}
        ${row("Cara's town", 'Town, State. For local news and weather.', txt('city', 'Yakima, Washington'))}
        ${row("Cara's studio", 'Her show: mood, how often she talks, pop-ins, Scratch, stingers and more.', `<button class="pill secondary small" data-act="open-cara">Open</button>`)}
      </section>
      <section><h2>About</h2>
        ${row(`Non Stop Pop DJ ${esc(S.boot.version)}`, 'Your keys are saved on this PC only.', `<button class="pill secondary small" data-act="welcome-again">Welcome setup</button>`)}
      </section></div>`;
  },
};
function settingsSheet() { go({ name: 'prefs' }); }
function playerNote(p) {
  p = p || {};
  if (p.status === 'ready') return `Ready (it runs in an off-screen ${p.browser || 'Chrome'} window).`;
  if (p.status === 'starting') return p.problem || 'Starting…';
  if (p.status === 'premium' || p.status === 'error') return p.problem || '';
  return '';
}

// ---------------------------------------------------------------- everything you can click
const ACTIONS = {
  'tab': el => tab(el.dataset.tab),
  'back': () => goBack(), 'forward': () => goForward(),
  'open-album': el => el.dataset.id && go({ name: 'album', id: el.dataset.id }),
  'open-artist': el => el.dataset.id && go({ name: 'artist', id: el.dataset.id }),
  'open-playlist': el => go({ name: 'playlist', id: el.dataset.id }),
  'open-liked': () => go({ name: 'liked', tab: 'library' }),
  'open-genre': el => go({ name: 'genre', id: el.dataset.id, tab: 'search' }),
  'lib': el => { S.lib = el.dataset.kind; if (S.route.name !== 'library') tab('library'); else refresh(); },
  'row-scroll': el => { const row = el.parentElement.querySelector('.row'); row.scrollBy({ left: +el.dataset.dir * row.clientWidth * 0.8, behavior: 'smooth' }); },
  'play-ctx': el => {
    const n = now();
    if (n && n.context === el.dataset.ctx) return cmd('toggle');
    play({ context: el.dataset.ctx, shuffle: shuffleOn() });
  },
  'play-liked': el => {
    const id = myId(), ctx = id ? `spotify:user:${id}:collection` : '', n = now();
    if (!id) return toast('Connect Spotify first.', 'warning');
    if (n && n.context === ctx) return cmd('toggle');
    play({ context: ctx, shuffle: shuffleOn() });
  },
  'play-list': el => {
    const uris = S.lists.get(el.dataset.list) || [], on = shuffleOn();
    if (uris.length) play({ uris, shuffle: on, position: on ? Math.floor(Math.random() * uris.length) : 0 });
  },
  'shuffle-toggle': () => toggleShuffle(),
  'play-row': el => playRow(el.closest('[data-row]')),
  'play-one': el => play({ uris: [el.dataset.uri] }),
  'select-row': () => {},
  'like': el => toggleLike(el.dataset.uri),
  'page-menu': el => { const r = el.getBoundingClientRect(); itemMenu(el.dataset.kind, el.dataset.id, r.left, r.bottom + 6); },
  'list-find': () => { const r = S.route; r.findOpen = !(r.findOpen || r.find); if (!r.findOpen) r.find = ''; refresh(); if (r.findOpen) { const q = $('.list-q'); if (q) q.focus(); ensureAll(r); } },
  'list-sort': el => { const b = el.getBoundingClientRect(); sortMenu(b.right - 200, b.bottom + 6); },
  'mute': () => { const v = volNow(); if (v > 0) { S.volBeforeMute = v; setVolume(0); } else setVolume(S.volBeforeMute || 60); },
  'track-menu': (el, e) => { const r = el.getBoundingClientRect(), row = el.closest('[data-q]'); trackMenu(el.dataset.uri, r.left - 200, r.bottom + 4, row ? { q: row.dataset.q, nth: +row.dataset.nth || 0 } : {}); },
  'save-page': async el => {
    const r = S.route, d = r.data; if (!d) return;
    const on = r.name === 'artist' ? !d.following : !d.saved;
    const ok = await api.set_saved(el.dataset.uri, on);
    if (ok === true) { if (r.name === 'artist') d.following = on; else d.saved = on; refresh(); toast(on ? (r.name === 'artist' ? 'Following' : 'Saved to Your Library') : (r.name === 'artist' ? 'Unfollowed' : 'Removed from Your Library'), on ? 'checkCircle' : 'close'); loadHome(true); }
    else toast("Spotify wouldn't save that.", 'warning');
  },
  'share': el => copy(el.dataset.url),
  'open-url': el => api.open_url(el.dataset.url),
  'open-spotify-song': async el => { const r = await api.open_in_spotify(el.dataset.uri); closeSheet(); toast(r === 'app' ? 'Opened in the Spotify app' : r === 'web' ? 'Opened in Spotify on the web' : (r && r.error) || r, r === 'app' || r === 'web' ? 'open' : 'warning'); },
  'artist-more': () => { S.route.allTop = !S.route.allTop; refresh(); },
  'new-playlist': () => askName(),
  'settings': () => settingsSheet(),
  'sheet-close': () => closeSheet(),
  'connect': () => api.connect(false).then(poke),
  'open-spotify': () => api.open_spotify(),
  'open-stingers': () => api.open_stingers(),
  'restinger': () => api.restinger().then(r => toast(r === true ? 'New takes on the way' : "Couldn't start over", r === true ? 'bolt' : 'warning')),
  'dj-toggle': async () => {
    const r = await api.dj_toggle();
    if (r === 'connect') toast('Connecting to Spotify first…', 'link');
    else if (r === 'started') toast('Cara is live', 'radio');
    else if (r === 'stopping') toast('Cara is signing off', 'stop');
    poke();
  },
  'dj-test': async el => { const r = await api.dj_test(el.dataset.what); if (typeof r === 'string' && r.startsWith('making:')) toast(`Making a ${r.slice(7)} stinger…`, 'bolt'); else if (r !== 'ok') toast(r, 'warning'); else toast({ popin: 'Cara pops in now', duo: 'Cara and Scratch, coming up', stinger: 'Stinger!', break: 'Breaking news, coming up', talk: 'Cara talks at the end of this song' }[el.dataset.what] || 'OK', 'bolt'); },
  'dj-queue': async el => { const r = await api.dj_queue(el.dataset.style); if (r !== 'ok') toast(r, 'warning'); poke(); },
  'cfg': el => setConfig({ [el.dataset.key]: el.dataset.val }),
  'cfg-toggle': el => setConfig({ [el.dataset.key]: !S.config[el.dataset.key] }),
  'cfg-step': el => { const k = el.dataset.key; const v = Math.max(+el.dataset.min, Math.min(+el.dataset.max, (+S.config[k] || 0) + +el.dataset.step)); const patch = { [k]: v }; if (k === 'break_min' && v > S.config.break_max) patch.break_max = v; if (k === 'break_max' && v < S.config.break_min) patch.break_min = v; setConfig(patch); },
  'set-town': () => { const v = $('#town').value.trim(); if (v) setConfig({ city: v }).then(() => toast(`Cara's town: ${v}`, 'place')); },
  'search-term': el => { S.search.q = el.dataset.q; S.search.scope = 'all'; refresh(); runSearch(); },
  'search-clear': () => { S.search.q = ''; S.search.results = null; $('#search-input').value = ''; $('#tb-clear').hidden = true; $('#search-input').focus(); if (S.route.name === 'search') refresh(); },
  'search-forget': async () => { await api.clear_searches(); S.config.recent_searches = []; refresh(); },
  'search-scope': el => { S.search.scope = el.dataset.scope; if (S.route.name !== 'search') tab('search'); runSearch(); },
  'lyric': el => { api.player('seek', +el.dataset.t); S.state.now.progress = +el.dataset.t; S.state.now.stamp = Date.now(); S.np.userScrolled = false; },
  'vis-open': () => Vis.open(),
  'player-mode': el => setConfig({ player: el.dataset.mode }).then(() => toast(el.dataset.mode === 'app' ? 'Music plays in this app' : 'Music plays in the Spotify app', 'speaker')),
  'player-retry': () => api.player_retry().then(() => toast('Starting the built-in player', 'speaker')),
  'home': () => { S.homeKind = 'all'; tab('home'); },
  'home-kind': el => { S.homeKind = el.dataset.kind; refresh(); },
  'browse': () => { S.search.q = ''; S.search.results = null; $('#search-input').value = ''; if (S.route.name !== 'search') tab('search'); else refresh(); },
  'lib-collapse': () => toggleLibrary(),
  'lib-kind': el => { S.ui.libKind = el.dataset.kind; saveUi(); renderLibrary(); },
  'lib-find': () => { const f = $('#lib-filter'); f.hidden = !f.hidden; if (!f.hidden) f.focus(); else { f.value = ''; S.libQuery = ''; renderLibrary(); } },
  'lib-sort': el => { const r = el.getBoundingClientRect(); libSortMenu(r.left - 60, r.bottom + 4); },
  'open-cara': () => go({ name: 'cara' }),
  'nv-lyrics': () => openNP('lyrics'),
  'nv-credits': () => { const t = nowTrack(); if (t) creditsSheet(t); },
  'nv-queue': () => openQueue(),
  'queue-close': () => toggleQueue(),
  'nv-video': async el => { const t = nowTrack(); const r = await api.play_named(el.dataset.title, t ? t.artist : ''); if (r !== 'ok') toast(typeof r === 'string' ? r : "Couldn't play that.", 'warning'); poke(); },
  'reveal': el => { const i = el.parentElement.querySelector('input'); i.type = i.type === 'password' ? 'text' : 'password'; },
  'pref-reconnect': async () => { await api.connect(true); toast('A browser tab opens: click Agree', 'link'); },
  'pref-logout': () => logOut(),
  'copy-redirect': () => copy(S.boot.redirect),
  'welcome-again': () => showWelcome(true),
  'ui-toggle': el => { const k = el.dataset.key; S.ui[k] = !S.ui[k]; saveUi(); applyUi(); refresh(); },
  'zoom': el => setZoom(+el.dataset.z),
  'ui-bg': el => setBg(el.dataset.bg),
  'nv-collapse': () => { S.nv.mode = 'now'; S.nv.qFromRail = false; S.ui.nvOpen = false; saveUi(); applyUi(); nvStopVideo(); updateBar(); },
  'nv-expand': () => openNV('now'),
  'open-mix': el => go({ name: 'mix', id: el.dataset.id }),
  'play-mix': el => playMix(el.dataset.mix),
  'dj-start': async () => {
    const d = (S.state && S.state.dj) || {};
    if (d.running) return go({ name: 'cara' });
    if (!nowTrack() && S.feed && S.feed.made && S.feed.made[0]) await playMix(S.feed.made[0].id);
    ACTIONS['dj-toggle']();
  },
};
async function setConfig(patch) {
  Object.assign(S.config, patch);
  if (S.route.name === 'cara' || S.route.name === 'prefs') refresh();
  const res = await api.set_config(patch);
  if (res && res.config) { S.config = res.config; if (S.route.name === 'cara' || S.route.name === 'prefs') refresh(); }
}
function poke() { setTimeout(pollOnce, 250); }
// play/pause/skip and friends: says so when it didn't work, then refreshes
function cmd(action, value) {
  return api.player(action, value).then(r => {
    if (r !== true) toast(typeof r === 'string' && r ? r : (r && r.error) || "Spotify didn't answer. Try again.", 'warning');
    poke();
    return r === true;
  });
}

document.addEventListener('click', e => {
  if (!e.target.closest('.menu, #tb-menu')) closeMenu();
  const el = e.target.closest('[data-act]');
  if (!el || el.disabled) return;
  const f = ACTIONS[el.dataset.act];
  if (f) { e.preventDefault(); e.stopPropagation(); f(el, e); }
});
document.addEventListener('dblclick', e => {
  const row = e.target.closest('.tr[data-row]');
  if (row && !e.target.closest('button')) playRow(row);
});
document.addEventListener('contextmenu', e => {
  const row = e.target.closest('[data-row][data-uri]');
  e.preventDefault();
  if (row) return trackMenu(row.dataset.uri, e.clientX, e.clientY, row.dataset.q ? { q: row.dataset.q, nth: +row.dataset.nth || 0 } : {});
  const it = e.target.closest('[data-act="open-album"][data-id],[data-act="open-playlist"][data-id],[data-act="open-artist"][data-id],[data-act="open-mix"][data-id]');
  if (it) itemMenu(it.dataset.act.slice(5), it.dataset.id, e.clientX, e.clientY);
});
document.addEventListener('input', e => {
  const el = e.target;
  if (el.type === 'range') paintRange(el);
  if (el.classList.contains('vol-range')) setVolume(+el.value);
  if (el.id === 'search-input') {
    S.search.q = el.value;
    $('#tb-clear').hidden = !el.value;
    if (S.route.name !== 'search') { go({ name: 'search', tab: 'search' }); el.focus(); }
    clearTimeout(S.search.timer);
    S.search.timer = setTimeout(() => { if (S.search.q.trim()) runSearch(); else { S.search.results = null; refresh(); } }, 300);
  }
  if (el.id === 'lib-filter') { S.libQuery = el.value; renderLibrary(); if (el.value.trim()) loadAllLibrary(); }
  if (el.classList.contains('list-q')) { S.route.find = el.value; ensureAll(S.route); clearTimeout(S.findTimer); S.findTimer = setTimeout(() => refreshKeep('.list-q'), 120); }
});
document.addEventListener('change', async e => {
  const el = e.target;
  if (el.type === 'range' && el.dataset.key) setConfig({ [el.dataset.key]: +el.value });
  if (el.dataset.pref) {
    const res = await api.set_config({ [el.dataset.pref]: el.value });
    if (res && res.config) { S.config = res.config; toast('Saved', 'check'); if (res.reconnect) api.connect(true); }
  }
});
// Spotify's keyboard shortcuts (they also keep the browser's own Ctrl+R reload, Ctrl+P print and Ctrl+S save away)
document.addEventListener('keydown', e => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement && document.activeElement.tagName);
  const ctrl = e.ctrlKey || e.metaKey, key = e.key, low = key.length === 1 ? key.toLowerCase() : key;
  if (key === 'Escape') {
    if ($('#menu')) return closeMenu(); if ($('#sheet-wrap')) return closeSheet(); if (Vis.isOpen) return Vis.close(); if (S.np.open) return closeNP();
    if (typing) { document.activeElement.blur(); return; }
  }
  const hot = fn => { e.preventDefault(); e.stopPropagation(); fn(); };
  if (key === 'F5' || (ctrl && e.shiftKey && low === 'r')) return e.preventDefault();
  if (key === 'F1') return hot(() => api.open_url('https://support.spotify.com/'));
  if (ctrl && e.shiftKey && low === 'q') return hot(() => api.quit());
  if (ctrl && e.shiftKey && low === 'w') return hot(logOut);
  if (ctrl && !e.shiftKey && low === 'n') return hot(() => askName());
  if (ctrl && !e.shiftKey && low === 'l') return hot(focusSearch);
  if (ctrl && !e.shiftKey && low === 'f') return hot(focusFilter);
  if (ctrl && !e.shiftKey && low === 'p') return hot(() => go({ name: 'prefs' }));
  if (ctrl && (key === '=' || key === '+')) return hot(() => zoomBy(10));
  if (ctrl && (key === '-' || key === '_')) return hot(() => zoomBy(-10));
  if (ctrl && key === '0') return hot(() => setZoom(100));
  if (ctrl && !e.shiftKey && low === 'r') return hot(() => { if (!typing) cycleRepeat(); });
  if (ctrl && !e.shiftKey && low === 's') return hot(() => { if (!typing) toggleShuffle(); });
  if (typing) { if (key === 'Enter' && e.target.id === 'search-input' && S.search.q.trim()) api.remember_search(S.search.q).then(l => { if (Array.isArray(l)) S.config.recent_searches = l; }); return; }
  if (key === ' ') { e.preventDefault(); cmd('toggle'); }
  else if (ctrl && key === 'ArrowRight') { e.preventDefault(); cmd('next'); }
  else if (ctrl && key === 'ArrowLeft') { e.preventDefault(); prevTrack(); }
  else if (ctrl && key === 'ArrowUp') { e.preventDefault(); volumeBy(10); }
  else if (ctrl && key === 'ArrowDown') { e.preventDefault(); volumeBy(-10); }
  else if (e.shiftKey && key === 'ArrowRight') { e.preventDefault(); seekBy(5000); }
  else if (e.shiftKey && key === 'ArrowLeft') { e.preventDefault(); seekBy(-5000); }
  else if (key === 'ArrowLeft' && e.altKey) goBack();
  else if (key === 'ArrowRight' && e.altKey) goForward();
  else if (low === 'v' && !ctrl && !e.altKey) Vis.isOpen ? Vis.close() : Vis.open();
  else if (key === '/') { e.preventDefault(); focusSearch(); }
});
document.addEventListener('mousedown', e => { if (e.button === 3) goBack(); if (e.button === 4) goForward(); });

// ---------------------------------------------------------------- keeping up with Spotify and Cara
let lastConnected = null;
async function pollOnce() {
  let s;
  try { s = await api.state(); } catch (e) { return; }
  if (!s || s.error) return;
  const prevUri = nowUri();
  S.state = s;
  const nid = s.notice ? s.notice.id : 0;             // something the app wants you to know, once
  if (S.noticeId != null && nid !== S.noticeId && s.notice) toast(s.notice.text, 'warning');
  S.noticeId = nid;
  if (s.connected !== lastConnected) {
    const was = lastConnected; lastConnected = s.connected;
    if (s.connected) { loadHome().then(() => { if (['home', 'library', 'liked'].includes(S.route.name)) { S.route.loaded = false; render(); } loadFeed(); }); }
    if (was !== null) render();
  }
  updateBar(); updateHero(); updateNP();
  if (nowUri() !== prevUri) { markNow(); if (S.np.open) loadNPTab(); ambient(); if (S.ui.nvOpen) nvRender(); }
  if ((s.now && s.now.context) !== S.lastCtx) { S.lastCtx = s.now && s.now.context; noteRecent(S.lastCtx); renderLibrary(); }
  else if (s.now && S.lastPlaying !== s.now.playing) markNow();
  const qsig = s.now ? `${s.now.shuffle}|${s.now.repeat}` : '';     // shuffle or repeat changed: Spotify reorders what's next
  if (S.qSig != null && qsig !== S.qSig) setTimeout(refreshQueues, 1300);
  S.qSig = qsig;
  S.lastPlaying = s.now && s.now.playing;
  if (S.route.name === 'home' || S.route.name === 'cara') { const b = $('.banner'); const want = banners(); if ((b ? b.outerHTML : '') !== want && !!b !== !!want) refresh(); }
  if (S.ui.nvOpen && S.nv.mode === 'queue' && nowUri() !== S.nv.qUri) { S.nv.qUri = nowUri(); nvQueue(false); }
  Vis.update(s);
  if (s.logCount !== S.logNext) pollLog();
}
async function pollLog() {
  const r = await api.log(S.logNext); if (!r || !r.lines) return;
  S.logNext = r.next;
  if (!r.lines.length) return;
  S.log.push(...r.lines); if (S.log.length > 600) S.log.splice(0, S.log.length - 600);
  const log = $('#log');
  if (log) { const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 30; log.textContent = S.log.join('\n'); if (atEnd) log.scrollTop = log.scrollHeight; }
}
async function pollLoop() { await pollOnce(); setTimeout(pollLoop, (S.np.open || Vis.isOpen) ? 700 : 1000); }

// the colour of what's playing, softly behind every page
function ambient() {
  const t = nowTrack(); const url = t ? (t.artMid || t.art) : '';
  if (url === S.artKey) return; S.artKey = url;
  const box = $('#ambient'), layers = $$('.layer', box);
  const next = layers.find(l => !l.classList.contains('on')) || layers[0];
  if (url) { const img = new Image(); img.onload = () => { next.style.backgroundImage = `url('${url}')`; layers.forEach(l => l.classList.toggle('on', l === next)); }; img.src = url; }
  else layers.forEach(l => l.classList.remove('on'));
}

// ---------------------------------------------------------------- resizing the panes
function wireDrag(el, key, dir) {
  el.addEventListener('pointerdown', e => {
    if ((key === 'libW' && S.ui.libMini) || (key === 'nvW' && !S.ui.nvOpen)) return;
    el.setPointerCapture(e.pointerId); el.classList.add('on'); document.body.classList.add('dragging');
    const x0 = e.clientX, w0 = S.ui[key];
    const move = ev => { S.ui[key] = w0 + dir * (ev.clientX - x0) / zf(); applyUi(); };
    const up = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.classList.remove('on'); document.body.classList.remove('dragging'); saveUi(); };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
  });
}

// ---------------------------------------------------------------- the welcome
function showWelcome(again = false) {
  const first = (S.boot && S.boot.first) || 'friend';
  const w = document.createElement('div'); w.id = 'welcome';
  w.innerHTML = `<div class="glow"></div><div class="stage" id="w-stage"></div>
    <div class="lid" id="w-lid" title="Open">${bars(false)}<div class="word">CARA DJ</div><div class="pull">${icon('forward', 0) && ''}<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>CLICK OR PULL UP TO OPEN<i></i></div></div>`;
  document.body.appendChild(w);
  const lid = $('#w-lid', w), stage = $('#w-stage', w);
  let step = -1, from = 36;
  const steps = [
    { icon: 'note', title: 'Spotify', text: `Make a free app at developer.spotify.com, set its Redirect URI to <b style="color:#fff">${esc(S.boot.redirect)}</b>, add your Spotify email under User Management, then paste its Client ID and Client Secret here.`, fields: [['Client ID', 'spotify_client_id'], ['Client Secret', 'spotify_client_secret', true]], link: ['Open the developer dashboard', 'https://developer.spotify.com/dashboard'], connect: true },
    { icon: 'wave', title: 'Her voice', text: 'Cara speaks with an ElevenLabs voice. Paste your API key (the secret one that starts with sk_) and her Voice ID. You can add these later.', fields: [['API key', 'elevenlabs_api_key', true], ['Voice ID', 'elevenlabs_voice_id']] },
    { icon: 'quote', title: 'Her words', text: 'Gemini writes what she says. A free key from Google AI Studio is plenty. Without one she still talks, with simpler lines.', fields: [['Gemini API key', 'gemini_api_key', true]], link: ['Get a free key', 'https://aistudio.google.com/apikey'] },
    { icon: 'place', title: 'Your town', text: 'She talks about your local news and weather. Town, State works best.', fields: [['Town', 'city']] },
  ];
  const dots = () => step >= 0 && step < steps.length ? `<div class="dots">${steps.map((_, i) => `<i class="${i === step ? 'on' : i < step ? 'done' : ''}"></i>`).join('')}</div><button class="round w-back" data-w="back" title="Back">${icon('back', 20)}</button>` : '';
  const show = html => { stage.innerHTML = dots() + `<div style="--from:${from}px">${html}</div>`; };
  const hello = () => show(`<div class="hello-stage"><div class="reveal">${bars(true)}</div><h1 class="reveal" style="--delay:.35s">Hi, I'm Cara.</h1><p class="reveal" style="--delay:.6s">Your music, now a radio station${first && first !== 'friend' ? `, ${esc(first)}` : ''}.</p>
    <button class="pill primary reveal" style="--delay:1s;width:300px;height:54px;font-size:16px" data-w="next">Set Up</button><div class="faint reveal" style="--delay:1.2s;margin-top:14px">Takes about a minute.</div></div>`);
  const stepView = () => {
    const st = steps[step], c = S.config, s = S.state || {};
    const ok = st.connect && s.connected;
    show(`<div class="step-card"><div class="ic">${icon(st.icon, 26)}</div><h2>${esc(st.title)}</h2><p>${st.text}</p>
      ${st.fields.map(([l, k, secret]) => `<div class="field"><label>${esc(l)}</label><div class="box"><input data-wkey="${k}" type="${secret ? 'password' : 'text'}" value="${esc(c[k] || '')}" spellcheck="false" autocomplete="off"></div></div>`).join('')}
      ${ok ? `<div class="ok">${icon('checkCircle', 20)}Connected${s.me ? ' as ' + esc(s.me.name) : ''}</div>` : ''}
      <div class="err" id="w-err"></div>
      ${st.link ? `<button class="link" data-w="link" data-url="${st.link[1]}">${esc(st.link[0])} ${icon('open', 14)}</button>` : ''}
      <div class="go"><button class="pill primary" data-w="${st.connect && !ok ? 'connect' : 'next'}">${st.connect && !ok ? 'Connect Spotify' : 'Continue'}</button><button class="link" data-w="skip" style="height:36px">Skip for now</button></div></div>`);
  };
  const finale = () => {
    const name = firstName();
    show(`<div class="finale"><svg class="ring" width="128" height="128" viewBox="0 0 128 128"><circle cx="64" cy="64" r="62" fill="rgba(255,255,255,.06)" stroke="rgba(255,255,255,.1)"/>
      <circle cx="64" cy="64" r="61" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-dasharray="384" stroke-dashoffset="384" transform="rotate(-90 64 64)"><animate attributeName="stroke-dashoffset" from="384" to="0" begin=".3s" dur=".85s" fill="freeze"/></circle>
      <path d="M41 68 L57 82 L89 47" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="80" stroke-dashoffset="80"><animate attributeName="stroke-dashoffset" from="80" to="0" begin="1.05s" dur=".4s" fill="freeze"/></path></svg>
      <h1 class="reveal" style="--delay:1.3s">You're all set${name ? ', ' + esc(name) : ''}.</h1><p class="reveal" style="--delay:1.55s">Enjoy the show.</p>
      <button class="pill primary reveal" style="--delay:1.9s;width:300px;height:54px;font-size:16px" data-w="finish">Start Listening</button></div>`);
  };
  const goStep = (n, dir = 1) => { from = dir * 36; step = n; if (step < 0) hello(); else if (step >= steps.length) finale(); else stepView(); };
  const save = async () => {
    const patch = {}; $$('[data-wkey]', stage).forEach(i => { patch[i.dataset.wkey] = i.value.trim(); });
    if (Object.keys(patch).length) { const r = await api.set_config(patch); if (r && r.config) S.config = r.config; }
  };
  const open = () => { if (lid.classList.contains('open')) return; lid.classList.add('open'); setTimeout(() => lid.remove(), 1300); setTimeout(() => goStep(-1), 350); };
  lid.addEventListener('click', open);
  let y0 = null;
  lid.addEventListener('pointerdown', e => { y0 = e.clientY; lid.setPointerCapture(e.pointerId); lid.style.transition = 'none'; });
  lid.addEventListener('pointermove', e => { if (y0 == null) return; const dy = Math.min(0, e.clientY - y0); lid.style.transform = `translateY(${dy * .72}px)`; });
  lid.addEventListener('pointerup', e => { if (y0 == null) return; const dy = e.clientY - y0; y0 = null; lid.style.transition = ''; lid.style.transform = ''; if (dy < -110) open(); });
  w.addEventListener('click', async e => {
    const b = e.target.closest('[data-w]'); if (!b) return;
    const what = b.dataset.w;
    if (what === 'next') { await save(); goStep(step + 1); }
    if (what === 'skip') goStep(step + 1);
    if (what === 'back') goStep(step - 1, -1);
    if (what === 'link') api.open_url(b.dataset.url);
    if (what === 'connect') {
      await save();
      const c = S.config;
      if (!c.spotify_client_id || !c.spotify_client_secret) { $('#w-err').textContent = 'Paste the Client ID and Client Secret first.'; return; }
      b.disabled = true; b.textContent = 'Connecting… (click Agree in your browser)';
      await api.connect(true);
      const t0 = Date.now();
      while (Date.now() - t0 < 180000) {
        await sleep(800); await pollOnce();
        const s = S.state || {};
        if (s.connected) { goStep(step); setTimeout(() => step === 0 && goStep(1), 900); return; }
        if (!s.connecting && Date.now() - t0 > 2500) { b.disabled = false; b.textContent = 'Connect Spotify'; $('#w-err').textContent = s.problem || "That didn't connect. Check the codes and the Redirect URI, then try again."; return; }
      }
      b.disabled = false; b.textContent = 'Connect Spotify';
    }
    if (what === 'finish') {
      await api.set_config({ welcomed: true }); S.config.welcomed = true;
      w.classList.add('leaving'); setTimeout(() => w.remove(), 650);
    }
  });
  if (again) { lid.remove(); goStep(-1); }
}

// ---------------------------------------------------------------- starting up
async function start() {
  S.boot = await api.boot();
  S.config = S.boot.config || {};
  wireSeek($('#bar-seek')); wireSeek($('#np-seek'));
  $('#scroller').addEventListener('scroll', () => {
    $('#main-head').classList.toggle('solid', $('#scroller').scrollTop > 60);
    loadMore();
  }, { passive: true });
  if (['song', 'black', 'white'].includes(S.config.ui_bg)) S.ui.bg = S.config.ui_bg;   // the window opened in this one
  applyUi();
  $('#lib-list').addEventListener('scroll', loadLibMore, { passive: true });
  $('#tb-menu').addEventListener('mousedown', e => e.preventDefault());
  $('#tb-menu').addEventListener('click', e => { e.stopPropagation(); openMainMenu(); });
  $('#tb-me').addEventListener('click', e => { e.stopPropagation(); accountMenu(); });
  $('#nv-full').addEventListener('click', () => openNP());
  $('#nv-more').addEventListener('click', e => { e.stopPropagation(); const t = nowTrack(); const r = e.currentTarget.getBoundingClientRect(); if (t) trackMenu(t.uri, r.left - 220, r.bottom + 6); });
  $('#playing-on').addEventListener('click', e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); devicesMenu(r.right - 300, r.top - 280); });
  $('#lib-filter').addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); ACTIONS['lib-find'](); } });
  wireDrag($('#drag-l'), 'libW', 1); wireDrag($('#drag-r'), 'nvW', -1);
  $('#drag-l').addEventListener('dblclick', toggleLibrary);
  $('#drag-r').addEventListener('dblclick', () => toggleNV(S.nv.mode || 'now'));
  $('#np-panel').addEventListener('wheel', () => { S.np.userScrolled = true; clearTimeout(S.np.usTimer); S.np.usTimer = setTimeout(() => { S.np.userScrolled = false; }, 4000); }, { passive: true });
  document.addEventListener('wheel', e => {
    if (!e.target.closest('.vol')) return;
    e.preventDefault();
    const step = Math.max(1, Math.round(Math.abs(e.deltaY) / 20));
    setVolume(volNow() + (e.deltaY < 0 ? Math.min(step, 5) : -Math.min(step, 5)));
  }, { passive: false });
  $('#bar-play').addEventListener('click', () => cmd('toggle'));
  $('#np-play').addEventListener('click', () => cmd('toggle'));
  for (const p of ['bar', 'np']) {
    $(`#${p}-next`).addEventListener('click', () => cmd('next'));
    $(`#${p}-prev`).addEventListener('click', () => { if (progressNow() > 3000) cmd('seek', 0); else cmd('previous'); });
    $(`#${p}-shuffle`).addEventListener('click', () => toggleShuffle());
    $(`#${p}-repeat`).addEventListener('click', () => { const r = (now() && now().repeat) || 'off'; cmd('repeat', r === 'off' ? 'context' : r === 'context' ? 'track' : 'off'); });
    $(`#${p}-like`).addEventListener('click', () => { const t = nowTrack(); if (t) toggleLike(t.uri); });
  }
  $('#bar-art').addEventListener('click', () => toggleNV('now'));
  $('#bar-title').addEventListener('click', () => { const t = nowTrack(); if (t && t.albumId) go({ name: 'album', id: t.albumId }); });
  $('#bar-lyrics').addEventListener('click', () => { S.np.open && S.np.tab === 'lyrics' ? closeNP() : openNP('lyrics'); updateBar(); });
  $('#bar-queue').addEventListener('click', toggleQueue);
  $('#bar-dev').addEventListener('click', e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); devicesMenu(r.left - 120, r.top - 220); });
  $('#bar-vis').addEventListener('click', () => Vis.open());
  $('#bar-expand').addEventListener('click', () => S.np.open ? closeNP() : openNP());
  $('#np-close').addEventListener('click', closeNP);
  $('#np-vis').addEventListener('click', () => Vis.open());
  $('#np-sleep').addEventListener('click', e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); sleepMenu(r.left - 190, r.bottom + 8); });
  $('#np-more').addEventListener('click', e => { e.stopPropagation(); const t = nowTrack(); const r = e.currentTarget.getBoundingClientRect(); if (t) trackMenu(t.uri, r.left - 180, r.bottom + 6); });
  $$('#np .tabs .chip').forEach(c => c.addEventListener('click', () => { S.np.tab = c.dataset.tab; S.np.userScrolled = false; updateNP(); loadNPTab(); }));
  $('#np-device').addEventListener('click', e => { e.stopPropagation(); devicesMenu(e.clientX, e.clientY - 200); });
  renderLibrary(); updateBar(); updateHero();
  render(true);
  if (S.ui.nvOpen) nvRender(true);
  requestAnimationFrame(tickProgress);
  pollLoop();
  if (!S.config.welcomed) showWelcome();
}
if (window.pywebview && window.pywebview.api) start(); else window.addEventListener('pywebviewready', start);
