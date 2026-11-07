// LLS band-set editor. Vanilla ES module, no build step. Data contract: docs/model.md.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const clone = (o) => JSON.parse(JSON.stringify(o));
const isMac = /Mac/.test(navigator.platform);

const TR = [
  ['live-vocal', 'Live vocal'], ['live-midi', 'Live MIDI'], ['live-looped', 'Live looped'], ['live-drums', 'Live drums'],
  ['live-guitar', 'Live guitar/bass'], ['backing-audio', 'Backing audio'], ['audio-loop', 'Audio loop'],
  ['midi-clip', 'MIDI clip'], ['click', 'Click'], ['dropped', 'Dropped'],
];
const TLABEL = Object.fromEntries(TR);
const LIVE = new Set(['live-vocal', 'live-midi', 'live-looped', 'live-drums', 'live-guitar']);
const RIG_KINDS = ['keys', 'drums', 'guitar', 'bass', 'vocal', 'grid'];
const RIG_ICON = { keys: '🎹', drums: '🥁', guitar: '🎸', bass: '🎸', vocal: '🎤', grid: '▦' };
const SEC_COLORS = ['#ff4fa3', '#b452ff', '#4f7dff', '#27d7ff', '#2ee6c0', '#ffb23d', '#ff8a3d', '#8c7dff'];
const PLAYBACK_ZONES = [['backing-audio', 'Backing audio'], ['midi-clip', 'MIDI clips'], ['audio-loop', 'Audio loops'], ['click', 'Click'], ['dropped', 'Dropped']];

const pref = {
  get(k, d) { try { const v = localStorage.getItem('lls.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('lls.' + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

const S = {
  songs: [], slug: null, song: null, saved: '', band: null, bandSaved: '', activity: null,
  lineupId: 'default', view: ['arrange', 'lineup', 'band'].includes(location.hash.slice(1)) ? location.hash.slice(1) : pref.get('view', 'arrange'), side: pref.get('side', 'checks'),
  selSec: null, renaming: null, highlight: null, zoom: 0,
  hist: [], fut: [], issues: [], vState: 'idle', vMsg: '', shellKey: '',
};

// ---------------------------------------------------------------- API
async function api(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, init);
  let j = null;
  try { j = await r.json(); } catch { /* empty */ }
  if (!r.ok) { const e = new Error((j && j.error) || `${r.status} ${r.statusText}`); e.status = r.status; throw e; }
  return j;
}

// ---------------------------------------------------------------- model helpers
const bpb = () => { const ts = S.song.time_signature || [4, 4]; return (ts[0] * 4) / ts[1]; };
const totalBeats = () => Math.max(S.song.length_beats || 0, ...S.song.sections.map((s) => s.end), bpb());
const lineup = () => S.song.lineups[S.lineupId];
const asg = (pid) => (lineup()?.assignments || {})[pid];
const partById = (pid) => S.song.parts.find((p) => p.id === pid);
const secById = (sid) => S.song.sections.find((s) => s.id === sid);
const lineupT = (part) => asg(part.id)?.treatment || part.default_treatment;
const effT = (part, sid) => asg(part.id)?.sections?.[sid] || lineupT(part);
const bandPerf = (id) => (S.band?.performers || []).find((p) => p.id === id);
const playable = (part) => ['analoglab', 'drumrack'].includes(part.instrument?.type);
const dirty = () => !!S.song && JSON.stringify(S.song) !== S.saved;
const bandDirty = () => !!S.band && JSON.stringify(S.band) !== S.bandSaved;
const slugify = (s) => (String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'lineup');

function normalize(song) {
  song.lineups ||= {};
  if (!Object.keys(song.lineups).length) song.lineups.default = { name: 'Default', performers: [], assignments: {} };
  for (const [id, lu] of Object.entries(song.lineups)) {
    // Older ingest wrote {treatments: {part: t}}; the contract is {assignments: {part: {treatment}}}.
    if (lu.treatments && !lu.assignments) {
      lu.assignments = Object.fromEntries(Object.entries(lu.treatments).map(([p, t]) => [p, { treatment: t }]));
      delete lu.treatments;
    }
    lu.assignments ||= {};
    lu.performers ||= [];
    lu.name ||= lu.description ? id[0].toUpperCase() + id.slice(1) : id;
  }
  for (const s of song.sections) { if (!('vamp' in s)) s.vamp = false; }
  return song;
}

function tidy(pid) {
  const lu = lineup(), a = lu.assignments[pid], part = partById(pid);
  if (!a) return;
  if (a.sections && !Object.keys(a.sections).length) delete a.sections;
  if (!LIVE.has(a.treatment)) { delete a.performer; delete a.rig; }
  if (!a.performer && !a.rig && !a.sections && (!a.treatment || a.treatment === part.default_treatment)) delete lu.assignments[pid];
}

function setLineupT(pid, t) {
  const lu = lineup(), part = partById(pid);
  const a = (lu.assignments[pid] ||= {});
  a.treatment = t;
  if (a.sections) for (const [sid, st] of Object.entries(a.sections)) if (st === t) delete a.sections[sid];
  tidy(pid);
  if (LIVE.has(t) && !a.performer) toast(`${part.name}: drag it onto a performer's rig on the Lineup board`);
}

function setOverride(pid, sid, t) {
  const lu = lineup(), part = partById(pid);
  const a = (lu.assignments[pid] ||= { treatment: part.default_treatment });
  a.treatment ||= part.default_treatment;
  a.sections ||= {};
  if (!t || t === a.treatment) delete a.sections[sid]; else a.sections[sid] = t;
  tidy(pid);
}

function rigTreatment(kind, cur) {
  if (kind === 'vocal') return 'live-vocal';
  if (kind === 'drums') return 'live-drums';
  if (kind === 'guitar' || kind === 'bass') return 'live-guitar';
  if (kind === 'grid') return 'live-looped';
  return cur === 'live-looped' ? 'live-looped' : 'live-midi';
}

function assignToRig(pid, perfId, rigId) {
  const lu = lineup(), perf = bandPerf(perfId), rig = perf?.rigs?.find((r) => r.id === rigId);
  if (!rig) return;
  const a = (lu.assignments[pid] ||= {});
  a.treatment = rigTreatment(rig.kind, a.treatment);
  a.performer = perfId; a.rig = rigId;
  if (!lu.performers.includes(perfId)) lu.performers.push(perfId);
  tidy(pid);
}

function toPlayback(pid, t) {
  const lu = lineup(), part = partById(pid);
  const a = (lu.assignments[pid] ||= {});
  delete a.performer; delete a.rig;
  if (!t) {
    if (!LIVE.has(part.default_treatment)) t = part.default_treatment;
    else t = part.kind === 'midi' && playable(part) ? 'midi-clip' : 'backing-audio';
  }
  a.treatment = t;
  tidy(pid);
}

function nextSecId() {
  const n = Math.max(0, ...S.song.sections.map((s) => +(/^s(\d+)$/.exec(s.id)?.[1] || 0)));
  return 's' + (n + 1);
}

function forEachAssignment(fn) {
  for (const lu of Object.values(S.song.lineups)) for (const [pid, a] of Object.entries(lu.assignments || {})) fn(a, pid, lu);
}

function splitSection(sid, at) {
  const secs = S.song.sections, i = secs.findIndex((s) => s.id === sid), s = secs[i], bp = bpb();
  if (at == null) at = s.start + Math.round((s.end - s.start) / 2 / bp) * bp;
  at = Math.round(at / bp) * bp;
  if (at <= s.start || at >= s.end) { toast('Section is too short to split there'); return; }
  mutate(() => {
    const ns = { ...clone(s), id: nextSecId(), name: s.name + ' b', start: at };
    s.end = at;
    secs.splice(i + 1, 0, ns);
    forEachAssignment((a) => { if (a.sections?.[sid]) a.sections[ns.id] = a.sections[sid]; });
    S.selSec = ns.id;
  });
}

function mergeSections(sid, dir = 1) {
  const secs = S.song.sections, i = secs.findIndex((s) => s.id === sid);
  const [a, b] = dir > 0 ? [i, i + 1] : [i - 1, i];
  if (a < 0 || b >= secs.length) { toast('Nothing to merge with'); return; }
  mutate(() => {
    const keep = secs[a], gone = secs[b];
    keep.end = gone.end;
    secs.splice(b, 1);
    forEachAssignment((as) => { if (as.sections) delete as.sections[gone.id]; });
    S.selSec = keep.id;
  });
}

// ---------------------------------------------------------------- history
function mutate(fn) {
  const before = JSON.stringify(S.song);
  fn();
  if (JSON.stringify(S.song) === before) { render(); return; }
  S.hist.push(before); if (S.hist.length > 200) S.hist.shift();
  S.fut = [];
  changed();
}
function pushHist(before) { S.hist.push(before); S.fut = []; changed(); }
function undo() {
  if (!S.hist.length) return;
  S.fut.push(JSON.stringify(S.song)); S.song = JSON.parse(S.hist.pop()); fixRefs(); changed();
}
function redo() {
  if (!S.fut.length) return;
  S.hist.push(JSON.stringify(S.song)); S.song = JSON.parse(S.fut.pop()); fixRefs(); changed();
}
function fixRefs() {
  if (!S.song.lineups[S.lineupId]) S.lineupId = Object.keys(S.song.lineups)[0];
  // keep S.selSec even if undo removed it: redo brings it back, and every use checks secById first
}
function changed() { render(); scheduleValidate(); }

// ---------------------------------------------------------------- activity
function secActivity(pid, sec) {
  const a = S.activity?.parts?.[pid];
  if (!a || !a.bars) return null;
  const bp = bpb(), lo = Math.floor(sec.start / bp), hi = Math.ceil(sec.end / bp);
  for (let b = lo; b < hi; b++) if (a.bars[b] > 0) return true;
  return false;
}

// ---------------------------------------------------------------- loading / saving
async function init() {
  applyTheme(pref.get('theme', 'dark'));
  try {
    [S.songs, S.band] = await Promise.all([api('GET', '/songs'), api('GET', '/band')]);
  } catch (e) { $('#main').innerHTML = `<div class="empty-state"><div class="err-box">${esc(e.message)}</div></div>`; return; }
  S.bandSaved = JSON.stringify(S.band);
  const want = pref.get('song', null);
  const slug = S.songs.find((s) => s.slug === want)?.slug || S.songs[0]?.slug;
  if (!slug) { renderTop(); $('#main').innerHTML = '<div class="empty-state"><b>No songs yet.</b><br>Run <code>python -m lls ingest &lt;project.logicx&gt; songs/&lt;slug&gt;</code> first.</div>'; return; }
  await loadSong(slug);
}

async function loadSong(slug) {
  const [song, activity] = await Promise.all([api('GET', `/songs/${slug}`), api('GET', `/songs/${slug}/activity`).catch(() => null)]);
  S.slug = slug; S.song = normalize(song); S.saved = JSON.stringify(S.song); S.activity = activity;
  S.hist = []; S.fut = []; S.selSec = null; S.highlight = null; S.shellKey = '';
  const lids = Object.keys(S.song.lineups);
  const wantL = pref.get('lineup:' + slug, null);
  S.lineupId = lids.includes(wantL) ? wantL : lids[0];
  pref.set('song', slug);
  render(); runValidate();
}

async function save() {
  try {
    if (dirty()) {
      const snap = JSON.stringify(S.song);
      await api('PUT', `/songs/${S.slug}`, S.song);
      S.saved = snap;
    }
    if (bandDirty()) {
      const snap = JSON.stringify(S.band);
      await api('PUT', '/band', S.band);
      S.bandSaved = snap;
    }
    toast('Saved');
  } catch (e) { toast('Save failed: ' + e.message, true); }
  renderTop();
}

// ---------------------------------------------------------------- validation
let vSeq = 0, vTimer = 0;
function scheduleValidate() { clearTimeout(vTimer); vTimer = setTimeout(runValidate, 250); }
async function runValidate() {
  if (!S.song) return;
  const seq = ++vSeq;
  try {
    const r = await api('POST', `/songs/${S.slug}/validate`, { lineup_id: S.lineupId, song: S.song, band: S.band });
    if (seq !== vSeq) return;
    S.issues = r.issues || []; S.vState = 'ok';
  } catch (e) {
    if (seq !== vSeq) return;
    S.issues = []; S.vState = e.status === 501 ? 'na' : 'err'; S.vMsg = e.message;
  }
  renderSide(); applyIssueMarkers();
}
function issueLevelFor(pid) {
  let lv = null;
  for (const i of S.issues) if (i.part === pid) { if (i.level === 'error') return 'error'; lv = 'warn'; }
  return lv;
}
function applyIssueMarkers() {
  for (const el of $$('[data-part].trow, .card[data-part]')) {
    const lv = issueLevelFor(el.dataset.part);
    if (lv) el.dataset.issue = lv; else delete el.dataset.issue;
  }
  const uniq = [...new Map(S.issues.map((i) => [`${i.level}|${i.part || ''}|${i.message}`, i])).values()];
  const errs = uniq.filter((i) => i.level === 'error').length, warns = uniq.length - errs;
  const c = $('#checks-count');
  c.className = 'count ' + (S.vState !== 'ok' ? '' : errs ? 'err' : warns ? 'warn' : 'ok');
  c.textContent = S.vState !== 'ok' ? '–' : errs ? errs : warns ? warns : '✓';
}

// ---------------------------------------------------------------- rendering
function render() { renderTop(); renderMain(); renderSide(); }

function renderTop() {
  const ss = $('#song-select');
  ss.innerHTML = S.songs.map((s) => `<option value="${esc(s.slug)}" ${s.slug === S.slug ? 'selected' : ''}>${esc(s.title)}</option>`).join('');
  const ls = $('#lineup-select');
  ls.innerHTML = S.song ? Object.entries(S.song.lineups).map(([id, lu]) =>
    `<option value="${esc(id)}" ${id === S.lineupId ? 'selected' : ''}>${esc(lu.name || id)}</option>`).join('') : '';
  for (const b of $$('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.view === S.view));
  for (const b of $$('.side-tabs button')) b.setAttribute('aria-selected', String(b.dataset.side === S.side));
  const d = dirty() || bandDirty(), st = $('#save-state');
  st.textContent = d ? 'Unsaved' : S.song ? 'Saved' : '';
  st.classList.toggle('dirty', d);
  $('#undo').disabled = !S.hist.length; $('#redo').disabled = !S.fut.length;
  $('#save').disabled = !d;
  document.title = (d ? '• ' : '') + (S.song ? S.song.title + ' — ' : '') + 'Live Band Sets';
}

function renderMain() {
  if (!S.song) return;
  const main = $('#main'), key = S.view + '|' + S.slug;
  const focusPart = document.activeElement?.closest?.('[data-part]')?.dataset.part;
  if (S.shellKey !== key) { main.innerHTML = VIEWS[S.view].shell(); S.shellKey = key; }
  VIEWS[S.view].fill();
  applyIssueMarkers();
  if (focusPart && S.view === 'lineup') $(`.card[data-part="${CSS.escape(focusPart)}"]`)?.focus({ preventScroll: true });
}

function chip(t, attrs = '', extra = '') {
  return `<button class="chip ${t === 'dropped' ? 'dropped' : ''} ${extra}" style="--tc:var(--t-${t})" ${attrs}>${esc(TLABEL[t] || t)}</button>`;
}

function instBadge(part) {
  const i = part.instrument || {};
  if (i.type === 'analoglab') return `<span class="badge analoglab" title="Analog Lab V preset (AU state injected)"><b>AL</b> ${esc(i.preset || '')}</span>`;
  if (i.type === 'drumrack') return `<span class="badge drumrack"><b>Rack</b> Drum Rack</span>`;
  if (i.type === 'logic-builtin') {
    const pre = i.preset ? ' · ' + String(i.preset).replace(/\.\w+$/, '') : '';
    return `<span class="badge logic" title="Logic built-in: not playable in Live — needs a stem"><b>Logic</b> ${esc(i.name || '')}${esc(pre)}</span>`;
  }
  if (i.type === 'audio' || part.kind === 'audio') return `<span class="badge audio" title="${esc(part.stem ? 'exported stem' : part.raw_stem ? 'raw stem (no FX)' : 'no stem yet')}"><b>Audio</b> ${part.stem ? 'stem' : part.raw_stem ? 'raw stem' : 'no stem'}</span>`;
  return `<span class="badge">${esc(i.type || part.kind || '?')}</span>`;
}

function whoLabel(pid) {
  const a = asg(pid);
  if (!a?.performer || !LIVE.has(a.treatment)) return '';
  const p = bandPerf(a.performer);
  return `<span class="who" title="${esc((p?.name || a.performer) + ' · ' + (a.rig || ''))}">${esc(p?.name || a.performer)}</span>`;
}

// ---------- Arrange view
const VIEWS = {};
VIEWS.arrange = {
  shell: () => `
    <div class="toolbar">
      <span class="song-meta" id="meta"></span>
      <span class="sep"></span>
      <div class="palette" title="Drag a treatment onto a part (whole song) or onto a cell (that section only)">
        ${TR.map(([t]) => chip(t, `draggable="true" data-drag-t="${t}" data-act="noop" tabindex="-1"`, 'mini')).join('')}
      </div>
      <span class="sep"></span>
      <button class="btn small" data-act="split" title="Split selected section (S) — or ⌥-click a section">Split</button>
      <button class="btn small" data-act="merge" title="Merge with next section (M)">Merge →</button>
      <button class="btn small" data-act="vamp" title="Toggle vamp/loop on the selected section (V)">Vamp</button>
      <span class="sep"></span>
      <label class="hint" title="Zoom (0 = fit)">Zoom <input type="range" id="zoom" min="0" max="48" step="2" value="${S.zoom}"></label>
    </div>
    <div class="tl" id="tl"><div class="tl-inner" id="tl-inner"></div></div>`,
  fill() {
    const song = S.song, bp = bpb(), total = totalBeats(), nb = Math.ceil(total / bp);
    const tl = $('#tl');
    const hdr = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--hdr')) || 268;
    const avail = Math.max(200, tl.clientWidth - hdr - 2);
    const W = S.zoom ? nb * S.zoom : Math.max(avail, nb * 5);
    const ppb = W / nb, pct = (b) => (b / total) * 100;
    $('#meta').innerHTML = `<b>${esc(song.title)}</b>${song.tempo} bpm · ${(song.time_signature || [4, 4]).join('/')} · ${nb} bars · ${song.sections.length} sections · ${song.parts.length} parts`;
    for (const b of $$('[data-act=split],[data-act=merge],[data-act=vamp]')) b.disabled = !secById(S.selSec);

    // ruler
    const every = ppb >= 26 ? 1 : ppb >= 13 ? 2 : ppb >= 6.5 ? 4 : 8;
    let ruler = '';
    for (let i = 0; i < nb; i++) {
      if (ppb < 5 && i % every) continue;
      const lab = i % every === 0 ? `<span>${i + 1}</span>` : '';
      ruler += `<div class="tick ${i % 4 === 0 ? 'major' : ''}" style="left:${pct(i * bp)}%">${lab}</div>`;
    }
    // sections
    const hl = S.highlight || {};
    let secs = '';
    song.sections.forEach((s, i) => {
      const bars = Math.round((s.end - s.start) / bp);
      const inner = S.renaming === s.id
        ? `<input id="rename" value="${esc(s.name)}" aria-label="Section name">`
        : `<span class="sname">${esc(s.name)}</span>${s.vamp ? '<span class="vamp" title="vamp">∞</span>' : ''}<span class="sbars">${bars}b</span>`;
      secs += `<div class="sec ${S.selSec === s.id ? 'sel' : ''} ${hl.section === s.id ? 'hl' : ''}" data-sid="${esc(s.id)}"
        style="left:${pct(s.start)}%;width:${pct(s.end - s.start)}%;--sc:${SEC_COLORS[i % SEC_COLORS.length]}"
        title="${esc(s.name)} · bar ${s.start / bp + 1}–${s.end / bp + 1}">${inner}</div>`;
      if (i > 0) secs += `<div class="bound" data-i="${i}" style="left:${pct(s.start)}%" title="Drag to move boundary (snaps to bars)"></div>`;
    });
    secs += '<div class="split-guide" id="split-guide"></div>';

    // parts
    let rows = '';
    for (const p of song.parts) {
      const lt = lineupT(p), ov = asg(p.id)?.sections || {};
      let cells = '';
      for (const s of song.sections) {
        const t = effT(p, s.id), act = secActivity(p.id, s);
        const cls = act === null ? 'unknown' : act ? 'on' : '';
        let bars = '';
        const a = S.activity?.parts?.[p.id]?.bars;
        if (a) {
          for (let b = Math.floor(s.start / bp); b < Math.ceil(s.end / bp); b++) {
            const d = a[b] || 0;
            bars += d > 0 ? `<i style="height:${Math.max(12, d * 100).toFixed(0)}%"></i>` : '<i class="z"></i>';
          }
        }
        cells += `<div class="cell ${cls} t-${t} ${ov[s.id] ? 'ovr' : ''} ${hl.section === s.id ? 'hl-sec' : ''}" data-part="${esc(p.id)}" data-sid="${esc(s.id)}"
          style="left:${pct(s.start)}%;width:${pct(s.end - s.start)}%;--tc:var(--t-${t})"
          title="${esc(p.name)} · ${esc(s.name)}: ${esc(TLABEL[t])}${ov[s.id] ? ' (override)' : ''}${act === null ? ' · activity unknown' : act ? '' : ' · silent'}"><div class="bars">${bars}</div></div>`;
      }
      rows += `<div class="trow part ${hl.part === p.id ? 'hl' : ''}" data-part="${esc(p.id)}">
        <div class="ph" data-part="${esc(p.id)}" data-droppart="1">
          <span class="issue-dot"></span>
          <div class="pmeta"><span class="pname" title="${esc(p.name)} (${esc(p.id)})">${esc(p.name)}</span>${instBadge(p)}</div>
          ${whoLabel(p.id)}
          ${chip(lt, `draggable="true" data-drag-t="${lt}" data-act="tmenu" data-part="${esc(p.id)}" title="Treatment in this lineup — click to change, drag onto a cell to override one section"`)}
        </div>
        <div class="lane" style="width:${W}px">${cells}</div></div>`;
    }
    $('#tl-inner').innerHTML = `
      <div class="trow head"><div class="ph">Bar</div><div class="lane" style="width:${W}px">${ruler}</div></div>
      <div class="trow secs"><div class="ph" title="⌥-click a section to split at a bar · drag edges to move · double-click to rename · right-click for more">Sections <span class="hint" style="margin-left:auto">${esc(lineup().name)}</span></div><div class="lane" id="sec-lane" style="width:${W}px">${secs}</div></div>
      ${rows}`;
    const ri = $('#rename');
    if (ri) { ri.focus(); ri.select(); }
  },
};

// ---------- Lineup board
function boardPlacement() {
  const lu = lineup(), place = { rigs: {}, needs: [], pb: {} };
  for (const p of S.song.parts) {
    const a = asg(p.id), t = lineupT(p);
    if (LIVE.has(t)) {
      const perf = a?.performer && bandPerf(a.performer), rig = perf?.rigs?.find((r) => r.id === a.rig);
      if (rig) (place.rigs[perf.id + '/' + rig.id] ||= []).push(p); else place.needs.push(p);
    } else (place.pb[t] ||= []).push(p);
  }
  const cols = [...lu.performers];
  for (const k of Object.keys(place.rigs)) { const pid = k.split('/')[0]; if (!cols.includes(pid)) cols.push(pid); }
  place.cols = cols.filter((id) => bandPerf(id));
  return place;
}

function card(p) {
  const strip = S.song.sections.map((s) => {
    const act = secActivity(p.id, s), t = effT(p, s.id);
    return `<i class="${act ? 'on' : ''}" style="--tc:var(--t-${t})" title="${esc(s.name)}: ${esc(TLABEL[t])}${act === null ? ' (unknown)' : act ? '' : ' (silent)'}"></i>`;
  }).join('');
  const t = lineupT(p), hl = S.highlight?.part === p.id;
  return `<div class="card ${hl ? 'hl' : ''}" draggable="true" tabindex="0" data-part="${esc(p.id)}" data-dragpart="${esc(p.id)}"
     title="${esc(p.name)} — drag onto a rig, or focus and press a zone number">
    <div class="c1"><span class="issue-dot"></span><span class="cname">${esc(p.name)}</span>
    ${chip(t, `data-act="tmenu" data-part="${esc(p.id)}" title="Change treatment"`, 'mini')}</div>
    <div class="c2">${instBadge(p)}<div class="strip" title="activity per section">${strip}</div></div></div>`;
}

VIEWS.lineup = {
  shell: () => `
    <div class="toolbar">
      <span class="lbl">Performers</span><div class="perf-toggles" id="perf-toggles"></div>
      <span class="sep"></span>
      <button class="btn small" data-act="suggest">Suggest for N performers…</button>
      <span class="hint">Drag parts onto a rig to play them live · back to Playback to unassign · focus a card and press a zone number</span>
    </div>
    <div class="board" id="board"></div>`,
  fill() {
    const lu = lineup(), pl = boardPlacement();
    $('#perf-toggles').innerHTML = (S.band?.performers || []).map((p) =>
      `<button class="ptog" data-act="ptoggle" data-perf="${esc(p.id)}" aria-pressed="${pl.cols.includes(p.id)}">${esc(p.name)}</button>`).join('')
      || '<span class="hint">No performers in band.json — add them on the Band tab</span>';
    let n = 0;
    const zoneNo = () => (++n <= 9 ? `<kbd>${n}</kbd>` : '');
    let pb = `<div class="zone needs ${pl.needs.length ? '' : ''}" data-drop="playback" ${pl.needs.length ? '' : 'hidden'}>
        <div class="zone-h"><span class="k" style="color:var(--err)">Live · needs a player</span><span class="io">${pl.needs.length}</span></div>
        ${pl.needs.map(card).join('')}</div>`;
    for (const [t, label] of PLAYBACK_ZONES) {
      const list = pl.pb[t] || [];
      if (!list.length && (t === 'click' || t === 'audio-loop')) {
        pb += `<div class="zone" data-drop="playback" data-t="${t}" style="min-height:30px"><div class="zone-h"><span class="chip mini" style="--tc:var(--t-${t})">${label}</span><span class="io">drop here</span></div></div>`;
        continue;
      }
      pb += `<div class="zone" data-drop="playback" data-t="${t}">
        <div class="zone-h"><span class="chip mini ${t === 'dropped' ? 'dropped' : ''}" style="--tc:var(--t-${t})">${label}</span><span class="io">${list.length}</span></div>
        ${list.map(card).join('') || '<div class="empty">drop parts here</div>'}</div>`;
    }
    let html = `<section class="col playback"><div class="col-h"><b>Playback</b><span class="roles">from the laptop</span><kbd class="x" title="Focus a card and press 0">0</kbd></div><div class="col-b">${pb}</div></section>`;
    for (const pid of pl.cols) {
      const p = bandPerf(pid);
      const rigs = (p.rigs || []).map((r) => {
        const list = pl.rigs[pid + '/' + r.id] || [];
        const io = r.kind === 'vocal' || r.kind === 'guitar' || r.kind === 'bass'
          ? `in ${Array.isArray(r.audio_input) ? r.audio_input.join('/') : r.audio_input ?? '?'}`
          : `${r.midi_port || 'all ins'} · ch ${r.midi_channel ?? 'all'}`;
        return `<div class="zone" data-drop="rig" data-perf="${esc(pid)}" data-rig="${esc(r.id)}" data-zone="${n + 1}">
          <div class="zone-h">${zoneNo()}<span class="rig-ic">${RIG_ICON[r.kind] || '?'}</span><span class="k">${esc(r.kind)}</span><span class="ctl" title="${esc(r.controller || r.id)}">${esc(r.controller || r.id)}</span><span class="io">${esc(io)}</span></div>
          ${list.map(card).join('') || `<div class="empty">drop to play live (${esc(TLABEL[rigTreatment(r.kind)])})</div>`}</div>`;
      }).join('');
      html += `<section class="col"><div class="col-h"><b>${esc(p.name)}</b><span class="roles">${esc((p.roles || []).join(' · '))}</span>
        <button class="btn icon x" data-act="ptoggle" data-perf="${esc(pid)}" title="Remove from this lineup (their parts go to playback)">×</button></div>
        <div class="col-b">${rigs || '<div class="empty">No rigs — add one on the Band tab</div>'}</div></section>`;
    }
    const absent = (S.band?.performers || []).filter((p) => !pl.cols.includes(p.id));
    if (absent.length) html += `<div class="add-col"><span class="lbl">Add to lineup</span>${absent.map((p) => `<button class="btn small" data-act="ptoggle" data-perf="${esc(p.id)}">+ ${esc(p.name)}</button>`).join('')}</div>`;
    $('#board').innerHTML = html;
    void lu;
  },
};

// ---------- Band editor
function ioText(v) { return Array.isArray(v) ? v.join(',') : v ?? ''; }
function parseIo(v) {
  v = String(v).trim();
  if (!v) return null;
  const parts = v.split(/[,\s/]+/).filter(Boolean).map(Number);
  if (parts.some((n) => !Number.isInteger(n))) return v;
  return parts.length === 1 ? parts[0] : parts;
}
VIEWS.band = {
  shell: () => `
    <div class="toolbar"><b>Band</b><span class="hint">band.json · shared by every song</span><span class="sep"></span>
      <button class="btn small" data-act="add-perf">+ Performer</button>
      <span class="spacer" style="flex:1"></span><button class="btn small" data-act="save">Save band</button></div>
    <div class="band" id="band"></div>`,
  fill() {
    const b = S.band, itf = (b.interface ||= {});
    itf.outputs ||= {}; itf.inputs ||= {};
    const ioRows = (kind) => Object.entries(itf[kind]).map(([k, v]) => `
      <div class="frow"><input class="inp mono" style="width:120px" data-iokey="${kind}" data-key="${esc(k)}" value="${esc(k)}" aria-label="${kind} name">
      <input class="inp mono" style="width:90px" data-ioval="${kind}" data-key="${esc(k)}" value="${esc(ioText(v))}" placeholder="${kind === 'outputs' ? '1,2' : '1'}" aria-label="${kind} channels">
      <button class="btn icon small danger" data-act="del-io" data-io="${kind}" data-key="${esc(k)}" title="Remove">×</button></div>`).join('');
    const perf = (p, pi) => `
      <div class="fcard">
        <h3><input class="inp" style="flex:1;font-weight:600" data-bp="performers.${pi}.name" value="${esc(p.name)}" aria-label="Name">
          <button class="btn small danger" data-act="del-perf" data-pi="${pi}">Delete</button></h3>
        <div class="frow">
          <label class="field">id<input class="inp mono" style="width:130px" data-bp="performers.${pi}.id" data-type="slug" value="${esc(p.id)}"></label>
          <label class="field" style="flex:1">roles<input class="inp" data-bp="performers.${pi}.roles" data-type="list" value="${esc((p.roles || []).join(', '))}" placeholder="vocal, keys"></label>
        </div>
        <table class="rigs"><thead><tr><th>rig id</th><th>kind</th><th>controller</th><th>MIDI port</th><th>ch</th><th>audio in</th><th></th></tr></thead><tbody>
        ${(p.rigs || []).map((r, ri) => `<tr>
          <td><input class="inp mono" data-bp="performers.${pi}.rigs.${ri}.id" data-type="slug" value="${esc(r.id)}"></td>
          <td><select class="inp" data-bp="performers.${pi}.rigs.${ri}.kind">${RIG_KINDS.map((k) => `<option ${k === r.kind ? 'selected' : ''}>${k}</option>`).join('')}</select></td>
          <td><input class="inp" data-bp="performers.${pi}.rigs.${ri}.controller" data-type="opt" value="${esc(r.controller ?? '')}"></td>
          <td><input class="inp" data-bp="performers.${pi}.rigs.${ri}.midi_port" data-type="null" value="${esc(r.midi_port ?? '')}" placeholder="all ins"></td>
          <td><input class="inp mono" style="width:44px" data-bp="performers.${pi}.rigs.${ri}.midi_channel" data-type="ch" value="${esc(r.midi_channel ?? '')}" placeholder="all"></td>
          <td><input class="inp mono" style="width:56px" data-bp="performers.${pi}.rigs.${ri}.audio_input" data-type="io" value="${esc(ioText(r.audio_input))}" placeholder="—"></td>
          <td><button class="btn icon small danger" data-act="del-rig" data-pi="${pi}" data-ri="${ri}" title="Remove rig">×</button></td></tr>`).join('')}
        </tbody></table>
        <div><button class="btn small" data-act="add-rig" data-pi="${pi}">+ Rig</button></div>
      </div>`;
    $('#band').innerHTML = `
      <div class="fcard" style="max-width:720px">
        <h3>Audio interface</h3>
        <div class="frow"><label class="field">name<input class="inp" style="width:220px" data-bp="interface.name" value="${esc(itf.name || '')}"></label></div>
        <div class="frow" style="align-items:flex-start;gap:24px">
          <div><div class="lbl" style="margin-bottom:4px">Outputs</div>${ioRows('outputs')}<button class="btn small" data-act="add-io" data-io="outputs">+ Output</button></div>
          <div><div class="lbl" style="margin-bottom:4px">Inputs</div>${ioRows('inputs')}<button class="btn small" data-act="add-io" data-io="inputs">+ Input</button></div>
        </div>
      </div>
      <div class="band-grid">${(b.performers || []).map(perf).join('')}</div>`;
  },
};

// ---------- side panel
function renderSide() {
  const el = $('#side-body');
  if (!S.song) { el.innerHTML = ''; return; }
  el.innerHTML = S.side === 'checks' ? checksHtml() : accountedHtml();
  applyIssueMarkers();
}

function checksHtml() {
  const lu = lineup();
  let h = `<div class="h">Lineup · ${esc(lu.name)}</div>`;
  if (S.vState === 'na') return h + `<div class="panel-note">The lineup engine (<code>lls/lineup.py</code>) isn't installed yet, so live checks are off. Everything else works; checks appear as soon as it lands.</div>`;
  if (S.vState === 'err') return h + `<div class="err-box">${esc(S.vMsg)}</div>`;
  if (S.vState === 'idle') return h + '<div class="panel-note">Checking…</div>';
  if (!S.issues.length) return h + '<div class="panel-note good">✓ No problems in this lineup.</div>';
  const order = { error: 0, warn: 1 };
  // group identical problems on the same part across sections (one row per part+message)
  const groups = new Map();
  for (const i of S.issues) {
    const k = `${i.level}|${i.part || ''}|${i.message}`;
    if (!groups.has(k)) groups.set(k, { ...i, sections: [] });
    if (i.section) groups.get(k).sections.push(i.section);
  }
  const list = [...groups.values()].sort((a, b) => (order[a.level] ?? 2) - (order[b.level] ?? 2));
  const nSec = (S.song.sections || []).length;
  return h + '<div class="checks-list">' + list.map((i) => {
    const p = i.part && partById(i.part);
    const secs = i.sections.length === 0 ? '' : i.sections.length === nSec ? 'all sections'
      : i.sections.length === 1 ? (secById(i.sections[0])?.name || i.sections[0]) : `${i.sections.length} sections`;
    const where = [p?.name || i.part, secs].filter(Boolean).join(' · ');
    return `<div class="issue ${esc(i.level)}" data-ipart="${esc(i.part || '')}" data-isec="${esc(i.sections.length === 1 ? i.sections[0] : '')}" tabindex="0">
      <span class="lv"></span><span class="where">${esc(where || 'lineup')}</span><span class="msg">${esc(i.message)}</span></div>`;
  }).join('') + '</div>';
}

function fate(p) {
  const t = lineupT(p), a = asg(p.id);
  let d = '', bad = false;
  if (LIVE.has(t)) {
    const perf = a?.performer && bandPerf(a.performer);
    if (perf) d = `${perf.name} · ${a.rig || '?'}`; else { d = 'needs a player'; bad = true; }
  } else if (t === 'backing-audio' || t === 'audio-loop') {
    if (p.stem) d = 'exported stem'; else if (p.raw_stem) d = 'raw stem (no FX) — export the Logic stem for FX';
    else if (p.kind === 'midi') { d = 'no stem yet — MIDI fallback, export from Logic'; bad = !playable(p); } else { d = 'no stem'; bad = true; }
  } else if (t === 'midi-clip') {
    const i = p.instrument || {};
    if (i.type === 'analoglab') d = `Analog Lab “${i.preset}”`; else if (i.type === 'drumrack') d = 'Drum Rack';
    else { d = `${i.name || 'instrument'} is Logic-only — needs a stem`; bad = true; }
  } else if (t === 'click') d = 'generated click'; else if (t === 'dropped') d = 'not in the set';
  const ov = Object.entries(a?.sections || {});
  if (ov.length) d += ` · ${ov.map(([sid, st]) => `${secById(sid)?.name || sid}→${TLABEL[st] || st}`).join(', ')}`;
  return { t, d, bad };
}

function accountedHtml() {
  const parts = S.song.parts.map((p) => {
    const f = fate(p);
    return `<div class="fate" data-ipart="${esc(p.id)}"><span class="n">${esc(p.name)}</span>${chip(f.t, 'tabindex="-1"', 'mini')}
      <span class="d ${f.bad ? 'bad' : ''}">${esc(f.d)}</span></div>`;
  }).join('');
  const ign = (S.song.ignored_tracks || []).map((i) => `<div class="fate" style="cursor:default"><span class="n">${esc(i.name)}</span><span class="pill ${esc(i.status)}">${esc(i.status)}</span><span class="d">${esc(i.reason)}</span></div>`).join('');
  const w = S.song.warnings || [];
  return `<div class="h">Parts · ${S.song.parts.length}</div>${parts}
    <div class="h">Not imported as parts · ${(S.song.ignored_tracks || []).length}</div>${ign || '<div class="panel-note">none</div>'}
    ${w.length ? `<details class="warnings"><summary>Ingest warnings (${w.length})</summary>${w.map((x) => `<p>${esc(x)}</p>`).join('')}</details>` : ''}`;
}

// ---------------------------------------------------------------- menus & dialogs
let menuState = null;
function openMenu(x, y, items) {
  const m = $('#menu');
  m.innerHTML = items.map((it, i) => {
    if (it === '-') return '<hr>';
    if (it.header) return `<div class="mh">${esc(it.header)}</div>`;
    return `<button class="mi ${it.cur ? 'cur' : ''}" data-mi="${i}" ${it.disabled ? 'disabled' : ''}>${it.color ? `<span class="sw" style="--tc:${it.color}"></span>` : ''}${esc(it.label)}${it.key ? `<span class="k">${esc(it.key)}</span>` : ''}</button>`;
  }).join('');
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = clamp(x, 4, innerWidth - r.width - 4) + 'px';
  m.style.top = clamp(y, 4, innerHeight - r.height - 4) + 'px';
  menuState = { items, prev: document.activeElement };
  (m.querySelector('.mi.cur') || m.querySelector('.mi'))?.focus();
}
function closeMenu() {
  if (!menuState) return;
  $('#menu').hidden = true;
  const prev = menuState.prev; menuState = null;
  if (prev && document.contains(prev)) prev.focus({ preventScroll: true });
}
function treatmentItems(cur, pick, title) {
  return [{ header: title }, ...TR.map(([t, l], i) => ({ label: l, color: `var(--t-${t})`, cur: t === cur, key: String((i + 1) % 10), action: () => pick(t) }))];
}
function menuAt(el, items) { const r = el.getBoundingClientRect(); openMenu(r.left, r.bottom + 4, items); }

function openDialog(html, cls = '') {
  const d = $('#dlg');
  d.className = cls; d.innerHTML = html; d.returnValue = '';
  if (!d.open) d.showModal();
  return d;
}
function closeDialog() { const d = $('#dlg'); if (d.open) d.close(); }

function promptDialog(title, label, value = '') {
  return new Promise((resolve) => {
    const d = openDialog(`<form method="dialog"><div class="dlg-h"><b>${esc(title)}</b></div>
      <div class="dlg-b"><label class="field">${esc(label)}<input class="inp" name="v" value="${esc(value)}" autofocus required></label></div>
      <div class="dlg-f"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn primary" value="ok">OK</button></div></form>`, 'small');
    const inp = d.querySelector('input'); inp.select();
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); if (inp.value.trim()) d.close('ok'); } });
    d.addEventListener('close', () => resolve(d.returnValue === 'ok' ? inp.value.trim() || null : null), { once: true });
  });
}
function confirmDialog(title, msg, ok = 'OK') {
  return new Promise((resolve) => {
    const d = openDialog(`<form method="dialog"><div class="dlg-h"><b>${esc(title)}</b></div><div class="dlg-b">${esc(msg)}</div>
      <div class="dlg-f"><button class="btn" value="cancel">Cancel</button><button class="btn primary" value="ok" autofocus>${esc(ok)}</button></div></form>`, 'small');
    d.addEventListener('close', () => resolve(d.returnValue === 'ok'), { once: true });
  });
}

let toastT = 0;
function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, err ? 5000 : 2200);
}

// ---------------------------------------------------------------- lineup management
function uniqueLineupId(base) {
  let id = slugify(base), n = 2;
  while (S.song.lineups[id]) id = `${slugify(base)}-${n++}`;
  return id;
}
function selectLineup(id) { S.lineupId = id; pref.set('lineup:' + S.slug, id); S.highlight = null; render(); runValidate(); }

async function newLineup() {
  const name = await promptDialog('New lineup', 'Name', 'Full band'); if (!name) return;
  const id = uniqueLineupId(name);
  mutate(() => { S.song.lineups[id] = { name, performers: [], assignments: {} }; S.lineupId = id; });
  selectLineup(id);
}
async function duplicateLineup() {
  const src = lineup();
  const name = await promptDialog('Duplicate lineup', 'Name', src.name + ' copy'); if (!name) return;
  const id = uniqueLineupId(name);
  mutate(() => { S.song.lineups[id] = { ...clone(src), name }; S.lineupId = id; });
  selectLineup(id);
}
async function renameLineup() {
  const name = await promptDialog('Rename lineup', 'Name', lineup().name); if (!name) return;
  mutate(() => { lineup().name = name; });
}
async function deleteLineup() {
  const ids = Object.keys(S.song.lineups);
  if (ids.length < 2) { toast('A song needs at least one lineup'); return; }
  if (!(await confirmDialog('Delete lineup', `Delete “${lineup().name}”? (⌘Z undoes it)`, 'Delete'))) return;
  const gone = S.lineupId;
  mutate(() => { delete S.song.lineups[gone]; S.lineupId = Object.keys(S.song.lineups)[0]; });
  selectLineup(S.lineupId);
}
function suggestDialog() {
  const perfs = S.band?.performers || [];
  if (!perfs.length) { toast('Add performers on the Band tab first'); return; }
  const pre = new Set(lineup().performers.length ? lineup().performers : perfs.map((p) => p.id));
  const d = openDialog(`<form method="dialog"><div class="dlg-h"><b>Suggest a lineup</b><span class="hint">pick who's playing; the engine assigns parts to their rigs</span></div>
    <div class="dlg-b"><div class="perf-pick">${perfs.map((p) => `<label><input type="checkbox" name="p" value="${esc(p.id)}" ${pre.has(p.id) ? 'checked' : ''}>
      <b>${esc(p.name)}</b><span class="roles">${esc((p.rigs || []).map((r) => r.kind).join(' · '))}</span></label>`).join('')}</div>
      <div id="sg-err"></div></div>
    <div class="dlg-f"><span id="sg-n" class="hint" style="margin-right:auto"></span><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn primary" id="sg-go" type="button">Suggest</button></div></form>`, 'small');
  const upd = () => { const n = $$('input[name=p]:checked', d).length; $('#sg-n', d).textContent = `${n} performer${n === 1 ? '' : 's'}`; $('#sg-go', d).textContent = `Suggest for ${n}`; };
  d.addEventListener('change', upd); upd();
  $('#sg-go', d).addEventListener('click', async () => {
    const ids = $$('input[name=p]:checked', d).map((i) => i.value);
    if (!ids.length) return;
    $('#sg-go', d).disabled = true;
    try {
      const { lineup: lu } = await api('POST', `/songs/${S.slug}/suggest`, { performers: ids, song: S.song, band: S.band });
      const name = lu.name || `Suggested · ${ids.map((i) => bandPerf(i)?.name || i).join(' + ')}`;
      const id = uniqueLineupId(lu.id || name);
      const nl = { ...lu, name, performers: lu.performers || ids, assignments: lu.assignments || {} };
      delete nl.id;
      closeDialog();
      mutate(() => { S.song.lineups[id] = nl; S.lineupId = id; });
      selectLineup(id);
      toast(`Created “${name}”`);
    } catch (e) {
      $('#sg-go', d).disabled = false;
      $('#sg-err', d).innerHTML = `<div class="err-box">${esc(e.status === 501 ? 'The lineup engine (lls/lineup.py) isn\'t installed yet, so suggestions are unavailable.' : e.message)}</div>`;
    }
  });
}
function lineupMenu(el) {
  menuAt(el, [
    { header: 'Lineup' },
    { label: 'New lineup…', action: newLineup },
    { label: `Duplicate “${lineup().name}”…`, action: duplicateLineup },
    { label: 'Rename…', action: renameLineup },
    { label: 'Delete…', action: deleteLineup, disabled: Object.keys(S.song.lineups).length < 2 },
    '-',
    { label: 'Suggest for N performers…', action: suggestDialog },
  ]);
}

// ---------------------------------------------------------------- build
function reportHtml(rep) {
  if (rep == null) return '';
  if (typeof rep === 'string') return `<pre class="md">${esc(rep)}</pre>`;
  if (Array.isArray(rep)) {
    return `<table class="report">${rep.map((r) => {
      if (Array.isArray(r)) return `<tr>${r.map((c, i) => `<td>${i === 1 ? chip(c, 'tabindex="-1"', 'mini') : esc(c)}</td>`).join('')}</tr>`;
      if (r && typeof r === 'object') {
        const name = r.name ?? r.part ?? '', t = r.treatment ?? r.status ?? '', note = r.note ?? r.message ?? r.detail ?? '';
        return `<tr><td>${esc(name)}</td><td>${TLABEL[t] ? chip(t, 'tabindex="-1"', 'mini') : esc(t)}</td><td>${esc(note || JSON.stringify(r))}</td></tr>`;
      }
      return `<tr><td colspan="3">${esc(r)}</td></tr>`;
    }).join('')}</table>`;
  }
  return `<pre class="md">${esc(JSON.stringify(rep, null, 2))}</pre>`;
}

async function doBuild() {
  if (!S.song) return;
  if (dirty() || bandDirty()) { await save(); if (dirty() || bandDirty()) return; }
  const head = `<div class="dlg-h"><b>Build · ${esc(S.song.title)}</b><span class="hint">lineup “${esc(lineup().name)}”</span></div>`;
  const foot = (extra = '') => `<div class="dlg-f">${extra}<button class="btn" data-act="close-dlg">Close</button></div>`;
  openDialog(head + `<div class="dlg-b"><div class="frow"><span class="spin"></span> Building the Live set…</div></div>` + foot());
  try {
    const r = await api('POST', `/songs/${S.slug}/build`, { lineup_id: S.lineupId });
    S.lastAls = r.als;
    openDialog(head + `<div class="dlg-b">
        ${r.als ? `<div class="lbl">Live set</div><div class="path">${esc(r.als)}</div>` : ''}
        <div class="lbl">Report</div>${reportHtml(r.report)}
        ${r.accounted_for ? `<details><summary class="lbl" style="cursor:pointer">accounted-for.md</summary><pre class="md">${esc(r.accounted_for)}</pre></details>` : ''}
      </div>` + foot(r.als ? '<button class="btn primary" data-act="open-live" style="margin-right:auto">Open in Live</button>' : ''));
  } catch (e) {
    const msg = e.status === 501 ? 'The lineup engine (lls/lineup.py) isn\'t installed yet, so lineup-aware builds are unavailable.' : e.message;
    openDialog(head + `<div class="dlg-b"><div class="err-box">${esc(msg)}</div></div>` + foot());
  }
}
async function openInLive() {
  try { await api('POST', '/open-in-live', { als: S.lastAls }); toast('Opening in Ableton Live…'); } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- events
function onAct(act, el, ev) {
  const pid = el.dataset.part;
  switch (act) {
    case 'tmenu': {
      const p = partById(pid);
      menuAt(el, treatmentItems(lineupT(p), (t) => mutate(() => setLineupT(pid, t)), `${p.name} · ${lineup().name}`));
      break;
    }
    case 'split': if (secById(S.selSec)) splitSection(S.selSec); break;
    case 'merge': if (secById(S.selSec)) mergeSections(S.selSec, 1); break;
    case 'vamp': if (secById(S.selSec)) mutate(() => { const s = secById(S.selSec); s.vamp = !s.vamp; }); break;
    case 'suggest': suggestDialog(); break;
    case 'ptoggle': togglePerformer(el.dataset.perf); break;
    case 'save': save(); break;
    case 'close-dlg': closeDialog(); break;
    case 'open-live': openInLive(); break;
    case 'add-perf': bandEdit(() => S.band.performers.push({ id: `player-${S.band.performers.length + 1}`, name: 'New player', roles: [], rigs: [] })); break;
    case 'del-perf': bandEdit(() => S.band.performers.splice(+el.dataset.pi, 1)); break;
    case 'add-rig': bandEdit(() => { const p = S.band.performers[+el.dataset.pi]; (p.rigs ||= []).push({ id: `${p.id}-rig-${p.rigs.length + 1}`, kind: 'keys', controller: '', midi_port: null, midi_channel: null }); }); break;
    case 'del-rig': bandEdit(() => S.band.performers[+el.dataset.pi].rigs.splice(+el.dataset.ri, 1)); break;
    case 'add-io': bandEdit(() => { const o = S.band.interface[el.dataset.io]; let k = 'new', n = 2; while (k in o) k = `new-${n++}`; o[k] = el.dataset.io === 'outputs' ? [] : null; }); break;
    case 'del-io': bandEdit(() => { delete S.band.interface[el.dataset.io][el.dataset.key]; }); break;
    default: break;
  }
  void ev;
}

function togglePerformer(perfId) {
  const lu = lineup();
  if (lu.performers.includes(perfId) || Object.values(lu.assignments).some((a) => a.performer === perfId)) {
    let moved = 0;
    mutate(() => {
      lu.performers = lu.performers.filter((p) => p !== perfId);
      for (const [pid, a] of Object.entries(lu.assignments)) if (a.performer === perfId) { toPlayback(pid); moved++; }
    });
    if (moved) toast(`Moved ${moved} part${moved === 1 ? '' : 's'} to playback (⌘Z to undo)`);
  } else mutate(() => lu.performers.push(perfId));
}

function bandEdit(fn) { fn(); renderMain(); renderTop(); scheduleValidate(); }

function setPath(obj, path, val) {
  const ks = path.split('.'); let o = obj;
  for (const k of ks.slice(0, -1)) o = o[k];
  const last = ks[ks.length - 1];
  if (val === undefined) delete o[last]; else o[last] = val;
}

function onBandInput(el) {
  const t = el.dataset.type, v = el.value;
  let val = v;
  if (t === 'list') val = v.split(',').map((s) => s.trim()).filter(Boolean);
  else if (t === 'null') val = v.trim() || null;
  else if (t === 'opt') val = v.trim() ? v : undefined;
  else if (t === 'ch') { const n = parseInt(v, 10); val = v.trim() === '' ? null : Number.isFinite(n) ? n : v; }
  else if (t === 'io') { const p = parseIo(v); val = p === null ? undefined : p; }
  else if (t === 'slug') val = v.trim();
  setPath(S.band, el.dataset.bp, val);
  renderTop(); scheduleValidate();
}

function onIoChange(el) {
  const kind = el.dataset.iokey || el.dataset.ioval, key = el.dataset.key, o = S.band.interface[kind];
  if (el.dataset.ioval) { o[key] = parseIo(el.value); bandEdit(() => {}); return; }
  const nk = el.value.trim();
  if (!nk || nk === key || nk in o) { el.value = key; return; }
  S.band.interface[kind] = Object.fromEntries(Object.entries(o).map(([k, v]) => [k === key ? nk : k, v]));
  bandEdit(() => {});
}

function highlight(part, section) {
  const same = S.highlight && S.highlight.part === (part || null) && S.highlight.section === (section || null);
  S.highlight = same ? null : { part: part || null, section: section || null };
  renderMain();
  if (!S.highlight) return;
  const target = part ? $(`.trow[data-part="${CSS.escape(part)}"], .card[data-part="${CSS.escape(part)}"]`)
    : section ? $(`.sec[data-sid="${CSS.escape(section)}"]`) : null;
  if (target) { target.scrollIntoView({ block: 'nearest', inline: 'nearest' }); target.classList.add('flash'); setTimeout(() => target.classList.remove('flash'), 1000); }
  if (section && S.view === 'arrange') { const c = $(`.cell[data-sid="${CSS.escape(section)}"]`); c?.scrollIntoView({ block: 'nearest', inline: 'center' }); }
}

function beatAt(lane, clientX) {
  const r = lane.getBoundingClientRect();
  return clamp((clientX - r.left) / r.width, 0, 1) * totalBeats();
}

function startBoundaryDrag(el, ev) {
  ev.preventDefault();
  const i = +el.dataset.i, secs = S.song.sections, bp = bpb();
  const before = JSON.stringify(S.song);
  const prev = secs[i - 1], cur = secs[i], joined = Math.abs(prev.end - cur.start) < 1e-6;
  const lo = prev.start + bp, hi = cur.end - bp;
  const tip = document.createElement('div'); tip.className = 'drag-tip'; document.body.appendChild(tip);
  el.classList.add('drag');
  document.body.style.cursor = 'ew-resize';
  const move = (e) => {
    const b = clamp(Math.round(beatAt($('#sec-lane'), e.clientX) / bp) * bp, lo, hi);
    tip.style.left = e.clientX + 12 + 'px'; tip.style.top = e.clientY + 12 + 'px';
    tip.textContent = `bar ${b / bp + 1} · ${prev.name} ${Math.round((b - prev.start) / bp)}b | ${cur.name} ${Math.round((cur.end - b) / bp)}b`;
    if (b !== cur.start) {
      cur.start = b; if (joined) prev.end = b;
      VIEWS.arrange.fill();
      $(`.bound[data-i="${i}"]`)?.classList.add('drag');
    }
  };
  const up = () => {
    removeEventListener('pointermove', move); removeEventListener('pointerup', up);
    tip.remove(); document.body.style.cursor = '';
    if (JSON.stringify(S.song) !== before) pushHist(before); else render();
  };
  addEventListener('pointermove', move); addEventListener('pointerup', up);
  move(ev);
}

function commitRename(save = true) {
  const inp = $('#rename'), sid = S.renaming;
  if (!inp || !sid) return;
  const v = inp.value.trim();
  S.renaming = null;
  if (save && v && v !== secById(sid).name) mutate(() => { secById(sid).name = v; }); else renderMain();
  $('#main').focus({ preventScroll: true });
}

function sectionMenu(sid, x, y, atBeat) {
  const s = secById(sid), i = S.song.sections.indexOf(s);
  S.selSec = sid; renderMain();
  openMenu(x, y, [
    { header: s.name },
    { label: 'Rename', key: '↵', action: () => { S.renaming = sid; renderMain(); } },
    { label: 'Split here', key: '⌥click', action: () => splitSection(sid, atBeat) },
    { label: 'Split in half', key: 'S', action: () => splitSection(sid) },
    { label: 'Merge with next', key: 'M', disabled: i === S.song.sections.length - 1, action: () => mergeSections(sid, 1) },
    { label: 'Merge with previous', disabled: i === 0, action: () => mergeSections(sid, -1) },
    { label: s.vamp ? 'Vamp: on' : 'Vamp: off', key: 'V', cur: !!s.vamp, action: () => mutate(() => { s.vamp = !s.vamp; }) },
    { label: 'Highlight in checks', action: () => highlight(null, sid) },
  ]);
}

function cellMenu(pid, sid, x, y) {
  const p = partById(pid), s = secById(sid), has = !!asg(pid)?.sections?.[sid];
  openMenu(x, y, [
    ...treatmentItems(effT(p, sid), (t) => mutate(() => setOverride(pid, sid, t)), `${p.name} · ${s.name}`),
    '-',
    { label: 'Clear override', disabled: !has, key: '⌥click', action: () => mutate(() => setOverride(pid, sid, null)) },
  ]);
}

function dropTypes(ev) { return ev.dataTransfer?.types || []; }

function wire() {
  const main = $('#main');

  document.addEventListener('click', (ev) => {
    const mi = ev.target.closest('.mi');
    if (mi && menuState) { const it = menuState.items[+mi.dataset.mi]; closeMenu(); it.action?.(); return; }
    const a = ev.target.closest('[data-act]');
    if (a && a.dataset.act !== 'noop') { ev.stopPropagation(); onAct(a.dataset.act, a, ev); }
  });
  document.addEventListener('mousedown', (ev) => { if (menuState && !ev.target.closest('#menu')) closeMenu(); });

  main.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-act]')) return;
    const sec = ev.target.closest('.sec');
    if (sec && !ev.target.closest('input')) {
      if (ev.altKey) splitSection(sec.dataset.sid, beatAt($('#sec-lane'), ev.clientX));
      else if (ev.detail >= 2) { S.renaming = sec.dataset.sid; S.selSec = sec.dataset.sid; renderMain(); }
      else if (S.selSec !== sec.dataset.sid) { S.selSec = sec.dataset.sid; renderMain(); }
      return;
    }
    const cell = ev.target.closest('.cell');
    if (cell && ev.altKey) { mutate(() => setOverride(cell.dataset.part, cell.dataset.sid, null)); return; }
    const card = ev.target.closest('.card');
    if (card) { highlight(card.dataset.part, null); return; }
    const ph = ev.target.closest('.ph[data-part]');
    if (ph) highlight(ph.dataset.part, null);
  });
  main.addEventListener('contextmenu', (ev) => {
    const sec = ev.target.closest('.sec'), cell = ev.target.closest('.cell'), ph = ev.target.closest('.ph[data-part]'), crd = ev.target.closest('.card');
    if (sec) { ev.preventDefault(); sectionMenu(sec.dataset.sid, ev.clientX, ev.clientY, beatAt($('#sec-lane'), ev.clientX)); }
    else if (cell) { ev.preventDefault(); cellMenu(cell.dataset.part, cell.dataset.sid, ev.clientX, ev.clientY); }
    else if (ph || crd) {
      ev.preventDefault();
      const pid = (ph || crd).dataset.part, p = partById(pid);
      openMenu(ev.clientX, ev.clientY, treatmentItems(lineupT(p), (t) => mutate(() => setLineupT(pid, t)), `${p.name} · ${lineup().name}`));
    }
  });
  main.addEventListener('pointerdown', (ev) => { const b = ev.target.closest('.bound'); if (b && ev.button === 0) startBoundaryDrag(b, ev); });
  main.addEventListener('mousemove', (ev) => {
    const g = $('#split-guide'); if (!g) return;
    const lane = ev.target.closest('#sec-lane');
    if (lane && ev.altKey) {
      const bp = bpb(), b = Math.round(beatAt(lane, ev.clientX) / bp) * bp;
      g.style.left = (b / totalBeats()) * 100 + '%'; g.classList.add('on');
    } else g.classList.remove('on');
  });
  main.addEventListener('input', (ev) => {
    if (ev.target.id === 'zoom') { S.zoom = +ev.target.value; VIEWS.arrange.fill(); return; }
    if (ev.target.dataset.bp) onBandInput(ev.target);
  });
  main.addEventListener('change', (ev) => {
    if (ev.target.dataset.iokey || ev.target.dataset.ioval) onIoChange(ev.target);
    else if (ev.target.dataset.bp && ev.target.tagName === 'SELECT') onBandInput(ev.target);
  });
  main.addEventListener('keydown', (ev) => {
    if (ev.target.id !== 'rename') return;
    if (ev.key === 'Enter') { ev.preventDefault(); commitRename(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); commitRename(false); }
    ev.stopPropagation();
  });
  main.addEventListener('focusout', (ev) => { if (ev.target.id === 'rename') setTimeout(() => commitRename(true), 0); });

  // drag & drop
  main.addEventListener('dragstart', (ev) => {
    const t = ev.target.closest('[data-drag-t]'), card = ev.target.closest('[data-dragpart]');
    if (t) { ev.dataTransfer.setData('application/x-lls-treatment', t.dataset.dragT); ev.dataTransfer.setData('text/plain', t.dataset.dragT); ev.dataTransfer.effectAllowed = 'copy'; }
    else if (card) { ev.dataTransfer.setData('application/x-lls-part', card.dataset.dragpart); ev.dataTransfer.setData('text/plain', card.dataset.dragpart); ev.dataTransfer.effectAllowed = 'move'; card.classList.add('dragging'); }
  });
  main.addEventListener('dragend', () => { $$('.dragging, .dropover').forEach((e) => e.classList.remove('dragging', 'dropover')); });
  const target = (ev) => {
    const ty = dropTypes(ev);
    if (ty.includes('application/x-lls-treatment')) return ev.target.closest('.cell, .ph[data-droppart]');
    if (ty.includes('application/x-lls-part')) return ev.target.closest('.zone[data-drop]');
    return null;
  };
  main.addEventListener('dragover', (ev) => {
    const el = target(ev); if (!el) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = dropTypes(ev).includes('application/x-lls-part') ? 'move' : 'copy';
    if (!el.classList.contains('dropover')) { $$('.dropover').forEach((e) => e.classList.remove('dropover')); el.classList.add('dropover'); }
  });
  main.addEventListener('dragleave', (ev) => { const el = target(ev); if (el && !el.contains(ev.relatedTarget)) el.classList.remove('dropover'); });
  main.addEventListener('drop', (ev) => {
    const el = target(ev); if (!el) return;
    ev.preventDefault();
    el.classList.remove('dropover');
    const t = ev.dataTransfer.getData('application/x-lls-treatment'), pid = ev.dataTransfer.getData('application/x-lls-part');
    if (t && el.classList.contains('cell')) mutate(() => setOverride(el.dataset.part, el.dataset.sid, t));
    else if (t) mutate(() => setLineupT(el.dataset.part, t));
    else if (pid) dropPart(pid, el);
  });

  // side panel
  $('#side-body').addEventListener('click', (ev) => {
    const it = ev.target.closest('[data-ipart]'); if (!it) return;
    highlight(it.dataset.ipart || null, it.dataset.isec || null);
  });
  $('#side-body').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && ev.target.dataset.ipart !== undefined) ev.target.click(); });
  for (const b of $$('.side-tabs button')) b.addEventListener('click', () => { S.side = b.dataset.side; pref.set('side', S.side); renderTop(); renderSide(); });

  // top bar
  for (const b of $$('.tabs button')) b.addEventListener('click', () => setView(b.dataset.view));
  $('#song-select').addEventListener('change', async (ev) => {
    if (dirty() && !(await confirmDialog('Unsaved changes', `Discard unsaved changes to “${S.song.title}”?`, 'Discard'))) { ev.target.value = S.slug; return; }
    loadSong(ev.target.value);
  });
  $('#lineup-select').addEventListener('change', (ev) => selectLineup(ev.target.value));
  $('#lineup-menu').addEventListener('click', (ev) => lineupMenu(ev.currentTarget));
  $('#undo').addEventListener('click', undo);
  $('#redo').addEventListener('click', redo);
  $('#save').addEventListener('click', save);
  $('#build').addEventListener('click', doBuild);
  $('#theme').addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  $('#help').addEventListener('click', showHelp);

  document.addEventListener('keydown', onKey);
  document.addEventListener('keyup', (ev) => { if (ev.key === 'Alt') document.body.classList.remove('alt'); });
  addEventListener('blur', () => document.body.classList.remove('alt'));
  addEventListener('beforeunload', (ev) => { if (dirty() || bandDirty()) { ev.preventDefault(); ev.returnValue = ''; } });
  let raf = 0;
  new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { if (S.song && S.view === 'arrange' && $('#tl')) VIEWS.arrange.fill(); }); }).observe(main);
}

function dropPart(pid, zone) {
  if (zone.dataset.drop === 'rig') mutate(() => assignToRig(pid, zone.dataset.perf, zone.dataset.rig));
  else mutate(() => toPlayback(pid, zone.dataset.t || null));
}

function setView(v) {
  S.view = v; pref.set('view', v); S.renaming = null; closeMenu();
  history.replaceState(null, '', '#' + v);
  render();
}

function applyTheme(t) { document.documentElement.dataset.theme = t; pref.set('theme', t); }

function showHelp() {
  const m = isMac ? '⌘' : 'Ctrl';
  const rows = [[`${m} S`, 'Save song + band'], [`${m} Z / ⇧${m} Z`, 'Undo / redo'], [`${m} B`, 'Build the Live set'], ['1 2 3', 'Arrange / Lineup / Band'],
    ['← →', 'Select section'], ['Enter', 'Rename section'], ['S', 'Split section (or ⌥-click at a bar)'], ['M', 'Merge with next'], ['V', 'Toggle vamp'],
    ['Drag chip → cell', 'Override one section'], ['⌥-click cell', 'Clear override'], ['Right-click', 'Context menus everywhere'],
    ['Card + 1–9 / 0', 'Assign focused card to a rig zone / Playback'], ['Esc', 'Clear selection / close'], ['?', 'This help']];
  openDialog(`<div class="dlg-h"><b>Shortcuts</b></div><div class="dlg-b"><div class="keys">${rows.map(([k, d]) => `<kbd>${esc(k)}</kbd><span>${esc(d)}</span>`).join('')}</div></div>
    <div class="dlg-f"><button class="btn" data-act="close-dlg">Close</button></div>`, 'small');
}

function onKey(ev) {
  if (ev.key === 'Alt') document.body.classList.add('alt');
  const mod = isMac ? ev.metaKey : ev.ctrlKey;
  if (mod && ev.key.toLowerCase() === 's') { ev.preventDefault(); save(); return; }
  if (mod && ev.key.toLowerCase() === 'b') { ev.preventDefault(); doBuild(); return; }
  if (menuState) {
    const items = $$('#menu .mi:not([disabled])'), i = items.indexOf(document.activeElement);
    if (ev.key === 'Escape') { ev.preventDefault(); closeMenu(); }
    else if (ev.key === 'ArrowDown') { ev.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (/^[0-9]$/.test(ev.key)) {
      const it = menuState.items.find((x) => x.key === ev.key && !x.disabled);
      if (it) { ev.preventDefault(); closeMenu(); it.action(); }
    }
    return;
  }
  if ($('#dlg').open) return;
  const typing = ev.target.closest?.('input, textarea, select, [contenteditable]');
  if (mod && ev.key.toLowerCase() === 'z' && !typing) { ev.preventDefault(); ev.shiftKey ? redo() : undo(); return; }
  if (typing || mod || ev.ctrlKey || ev.metaKey) return;
  if (!S.song) return;
  const card = ev.target.closest?.('.card');
  if (card && /^[0-9]$/.test(ev.key)) {
    ev.preventDefault();
    if (ev.key === '0') { dropPart(card.dataset.part, { dataset: { drop: 'playback' } }); return; }
    const z = $(`.zone[data-zone="${ev.key}"]`);
    if (z) dropPart(card.dataset.part, z);
    return;
  }
  if (card && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); $('.chip', card).click(); return; }
  if (['1', '2', '3'].includes(ev.key)) { setView(['arrange', 'lineup', 'band'][+ev.key - 1]); return; }
  if (ev.key === '?') { showHelp(); return; }
  if (ev.key === 'Escape') { S.selSec = null; S.highlight = null; renderMain(); return; }
  if (S.view !== 'arrange') return;
  const secs = S.song.sections, i = secs.findIndex((s) => s.id === S.selSec);
  if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
    ev.preventDefault();
    const ni = i < 0 ? 0 : clamp(i + (ev.key === 'ArrowRight' ? 1 : -1), 0, secs.length - 1);
    S.selSec = secs[ni].id; renderMain();
    $(`.sec[data-sid="${CSS.escape(S.selSec)}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    return;
  }
  if (i < 0) return;
  const k = ev.key.toLowerCase();
  if (ev.key === 'Enter' || ev.key === 'F2') { ev.preventDefault(); S.renaming = S.selSec; renderMain(); }
  else if (k === 's') splitSection(S.selSec);
  else if (k === 'm') mergeSections(S.selSec, 1);
  else if (k === 'v') mutate(() => { const s = secById(S.selSec); s.vamp = !s.vamp; });
}

wire();
init();
