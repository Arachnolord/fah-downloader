// ============================================================================
// Erdacht und programmiert von Fabian Hammer.
// (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
// ============================================================================
// Grundsatz in dieser Datei: Fremdtexte (Titel, Dateinamen, Pfade, Fehler)
// werden NIE per innerHTML eingesetzt, sondern ausschliesslich per textContent
// bzw. dataset. Ein Ordnername mit Anfuehrungszeichen reichte frueher aus, um
// aus einem HTML-Attribut auszubrechen.
// ============================================================================
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/** Baut ein Element mit Klasse und Text – ohne HTML-Parsing. */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

// ── Helfer ──────────────────────────────────────────────────────────────────
function fmtBytes(b) {
  if (!b) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(b < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
}
function fmtSpeed(s) { return s ? fmtBytes(s) + '/s' : '–'; }
function fmtEta(e) {
  if (e == null) return '–';
  const h = Math.floor(e / 3600), m = Math.floor((e % 3600) / 60), s = e % 60;
  if (h) return `${h}h ${m}m`;
  return m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`;
}
function fmtDate(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleString('de-DE',
    { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function fmtDur(sec) {
  if (!sec) return '';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function basename(p) { return String(p || '').split('/').pop(); }

const STATUS_TXT = {
  queued: 'Warteschlange', downloading: 'Lädt…', processing: 'Verarbeite…',
  canceling: 'Wird abgebrochen…', done: 'Fertig', error: 'Fehler',
  canceled: 'Abgebrochen',
};
const ACTIVE = ['queued', 'downloading', 'processing', 'canceling'];

async function api(url, opts) {
  const r = await fetch(url, opts);
  let j = null;
  try { j = await r.json(); } catch (_) { /* z.B. 204 */ }
  if (!r.ok) throw new Error((j && j.error) || `HTTP ${r.status}`);
  return j;
}

// ── Tabs ────────────────────────────────────────────────────────────────────
$$('.tab').forEach(t => t.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.tab-panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  $('#tab-' + t.dataset.tab).classList.add('active');
  if (t.dataset.tab === 'lib') loadLibrary();
  if (t.dataset.tab === 'nas') loadShares();
  if (t.dataset.tab === 'settings') { loadCookieStatus(); loadLogins(); loadCacheInfo(); }
}));

$('#logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.href = '/login';
});

// ── Modal-Grundgeruest ──────────────────────────────────────────────────────
function openModal(sel) { $(sel).classList.remove('hidden'); }
function closeModal(sel) {
  const m = $(sel);
  m.classList.add('hidden');
  if (sel === '#player') {                       // Wiedergabe + Abfrage stoppen
    stopPlayPoll();
    $('#player-body').replaceChildren();
  }
}
$$('.modal').forEach(m => m.addEventListener('mousedown', (e) => {
  if (e.target === m) closeModal('#' + m.id);
}));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $$('.modal:not(.hidden)').forEach(m => closeModal('#' + m.id));
});

// ── Formularlogik: Modus steuert die sichtbaren Felder ──────────────────────
function syncFormatUI() {
  const m = $('#format_mode').value;
  $('#audio-wrap').style.display = m === 'audio' ? '' : 'none';
  $('#qual-wrap').style.display = m === 'best' ? '' : 'none';
  $('#container-wrap').style.display = m === 'best' ? '' : 'none';
  $('#container-hint').style.display =
    (m === 'best' && $('#video_container').value !== 'auto') ? '' : 'none';
}
$('#format_mode').addEventListener('change', syncFormatUI);
$('#video_container').addEventListener('change', syncFormatUI);

// ── Download starten ────────────────────────────────────────────────────────
$('#start').addEventListener('click', async () => {
  const urls = $('#urls').value.trim();
  const msg = $('#add-msg');
  msg.textContent = '';
  msg.className = 'hint';
  if (!urls) { msg.textContent = 'Bitte mindestens eine URL einfügen.'; return; }
  const body = {
    urls: [urls],
    format_mode: $('#format_mode').value,
    max_quality: $('#max_quality').value,
    audio_format: $('#audio_format').value,
    video_container: $('#video_container').value,
    playlist_mode: $('#playlist_mode').value,
  };
  if ($('#dest').value) body.dest = $('#dest').value;
  try {
    const r = await api('/api/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#urls').value = '';
    const parts = [];
    if (r.added.length) parts.push(`${r.added.length} Download(s) eingereiht.`);
    if (r.skipped.length) parts.push(`${r.skipped.length} übersprungen (läuft bereits).`);
    const abgelehnt = r.abgelehnt || [];
    if (abgelehnt.length) {
      // Abgelehnte Adressen bleiben im Feld stehen, damit nichts verloren geht.
      $('#urls').value = abgelehnt.map(a => a.url).join('\n');
      parts.push(`${abgelehnt.length} abgelehnt: ${abgelehnt[0].grund}`);
      msg.className = 'hint error';
    }
    msg.textContent = parts.join(' ');
    if (!abgelehnt.length) setTimeout(() => { msg.textContent = ''; }, 5000);
  } catch (e) {
    msg.className = 'hint error';
    msg.textContent = e.message;
  }
});

// ── Aktive Jobs: einmal bauen, danach nur noch Werte aktualisieren ──────────
// Frueher wurde bei JEDEM Fortschritts-Event das komplette innerHTML neu
// gesetzt. Dadurch wanderte der Abbrechen-Button bei jeder ETA-Aenderung, die
// Listener mussten neu gebunden werden und die Liste flackerte.
const jobsEl = {};

function buildJobNode(id) {
  const box = el('div', 'job');
  box.dataset.id = id;

  const head = el('div', 'job-head');
  const title = el('span', 'job-title');
  const status = el('span', 'job-status');
  const actions = el('div', 'job-actions');
  const cancel = el('button', 'ghost small cancel', 'Abbrechen');
  cancel.addEventListener('click', async () => {
    cancel.disabled = true;
    cancel.textContent = 'Bricht ab…';
    try { await api('/api/cancel/' + id, { method: 'POST' }); }
    catch (_) { cancel.disabled = false; cancel.textContent = 'Abbrechen'; }
  });
  actions.append(cancel);
  head.append(title, status, actions);

  const bar = el('div', 'bar');
  const fill = el('div', 'bar-fill');
  bar.append(fill);

  const meta = el('div', 'job-meta');
  const pct = el('span', 'm-pct'), size = el('span', 'm-size');
  const speed = el('span', 'm-speed'), eta = el('span', 'm-eta');
  meta.append(pct, size, speed, eta);

  const err = el('div', 'job-err');

  box.append(head, bar, meta, err);
  box.refs = { title, status, cancel, fill, pct, size, speed, eta, err };
  return box;
}

function renderJob(j) {
  let box = jobsEl[j.id];
  if (!box) {
    if (!ACTIVE.includes(j.status)) return;      // Fertiges gehört in die Bibliothek
    box = buildJobNode(j.id);
    jobsEl[j.id] = box;
    $('#queue').prepend(box);
  }
  const r = box.refs;
  const p = j.progress || 0;

  box.className = 'job ' + j.status;
  r.title.textContent = j.title || j.url;
  r.title.title = j.url || '';

  const pl = j.playlist_index ? ` · ${j.playlist_index}/${j.playlist_count || '?'}` : '';
  r.status.textContent = (STATUS_TXT[j.status] || j.status) + pl;
  r.status.className = 'job-status ' + j.status;

  r.fill.style.width = p + '%';
  box.classList.toggle('indeterminate', j.status === 'processing');

  r.pct.textContent = p.toFixed(1) + ' %';
  r.size.textContent = j.total
    ? `${fmtBytes(j.downloaded)} / ${fmtBytes(j.total)}`
    : fmtBytes(j.downloaded);
  r.speed.textContent = j.status === 'downloading' ? fmtSpeed(j.speed) : '–';
  r.eta.textContent = j.eta != null ? '≈ ' + fmtEta(j.eta) : '–';

  r.err.textContent = j.error || '';
  r.err.style.display = j.error ? '' : 'none';

  const busy = ACTIVE.includes(j.status);
  r.cancel.style.display = busy ? '' : 'none';
  r.cancel.disabled = j.status === 'canceling';
  r.cancel.textContent = j.status === 'canceling' ? 'Bricht ab…' : 'Abbrechen';

  if (!busy) {
    // Endzustand kurz stehen lassen, dann in die Bibliothek ueberfuehren.
    setTimeout(() => dropJob(j.id), 3500);
    refreshCounts();
  }
  checkEmpty();
}

function dropJob(id) {
  const box = jobsEl[id];
  if (box) { box.remove(); delete jobsEl[id]; }
  checkEmpty();
}

function checkEmpty() {
  const n = Object.keys(jobsEl).length;
  $('#queue-empty').style.display = n ? 'none' : '';
  $('#cancel-all').style.display = n ? '' : 'none';
}

$('#cancel-all').addEventListener('click', async () => {
  await api('/api/cancel-all', { method: 'POST' });
});

// ── WebSocket Live-Fortschritt ──────────────────────────────────────────────
let wsRetry = 1000;
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { wsRetry = 1000; };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'snapshot') {
      Object.keys(jobsEl).forEach(dropJob);
      (msg.jobs || []).forEach(renderJob);
    } else if (msg.type === 'job') {
      renderJob(msg.job);
    } else if (msg.type === 'removed') {
      dropJob(msg.id);
    } else if (msg.type === 'history') {
      refreshCounts();
      if ($('#tab-lib').classList.contains('active')) loadLibrary();
    }
  };
  ws.onclose = () => {
    // Exponentiell zurueckziehen statt im Sekundentakt gegen einen toten
    // Server zu laufen (z.B. waehrend eines Container-Neustarts).
    setTimeout(connectWS, wsRetry);
    wsRetry = Math.min(wsRetry * 1.7, 20000);
  };
}

// ── Bibliothek ──────────────────────────────────────────────────────────────
let libStatus = 'all';
let libRubrik = '';          // '' = alle Herkünfte
let libSearch = '';
let libTimer = null;

$$('#lib-filters .chip').forEach(c => c.addEventListener('click', () => {
  $$('#lib-filters .chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  libStatus = c.dataset.status;
  loadLibrary();
}));

$('#lib-search').addEventListener('input', () => {
  clearTimeout(libTimer);
  libTimer = setTimeout(() => { libSearch = $('#lib-search').value.trim(); loadLibrary(); }, 250);
});

async function refreshCounts() {
  try {
    const { counts } = await api('/api/history?limit=1');
    $$('#lib-filters .chip').forEach(c => {
      c.querySelector('b').textContent = counts[c.dataset.status] || 0;
    });
    const bad = (counts.error || 0) + (counts.canceled || 0);
    const badge = $('#lib-badge');
    badge.textContent = bad || '';
    badge.style.display = bad ? '' : 'none';
  } catch (_) { /* nicht angemeldet o.ä. */ }
}

function libActions(h) {
  const row = el('div', 'job-actions lib-actions');

  if (h.status === 'done' && h.exists) {
    const play = el('button', 'ghost small', h.playable ? '▶ Ansehen' : '▶ Öffnen');
    play.addEventListener('click', () => openPlayer(h));
    row.append(play);

    const dl = el('button', 'ghost small', '⬇ Laden');
    dl.addEventListener('click', () => {
      location.href = `/api/file/${encodeURIComponent(h.id)}?idx=0&dl=1`;
    });
    row.append(dl);
  }

  if (h.status === 'error' || h.status === 'canceled') {
    const again = el('button', 'primary small',
      h.status === 'canceled' ? '▶ Fortsetzen' : '↻ Erneut versuchen');
    again.addEventListener('click', async () => {
      again.disabled = true;
      try {
        await api(`/api/history/${encodeURIComponent(h.id)}/retry`, { method: 'POST' });
        $$('.tab').find(t => t.dataset.tab === 'dl').click();
      } catch (e) { alert(e.message); again.disabled = false; }
    });
    row.append(again);
  }

  if (h.log) {
    const log = el('button', 'ghost small', '📄 Protokoll');
    log.addEventListener('click', () => openLog(h));
    row.append(log);
  }

  if (h.dest) {
    const copy = el('button', 'ghost small', '📋 Pfad');
    copy.title = 'Zielpfad in die Zwischenablage kopieren';
    copy.addEventListener('click', async () => {
      const p = h.files[0] || h.dest;
      try {
        await navigator.clipboard.writeText(p);
        copy.textContent = '✓ Kopiert';
      } catch (_) {
        // Ohne HTTPS gibt es keine Zwischenablage – dann wenigstens anzeigen.
        prompt('Pfad:', p);
      }
      setTimeout(() => { copy.textContent = '📋 Pfad'; }, 1500);
    });
    row.append(copy);
  }

  const del = el('button', 'ghost small danger-ghost', '🗑');
  del.title = 'Aus der Bibliothek entfernen';
  del.addEventListener('click', () => askDelete(h));
  row.append(del);

  return row;
}

function libEntry(h) {
  const box = el('div', 'job lib ' + h.status);

  const head = el('div', 'job-head');
  const title = el('span', 'job-title', h.title || h.url);
  title.title = h.url || '';
  const status = el('span', 'job-status ' + h.status,
    STATUS_TXT[h.status] || h.status);
  head.append(title, status, libActions(h));

  const meta = el('div', 'job-meta lib-meta');
  meta.append(
    el('span', null, h.filename ? basename(h.filename) : '–'),
    el('span', null, fmtBytes(h.filesize)),
    el('span', null, fmtDur(h.duration)),
    el('span', null, fmtDate(h.finished)));

  const path = el('div', 'browser-path', h.dest || '');
  box.append(head, meta, path);

  if (h.status === 'done' && !h.exists) {
    box.append(el('div', 'job-err', 'Datei nicht mehr am Zielort vorhanden.'));
  }
  if (h.error) box.append(el('div', 'job-err', h.error));
  return box;
}

function renderRubriken(rubriken) {
  const leiste = $('#lib-rubriken');
  const belegt = rubriken.filter(r => r.count > 0);
  if (!belegt.length) { leiste.replaceChildren(); return; }
  const knoepfe = [['', 'Alle Quellen', belegt.reduce((n, r) => n + r.count, 0)]]
    .concat(belegt.map(r => [r.key, r.label, r.count]));
  leiste.replaceChildren(...knoepfe.map(([key, label, count]) => {
    const b = el('button', 'chip' + (libRubrik === key ? ' active' : ''), label + ' ');
    b.append(el('b', null, String(count)));
    b.addEventListener('click', () => { libRubrik = key; loadLibrary(); });
    return b;
  }));
}

async function loadLibrary() {
  const p = new URLSearchParams({ status: libStatus, q: libSearch });
  if (libRubrik) p.set('rubrik', libRubrik);
  let data;
  try { data = await api('/api/history?' + p.toString()); }
  catch (_) { return; }

  const list = $('#library');
  list.replaceChildren(...data.history.map(libEntry));
  $('#lib-empty').style.display = data.history.length ? 'none' : '';

  $$('#lib-filters .chip').forEach(c => {
    c.querySelector('b').textContent = data.counts[c.dataset.status] || 0;
  });
  renderRubriken(data.rubriken || []);
  const bad = (data.counts.error || 0) + (data.counts.canceled || 0);
  const badge = $('#lib-badge');
  badge.textContent = bad || '';
  badge.style.display = bad ? '' : 'none';

  // Sammel-Wiederholung nur da anbieten, wo sie sinnvoll ist.
  const retryable = data.history.filter(h => h.status === 'error' || h.status === 'canceled');
  const btn = $('#lib-retry-all');
  btn.style.display = (libStatus !== 'done' && retryable.length > 1) ? '' : 'none';
  btn.textContent = `↻ Alle ${retryable.length} wiederholen`;
  btn.onclick = async () => {
    btn.disabled = true;
    for (const h of retryable) {
      try { await api(`/api/history/${encodeURIComponent(h.id)}/retry`, { method: 'POST' }); }
      catch (_) { /* einzelne Fehlschlaege nicht die Schleife killen lassen */ }
    }
    btn.disabled = false;
    $$('.tab').find(t => t.dataset.tab === 'dl').click();
  };

  $('#lib-clear').textContent = libStatus === 'all'
    ? '🗑 Bibliothek leeren' : '🗑 Rubrik leeren';
}

$('#lib-clear').addEventListener('click', async () => {
  const label = $$('#lib-filters .chip').find(c => c.dataset.status === libStatus)
    .textContent.replace(/\d+$/, '').trim();
  if (!confirm(`Rubrik „${label}" wirklich leeren?\n\nEs werden nur Einträge entfernt – Dateien bleiben erhalten.`)) return;
  await api('/api/clear-history?status=' + encodeURIComponent(libStatus), { method: 'POST' });
  loadLibrary();
});

// ── Loeschen mit klarer Wahl: Eintrag oder Eintrag + Datei ──────────────────
let pendingDelete = null;
function askDelete(h) {
  pendingDelete = h;
  $('#confirm-title').textContent = 'Eintrag entfernen?';
  $('#confirm-text').textContent = h.title || h.url;
  $('#confirm-file').style.display = h.exists ? '' : 'none';
  openModal('#confirm');
}
async function doDelete(withFile) {
  if (!pendingDelete) return;
  const id = pendingDelete.id;
  pendingDelete = null;
  closeModal('#confirm');
  await api(`/api/history/${encodeURIComponent(id)}?file=${withFile ? 1 : 0}`,
    { method: 'DELETE' });
  loadLibrary();
}
$('#confirm-entry').addEventListener('click', () => doDelete(false));
$('#confirm-file').addEventListener('click', () => doDelete(true));
$('#confirm-cancel').addEventListener('click', () => { pendingDelete = null; closeModal('#confirm'); });

// ── Player ──────────────────────────────────────────────────────────────────
const VIDEO_EXT = ['mp4', 'm4v', 'webm', 'ogv', 'mov'];
const AUDIO_EXT = ['mp3', 'm4a', 'opus', 'ogg', 'oga', 'wav', 'flac', 'aac'];

let playPoll = null;                 // laufende Fortschritts-Abfrage

function stopPlayPoll() {
  if (playPoll) { clearTimeout(playPoll); playPoll = null; }
}

function playerMedia(h, idx, quelle, tag) {
  const p = document.createElement(tag);
  p.controls = true;
  p.autoplay = true;
  p.preload = 'metadata';
  p.src = quelle;
  p.className = 'player-media';
  return p;
}

async function mountPlayer(h, idx, force) {
  stopPlayPoll();
  const body = $('#player-body');
  body.replaceChildren();
  const name = basename(h.files[idx] || '');
  const ext = name.split('.').pop().toLowerCase();
  const datei = `/api/file/${encodeURIComponent(h.id)}?idx=${idx}`;
  const strom = `/api/play/${encodeURIComponent(h.id)}/stream?idx=${idx}`;

  $('#player-path').textContent = h.files[idx] || '';
  $('#player-dl').onclick = () => { location.href = datei + '&dl=1'; };

  if (force) {                       // „Trotzdem versuchen“ – roh ausliefern
    body.append(playerMedia(h, idx, datei, AUDIO_EXT.includes(ext) ? 'audio' : 'video'));
    body.append(el('p', 'hint',
      'Falls nichts zu sehen ist, kann dein Browser diesen Container nicht abspielen.'));
    return;
  }

  body.append(el('p', 'muted', 'Prüfe, wie sich die Datei abspielen lässt …'));
  let info;
  try { info = await api(`/api/play/${encodeURIComponent(h.id)}?idx=${idx}`); }
  catch (e) { body.replaceChildren(el('p', 'hint error', e.message)); return; }

  if (info.modus === 'direkt') {
    body.replaceChildren(playerMedia(h, idx, strom,
      AUDIO_EXT.includes(ext) ? 'audio' : 'video'));
    return;
  }

  if (info.modus === 'nein') {
    body.replaceChildren(playerHinweis(h, idx, ext, info.grund, datei));
    return;
  }

  // Umpacken: anstossen und den Fortschritt zeigen.
  const box = el('div', 'notice');
  box.append(el('div', 'notice-icon', '🎞'));
  box.append(el('p', null, `.${ext} kann dein Browser nicht direkt abspielen – `
    + `die Datei wird gerade verlustfrei nach ${(info.ziel || '.mp4').replace('.', '').toUpperCase()} `
    + 'umgepackt. Bild und Ton bleiben unverändert.'));
  const bar = el('div', 'bar');
  const fill = el('div', 'bar-fill');
  bar.append(fill);
  const txt = el('p', 'muted', 'Startet …');
  box.append(bar, txt);
  const dl = el('button', 'ghost small', '⬇ Stattdessen herunterladen');
  dl.addEventListener('click', () => { location.href = datei + '&dl=1'; });
  box.append(dl);
  body.replaceChildren(box);

  try { await api(`/api/play/${encodeURIComponent(h.id)}?idx=${idx}`, { method: 'POST' }); }
  catch (e) { txt.textContent = e.message; return; }

  const frage = async () => {
    let st;
    try { st = await api(`/api/play/${encodeURIComponent(h.id)}?idx=${idx}`); }
    catch (e) { txt.textContent = e.message; return; }
    if (st.fehler) {
      body.replaceChildren(playerHinweis(h, idx, ext, st.fehler, datei));
      return;
    }
    const pct = Math.max(0, Math.min(100, st.fortschritt || 0));
    fill.style.width = pct + '%';
    txt.textContent = st.bereit ? 'Fertig.' : `Wird vorbereitet … ${pct}%`;
    if (st.bereit) {
      body.replaceChildren(playerMedia(h, idx, strom, 'video'));
      return;
    }
    playPoll = setTimeout(frage, 1500);
  };
  frage();
}

function playerHinweis(h, idx, ext, grund, datei) {
  const note = el('div', 'notice');
  note.append(el('div', 'notice-icon', '🎬'));
  note.append(el('p', null, grund
    || `Dateien im Format .${ext} kann ein Browser in der Regel nicht abspielen.`));
  note.append(el('p', 'muted',
    'Lade die Datei herunter oder öffne sie in VLC / Infuse / einem Mediaplayer.'));
  const row = el('div', 'row');
  const dl = el('button', 'primary', '⬇ Herunterladen');
  dl.addEventListener('click', () => { location.href = datei + '&dl=1'; });
  const anyway = el('button', 'ghost', 'Trotzdem versuchen');
  anyway.addEventListener('click', () => mountPlayer(h, idx, true));
  row.append(dl, anyway);
  note.append(row);
  return note;
}

function openPlayer(h) {
  $('#player-title').textContent = h.title || h.url;
  const files = $('#player-files');
  files.replaceChildren();
  // Playlists erzeugen mehrere Dateien – dann eine Auswahl anbieten.
  if (h.files.length > 1) {
    h.files.forEach((f, i) => {
      const c = el('button', 'chip' + (i === 0 ? ' active' : ''), basename(f));
      c.addEventListener('click', () => {
        Array.from(files.children).forEach(x => x.classList.remove('active'));
        c.classList.add('active');
        mountPlayer(h, i, false);
      });
      files.append(c);
    });
  }
  mountPlayer(h, 0, false);
  openModal('#player');
}
$('#player-close').addEventListener('click', () => closeModal('#player'));

// ── Protokoll ───────────────────────────────────────────────────────────────
function openLog(h) {
  $('#log-body').textContent = h.log || '(kein Protokoll aufgezeichnet)';
  $('#log-url').textContent = h.url || '';
  $('#log-copy').onclick = async () => {
    try {
      await navigator.clipboard.writeText(h.log || '');
      $('#log-copy').textContent = '✓ Kopiert';
      setTimeout(() => { $('#log-copy').textContent = '📋 Kopieren'; }, 1500);
    } catch (_) { /* ohne HTTPS nicht verfuegbar */ }
  };
  openModal('#logbox');
}
$('#log-close').addEventListener('click', () => closeModal('#logbox'));

// ── Einstellungen ───────────────────────────────────────────────────────────
const SETTING_FIELDS = [
  ['default_dest', 'Standard-Zielordner', 'text', null, 'Wird genutzt, wenn beim Download kein eigenes Ziel gewählt wurde.'],
  ['output_template', 'Dateiname-Vorlage', 'text', null, 'yt-dlp-Vorlage, z.B. %(title)s [%(id)s].%(ext)s'],
  ['format_mode', 'Standard-Modus', 'select', ['best', 'audio', 'custom']],
  ['video_container', 'Standard-Zielformat', 'select', ['auto', 'mkv', 'mp4', 'webm', 'mov'], 'Verlustfreies Umpacken – es wird nicht neu kodiert.'],
  ['audio_format', 'Audioformat', 'select', ['mp3', 'm4a', 'opus', 'flac', 'wav']],
  ['audio_quality', 'Audio-Qualität (0=beste)', 'text'],
  ['max_quality', 'Max. Höhe (0=unbegrenzt)', 'text'],
  ['custom_format', 'Eigenes yt-dlp-Format', 'text', null, 'Formatausdruck wie bv*[height<=1080]+ba – NICHT der Container (dafür „Zielformat“).'],
  ['playlist_mode', 'Playlist-Vorgabe', 'select', ['playlist', 'single'], 'Standard für neue Downloads: ganze Liste oder nur der angeklickte Titel (Video wie Lied). Im Download-Tab pro Auftrag umstellbar.'],
  ['playlist_items', 'Playlist-Items (z.B. 1-10)', 'text', null, 'Nur wirksam, wenn die ganze Liste geladen wird.'],
  ['sub_langs', 'Untertitel-Sprachen', 'text'],
  ['rate_limit', 'Tempolimit (z.B. 5M)', 'text'],
  ['concurrent_downloads', 'Parallele Downloads', 'number'],
  ['concurrent_fragments', 'Fragmente/Job', 'number'],
  ['play_cache_gb', 'Player-Zwischenspeicher (GB)', 'number', null, 'Obergrenze für umgepackte Fassungen zum Ansehen im Browser. Älteste fliegen zuerst raus; 0 = unbegrenzt.'],
  ['job_retention_min', 'Fertige Jobs ausblenden nach (Min.)', 'number', null, 'Betrifft nur die Live-Liste – die Bibliothek behält alles.'],
  ['job_timeout_h', 'Zeitlimit je Auftrag (Std.)', 'number', null, 'Läuft ein Download länger, wird er abgebrochen und als „Fehlgeschlagen“ verbucht; der Teil-Download bleibt für „Fortsetzen“ erhalten. Schützt davor, dass ein eingeschlafener Auftrag einen Download-Platz dauerhaft belegt. 0 = kein Limit.'],
  ['browser_tarnung', 'Browser-Tarnung', 'select', ['auto', 'immer', 'aus'], 'Manche Seiten (z.B. PornHub, Cloudflare-geschützte Portale) sperren Programme, die sich nicht wie ein Browser melden – Fehler „HTTP 403 Forbidden“. „auto“: yt-dlp tarnt sich, wo die Seite es verlangt, und wiederholt jeden mit 403 abgewiesenen Auftrag einmal als Chrome. „immer“: jede Anfrage als Chrome – hilft bei unbekannten Sperren, kann aber Tempo kosten. „aus“: keine Wiederholung.'],
  ['embed_metadata', 'Metadaten einbetten', 'bool'],
  ['embed_thumbnail', 'Thumbnail einbetten', 'bool', null, 'Legt das Vorschaubild als Cover in die Datei – bei MP3, M4A, OPUS, FLAC, MKV, MP4 und MOV. WAV und WEBM können keins tragen, dort wird es einfach weggelassen.'],
  ['write_subs', 'Untertitel laden', 'bool'],
  ['embed_subs', 'Untertitel einbetten', 'bool'],
  ['sponsorblock', 'SponsorBlock (Werbung raus)', 'bool'],
  ['restrict_filenames', 'ASCII-Dateinamen', 'bool'],
  ['organize_subdirs', 'Unterordner automatisch anlegen', 'bool', null, 'Sortiert im Zielordner nach Quelle, Art, Jahr und Monat – z.B. Youtube/video/2026/09. Aus: alles landet flach im Zielordner.'],
  ['local_temp', 'Lokal zwischenspeichern', 'bool', null, 'Empfohlen bei NAS-/SMB-Zielen: Fragmente und ffmpeg-Merge laufen lokal, nur das Ergebnis geht übers Netz.'],
  ['use_cookies', 'Cookie-Datei verwenden', 'bool', null, 'Nur wirksam, wenn unten eine Cookie-Datei hochgeladen ist.'],
];

async function loadSettings() {
  let s;
  try { s = await api('/api/settings'); } catch (_) { return; }
  const grid = $('#settings-grid');
  grid.replaceChildren();

  for (const [k, label, type, opts, hint] of SETTING_FIELDS) {
    let wrap;
    if (type === 'bool') {
      wrap = el('label', 'chk');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.k = k;
      cb.checked = !!s[k];
      wrap.append(cb, document.createTextNode(' ' + label));
    } else {
      wrap = el('label', null, label);
      let field;
      if (type === 'select') {
        field = document.createElement('select');
        for (const o of opts) {
          const opt = document.createElement('option');
          opt.value = o;
          opt.textContent = o;
          opt.selected = String(s[k]) === String(o);
          field.append(opt);
        }
      } else {
        field = document.createElement('input');
        field.type = type === 'number' ? 'number' : 'text';
        field.value = s[k] == null ? '' : String(s[k]);
      }
      field.dataset.k = k;
      wrap.append(field);
    }
    if (hint) wrap.append(el('span', 'hint', hint));
    grid.append(wrap);
  }

  $('#version-line').textContent = 'FAH Media Downloader – Version ' + (s._version || '?');

  // Startwerte des Download-Formulars aus den Einstellungen uebernehmen.
  $('#format_mode').value = s.format_mode;
  $('#video_container').value = s.video_container || 'auto';
  $('#audio_format').value = s.audio_format;
  $('#max_quality').value = String(s.max_quality || '0');
  $('#playlist_mode').value = s.playlist_mode || 'playlist';
  syncFormatUI();
}

$('#save-settings').addEventListener('click', async () => {
  const body = {};
  $$('#settings-grid [data-k]').forEach(f => {
    body[f.dataset.k] = f.type === 'checkbox' ? f.checked : f.value;
  });
  const out = $('#settings-saved');
  try {
    await api('/api/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    out.className = 'ok';
    out.textContent = '✓ Gespeichert';
  } catch (e) {
    out.className = 'error';
    out.textContent = e.message;
  }
  setTimeout(() => { out.textContent = ''; }, 3000);
  loadSettings();
});

// ── Cookies ─────────────────────────────────────────────────────────────────
async function loadCookieStatus() {
  try {
    const c = await api('/api/cookies');
    $('#cookie-status').textContent = c.present
      ? `✓ Datei vorhanden (${fmtBytes(c.size)}, ${fmtDate(c.mtime)})`
      : 'Keine Cookie-Datei hinterlegt.';
  } catch (_) { /* ignorieren */ }
}
$('#cookie-upload').addEventListener('click', async () => {
  const f = $('#cookie-file').files[0];
  const out = $('#cookie-status');
  if (!f) { out.textContent = 'Bitte zuerst eine Datei wählen.'; return; }
  const fd = new FormData();
  fd.append('file', f);
  try {
    await api('/api/cookies', { method: 'POST', body: fd });
    $('#cookie-file').value = '';
    loadCookieStatus();
  } catch (e) { out.textContent = e.message; }
});
$('#cookie-delete').addEventListener('click', async () => {
  await api('/api/cookies', { method: 'DELETE' });
  loadCookieStatus();
});

// ── NAS-Freigaben ───────────────────────────────────────────────────────────
// ── Zugangsdaten je Seite ───────────────────────────────────────────────────
async function loadLogins() {
  let data;
  try { data = await api('/api/logins'); } catch (_) { return; }
  const tbl = $('#login-list');
  if (!data.logins.length) {
    tbl.replaceChildren();
    return;
  }
  const kopf = el('tr');
  ['Domain', 'Benutzer', 'Passwort', ''].forEach(t => kopf.append(el('th', null, t)));
  const zeilen = data.logins.map(l => {
    const tr = el('tr');
    tr.append(el('td', null, l.domain));
    tr.append(el('td', null, l.username || '—'));
    tr.append(el('td', null, l.has_password
      ? (l.has_videopassword ? '••••• + Video' : '•••••') : '—'));
    const td = el('td');
    const weg = el('button', 'ghost small', '🗑');
    weg.title = 'Zugangsdaten entfernen';
    weg.addEventListener('click', async () => {
      if (!confirm(`Zugangsdaten für ${l.domain} entfernen?`)) return;
      await api('/api/logins/' + encodeURIComponent(l.domain), { method: 'DELETE' });
      loadLogins();
    });
    td.append(weg);
    tr.append(td);
    return tr;
  });
  tbl.replaceChildren(kopf, ...zeilen);
}

$('#login-save').addEventListener('click', async () => {
  const msg = $('#login-msg');
  msg.className = 'hint';
  const koerper = {
    domain: $('#login-domain').value.trim(),
    username: $('#login-user').value.trim(),
    password: $('#login-pass').value,
    videopassword: $('#login-vpass').value,
  };
  if (!koerper.domain) { msg.textContent = 'Bitte eine Domain angeben.'; return; }
  try {
    const r = await api('/api/logins', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(koerper),
    });
    msg.className = 'hint ok';
    msg.textContent = `Gespeichert für ${r.login.domain}.`;
    ['#login-domain', '#login-user', '#login-pass', '#login-vpass']
      .forEach(s => { $(s).value = ''; });
    loadLogins();
  } catch (e) {
    msg.className = 'hint error';
    msg.textContent = e.message;
  }
});

// ── Altbestand einsortieren ─────────────────────────────────────────────────
function mb(bytes) {
  const gb = bytes / (1024 ** 3);
  return gb >= 1 ? gb.toFixed(2) + ' GB' : (bytes / (1024 ** 2)).toFixed(1) + ' MB';
}

$('#org-preview').addEventListener('click', async () => {
  const msg = $('#org-msg');
  const liste = $('#org-list');
  msg.textContent = 'Prüfe …';
  liste.replaceChildren();
  let plan;
  try { plan = await api('/api/organize'); }
  catch (e) { msg.textContent = e.message; return; }
  if (!plan.moves.length) {
    msg.textContent = plan.uebersprungen
      ? `Nichts zu tun – ${plan.uebersprungen} Datei(en) liegen bereits richtig.`
      : 'Nichts zu tun.';
    $('#org-run').style.display = 'none';
    return;
  }
  msg.textContent = `${plan.moves.length} Datei(en), ${mb(plan.gesamt_bytes)}`
    + (plan.uebersprungen ? ` · ${plan.uebersprungen} liegen schon richtig` : '')
    + (plan.fehlend ? ` · ${plan.fehlend} nicht mehr auffindbar` : '');
  liste.replaceChildren(...plan.moves.slice(0, 200).map(m => {
    const z = el('div', 'org-row' + (m.kollision ? ' warn' : ''));
    z.append(el('span', 'org-name', m.titel));
    z.append(el('span', 'muted', m.kollision
      ? '→ am Ziel liegt schon eine gleichnamige Datei, wird übersprungen'
      : '→ ' + m.nach));
    return z;
  }));
  if (plan.moves.length > 200) {
    liste.append(el('div', 'muted', `… und ${plan.moves.length - 200} weitere`));
  }
  $('#org-run').style.display = '';
});

$('#org-run').addEventListener('click', async () => {
  if (!confirm('Dateien jetzt in die Unterordner verschieben?\n'
    + 'Der Verlauf wird mitgezogen, damit Ansehen und Löschen weiter funktionieren.')) return;
  const msg = $('#org-msg');
  msg.textContent = 'Verschiebe …';
  let r;
  try { r = await api('/api/organize', { method: 'POST' }); }
  catch (e) { msg.textContent = e.message; return; }
  msg.textContent = `${r.verschoben} verschoben`
    + (r.uebersprungen ? `, ${r.uebersprungen} übersprungen` : '')
    + (r.fehler.length ? `, ${r.fehler.length} Fehler` : '');
  $('#org-list').replaceChildren(...r.fehler.map(f => el('div', 'org-row warn', f)));
  $('#org-run').style.display = 'none';
  loadLibrary();
});

async function loadCacheInfo() {
  try {
    const c = await api('/api/playcache');
    $('#cache-msg').textContent = c.bytes
      ? `belegt: ${mb(c.bytes)}` : 'leer';
  } catch (_) { /* nicht schlimm */ }
}

$('#cache-clear').addEventListener('click', async () => {
  const r = await api('/api/playcache', { method: 'DELETE' });
  $('#cache-msg').textContent = `${mb(r.freigegeben)} freigegeben`;
});

async function loadShares() {
  let data;
  try { data = await api('/api/shares'); } catch (_) { return; }
  const box = $('#shares');
  box.replaceChildren();
  if (!data.shares.length) {
    box.append(el('div', 'muted', 'Noch keine Netzlaufwerke.'));
    return;
  }
  for (const s of data.shares) {
    const card = el('div', 'job ' + (s.mounted ? 'done' : 'error'));
    const head = el('div', 'job-head');
    head.append(
      el('span', 'job-title', `${s.name} → //${s.server}/${s.share}`),
      el('span', 'job-status ' + (s.mounted ? 'done' : 'error'),
        s.mounted ? '🟢 verbunden' : '🔴 getrennt'));
    const actions = el('div', 'job-actions');
    const del = el('button', 'ghost small danger-ghost', 'Entfernen');
    del.addEventListener('click', async () => {
      if (!confirm(`Netzlaufwerk „${s.name}" aushängen und entfernen?`)) return;
      await api('/api/shares/' + encodeURIComponent(s.name), { method: 'DELETE' });
      loadShares();
    });
    actions.append(del);
    head.append(actions);

    const meta = el('div', 'job-meta lib-meta');
    meta.append(el('span', null, s.mountpoint),
      el('span', null, 'Benutzer: ' + (s.username || 'Gast')));
    card.append(head, meta);
    box.append(card);
  }
}

$('#add-share').addEventListener('click', async () => {
  const msg = $('#share-msg');
  msg.textContent = '';
  try {
    await api('/api/shares', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: $('#s-name').value, server: $('#s-server').value,
        share: $('#s-share').value, domain: $('#s-domain').value,
        username: $('#s-user').value, password: $('#s-pass').value,
      }),
    });
    $('#s-pass').value = '';
    loadShares();
  } catch (e) { msg.textContent = e.message; }
});

// ── Ordner-Browser ──────────────────────────────────────────────────────────
let browserPath = '';
$('#pick-dest').addEventListener('click', () => openBrowser());
$('#browser-close').addEventListener('click', () => closeModal('#browser'));
$('#browser-select').addEventListener('click', () => {
  if (browserPath) $('#dest').value = browserPath;
  closeModal('#browser');
});
$('#browser-mkdir').addEventListener('click', async () => {
  const name = prompt('Name des neuen Ordners:');
  if (!name || !browserPath) return;
  try {
    await api('/api/mkdir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: browserPath, name }),
    });
    browse(browserPath);
  } catch (e) { alert(e.message); }
});

async function openBrowser() {
  openModal('#browser');
  const { roots, hinweis } = await api('/api/roots');
  const box = $('#browser-roots');
  box.replaceChildren();
  $('#browser-hint').textContent = hinweis || '';
  for (const r of roots) {
    const c = el('button', 'chip', r.label);
    c.addEventListener('click', () => browse(r.path));
    box.append(c);
  }
  if (roots.length) browse($('#dest').value || roots[0].path);
}

async function browse(path) {
  const list = $('#browser-list');
  let j;
  try {
    j = await api('/api/browse?path=' + encodeURIComponent(path));
  } catch (e) {
    list.replaceChildren(el('div', 'err-txt', e.message));
    return;
  }
  browserPath = j.path;
  $('#browser-current').textContent = j.path;
  list.replaceChildren();
  if (j.parent) {
    const up = el('div', 'browser-item up', '⬆  ..');
    up.addEventListener('click', () => browse(j.parent));
    list.append(up);
  }
  for (const d of j.dirs) {
    const it = el('div', 'browser-item', '📁  ' + d.name);
    it.addEventListener('click', () => browse(d.path));
    list.append(it);
  }
  if (!j.dirs.length && !j.parent) list.append(el('div', 'muted', 'Keine Unterordner.'));
}

// ── Init ────────────────────────────────────────────────────────────────────
loadSettings();
refreshCounts();
connectWS();
