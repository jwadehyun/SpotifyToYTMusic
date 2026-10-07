const $ = (id) => document.getElementById(id);
const CONCURRENCY = 4;

const STATUS = {
  pending: 'Searching…',
  exact: 'Exact song',
  likely: 'Close match: check',
  video: 'Video version',
  none: 'Not found',
  manual: 'Your pick',
  error: 'Search failed',
};

let playlist = null;
let rows = [];
let filter = 'all';
let runId = 0;

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const fmt = (sec) => (sec ? `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}` : '');

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

/* ---------- Loading & matching ---------- */

$('loadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.submitter;
  $('loadError').hidden = true;
  btn.disabled = true;
  btn.textContent = 'Loading…';
  try {
    playlist = await api(`/api/spotify?url=${encodeURIComponent($('linkInput').value)}`);
    showPlaylist();
  } catch (err) {
    $('loadError').textContent = err.message;
    $('loadError').hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find songs';
  }
});

function showPlaylist() {
  $('playlist').hidden = false;
  $('createBar').hidden = false;
  $('plCover').src = playlist.image || '';
  $('plKind').textContent = playlist.kind === 'album' ? 'Spotify album' : 'Spotify playlist';
  $('plName').textContent = playlist.name;
  $('plMeta').textContent = [playlist.owner, `${playlist.tracks.length} songs`].filter(Boolean).join(' · ');
  $('truncWarn').hidden = !playlist.maybeTruncated;
  $('cName').value = playlist.name;
  $('cDesc').value = `Copied from Spotify: https://open.spotify.com/${playlist.kind}/${playlist.id}`;
  $('createResult').hidden = true;

  rows = playlist.tracks.map((track) => ({ track, status: 'pending', candidates: [], choice: null }));
  filter = 'all';
  renderAll();
  matchAll();
}

async function matchAll() {
  const myRun = ++runId;
  let next = 0;
  let done = 0;
  const worker = async () => {
    while (next < rows.length) {
      const i = next++;
      await matchRow(i, myRun);
      if (myRun !== runId) return; // a newer playlist was loaded
      done++;
      setProgress(done / rows.length);
    }
  };
  setProgress(0);
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
}

async function matchRow(i, myRun) {
  const row = rows[i];
  try {
    const res = await api('/api/match', { method: 'POST', body: JSON.stringify(row.track) });
    if (myRun !== runId) return;
    Object.assign(row, res, { autoStatus: res.status, autoChoice: res.choice });
  } catch (err) {
    if (myRun !== runId) return;
    row.status = 'error';
    row.error = err.message;
  }
  updateRow(i);
}

function setProgress(f) {
  $('progressBar').style.width = `${f * 100}%`;
  $('progressWrap').classList.toggle('done', f >= 1);
}

/* ---------- Rendering ---------- */

const FILTERS = [
  ['all', 'All', () => true],
  ['review', 'Needs review', (r) => ['likely', 'none', 'error'].includes(r.status)],
  ['video', 'Video versions', (r) => r.status === 'video'],
  ['exact', 'Exact', (r) => r.status === 'exact'],
  ['skipped', 'Skipped', (r) => r.status !== 'pending' && !r.choice],
];

function renderFilters() {
  $('filters').innerHTML = FILTERS.map(([key, label, fn]) => {
    const n = rows.filter(fn).length;
    return `<button data-f="${key}" class="${filter === key ? 'active' : ''}">${label} <span class="muted">${n}</span></button>`;
  }).join('');
}

$('filters').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  filter = b.dataset.f;
  renderAll();
});

function renderAll() {
  renderFilters();
  const fn = FILTERS.find(([k]) => k === filter)[2];
  $('rows').replaceChildren(...rows.map((r, i) => (fn(r) ? rowEl(i) : null)).filter(Boolean));
  updateSelection();
}

function updateRow(i) {
  const old = document.querySelector(`.row[data-i="${i}"]`);
  if (old) old.replaceWith(rowEl(i));
  renderFilters();
  updateSelection();
}

function optionLabel(c) {
  const icon = c.type === 'video' ? '▶' : '♪';
  return `${icon} ${c.title} · ${c.artists.join(', ')}${c.duration ? ` · ${fmt(c.duration)}` : ''}${c.type === 'video' ? ' (video)' : ''}`;
}

function rowEl(i) {
  const r = rows[i];
  const t = r.track;
  const chosen = r.candidates.find((c) => c.videoId === r.choice);
  const el = document.createElement('div');
  el.className = `row${r.status !== 'pending' && !r.choice ? ' skipped' : ''}`;
  el.dataset.i = i;

  const badge = r.status !== 'pending' && !r.choice && r.status !== 'none' && r.status !== 'error'
    ? `<span class="badge skip">Skipped</span>`
    : `<span class="badge ${r.status}" title="${esc(r.error || '')}">${STATUS[r.status]}</span>`;

  let matchHtml;
  if (r.status === 'pending') {
    matchHtml = `<div class="thumb-empty"></div><div class="match-info"><div class="match-line">${badge}</div></div>`;
  } else {
    const options = r.candidates
      .map((c) => `<option value="${esc(c.videoId)}" ${c.videoId === r.choice ? 'selected' : ''}>${esc(optionLabel(c))}</option>`)
      .join('');
    matchHtml = `
      ${chosen?.thumbnail ? `<img src="${esc(chosen.thumbnail)}" alt="" loading="lazy" />` : '<div class="thumb-empty"></div>'}
      <div class="match-info">
        ${chosen
          ? `<div class="song-title"><a href="https://music.youtube.com/watch?v=${esc(chosen.videoId)}" target="_blank" rel="noreferrer">${esc(chosen.title)}</a></div>
             <div class="song-sub">${esc([chosen.artists.join(', '), chosen.album].filter(Boolean).join(' · '))}${chosen.duration ? ` · ${fmt(chosen.duration)}` : ''}</div>`
          : `<div class="song-sub">${r.status === 'error' ? esc(r.error) : 'No song will be added.'}</div>`}
        <div class="match-line">
          ${badge}
          ${r.status === 'error'
            ? `<button class="link-btn" data-act="retry">Retry</button>`
            : `<select data-act="choose">
                 ${options}
                 <option value="__custom">Paste a YouTube link…</option>
                 <option value="__skip" ${!r.choice ? 'selected' : ''}>Skip this song</option>
               </select>`}
        </div>
        ${r.customOpen
          ? `<div class="custom"><input placeholder="https://music.youtube.com/watch?v=…" data-act="customInput" />
             <button class="ghost" data-act="customSave">Use</button></div>`
          : ''}
      </div>`;
  }

  el.innerHTML = `
    <div class="num">${i + 1}</div>
    <div>
      <div class="song-title">${esc(t.title)}${t.explicit ? '<span class="e">E</span>' : ''}</div>
      <div class="song-sub">${esc(t.artistText)} · ${fmt(t.durationMs / 1000)}</div>
    </div>
    <div class="match">${matchHtml}</div>`;
  return el;
}

$('rows').addEventListener('change', (e) => {
  if (e.target.dataset.act !== 'choose') return;
  const i = Number(e.target.closest('.row').dataset.i);
  const r = rows[i];
  const v = e.target.value;
  r.customOpen = v === '__custom';
  if (v === '__skip') r.choice = null;
  else if (v !== '__custom') {
    r.choice = v;
    r.status = v === r.autoChoice ? r.autoStatus : 'manual';
  }
  updateRow(i);
  if (r.customOpen) document.querySelector(`.row[data-i="${i}"] [data-act="customInput"]`)?.focus();
});

$('rows').addEventListener('click', async (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  const i = Number(e.target.closest('.row').dataset.i);
  const r = rows[i];
  if (act === 'retry') {
    r.status = 'pending';
    updateRow(i);
    matchRow(i, runId);
  }
  if (act === 'customSave') {
    const input = e.target.closest('.custom').querySelector('input');
    const id = parseVideoId(input.value);
    if (!id) return input.setCustomValidity('Not a YouTube link'), input.reportValidity();
    e.target.disabled = true;
    try {
      const c = await api(`/api/video/${encodeURIComponent(id)}`);
      r.candidates = [c, ...r.candidates.filter((x) => x.videoId !== id)];
      r.choice = id;
      r.status = 'manual';
      r.customOpen = false;
      updateRow(i);
    } catch (err) {
      input.setCustomValidity(err.message);
      input.reportValidity();
      e.target.disabled = false;
    }
  }
});

$('rows').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.dataset.act === 'customInput') {
    e.preventDefault();
    e.target.closest('.custom').querySelector('[data-act="customSave"]').click();
  }
});
$('rows').addEventListener('input', (e) => e.target.setCustomValidity?.(''));

function parseVideoId(s) {
  s = s.trim();
  if (/^[\w-]{11}$/.test(s)) return s;
  try {
    const u = new URL(s);
    if (u.hostname === 'youtu.be') return u.pathname.slice(1, 12);
    return u.searchParams.get('v');
  } catch {
    return null;
  }
}

function selectedIds() {
  return rows.filter((r) => r.choice).map((r) => r.choice);
}

function updateSelection() {
  const pending = rows.filter((r) => r.status === 'pending').length;
  const sel = rows.filter((r) => r.choice);
  const videos = sel.filter((r) => r.candidates.find((c) => c.videoId === r.choice)?.type === 'video').length;
  $('selCount').textContent = sel.length;
  $('selDetail').textContent = pending
    ? `· matching ${rows.length - pending}/${rows.length}…`
    : `of ${rows.length}${videos ? ` · ${videos} as video` : ''}`;
  $('openCreate').disabled = pending > 0 || sel.length === 0;
}

/* ---------- Creating the playlist ---------- */

const BATCH = 10;
// Set when Google's daily limit stops a run, so the rest can be added later.
let unfinished = null;

$('openCreate').addEventListener('click', async () => {
  const auth = await api('/api/auth');
  renderAccount(auth);
  if (!auth.connected) return openAuth(auth, true);
  $('createError').hidden = true;
  $('createResult').hidden = true;
  const btn = $('createSubmit');
  btn.hidden = false;
  btn.disabled = false;
  btn.textContent = `Create with ${selectedIds().length} songs`;
  unfinished = null;
  $('createDlg').showModal();
});

function showCreateStatus(html, kind = 'success') {
  $('createResult').innerHTML = `<div class="${kind}">${html}</div>`;
  $('createResult').hidden = false;
}

$('createForm').addEventListener('submit', async (e) => {
  if (e.submitter?.value === 'cancel') return;
  e.preventDefault();
  const btn = $('createSubmit');
  btn.disabled = true;
  $('createError').hidden = true;

  let { playlistId, url, ids, added, failed } = unfinished ?? {
    ids: selectedIds(),
    added: 0,
    failed: [],
  };
  try {
    if (!playlistId) {
      btn.textContent = 'Creating playlist…';
      ({ playlistId, url } = await api('/api/playlist', {
        method: 'POST',
        body: JSON.stringify({ title: $('cName').value, description: $('cDesc').value, privacy: $('cPrivacy').value }),
      }));
    }
    const total = added + ids.length;
    while (ids.length) {
      btn.textContent = `Adding songs… ${added}/${total}`;
      const res = await api(`/api/playlist/${encodeURIComponent(playlistId)}/add`, {
        method: 'POST',
        body: JSON.stringify({ videoIds: ids.slice(0, BATCH) }),
      });
      added += res.added.length;
      failed = failed.concat(res.failed);
      ids = res.quotaExceeded ? res.remaining.concat(ids.slice(BATCH)) : ids.slice(BATCH);
      if (res.quotaExceeded) {
        unfinished = { playlistId, url, ids, added, failed };
        showCreateStatus(
          `Added ${added} songs, then hit Google's daily YouTube limit (about 200 songs a day). ` +
            `<b>${ids.length} songs are left.</b> Keep this tab open and press the button again tomorrow. ` +
            `<a href="${esc(url)}" target="_blank" rel="noreferrer">Open playlist →</a>`,
          'warn',
        );
        btn.disabled = false;
        btn.textContent = `Add remaining ${ids.length} songs`;
        return;
      }
    }
    unfinished = null;
    showCreateStatus(
      `Created! Added ${added} songs${failed.length ? `; ${failed.length} couldn't be added (removed or region-blocked videos)` : ''}. ` +
        `<a href="${esc(url)}" target="_blank" rel="noreferrer">Open in YouTube Music →</a>`,
    );
    btn.hidden = true;
  } catch (err) {
    if (playlistId) unfinished = { playlistId, url, ids, added, failed };
    $('createError').textContent = err.message;
    $('createError').hidden = false;
    btn.disabled = false;
    btn.textContent = playlistId ? `Continue adding ${ids.length} songs` : 'Try again';
    if (/sign in/i.test(err.message)) renderAccount(await api('/api/auth'));
  }
});

/* ---------- Google account ---------- */

let continueToCreate = false;

function renderAccount(auth) {
  const b = $('accountBtn');
  b.innerHTML = auth.connected
    ? `${auth.photo ? `<img src="${esc(auth.photo)}" alt="" referrerpolicy="no-referrer" />` : ''}${esc(auth.name || 'Signed in')}`
    : 'Sign in with Google';
}

function openAuth(auth, thenCreate = false) {
  continueToCreate = thenCreate;
  $('authSetup').hidden = auth.configured;
  $('authSignIn').hidden = !auth.configured || auth.connected;
  $('authConnected').hidden = !auth.connected;
  $('authName').textContent = auth.name || 'your account';
  $('authError').hidden = true;
  $('authDlg').showModal();
}

$('accountBtn').addEventListener('click', async () => openAuth(await api('/api/auth')));

$('googleBtn').addEventListener('click', () => {
  $('authError').hidden = true;
  const w = 500, h = 650;
  const popup = window.open(
    '/oauth/start',
    'google-auth',
    `width=${w},height=${h},left=${screenX + (outerWidth - w) / 2},top=${screenY + (outerHeight - h) / 2}`,
  );
  if (!popup) location.href = '/oauth/start'; // popup blocked: fall back to a full-page sign-in
});

new BroadcastChannel('google-auth').addEventListener('message', async ({ data }) => {
  if (data?.type !== 'google-auth') return;
  const auth = await api('/api/auth');
  renderAccount(auth);
  if (!data.ok || !auth.connected) {
    $('authError').textContent = data.message;
    $('authError').hidden = false;
    if (!$('authDlg').open) openAuth(auth, continueToCreate);
    return;
  }
  $('authDlg').close();
  if (continueToCreate) $('openCreate').click();
});

$('authForm').addEventListener('submit', async (e) => {
  if (e.submitter?.id !== 'authLogout') return;
  renderAccount(await api('/api/auth/disconnect', { method: 'POST' }));
});

api('/api/auth').then(renderAccount).catch(() => {});
