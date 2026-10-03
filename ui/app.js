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
};

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
  $('#topbar .title').textContent = v.title ? v.title(r) : '';
  $('#nav-back').disabled = !S.history.length;
  $('#nav-fwd').disabled = !S.future.length;
  $$('.nav button').forEach(b => b.classList.toggle('on', b.dataset.tab === r.tab));
  $$('.side-item[data-route]').forEach(b => b.classList.toggle('on', b.dataset.route === routeKey(r)));
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
  const live = !!d.running;
  $('#brand-dot').className = 'dot' + (live ? ' live' : '');
  $('#brand-status').textContent = live ? (d.speaking ? 'On the mic' : d.status) : (d.stationFull || 'Off air');
  $('#brand-bars').outerHTML = bars(live).replace('class="bars', 'id="brand-bars" class="bars');
  const b = $('#dj-btn');
  b.className = 'dj-btn' + (d.speaking ? ' speaking' : live ? ' live' : '');
  b.innerHTML = `${bars(live)}<span>${d.speaking ? 'On Air' : live ? 'Cara · Live' : 'Go Live'}</span>`;
  b.title = live ? 'Cara is live. Click to end her show.' : 'Start Cara';
}
function banners() {
  const s = S.state || {};
  if (!s.keys) return `<div class="banner glass">${'<div class="ic">' + icon('radio') + '</div>'}<div class="txt"><b>Connect Spotify</b><span>Add your Spotify app's Client ID and Secret in Settings. Your music plays through the Spotify app; Cara talks over it from here.</span></div><button class="pill primary small" data-act="settings">Open Settings</button></div>`;
  if (s.connecting) return `<div class="banner glass"><div class="ic"><div class="spinner" style="width:20px;height:20px"></div></div><div class="txt"><b>Connecting to Spotify…</b><span>The first time, a browser tab opens: click Agree, then come back here.</span></div></div>`;
  if (!s.connected) return `<div class="banner glass warn"><div class="ic">${icon('warning')}</div><div class="txt"><b>Spotify isn't connected</b><span>${esc(s.problem || 'Press Connect to log in.')}</span></div><button class="pill primary small" data-act="connect">Connect</button><button class="pill secondary small" data-act="settings">Settings</button></div>`;
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
    const h = S.home;
    const date = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }).toUpperCase();
    let out = `<div class="hello"><div class="date">${esc(date)}</div><h1>${esc(greet())}${firstName() ? ', ' + esc(firstName()) : ''}</h1></div>${banners()}${heroHTML()}`;
    if (!h) return out + (S.state && S.state.connected ? loading() : '');
    if (h.recentAlbums && h.recentAlbums.length) out += `<section class="block">${head('Recently Played')}${rowOf(h.recentAlbums.map(a => albumCard(a)))}</section>`;
    if (h.topTracks && h.topTracks.length) out += `<section class="block">${head('On Repeat', 'Your most played lately')}${songGrid(h.topTracks.slice(0, 12), 'On Repeat')}</section>`;
    if (h.topArtists && h.topArtists.length) out += `<section class="block">${head('Artists You Love')}${rowOf(h.topArtists.map(artistCard))}</section>`;
    if (h.playlists && h.playlists.length) out += `<section class="block">${head('Your Playlists', '', 'data-act="lib" data-kind="playlists"')}${rowOf([likedCard(), ...h.playlists.slice(0, 20).map(playlistCard)])}</section>`;
    if (h.albums && h.albums.length) out += `<section class="block">${head('Your Albums', '', 'data-act="lib" data-kind="albums"')}${rowOf(h.albums.slice(0, 20).map(a => albumCard(a)))}</section>`;
    return out;
  },
  load: async () => { if (S.state && S.state.connected) await loadHome(); },
};
async function loadHome(force = false) {
  const h = await api.home(!!force);
  if (h && !h.error) { S.home = h; remember(h.topTracks); remember(h.liked); renderSidebar(); }
}

VIEWS.cara = {
  title: () => 'Cara',
  html: () => {
    const c = S.config, d = (S.state && S.state.dj) || {};
    const seg = (key, opts) => `<div class="seg">${opts.map(([v, l]) => `<button class="${String(c[key]) === String(v) ? 'on' : ''}" data-act="cfg" data-key="${key}" data-val="${v}">${l}</button>`).join('')}</div>`;
    const tog = key => `<button class="toggle ${c[key] ? 'on' : ''}" data-act="cfg-toggle" data-key="${key}" aria-pressed="${!!c[key]}"></button>`;
    const step = (key, stepBy, min, max) => `<div class="stepper"><button data-act="cfg-step" data-key="${key}" data-step="${-stepBy}" data-min="${min}" data-max="${max}">${icon('minus', 16)}</button><button data-act="cfg-step" data-key="${key}" data-step="${stepBy}" data-min="${min}" data-max="${max}">${icon('plus', 16)}</button></div>`;
    const slider = key => `<input type="range" min="0" max="100" value="${c[key]}" data-key="${key}">`;
    const moods = { chill: 'Laid-back, warm and smooth, with dry wit.', normal: 'Her usual bubbly, cheeky, quick self.', unhinged: 'Maximum playful chaos and mock outrage.', mixed: 'A different mood every break.' };
    const chats = { quick: 'A line or two, then the music.', normal: 'A short story or bit, then the music.', chatty: 'Proper segments: stories, games, news.' };
    return `<h1 class="page-title">Cara</h1>${banners()}${heroHTML()}
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
        <div class="set-row"><span>Station tags in her voice</span>${tog('stingers')}</div>
        <button class="link" data-act="open-stingers">${icon('folder', 16)}Open my stingers folder</button></div>
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
    const total = (h && h.likedTotal) || tracks.length;
    return `<div class="page-hero">${likedArt(236, 14)}<div class="meta"><div class="caps">Playlist</div><h1>Liked Songs</h1>
      <div class="line">${firstName() ? `<b>${esc(firstName())}</b> •` : ''} ${plural(total, 'song')}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-liked" title="Play">${icon('play', 28)}</button><button class="icon-btn" style="width:46px;height:46px" data-act="play-liked" data-shuffle="1" title="Shuffle">${icon('shuffle', 26)}</button></div>
      ${tracks.length ? trackTable(tracks, { ctx: myId() ? `spotify:user:${myId()}:collection` : '' }) : (h ? empty('heart', 'No liked songs yet', 'Tap the heart on any song to save it here.') : loading())}
      ${tracks.length < total ? `<div class="loading" data-more="liked"><div class="spinner"></div></div>` : ''}`;
  },
  load: async r => { if (!S.home) await loadHome(); r.tracks = (S.home && S.home.liked || []).slice(); },
};

VIEWS.playlist = {
  title: r => (r.data && r.data.playlist.name) || 'Playlist',
  html: r => {
    const d = r.data;
    if (!d) return r.loaded ? empty('warning', "Couldn't load this playlist", 'Spotify didn\'t answer. Try again in a moment.') : loading();
    const p = d.playlist, mine = p.ownerId && p.ownerId === myId();
    const length = d.tracks.reduce((a, t) => a + (t.duration || 0), 0);
    return `<div class="page-hero">${art(p.image || p.imageMid, 236, { icon: 'queue' })}<div class="meta"><div class="caps">${p.collaborative ? 'Collaborative playlist' : 'Playlist'}</div><h1>${esc(p.name)}</h1>
      ${p.about ? `<div class="about">${esc(p.about)}</div>` : ''}
      <div class="line"><b>${esc(p.owner)}</b> • ${plural(d.total, 'song')}${d.canList && d.tracks.length >= d.total ? ', ' + fmtLength(length) : ''}</div></div></div>
      <div class="actions-row"><button class="big-play" data-act="play-ctx" data-ctx="${esc(p.uri)}" title="Play">${icon('play', 28)}</button>
        <button class="icon-btn" style="width:46px;height:46px" data-act="play-ctx" data-ctx="${esc(p.uri)}" data-shuffle="1" title="Shuffle">${icon('shuffle', 26)}</button>
        ${mine ? '' : `<button class="icon-btn ${d.saved ? 'on' : ''}" style="width:46px;height:46px" data-act="save-page" data-uri="${esc(p.uri)}" title="${d.saved ? 'Remove from Your Library' : 'Save to Your Library'}">${icon(d.saved ? 'checkCircle' : 'addCircle', 30)}</button>`}
        <button class="icon-btn" style="width:46px;height:46px" data-act="share" data-url="https://open.spotify.com/playlist/${esc(p.id)}" title="Copy link">${icon('share', 22)}</button></div>
      ${!d.canList ? `<div class="locked">${icon('lock', 26)}<p>Spotify only lets apps like this one list the songs in playlists you made or collaborate on. You can still play this one.</p></div>`
        : d.tracks.length ? trackTable(d.tracks, { ctx: p.uri }) : empty('queue', 'Empty playlist', "Add songs from any song's ••• menu.")}
      ${d.canList && d.tracks.length < d.total ? `<div class="loading" data-more="playlist"><div class="spinner"></div></div>` : ''}`;
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
        <button class="icon-btn" style="width:46px;height:46px" data-act="play-ctx" data-ctx="${esc(a.uri)}" data-shuffle="1" title="Shuffle">${icon('shuffle', 26)}</button>
        <button class="icon-btn ${d.saved ? 'on' : ''}" style="width:46px;height:46px" data-act="save-page" data-uri="${esc(a.uri)}" title="${d.saved ? 'Remove from Your Library' : 'Save to Your Library'}">${icon(d.saved ? 'checkCircle' : 'addCircle', 30)}</button>
        <button class="icon-btn" style="width:46px;height:46px" data-act="share" data-url="https://open.spotify.com/album/${esc(a.id)}" title="Copy link">${icon('share', 22)}</button></div>
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
        <button class="icon-btn" style="width:46px;height:46px" data-act="share" data-url="https://open.spotify.com/artist/${esc(a.id)}" title="Copy link">${icon('share', 22)}</button></div>
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
        <button class="icon-btn" style="width:46px;height:46px" data-act="play-list" data-list="${r.list}" data-shuffle="1" data-name="${esc(g.title)}" title="Shuffle">${icon('shuffle', 26)}</button></div>
        <section class="block">${head('Songs')}${trackTable(d.tracks, { list: r.list })}</section>` : ''}
      ${d.playlists.length ? `<section class="block">${head('Playlists')}${rowOf(d.playlists.map(playlistCard))}</section>` : ''}`}`;
  },
  load: async r => { const d = await api.genre(r.id); if (d && !d.error) { r.data = d; remember(d.tracks); } },
};
function tile(h) { return `linear-gradient(135deg, hsl(${h * 360} 62% 50%), hsl(${((h + .06) % 1) * 360} 78% 30%))`; }

VIEWS.search = {
  title: () => 'Search',
  html: () => {
    const s = S.search, c = S.config;
    let out = `<div class="search-box">${icon('search', 22)}<input id="search-input" placeholder="What do you want to play?" value="${esc(s.q)}" spellcheck="false" autocomplete="off">
      ${s.q ? `<button class="icon-btn" data-act="search-clear" title="Clear">${icon('close', 18)}</button>` : ''}</div>`;
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

// ---------------------------------------------------------------- the sidebar
function renderSidebar() {
  const h = S.home, list = $('#side-list');
  const playing = now() && now().context;
  const items = [`<button class="side-item" data-route="liked:" data-act="open-liked">${likedArt(40, 7)}<div class="ell"><b class="ell">Liked Songs</b><small class="ell">${h ? plural(h.likedTotal || 0, 'song') : 'Playlist'}</small></div></button>`];
  for (const p of (h && h.playlists) || []) items.push(`<button class="side-item ${playing === p.uri ? 'playing' : ''}" data-route="playlist:${esc(p.id)}" data-act="open-playlist" data-id="${esc(p.id)}">${art(p.imageMid || p.image, 40, { icon: 'queue' })}<div class="ell"><b class="ell">${esc(p.name)}</b><small class="ell">Playlist • ${esc(p.owner)}</small></div></button>`);
  list.innerHTML = items.join('');
  $$('.side-item[data-route]').forEach(b => b.classList.toggle('on', b.dataset.route === routeKey(S.route)));
  const me = S.state && S.state.me;
  $('#me-avatar').style.backgroundImage = me && me.image ? `url('${me.image}')` : '';
  $('#me-avatar').innerHTML = me && me.image ? '' : icon('person', 18);
  $('#me-name').textContent = me ? me.name : 'Settings';
}

// ---------------------------------------------------------------- playing music
async function play(opts, name) {
  const r = await api.play(opts.context || null, opts.offset || null, opts.uris || null, opts.position ?? null, opts.shuffle ?? null);
  if (r !== 'ok') toast(typeof r === 'string' ? r : "Spotify wouldn't play that.", 'warning');
}
function playRow(el) {
  const uri = el.dataset.uri, ctx = el.dataset.ctx, list = el.dataset.list;
  if (ctx) return play({ context: ctx, offset: uri });
  const uris = S.lists.get(list) || [uri];
  play({ uris, offset: uri });
}
function updateLike(uri, on) {
  S.liked.set(uri, on);
  $$(`[data-act=like][data-uri="${CSS.escape(uri)}"]`).forEach(b => { b.classList.toggle('liked', on); b.innerHTML = icon(on ? 'heart' : 'heartOutline', 18); });
  if (S.state && S.state.now && S.state.now.track && S.state.now.track.uri === uri) { S.state.now.liked = on; updateBar(); updateNP(); }
}
async function toggleLike(uri) {
  const on = !S.liked.get(uri) && !(nowUri() === uri && now().liked);
  updateLike(uri, on);
  const ok = await api.set_saved(uri, on);
  if (ok === true) toast(on ? 'Added to Liked Songs' : 'Removed from Liked Songs', on ? 'heart' : 'heartOutline');
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
      $('#bar-art').innerHTML = art(t.artMid || t.art, 60) + `<div class="expand">${icon('expand', 20)}</div>`;
      $('#bar-title').textContent = t.title;
      $('#bar-artist').innerHTML = artistLinks(t);
    }
    const liked = n.liked ?? S.liked.get(t.uri);
    const lb = $('#bar-like'); lb.hidden = false; lb.classList.toggle('liked', !!liked); lb.innerHTML = icon(liked ? 'heart' : 'heartOutline', 18);
  } else {
    bar.dataset.uri = '';
    $('#bar-art').innerHTML = art('', 60);
    $('#bar-title').textContent = S.state && S.state.connected ? 'Nothing playing' : 'Not connected';
    $('#bar-artist').textContent = S.state && S.state.connected ? 'Pick something to play' : 'Connect Spotify in Settings';
    $('#bar-like').hidden = true;
  }
  const playing = !!(n && n.playing);
  $('#bar-play').innerHTML = icon(playing ? 'pause' : 'play', 22);
  $('#bar-shuffle').classList.toggle('on', !!(n && n.shuffle));
  const rep = (n && n.repeat) || 'off';
  $('#bar-repeat').classList.toggle('on', rep !== 'off');
  $('#bar-repeat').innerHTML = icon(rep === 'track' ? 'repeatOne' : 'repeat', 20);
  const dev = n && n.device;
  $('#bar-dev').classList.toggle('on', !!dev);
  $('#bar-dev').title = dev ? `Playing on ${dev.name}` : 'Devices';
  const vol = $('#bar-vol');
  if (dev && dev.volume != null && document.activeElement !== vol && Date.now() - (vol.dataset.t || 0) > 2500) { vol.value = dev.volume; paintRange(vol); }
  $('#bar-mute').innerHTML = icon(dev && dev.volume === 0 ? 'volumeOff' : 'volume', 20);
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
  const lb = $('#np-like'); lb.classList.toggle('liked', !!liked); lb.innerHTML = icon(liked ? 'heart' : 'heartOutline', 24);
  $('#np-play').innerHTML = icon(n && n.playing ? 'pause' : 'play', 34);
  $('#np-shuffle').classList.toggle('on', !!(n && n.shuffle));
  const rep = (n && n.repeat) || 'off';
  $('#np-repeat').classList.toggle('on', rep !== 'off'); $('#np-repeat').innerHTML = icon(rep === 'track' ? 'repeatOne' : 'repeat', 24);
  const dj = $('#np-dj'); dj.classList.toggle('on', !!d.running); dj.title = d.running ? 'End her show' : 'Go live';
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
    const q = await api.up_next();
    if (S.np.tab !== 'queue') return;
    const list = Array.isArray(q) ? remember(q) : [];
    panel.innerHTML = list.length ? `<div class="caps" style="margin:0 10px 10px">Next up</div>${list.map(x => `<div class="q-row" data-row data-uri="${esc(x.uri)}">${art(x.artMid, 48)}<div class="ell"><b class="ell">${esc(x.title)}</b><small class="ell">${esc(x.artistLine)}</small></div><button class="icon-btn" data-act="track-menu" data-uri="${esc(x.uri)}">${icon('more', 18)}</button></div>`).join('')}` : `<div class="lyrics"><div class="note">Nothing queued after this song.</div></div>`;
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
function showMenu(items, x, y) {
  closeMenu();
  const m = document.createElement('div'); m.id = 'menu';
  m.innerHTML = items.map((it, i) => it === '-' ? '<hr>' : it.head ? `<div class="menu-head">${esc(it.head)}</div>` : `<button data-i="${i}" class="${it.on ? 'on' : ''}">${icon(it.icon || 'note', 18)}<span class="ell">${esc(it.label)}</span></button>`).join('');
  document.body.appendChild(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 10) + 'px';
  m.style.top = Math.min(y, innerHeight - r.height - 10) + 'px';
  m.addEventListener('click', e => { const b = e.target.closest('button[data-i]'); if (!b) return; e.stopPropagation(); closeMenu(); items[+b.dataset.i].run(); });
}
function closeMenu() { const m = $('#menu'); if (m) m.remove(); }
function trackMenu(uri, x, y) {
  const t = S.tracks.get(uri) || (nowUri() === uri ? nowTrack() : null); if (!t) return;
  const liked = S.liked.get(uri) || (nowUri() === uri && now().liked);
  const items = [
    { label: 'Add to queue', icon: 'queue', run: async () => toast((await api.queue_add(uri)) ? 'Added to the queue' : "Spotify wouldn't queue that.", 'queue') },
    { label: liked ? 'Remove from Liked Songs' : 'Add to Liked Songs', icon: liked ? 'heart' : 'heartOutline', run: () => toggleLike(uri) },
    { label: 'Add to a playlist…', icon: 'playlistAdd', run: () => addToPlaylist(t) },
    '-',
  ];
  if (t.albumId) items.push({ label: 'Go to album', icon: 'album', run: () => go({ name: 'album', id: t.albumId }) });
  const aid = t.artists && t.artists[0] && t.artists[0].id;
  if (aid) items.push({ label: 'Go to artist', icon: 'mic', run: () => go({ name: 'artist', id: aid }) });
  if (t.id) items.push({ label: 'Copy song link', icon: 'link', run: () => copy(`https://open.spotify.com/track/${t.id}`) });
  showMenu(items, x, y);
}
function copy(text) { navigator.clipboard.writeText(text).then(() => toast('Link copied', 'link'), () => toast(text, 'link')); }
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
async function devicesMenu(x, y) {
  const list = await api.devices();
  const devs = Array.isArray(list) ? list : [];
  const items = [{ head: 'Play on' }];
  if (!devs.length) items.push({ label: 'No devices: open Spotify somewhere', icon: 'warning', run: () => api.open_spotify() });
  const pl = (S.state && S.state.player) || {};
  const mine = d => (pl.deviceId && d.id === pl.deviceId) || d.name === (pl.name || 'Non Stop Pop DJ');
  devs.sort((a, b) => mine(b) - mine(a));
  for (const d of devs) items.push({ label: (mine(d) ? 'This app (built-in player)' : d.name) + (d.active ? '  (playing)' : ''), icon: mine(d) ? 'headphones' : d.type === 'computer' ? 'computer' : d.type === 'smartphone' ? 'phone' : 'speaker', on: d.active, run: async () => { if (!d.restricted) { await api.transfer(d.id); toast(mine(d) ? 'Playing in this app' : `Playing on ${d.name}`, 'speaker'); } } });
  if (pl.mode === 'app' && pl.ready && !devs.some(mine)) items.push({ label: 'This app (built-in player)', icon: 'headphones', run: async () => { await api.use_builtin(); toast('Playing in this app', 'speaker'); } });
  if (pl.mode === 'app' && !pl.ready) items.push({ label: pl.status === 'starting' ? 'This app: starting…' : 'This app: not available', icon: 'headphones', run: () => settingsSheet() });
  items.push('-', { label: "Cara's voice comes out of this PC", icon: 'mic', run: () => {} });
  showMenu(items, x, y);
}
function sleepMenu(x, y) {
  const s = S.state && S.state.sleep;
  const items = [{ head: 'Sleep timer' }];
  for (const [label, m] of [['15 minutes', 15], ['30 minutes', 30], ['45 minutes', 45], ['1 hour', 60], ['End of this song', -1]]) items.push({ label, icon: 'moon', run: async () => { await api.sleep(m); toast(m === -1 ? 'Stopping after this song' : `Stopping in ${label}`, 'moon'); } });
  if (s && (s.at || s.endOfSong)) items.push('-', { label: 'Turn off the sleep timer', icon: 'close', run: async () => { await api.sleep(0); toast('Sleep timer off', 'moon'); } });
  showMenu(items, x, y);
}

// ---------------------------------------------------------------- Settings
function settingsSheet() {
  const c = S.config, s = S.state || {}, me = s.me;
  const field = (label, key, o = {}) => `<div class="field"><label>${esc(label)}</label><div class="box"><input data-key="${key}" type="${o.secret ? 'password' : 'text'}" value="${esc(c[key] || '')}" placeholder="${esc(o.ph || '')}" spellcheck="false" autocomplete="off">${o.secret ? `<button class="icon-btn" data-reveal title="Show">${icon('eye', 18)}</button>` : ''}</div>${o.hint ? `<div class="hint">${o.hint}</div>` : ''}</div>`;
  const w = sheet('Settings', `
    <div class="group"><div class="account-row"><div class="avatar" style="${me && me.image ? `background-image:url('${esc(me.image)}')` : ''}">${me && me.image ? '' : icon('person', 24)}</div>
      <div class="ell" style="flex:1"><b class="ell">${esc(me ? me.name : s.connected ? 'Connected' : 'Not connected')}</b><span class="muted">${s.connected ? 'Spotify connected' : s.connecting ? 'Connecting…' : 'Spotify not connected'}</span></div>
      <button class="pill secondary small" data-set="reconnect">Reconnect</button>${s.connected ? '<button class="pill secondary small" data-set="logout">Log out</button>' : ''}</div></div>
    <div class="group"><div class="caps">Where your music plays</div>
      <div class="seg">${[['app', 'This app'], ['spotify', 'Spotify app']].map(([v, l]) => `<button class="${(c.player || 'app') === v ? 'on' : ''}" data-player="${v}">${l}</button>`).join('')}</div>
      <div class="note">${(c.player || 'app') === 'app'
        ? `Music plays right here, no Spotify app needed (Spotify Premium only). ${esc(playerNote(s.player))}`
        : 'Music plays in the Spotify app, on this PC or any other device, and this app controls it.'}</div></div>
    <div class="group"><div class="caps">Spotify app</div>
      ${field('Client ID', 'spotify_client_id', { ph: 'Paste it here' })}
      ${field('Client Secret', 'spotify_client_secret', { secret: true, ph: 'Paste it here' })}
      <div class="note">From <button class="link" data-set="dashboard">developer.spotify.com/dashboard</button>. Its Redirect URI must be <b class="num" style="color:#fff">${esc(S.boot.redirect)}</b> <button class="link" data-set="copy-redirect">Copy</button>, and everyone who uses it has to be added under User Management.</div></div>
    <div class="group"><div class="caps">Her voice</div>
      <div class="field"><label>Voice engine</label><div class="box"><select data-key="tts_engine">${[['elevenlabs', 'ElevenLabs (her real voice)'], ['gemini', 'Gemini voice'], ['edge', 'Microsoft voice (free, no key)']].map(([v, l]) => `<option value="${v}" ${c.tts_engine === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div></div>
      ${field('ElevenLabs API key', 'elevenlabs_api_key', { secret: true, ph: 'sk_…' })}
      ${field('Cara\'s Voice ID', 'elevenlabs_voice_id', { ph: 'Voice ID' })}
      <div class="field"><label>Model</label><div class="box"><select data-key="eleven_model">${[['eleven_v4', 'Eleven v4 (most expressive)'], ['eleven_v4_turbo', 'Eleven v4 Turbo (faster)'], ['eleven_multilingual_v2', 'Multilingual v2 (older)']].map(([v, l]) => `<option value="${v}" ${c.eleven_model === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div></div></div>
    <div class="group"><div class="caps">Scratch's voice (co-host)</div>
      ${field("Scratch's Voice ID", 'cohost_voice', { ph: 'Leave blank for a deep, warm radio voice', hint: 'Add any voice from the ElevenLabs Voice Library to My Voices and paste its ID here.' })}</div>
    <div class="group"><div class="caps">Her words (Gemini)</div>
      ${field('Gemini API key', 'gemini_api_key', { secret: true, ph: 'Paste it here', hint: 'A free key from <button class="link" data-set="aistudio">Google AI Studio</button>. Without one she still talks, with simpler lines.' })}</div>
    <div class="group"><div class="caps">Your town</div>${field('Town', 'city', { ph: 'Yakima, Washington', hint: 'Town, State. For local news and weather.' })}</div>
    <div class="group"><button class="pill secondary" data-set="welcome">Show the welcome setup again</button>
      <div class="note">Non Stop Pop DJ ${esc(S.boot.version)}. Music plays through the Spotify app; Cara talks over it from here. Your keys are saved on this PC only.</div></div>`, true);
  w.addEventListener('change', async e => {
    const el = e.target.closest('[data-key]'); if (!el) return;
    const res = await api.set_config({ [el.dataset.key]: el.value });
    if (res && res.config) { S.config = res.config; toast('Saved', 'check'); if (res.reconnect) api.connect(true); }
  });
  w.addEventListener('click', async e => {
    const rv = e.target.closest('[data-reveal]'); if (rv) { const i = rv.parentElement.querySelector('input'); i.type = i.type === 'password' ? 'text' : 'password'; return; }
    const pb = e.target.closest('[data-player]');
    if (pb) { await setConfig({ player: pb.dataset.player }); closeSheet(); settingsSheet(); return; }
    const b = e.target.closest('[data-set]'); if (!b) return;
    const what = b.dataset.set;
    if (what === 'reconnect') { await api.connect(true); toast('A browser tab opens: click Agree', 'link'); closeSheet(); }
    if (what === 'logout') { await api.logout(); S.home = null; closeSheet(); render(); toast('Logged out of Spotify', 'person'); }
    if (what === 'dashboard') api.open_url('https://developer.spotify.com/dashboard');
    if (what === 'aistudio') api.open_url('https://aistudio.google.com/apikey');
    if (what === 'copy-redirect') copy(S.boot.redirect);
    if (what === 'welcome') { closeSheet(); showWelcome(true); }
  });
}

function playerNote(p) {
  p = p || {};
  if (p.status === 'ready') return `Ready (it runs in a hidden ${p.browser || 'Edge'} window).`;
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
    if (n && n.context === el.dataset.ctx && !el.dataset.shuffle) return api.player('toggle').then(poke);
    play({ context: el.dataset.ctx, shuffle: el.dataset.shuffle ? true : null });
  },
  'play-liked': el => { const id = myId(); if (id) play({ context: `spotify:user:${id}:collection`, shuffle: el.dataset.shuffle ? true : null }); else toast('Connect Spotify first.', 'warning'); },
  'play-list': el => { const uris = S.lists.get(el.dataset.list) || []; if (uris.length) play({ uris, shuffle: el.dataset.shuffle ? true : null, position: el.dataset.shuffle ? Math.floor(Math.random() * uris.length) : 0 }); },
  'play-row': el => playRow(el.closest('[data-row]')),
  'play-one': el => play({ uris: [el.dataset.uri] }),
  'select-row': () => {},
  'like': el => toggleLike(el.dataset.uri),
  'track-menu': (el, e) => { const r = el.getBoundingClientRect(); trackMenu(el.dataset.uri, r.left - 200, r.bottom + 4); },
  'save-page': async el => {
    const r = S.route, d = r.data; if (!d) return;
    const on = r.name === 'artist' ? !d.following : !d.saved;
    const ok = await api.set_saved(el.dataset.uri, on);
    if (ok === true) { if (r.name === 'artist') d.following = on; else d.saved = on; refresh(); toast(on ? (r.name === 'artist' ? 'Following' : 'Saved to Your Library') : (r.name === 'artist' ? 'Unfollowed' : 'Removed from Your Library'), on ? 'checkCircle' : 'close'); loadHome(true); }
    else toast("Spotify wouldn't save that.", 'warning');
  },
  'share': el => copy(el.dataset.url),
  'artist-more': () => { S.route.allTop = !S.route.allTop; refresh(); },
  'new-playlist': () => askName(),
  'settings': () => settingsSheet(),
  'sheet-close': () => closeSheet(),
  'connect': () => api.connect(false).then(poke),
  'open-spotify': () => api.open_spotify(),
  'open-stingers': () => api.open_stingers(),
  'dj-toggle': async () => {
    const r = await api.dj_toggle();
    if (r === 'connect') toast('Connecting to Spotify first…', 'link');
    else if (r === 'started') toast('Cara is live', 'radio');
    else if (r === 'stopping') toast('Cara is signing off', 'stop');
    poke();
  },
  'dj-test': async el => { const r = await api.dj_test(el.dataset.what); if (r !== 'ok') toast(r, 'warning'); else toast({ popin: 'Cara pops in now', duo: 'Cara and Scratch, coming up', stinger: 'Stinger!', break: 'Breaking news, coming up' }[el.dataset.what] || 'OK', 'bolt'); },
  'dj-queue': async el => { const r = await api.dj_queue(el.dataset.style); if (r !== 'ok') toast(r, 'warning'); poke(); },
  'cfg': el => setConfig({ [el.dataset.key]: el.dataset.val }),
  'cfg-toggle': el => setConfig({ [el.dataset.key]: !S.config[el.dataset.key] }),
  'cfg-step': el => { const k = el.dataset.key; const v = Math.max(+el.dataset.min, Math.min(+el.dataset.max, (+S.config[k] || 0) + +el.dataset.step)); const patch = { [k]: v }; if (k === 'break_min' && v > S.config.break_max) patch.break_max = v; if (k === 'break_max' && v < S.config.break_min) patch.break_min = v; setConfig(patch); },
  'set-town': () => { const v = $('#town').value.trim(); if (v) setConfig({ city: v }).then(() => toast(`Cara's town: ${v}`, 'place')); },
  'search-term': el => { S.search.q = el.dataset.q; S.search.scope = 'all'; refresh(); runSearch(); },
  'search-clear': () => { S.search.q = ''; S.search.results = null; S.route.focus = true; refresh(); },
  'search-forget': async () => { await api.clear_searches(); S.config.recent_searches = []; refresh(); },
  'search-scope': el => { S.search.scope = el.dataset.scope; if (S.route.name !== 'search') tab('search'); runSearch(); },
  'lyric': el => { api.player('seek', +el.dataset.t); S.state.now.progress = +el.dataset.t; S.state.now.stamp = Date.now(); S.np.userScrolled = false; },
  'vis-open': () => Vis.open(),
  'player-mode': el => setConfig({ player: el.dataset.mode }).then(() => toast(el.dataset.mode === 'app' ? 'Music plays in this app' : 'Music plays in the Spotify app', 'speaker')),
  'player-retry': () => api.player_retry().then(() => toast('Starting the built-in player', 'speaker')),
};
async function setConfig(patch) {
  Object.assign(S.config, patch);
  if (S.route.name === 'cara') refresh();
  const res = await api.set_config(patch);
  if (res && res.config) { S.config = res.config; if (S.route.name === 'cara') refresh(); }
}
function poke() { setTimeout(pollOnce, 250); }

document.addEventListener('click', e => {
  if (!e.target.closest('#menu')) closeMenu();
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
  if (row) trackMenu(row.dataset.uri, e.clientX, e.clientY);
});
document.addEventListener('input', e => {
  const el = e.target;
  if (el.type === 'range') paintRange(el);
  if (el.id === 'search-input') {
    S.search.q = el.value;
    clearTimeout(S.search.timer);
    S.search.timer = setTimeout(() => { if (S.search.q.trim()) runSearch(); else { S.search.results = null; refresh(); $('#search-input').focus(); } }, 320);
  }
});
document.addEventListener('change', e => {
  const el = e.target;
  if (el.type === 'range' && el.dataset.key) setConfig({ [el.dataset.key]: +el.value });
});
document.addEventListener('keydown', e => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement && document.activeElement.tagName);
  if (e.key === 'Escape') { if ($('#menu')) return closeMenu(); if ($('#sheet-wrap')) return closeSheet(); if (Vis.isOpen) return Vis.close(); if (S.np.open) return closeNP(); }
  if (typing) { if (e.key === 'Enter' && e.target.id === 'search-input' && S.search.q.trim()) api.remember_search(S.search.q).then(l => { if (Array.isArray(l)) S.config.recent_searches = l; }); return; }
  if (e.key === ' ') { e.preventDefault(); api.player('toggle').then(poke); }
  else if (e.key === 'ArrowRight' && e.ctrlKey) api.player('next').then(poke);
  else if (e.key === 'ArrowLeft' && e.ctrlKey) api.player('previous').then(poke);
  else if (e.key === 'ArrowLeft' && e.altKey) goBack();
  else if (e.key === 'ArrowRight' && e.altKey) goForward();
  else if (e.key.toLowerCase() === 'v' && !e.ctrlKey) Vis.isOpen ? Vis.close() : Vis.open();
  else if ((e.key === 'f' && e.ctrlKey) || e.key === '/') { e.preventDefault(); S.route.focus = true; if (S.route.name !== 'search') tab('search'); else afterRender(); }
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
  if (s.connected !== lastConnected) {
    const was = lastConnected; lastConnected = s.connected;
    if (s.connected) { loadHome().then(() => { if (['home', 'library', 'liked'].includes(S.route.name)) { S.route.loaded = false; render(); } }); }
    if (was !== null) render();
  }
  updateBar(); updateHero(); updateNP();
  if (nowUri() !== prevUri) { markNow(); if (S.np.open) loadNPTab(); ambient(); }
  else if (s.now && S.lastPlaying !== s.now.playing) markNow();
  S.lastPlaying = s.now && s.now.playing;
  if (S.route.name === 'home' || S.route.name === 'cara') { const b = $('.banner'); const want = banners(); if ((b ? b.outerHTML : '') !== want && !!b !== !!want) refresh(); }
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
    $('#topbar').classList.toggle('solid', $('#scroller').scrollTop > 40);
    loadMore();
  }, { passive: true });
  $('#np-panel').addEventListener('wheel', () => { S.np.userScrolled = true; clearTimeout(S.np.usTimer); S.np.usTimer = setTimeout(() => { S.np.userScrolled = false; }, 4000); }, { passive: true });
  const vol = $('#bar-vol');
  vol.addEventListener('change', () => { vol.dataset.t = Date.now(); api.player('volume', +vol.value); });
  $('#bar-mute').addEventListener('click', () => { const v = +vol.value ? 0 : 60; vol.value = v; paintRange(vol); vol.dataset.t = Date.now(); api.player('volume', v); });
  $('#bar-play').addEventListener('click', () => api.player('toggle').then(poke));
  $('#np-play').addEventListener('click', () => api.player('toggle').then(poke));
  for (const p of ['bar', 'np']) {
    $(`#${p}-next`).addEventListener('click', () => api.player('next').then(poke));
    $(`#${p}-prev`).addEventListener('click', () => { if (progressNow() > 3000) api.player('seek', 0).then(poke); else api.player('previous').then(poke); });
    $(`#${p}-shuffle`).addEventListener('click', () => api.player('shuffle', !(now() && now().shuffle)).then(poke));
    $(`#${p}-repeat`).addEventListener('click', () => { const r = (now() && now().repeat) || 'off'; api.player('repeat', r === 'off' ? 'context' : r === 'context' ? 'track' : 'off').then(poke); });
    $(`#${p}-like`).addEventListener('click', () => { const t = nowTrack(); if (t) toggleLike(t.uri); });
  }
  $('#bar-art').addEventListener('click', () => openNP());
  $('#bar-title').addEventListener('click', () => { const t = nowTrack(); if (t && t.albumId) go({ name: 'album', id: t.albumId }); });
  $('#bar-lyrics').addEventListener('click', () => S.np.open && S.np.tab === 'lyrics' ? closeNP() : openNP('lyrics'));
  $('#bar-queue').addEventListener('click', () => S.np.open && S.np.tab === 'queue' ? closeNP() : openNP('queue'));
  $('#bar-dev').addEventListener('click', e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); devicesMenu(r.left - 120, r.top - 220); });
  $('#bar-sleep').addEventListener('click', e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); sleepMenu(r.left - 120, r.top - 250); });
  $('#bar-vis').addEventListener('click', () => Vis.open());
  $('#bar-expand').addEventListener('click', () => S.np.open ? closeNP() : openNP());
  $('#dj-btn').addEventListener('click', () => ACTIONS['dj-toggle']());
  $('#np-close').addEventListener('click', closeNP);
  $('#np-dj').addEventListener('click', () => ACTIONS['dj-toggle']());
  $('#np-vis').addEventListener('click', () => Vis.open());
  $('#np-sleep').addEventListener('click', e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); sleepMenu(r.left - 190, r.bottom + 8); });
  $('#np-more').addEventListener('click', e => { e.stopPropagation(); const t = nowTrack(); const r = e.currentTarget.getBoundingClientRect(); if (t) trackMenu(t.uri, r.left - 180, r.bottom + 6); });
  $$('#np .tabs .chip').forEach(c => c.addEventListener('click', () => { S.np.tab = c.dataset.tab; S.np.userScrolled = false; updateNP(); loadNPTab(); }));
  $('#np-device').addEventListener('click', e => { e.stopPropagation(); devicesMenu(e.clientX, e.clientY - 200); });
  renderSidebar(); updateBar(); updateHero();
  render(true);
  requestAnimationFrame(tickProgress);
  pollLoop();
  if (!S.config.welcomed) showWelcome();
}
if (window.pywebview && window.pywebview.api) start(); else window.addEventListener('pywebviewready', start);
