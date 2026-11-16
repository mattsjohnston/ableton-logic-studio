// LLS band-set editor. Vanilla ES module, no build step. Data contract: docs/model.md.
import { Preview } from './preview.js';
import { initTips, tipHtml } from './tip.js';

const $ =(s, el = document) => el.querySelector(s);
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
const TDESC = {
  'live-vocal': 'Sung live into a mic on a vocal rig.', 'live-midi': 'Played live on a keyboard rig through the part’s instrument in Live.',
  'live-looped': 'Triggered live from a grid controller.', 'live-drums': 'Played live on an e-kit through a Drum Rack.',
  'live-guitar': 'Played live on guitar/bass through an interface input.', 'backing-audio': 'The laptop plays the exported stem.',
  'audio-loop': 'The laptop plays the stem as a looping audio clip.', 'midi-clip': 'The laptop plays the MIDI through the instrument in Live (Analog Lab / Drum Rack only).',
  click: 'Replaced by the generated click.', dropped: 'Left out of the set.',
};
const LIVE = new Set(['live-vocal', 'live-midi', 'live-looped', 'live-drums', 'live-guitar']);
const RIG_KINDS = ['keys', 'drums', 'guitar', 'bass', 'vocal', 'grid'];
const RIG_ICON = { keys: '🎹', drums: '🥁', guitar: '🎸', bass: '🎸', vocal: '🎤', grid: '▦' };
const SEC_COLORS = ['#ff4fa3', '#b452ff', '#4f7dff', '#27d7ff', '#2ee6c0', '#ffb23d', '#ff8a3d', '#8c7dff'];
const PLAYBACK_ZONES = [['backing-audio', 'Backing audio'], ['midi-clip', 'MIDI clips'], ['audio-loop', 'Audio loops'], ['click', 'Click'], ['dropped', 'Dropped']];
const LAPTOP = new Set(['backing-audio', 'audio-loop', 'midi-clip']);   // what the laptop plays (= preview "Lineup backing")
const VIEW_IDS = ['arrange', 'lineup', 'band', 'setlists', 'import'];
const SONG_VIEWS = new Set(['arrange', 'lineup', 'band']);
// Logic export steps (README "Export from Logic"): shown as a checklist on the Import view.
const EXPORT_STEPS = [
  ['copy', 'Work on a copy of the project, never the original (the importer also clones it before reading).'],
  ['tracks', 'File → Export → All Tracks as Audio Files…: WAVE, 24-bit, Normalize off, Bypass Effect Plug-ins off, Include Volume/Pan Automation on, range “Extend File Length to Project End”.'],
  ['mix', 'File → Bounce → Project: the full mix, 24-bit WAV.'],
  ['aux', 'Aux returns (reverb/delay): create tracks for the aux strips and export them too, otherwise the backing loses its send FX.'],
  ['folder', 'Put everything in one folder, then choose it below as the export folder.'],
];

const pref = {
  get(k, d) { try { const v = localStorage.getItem('lls.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('lls.' + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

const S = {
  songs: [], slug: null, song: null, saved: '', band: null, bandSaved: '', activity: null,
  lineupId: 'default', view: VIEW_IDS.includes(location.hash.slice(1)) ? location.hash.slice(1) : pref.get('view', 'arrange'), side: pref.get('side', 'checks'),
  selSec: null, renaming: null, highlight: null, zoom: 0,
  hist: [], fut: [], issues: [], vState: 'idle', vMsg: '', shellKey: '',
  imp: null,                                                   // Import view state
  sls: [], slSlug: pref.get('setlist', null), sl: null, slSaved: '', slJob: null, slResult: null, slErr: '',
  pv: { mode: pref.get('pv.mode', 'lineup'), click: pref.get('pv.click', false), vol: pref.get('pv.vol', 0.8),
        loop: null, solo: new Set(), mute: new Set(), state: {},
        cues: false, cuesVol: pref.get('pv.cuesVol', 0.9), cue: null, cueSig: '', busy: null },
  eng: pref.get('engine', 'browser') === 'live' ? 'live' : 'browser',   // transport engine: browser preview | Ableton Live
  live: { st: null, err: '', at: 0, cursor: 0, loop: null, launch: null, opening: false, sig: '', rowSig: '' },
  tlW: 0,
};
if (S.view === 'import') S.view = 'arrange';  // the import view needs a mode; open it from the song picker

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
function changed() { render(); scheduleValidate(); pvRestartSoon(); }

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
  if (slug) await loadSong(slug); else render();
  if (S.view === 'setlists') loadSetlists();
}

async function refreshSongs() { S.songs = await api('GET', '/songs'); renderTop(); }

async function loadSong(slug) {
  const [song, activity] = await Promise.all([api('GET', `/songs/${slug}`), api('GET', `/songs/${slug}/activity`).catch(() => null)]);
  if (slug !== S.slug) { PV.reset(); S.pv.solo.clear(); S.pv.mute.clear(); S.pv.loop = null; S.live.loop = null; S.live.cursor = 0; S.pv.cue = null; S.pv.cueSig = ''; S.pv.cues = !!(song.options || {}).cues; }
  S.slug = slug; S.song = normalize(song); S.saved = JSON.stringify(S.song); S.activity = activity;
  S.hist = []; S.fut = []; S.selSec = null; S.highlight = null; S.shellKey = '';
  const lids = Object.keys(S.song.lineups);
  const wantL = pref.get('lineup:' + slug, null);
  S.lineupId = lids.includes(wantL) ? wantL : lids[0];
  pref.set('song', slug);
  render(); runValidate(); lvSchedule(0);
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
  ss.innerHTML = (S.song ? '' : '<option value="" selected disabled>No song</option>')
    + S.songs.map((s) => `<option value="${esc(s.slug)}" ${s.slug === S.slug ? 'selected' : ''}>${esc(s.title)}</option>`).join('')
    + '<option disabled>──────────</option><option value="__new">+ New song…</option>'
    + (S.song ? `<option value="__reimport">↻ Re-import / Link stems…</option>` : '')
    + '<option disabled>──────────</option>'
    + (S.song ? '<option value="__pkg_export">⇪ Export song package…</option>' : '')
    + '<option value="__pkg_import">⇩ Import song package…</option>';
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
  const main = $('#main');
  if (SONG_VIEWS.has(S.view) && !S.song) {
    S.shellKey = '';
    main.innerHTML = `<div class="empty-state"><b>No songs yet.</b><p>Export your Logic project's stems, then import it here.</p>
      <button class="btn primary" data-act="new-song">+ New song</button></div>`;
    return;
  }
  const key = S.view + '|' + S.slug;
  const focusPart = document.activeElement?.closest?.('[data-part]')?.dataset.part;
  if (S.shellKey !== key) { main.innerHTML = VIEWS[S.view].shell(); S.shellKey = key; }
  VIEWS[S.view].fill();
  applyIssueMarkers();
  if (focusPart && S.view === 'lineup') $(`.card[data-part="${CSS.escape(focusPart)}"]`)?.focus({ preventScroll: true });
}

function chip(t, attrs = '', extra = '') {
  const tip = /data-tip/.test(attrs) ? '' : `data-tip-rich="chip" data-t="${esc(t)}"`;
  return `<button class="chip ${t === 'dropped' ? 'dropped' : ''} ${extra}" style="--tc:var(--t-${t})" ${tip} ${attrs}>${esc(TLABEL[t] || t)}</button>`;
}

function instBadge(part, tip = true) {
  if (!tip) return instBadge(part).replace(/ data-tip="[^"]*"/, '');
  const i = part.instrument || {};
  if (i.type === 'analoglab') return `<span class="badge analoglab" data-tip="Analog Lab V · ${esc(i.preset || '')}&#10;Plays in Live: the preset’s AU state is injected"><b>AL</b> ${esc(i.preset || '')}</span>`;
  if (i.type === 'drumrack') return `<span class="badge drumrack" data-tip="Drum Rack&#10;Built in Live from the part’s drum notes"><b>Rack</b> Drum Rack</span>`;
  if (i.type === 'logic-builtin') {
    const pre = i.preset ? ' · ' + String(i.preset).replace(/\.\w+$/, '') : '';
    return `<span class="badge logic" data-tip="Logic ${esc(i.name || '')}${esc(pre)}&#10;Logic-only instrument: Live can’t play it, so it needs a stem"><b>Logic</b> ${esc(i.name || '')}${esc(pre)}</span>`;
  }
  if (i.type === 'audio' || part.kind === 'audio') return `<span class="badge audio" data-tip="${esc(part.stem ? 'exported stem' : part.raw_stem ? 'raw stem (no FX)' : 'no stem yet')}"><b>Audio</b> ${part.stem ? 'stem' : part.raw_stem ? 'raw stem' : 'no stem'}</span>`;
  return `<span class="badge">${esc(i.type || part.kind || '?')}</span>`;
}

function whoLabel(pid, tip = true) {
  const a = asg(pid);
  if (!a?.performer || !LIVE.has(a.treatment)) return '';
  const p = bandPerf(a.performer);
  return `<span class="who" ${tip ? `data-tip="${esc((p?.name || a.performer) + ' · ' + (a.rig || ''))}"` : ''}>${esc(p?.name || a.performer)}</span>`;
}

// ---------- Arrange view
const VIEWS = {};
VIEWS.arrange = {
  shell: () => `
    <div class="toolbar">
      <span class="song-meta" id="meta"></span>
      <span class="sep"></span>
      <div class="palette" data-tip="Drag a treatment onto a part (whole song) or onto a cell (that section only)">
        ${TR.map(([t]) => chip(t, `draggable="true" data-drag-t="${t}" data-act="noop" tabindex="-1" data-tip-rich="chip" data-t="${t}" data-palette="1" data-tip-pos="below"`, 'mini')).join('')}
      </div>
      <span class="sep"></span>
      <button class="btn small" data-act="split" data-tip="Split section&#10;Splits the selected section in half (snaps to bars); ⌥-click a section to split at a bar" data-tip-key="S">Split</button>
      <button class="btn small" data-act="merge" data-tip="Merge with next&#10;Joins the selected section with the one after it" data-tip-key="M">Merge →</button>
      <button class="btn small" data-act="vamp" data-tip="Vamp&#10;The selected section’s scene loops until you launch the next one" data-tip-key="V">Vamp</button>
      <span class="sep"></span>
      <label class="hint" data-tip="Zoom&#10;Pixels per bar; 0 fits the song to the window">Zoom <input type="range" id="zoom" min="0" max="48" step="2" value="${S.zoom}"></label>
      <span class="sep"></span>
      <span class="lbl">Set</span>
      <button class="ptog" data-act="opt" data-opt="cues" data-tip="Spoken cues in the Live set&#10;A voice names each section one bar before it, on the click output (options.cues)&#10;Also the default for the preview’s Cues toggle">Cues</button>
      <button class="ptog" data-act="opt" data-opt="auto_advance" data-tip="Auto-advance&#10;Scenes follow on to the next section automatically; vamp sections hold (options.auto_advance)">Auto-advance</button>
      <span class="spacer" style="flex:1"></span>
      <button class="btn small" data-act="reimport" data-tip="Re-import&#10;Re-read the Logic project and/or link newly exported stems">↻ Re-import</button>
    </div>
    <div class="toolbar transport" id="transport"></div>
    <div class="toolbar inspector" id="inspector" hidden></div>
    <div class="tl" id="tl"><div class="tl-inner" id="tl-inner"></div></div>`,
  fill() {
    const song = S.song, bp = bpb(), total = totalBeats(), nb = Math.ceil(total / bp);
    const tl = $('#tl');
    const hdr = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--hdr')) || 268;
    const avail = Math.max(200, tl.clientWidth - hdr - 2);
    const W = S.zoom ? nb * S.zoom : Math.max(avail, nb * 5);
    const ppb = W / nb, pct = (b) => (b / total) * 100;
    S.tlW = W;
    $('#meta').innerHTML = `<b>${esc(song.title)}</b>${song.tempo} bpm · ${(song.time_signature || [4, 4]).join('/')} · ${nb} bars · ${song.sections.length} sections · ${song.parts.length} parts`;
    for (const b of $$('[data-act=split],[data-act=merge],[data-act=vamp]')) b.disabled = !secById(S.selSec);
    const opts = song.options || {};
    for (const b of $$('[data-act=opt]')) b.setAttribute('aria-pressed', String(!!opts[b.dataset.opt]));
    renderTransport(); renderInspector();

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
        : `<span class="sname">${esc(s.name)}</span>${s.vamp ? '<span class="vamp">∞</span>' : ''}${s.cue ? '<span class="cue-mk">❝</span>' : ''}${curLoop() === s.id ? '<span class="loop-mk">⟲</span>' : ''}<span class="sbars">${bars}b</span>`;
      secs += `<div class="sec ${S.selSec === s.id ? 'sel' : ''} ${hl.section === s.id ? 'hl' : ''}" data-sid="${esc(s.id)}"
        style="left:${pct(s.start)}%;width:${pct(s.end - s.start)}%;--sc:${SEC_COLORS[i % SEC_COLORS.length]}"
        ${S.renaming === s.id ? '' : 'data-tip-rich="sec" data-tip-pos="below"'}>${inner}</div>`;
      if (i > 0) secs += `<div class="bound" data-i="${i}" style="left:${pct(s.start)}%" data-tip="${esc(song.sections[i - 1].name)} | ${esc(s.name)} · bar ${s.start / bp + 1}&#10;Drag to move the boundary (snaps to bars)"></div>`;
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
          data-tip-rich="cell"><div class="bars">${bars}</div></div>`;
      }
      const [aud, smTip] = isLive() ? lvPartSM(p) : [pvAudible(p), pvAudible(p) ? `Preview solo / mute\n${synthOf(p) ? 'Browser synth (MIDI-only part)' : 'Stem playback'}` : `Not in the preview\n${pvWhyOff(p)}`];
      rows += `<div class="trow part ${hl.part === p.id ? 'hl' : ''} ${aud ? '' : 'pv-off'}" data-part="${esc(p.id)}">
        <div class="ph" data-part="${esc(p.id)}" data-droppart="1" data-tip-rich="part" data-tip-pos="right">
          <span class="issue-dot"></span>
          <div class="pmeta"><span class="pname">${esc(p.name)}</span><div class="prow2">${synthOf(p) ? `<span class="pv-syn">♪ synth preview</span><span class="pv-syn-i">${esc((p.instrument || {}).name || '')}</span>` : instBadge(p, false)}</div></div>
          ${whoLabel(p.id, false)}
          <span class="sm" data-tip="${esc(smTip)}"><button class="smb ${S.pv.solo.has(p.id) ? 'on' : ''}" data-act="solo" data-part="${esc(p.id)}" tabindex="-1" aria-label="Solo ${esc(p.name)}">S</button><button class="smb m ${S.pv.mute.has(p.id) ? 'on' : ''}" data-act="mute" data-part="${esc(p.id)}" tabindex="-1" aria-label="Mute ${esc(p.name)}">M</button></span>
          ${chip(lt, `draggable="true" data-drag-t="${lt}" data-act="tmenu" data-part="${esc(p.id)}" data-tip-rich="chip" data-t="${lt}"`)}
        </div>
        <div class="lane" style="width:${W}px">${cells}</div></div>`;
    }
    $('#tl-inner').innerHTML = `
      <div class="playhead" id="playhead"></div>
      <div class="trow head"><div class="ph">Bar</div><div class="lane" id="ruler" style="width:${W}px" data-tip="Ruler&#10;Click to move the playhead (playback jumps there)" data-tip-pos="below">${ruler}</div></div>
      <div class="trow secs"><div class="ph" data-tip="Sections&#10;⌥-click a section to split at a bar · drag edges to move · double-click to rename · right-click for more" data-tip-pos="below">Sections <span class="hint" style="margin-left:auto">${esc(lineup().name)}</span></div><div class="lane" id="sec-lane" style="width:${W}px">${secs}</div></div>
      ${rows}`;
    const ri = $('#rename');
    if (ri) { ri.focus(); ri.select(); }
    drawPlayhead();
  },
};

// ---------- preview playback (Web Audio; see preview.js)
const PV = new Preview((st) => { S.pv.state = st; if (S.view === 'arrange' && $('#transport')) renderTransport(); });
let TIP = null;
const spb = () => 60 / S.song.tempo;
const songSecs = () => totalBeats() * spb();
const stemOf = (p) => p.stem || p.raw_stem;
const synthOf = (p) => !stemOf(p) && !!p.midi;          // MIDI-only part: the preview plays its notes on a browser synth

function pvAudible(p) {
  if (!stemOf(p) && !synthOf(p)) return false;
  if (S.pv.mode === 'full') return true;
  return S.song.sections.some((s) => LAPTOP.has(effT(p, s.id)));
}
function pvWhyOff(p) {
  if (!stemOf(p) && !synthOf(p)) return p.kind === 'midi' ? 'No stem and no MIDI notes' : 'No stem: export it from Logic';
  return `Not played by the laptop in “${lineup().name}” (${TLABEL[lineupT(p)]})`;
}

// browser-synth voice for a MIDI-only part: drum voices by name (or GM pitch), else a lead/bass/pad/keys preset
const DRUMISH = /kick|snare|hat|tom|crash|cymbal|ride|shaker|clap|perc|rim/i;
function voiceFor(p) {
  const nm = `${p.name || ''} ${p.id}`, inst = p.instrument || {};
  if (p.role_hint === 'drums' || inst.name === 'Quick Sampler' || inst.type === 'drumrack' || DRUMISH.test(nm)) {
    const m = /kick|snare|open.?hat|hat|tom|crash|cymbal|ride|shaker|clap|rim/i.exec(nm);
    const kind = m ? m[0].toLowerCase().replace(/[^a-z]/g, '').replace('cymbal', 'crash') : null;
    return { drum: true, kind, size: /low|floor/i.test(nm) ? 'low' : /high|\bhi\b/i.test(nm) ? 'high' : 'mid' };
  }
  return { drum: false, kind: ['bass', 'lead', 'pad'].includes(p.role_hint) ? p.role_hint : 'keys' };
}
const NOTES = new Map();   // `${slug}/${pid}` -> Promise<notes | null>
function loadNotes(pid) {
  const k = `${S.slug}/${pid}`;
  if (!NOTES.has(k)) NOTES.set(k, api('GET', `/songs/${encodeURIComponent(S.slug)}/midi/${encodeURIComponent(pid)}`).then((r) => r.notes || []).catch(() => null));
  return NOTES.get(k);
}

// spoken cues: rendered server-side (lls.build.cues_wav); the signature tells when edits made the rendered file stale
const cueSig = () => JSON.stringify([S.song.tempo, S.song.time_signature, S.song.length_beats, S.song.sections.map((s) => [s.name, s.start, s.cue || ''])]);
const cuesStale = () => !S.pv.cue || S.pv.cueSig !== cueSig();
async function ensureCues() {
  const sig = cueSig();
  if (S.pv.cue && S.pv.cueSig === sig) return S.pv.cue;
  const r = await api('POST', `/songs/${encodeURIComponent(S.slug)}/cues.wav`, { song: S.song });
  if (S.pv.cue && S.pv.cue.hash !== r.hash) PV.forget('cues:' + S.pv.cue.hash);
  S.pv.cue = r; S.pv.cueSig = sig;
  return r;
}

function pvPlan() {
  const song = S.song, k = spb(), sources = [];
  const url = (pid) => `/api/songs/${encodeURIComponent(S.slug)}/stem/${encodeURIComponent(pid)}`;
  const anySM = S.pv.solo.size || S.pv.mute.size;
  if (S.pv.mode === 'full' && song.mix && !anySM) sources.push({ key: S.slug + '/_mix', url: url('_mix'), pid: '_mix', windows: null });
  else {
    for (const p of song.parts) {
      if (!stemOf(p)) continue;
      if (S.pv.mode === 'full') { sources.push({ key: `${S.slug}/${p.id}`, url: url(p.id), pid: p.id, windows: null }); continue; }
      const wins = song.sections.filter((s) => LAPTOP.has(effT(p, s.id))).map((s) => [s.start * k, s.end * k]);
      if (wins.length) sources.push({ key: `${S.slug}/${p.id}`, url: url(p.id), pid: p.id, windows: wins });
    }
  }
  const synths = [];
  if (!(S.pv.mode === 'full' && song.mix && !anySM)) {     // the mix bounce already contains the MIDI-only parts
    for (const p of song.parts) {
      if (!synthOf(p)) continue;
      const wins = S.pv.mode === 'full' ? null : song.sections.filter((s) => LAPTOP.has(effT(p, s.id))).map((s) => [s.start * k, s.end * k]);
      if (!wins || wins.length) synths.push({ pid: p.id, voice: voiceFor(p), windows: wins });
    }
  }
  const ls = S.pv.loop && secById(S.pv.loop);
  return { sources, synths, loop: ls ? { a: ls.start * k, b: ls.end * k } : null, tempo: song.tempo, bpb: bpb(), total: songSecs() };
}

function pvGain(pid) {
  if (pid === '_cues') return S.pv.cues ? S.pv.cuesVol : 0;
  if (pid === '_mix') return 1;
  if (S.pv.mute.has(pid)) return 0;
  return S.pv.solo.size && !S.pv.solo.has(pid) ? 0 : 1;
}

let pvTok = 0;
async function pvPlay(from = PV.pos()) {
  if (!S.song) return;
  const tok = ++pvTok, slug = S.slug, k = spb();
  const plan = pvPlan();
  if (!plan.sources.length && !plan.synths.length && !S.pv.cues) { toast(S.pv.mode === 'full' ? 'No stems or mix linked yet — Re-import → link stems' : 'Nothing in this lineup plays from the laptop (or no stems linked)'); return; }
  PV.setGains(pvGain); PV.setClick(S.pv.click); PV.setVolume(S.pv.vol);
  const needCues = S.pv.cues && cuesStale();
  if (needCues || plan.synths.some((x) => !NOTES.has(`${slug}/${x.pid}`))) { S.pv.busy = needCues ? 'cues' : 'notes'; renderTransport(); }
  const [cue, notes] = await Promise.all([
    S.pv.cues ? ensureCues().catch((e) => { toast('Cues: ' + e.message, true); return null; }) : null,
    Promise.all(plan.synths.map((x) => loadNotes(x.pid))),
  ]);
  if (tok !== pvTok || slug !== S.slug) return;          // stopped / restarted / switched song meanwhile
  S.pv.busy = null;
  if (cue) plan.sources.push({ key: 'cues:' + cue.hash, url: cue.url, pid: '_cues', windows: null });
  plan.synths = plan.synths.map((x, i) => notes[i] && notes[i].length ? {
    ...x, notes: notes[i].map((n) => [n[1] * k, Math.max(0.02, n[2] * k), n[0], n[3] ?? 100]).sort((a, b) => a[0] - b[0]),
  } : null).filter(Boolean);
  PV.play(from, plan);
  pvAnimate();
}
function pvToggle() {
  if (PV.playing || PV.preparing || S.pv.busy) { pvTok++; S.pv.busy = null; PV.stop(); renderTransport(); drawPlayhead(); } else pvPlay();
}
function pvFromSection(sid) { const s = secById(sid); if (s) pvPlay(s.start * spb()); }
function pvLoop(sid) {
  S.pv.loop = S.pv.loop === sid ? null : sid;
  if (S.pv.loop) { S.selSec = sid; pvPlay(secById(sid).start * spb()); } else if (PV.playing) pvPlay(PV.pos());
  renderMain();
}
let pvRestartT = 0;
function pvRestartSoon() {
  if (!PV.playing) return;
  clearTimeout(pvRestartT); pvRestartT = setTimeout(() => { if (PV.playing) pvPlay(PV.pos()); }, 150);
}
function pvSetCues(on) {
  S.pv.cues = on;
  const inPlan = PV.nodes.some((n) => n.pid === '_cues');
  if (PV.playing && on && (!inPlan || cuesStale())) pvPlay(PV.pos()); else PV.setGains(pvGain);
  renderTransport();
}
function pvSoloMute(kind, pid) {
  const set = S.pv[kind];
  if (set.has(pid)) set.delete(pid); else set.add(pid);
  if (isLive()) { lvMix(); renderMain(); return; }
  const needsStems = S.pv.mode === 'full' && S.song.mix;
  if (PV.playing && needsStems) pvPlay(PV.pos()); else PV.setGains(pvGain);
  renderMain();
}

let pvRaf = 0;
function pvAnimate() {
  cancelAnimationFrame(pvRaf);
  const step = () => { drawPlayhead(); updateClock(); if (tpPlaying() || PV.preparing) pvRaf = requestAnimationFrame(step); };
  pvRaf = requestAnimationFrame(step);
}
function drawPlayhead() {
  const ph = $('#playhead'); if (!ph || !S.song) return;
  const frac = clamp(tpPos() / songSecs(), 0, 1);
  ph.style.transform = `translateX(${(frac * S.tlW).toFixed(1)}px)`;
  ph.classList.toggle('on', tpPlaying());
  ph.classList.toggle('live', isLive());
}
function fmtPos(sec) {
  const beats = sec / spb(), bp = bpb();
  const bar = Math.floor(beats / bp) + 1, beat = Math.floor(beats % bp) + 1;
  return `${bar}.${beat} · ${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
}
function updateClock() { const c = $('#pv-clock'); if (c && S.song) c.textContent = fmtPos(tpPos()); }

const ENGINE_SEG = () => `<span class="lbl">Engine</span><div class="seg" role="group" aria-label="Engine">
      <button data-act="engine" data-eng="browser" aria-pressed="${!isLive()}" data-tip="Browser engine&#10;Previews stems and a simple synth in the browser (rehearsal aid)">Browser</button>
      <button data-act="engine" data-eng="live" aria-pressed="${isLive()}" data-tip="Live engine&#10;The transport drives Ableton Live through AbletonOSC: real instruments, stems and live inputs">Live</button>
    </div>
    <span class="sep"></span>`;

function renderTransport() {
  const el = $('#transport'); if (!el || !S.song) return;
  if (isLive()) { renderLiveTransport(el); return; }
  const st = S.pv.state || {}, plan = pvPlan(), sel = secById(S.selSec), lp = S.pv.loop && secById(S.pv.loop);
  const stems = plan.sources.filter((s) => s.pid !== '_cues');
  const loaded = stems.filter((s) => PV.status(s.key) === 'ok').length;
  const errs = plan.sources.filter((s) => PV.status(s.key) === 'err');
  const silent = S.song.parts.filter((p) => !stemOf(p) && !synthOf(p) && (S.pv.mode === 'full' || S.song.sections.some((s) => LAPTOP.has(effT(p, s.id)))));
  const nsyn = plan.synths.length;
  let status;
  if (S.pv.busy === 'cues') status = '<span class="spin sm"></span> Rendering cues…';
  else if (S.pv.busy) status = '<span class="spin sm"></span> Loading MIDI…';
  else if (st.preparing || st.loading) status = `<span class="spin sm"></span> Loading stems ${loaded}/${stems.length}…`;
  else if (!stems.length && !nsyn) status = S.pv.mode === 'full' ? 'No stems linked yet' : 'Nothing plays from the laptop in this lineup';
  else status = [stems.length === 1 && stems[0].pid === '_mix' ? 'full mix bounce' : stems.length ? `${stems.length} stem${stems.length === 1 ? '' : 's'}` : '',
    nsyn ? `<span class="syn" data-tip="${esc(`Synth preview · ${nsyn} MIDI-only part${nsyn === 1 ? '' : 's'}\n${plan.synths.map((x) => partById(x.pid)?.name || x.pid).join(', ')}\nNo stem exported, so the browser plays the notes on a simple synth. Live plays the real instrument.`)}">${nsyn} synth</span>` : '']
    .filter(Boolean).join(' · ') + (loaded < stems.length ? ` · ${loaded} loaded` : '');
  const warn = [errs.length ? `<span class="pv-err" data-tip="${esc(`Failed to load\n${errs.map((s) => `${s.pid}: ${PV.error(s.key)}`).join('\n')}`)}">⚠ ${errs.length} failed</span>` : '',
    silent.length ? `<span class="pv-warn" data-tip="${esc(`Silent in the preview\n${silent.map((p) => p.name).join(', ')}\nNo stem and no MIDI notes`)}">${silent.length} silent</span>` : ''].join('');
  const playing = PV.playing || st.preparing || !!S.pv.busy;
  const stale = S.pv.cues && S.pv.cue && cuesStale();
  el.innerHTML = `
    ${ENGINE_SEG()}
    <button class="btn icon tp ${playing ? 'on' : ''}" data-act="pv-toggle" data-tip="${playing ? 'Stop' : 'Play'}" data-tip-key="Space" aria-label="${playing ? 'Stop' : 'Play'}">${playing ? '■' : '▶'}</button>
    <button class="btn small" data-act="pv-from" ${sel ? '' : 'disabled'} data-tip="Play from the selected section" data-tip-key="P">▶ ${esc(sel ? sel.name : 'section')}</button>
    <button class="btn small ${lp ? 'act' : ''}" data-act="pv-loop" ${sel || lp ? '' : 'disabled'} data-tip="Loop the selected section&#10;Click again to stop looping" data-tip-key="L">⟲ ${lp ? 'Looping ' + esc(lp.name) : 'Loop'}</button>
    <button class="btn icon small" data-act="pv-zero" data-tip="Back to bar 1" data-tip-key="Home" aria-label="Back to start">⇤</button>
    <span class="pv-clock" id="pv-clock">${fmtPos(PV.pos())}</span>
    <span class="sep"></span>
    <div class="seg" role="group" aria-label="Preview mode">
      <button data-act="pv-mode" data-mode="lineup" aria-pressed="${S.pv.mode === 'lineup'}" data-tip="Lineup backing&#10;What the laptop plays in “${esc(lineup().name)}”: backing audio, audio loops and MIDI clips (stems, or the synth for MIDI-only parts)">Lineup backing</button>
      <button data-act="pv-mode" data-mode="full" aria-pressed="${S.pv.mode === 'full'}" data-tip="Full song&#10;The full-mix bounce if linked (stems when soloing/muting), else every stem + synth">Full song</button>
    </div>
    <button class="ptog" data-act="pv-click" aria-pressed="${S.pv.click}" data-tip="Click&#10;Metronome at the song tempo, accent on the downbeat">Click</button>
    <span class="pv-cues"><button class="ptog ${stale ? 'stale' : ''}" data-act="pv-cues" aria-pressed="${S.pv.cues}" data-tip="${esc(`Spoken cues in the preview\nThe voice names each section one bar before it (macOS say, as in the Live set)\n${stale ? 'Sections changed: re-rendered on the next play' : S.pv.cue ? 'Rendered and cached' : 'Rendered on first play'} · song default ${(S.song.options || {}).cues ? 'on' : 'off'}`)}">Cues</button><input type="range" id="pv-cvol" min="0" max="1.5" step="0.05" value="${S.pv.cuesVol}" aria-label="Cue volume" data-tip="Cue volume" ${S.pv.cues ? '' : 'disabled'}></span>
    <label class="hint" data-tip="Preview volume">Vol <input type="range" id="pv-vol" min="0" max="1" step="0.05" value="${S.pv.vol}"></label>
    ${S.pv.solo.size || S.pv.mute.size ? `<button class="btn small" data-act="pv-clear" data-tip="Clear solo / mute">Clear S/M</button>` : ''}
    <span class="pv-status">${status}${warn}</span>
    <span class="hint pv-note" data-tip="Rehearsal aid only&#10;On stage the set runs in Ableton Live.">preview · Live runs the show</span>`;
}

// ---------- Live engine: the transport drives Ableton Live through AbletonOSC (server: /api/live/*, lls/live_engine.py)
// Session View: scene i = section i. Launching a scene is "play from"; its clips loop until another scene is launched,
// so "loop" only means "don't move on". Position = section start + beats into the playing Click clip (from Live).
const LV = S.live;
const isLive = () => S.eng === 'live';
const curLoop = () => (isLive() ? LV.loop : S.pv.loop);
const lvConnected = () => !!LV.st?.connected;
const lvSongOk = () => !!LV.st?.song?.scenes_match && LV.st.song.slug === S.slug;
const lvSetOk = () => lvSongOk() && !!LV.st.song.lineup_match && LV.st.song.lineup === S.lineupId;
const secAtBeat = (b) => S.song.sections.find((s) => b >= s.start && b < s.end) || S.song.sections[0];
const LV_HELP = 'Live not connected — enable AbletonOSC\nOpen Ableton Live, then Settings → Link, Tempo & MIDI → Control Surface: AbletonOSC\n'
  + 'Not in the list? Copy AbletonOSC into ~/Music/Ableton/User Library/Remote Scripts and restart Live\nAbletonOSC listens on UDP 11000 and replies on 11001';

function lvPos() {   // seconds from bar 1
  const st = LV.st, sec = st?.playing && lvSongOk() && st.scene != null ? S.song.sections[st.scene] : null;
  if (!sec) return LV.cursor;
  const len = sec.end - sec.start;
  let off = st.offset != null ? st.offset : (st.song_beat ?? 0) - (LV.launch?.beat ?? st.song_beat ?? 0);
  off += ((performance.now() - LV.at) / 1000) * (st.tempo || S.song.tempo) / 60;   // between polls: run on at tempo
  return (sec.start + (((off % len) + len) % len)) * spb();
}
const tpPos = () => (isLive() ? lvPos() : PV.pos());
const tpPlaying = () => (isLive() ? !!LV.st?.playing : PV.playing);

let lvTimer = 0, lvBusy = false;
function lvSchedule(ms) { clearTimeout(lvTimer); lvTimer = 0; if (isLive() && ms >= 0) lvTimer = setTimeout(lvPoll, ms); }
async function lvPoll() {
  if (!isLive()) return;
  if (lvBusy) { lvSchedule(50); return; }
  lvBusy = true;
  const was = LV.st?.playing && S.song ? lvPos() : null, prevScene = LV.st?.playing ? LV.st.scene : null;
  const q = S.slug ? `?slug=${encodeURIComponent(S.slug)}&lineup=${encodeURIComponent(S.lineupId)}` : '';
  try { LV.st = await api('GET', '/live/status' + q); LV.err = ''; } catch (e) { LV.st = null; LV.err = e.message; }
  lvBusy = false; LV.at = performance.now();
  const st = LV.st;
  if (st?.playing && st.scene != null && st.scene !== prevScene) LV.launch = { scene: st.scene, beat: st.song_beat ?? 0 };
  if (was != null && !st?.playing) LV.cursor = was;
  lvRefresh();
  if (st?.playing) pvAnimate();
  lvSchedule(document.hidden ? 3000 : st?.playing || st?.fired != null ? 100 : 1000);
}
function lvRefresh() {
  if (S.view !== 'arrange' || !S.song || !isLive()) return;
  const st = LV.st || {};
  const rowSig = JSON.stringify([st.connected, st.song?.parts || null]);
  const sig = JSON.stringify([LV.err, st.connected, st.busy, st.playing, st.scene, st.fired, st.loaded, st.song, LV.opening]);
  if (rowSig !== LV.rowSig) { LV.rowSig = rowSig; LV.sig = sig; renderMain(); return; }
  if (sig !== LV.sig) { LV.sig = sig; renderTransport(); }
  if (!st.playing) { drawPlayhead(); updateClock(); }
}
async function lvPost(path, body) {
  try { return await api('POST', '/live/' + path, body); } catch (e) { toast(e.message, true); return null; } finally { lvSchedule(30); }
}
function lvReady() {
  if (!lvConnected()) { toast(LV.st?.error || LV.err || 'Live not connected: open Ableton Live with AbletonOSC enabled', true); return false; }
  if (!lvSongOk()) { toast('Live has a different set open: click “Open in Live” first', true); return false; }
  return true;
}
function lvFire(sid) {
  const s = secById(sid); if (!s || !lvReady()) return;
  const loopOff = LV.loop && LV.loop !== sid;
  if (loopOff) LV.loop = null;
  LV.cursor = s.start * spb();
  lvPost('fire', { slug: S.slug, section_id: sid });
  if (loopOff) renderMain(); else { renderTransport(); drawPlayhead(); updateClock(); }
}
function lvLoop(sid) {
  if (!secById(sid)) return;
  const on = LV.loop !== sid;
  if (on && !lvReady()) return;
  LV.loop = on ? sid : null;
  if (on) { S.selSec = sid; LV.cursor = secById(sid).start * spb(); }
  lvPost('loop', { slug: S.slug, section_id: sid, on });
  renderMain();
}
function lvMix() {
  if (!lvConnected()) { toast('Live not connected: solo / mute apply to the browser preview only', true); return; }
  if (!lvSongOk()) return;      // another set is open: nothing sensible to map the parts onto
  lvPost('mix', { slug: S.slug, lineup_id: S.lineupId, solo: [...S.pv.solo], mute: [...S.pv.mute] });
}
async function lvOpen() {
  if (!S.song || LV.opening) return;
  if (dirty() || bandDirty()) { await save(); if (dirty() || bandDirty()) return; }
  LV.opening = true; renderTransport();
  try {
    const r = await api('POST', '/live/open', { slug: S.slug, lineup_id: S.lineupId });
    if (r.state === 'loaded') toast(r.already ? 'Already open in Live' : `Opened in Live${r.built ? ' (built first)' : ''}`);
    else toast(r.message || 'Waiting for Live (check for a save dialog)', true);
  } catch (e) { toast(e.message, true); }
  LV.opening = false; LV.sig = ''; renderTransport(); lvSchedule(0);
}
function lvPartSM(p) {   // [has a Live track, S/M tooltip] for a part row in Live mode
  if (!lvConnected()) return [true, 'Live solo / mute\nLive is not connected'];
  const names = lvSongOk() ? LV.st.song.parts?.[p.id] : null;
  if (!names) return [true, 'Live solo / mute\nOpen this song in Live to map its parts to tracks'];
  if (!names.length) return [false, `No track in the Live set\n${p.name} has no track in the set open in Live (“${lineup().name}” doesn’t play it, or Live has another lineup)`];
  return [true, `Live solo / mute\nTracks: ${names.join(', ')}` + (names.some((n) => n.includes('(live:')) ? '\nShared live input: muting it mutes every part on that input' : '')];
}

function tpToggle() {
  if (!isLive()) { pvToggle(); return; }
  if (LV.st?.playing || LV.st?.fired != null) { lvPost('stop', {}); return; }
  const s = (LV.loop && secById(LV.loop)) || secAtBeat(LV.cursor / spb());
  if (s) lvFire(s.id);
}
function tpFrom(sid) { if (isLive()) lvFire(sid); else pvFromSection(sid); }
function tpLoop(sid) { if (isLive()) lvLoop(sid); else pvLoop(sid); }
function tpSeek(sec) {
  if (!isLive()) { if (PV.playing) pvPlay(sec); else { PV.seek(sec); drawPlayhead(); updateClock(); } return; }
  LV.cursor = sec;
  if (LV.st?.playing) lvFire(secAtBeat(sec / spb()).id); else { drawPlayhead(); updateClock(); }
}
function setEngine(e) {
  if (e === S.eng || !['browser', 'live'].includes(e)) return;
  if (e === 'live' && (PV.playing || PV.preparing || S.pv.busy)) pvToggle();
  if (e === 'live') LV.cursor = PV.pos(); else PV.seek(LV.cursor);
  S.eng = e; pref.set('engine', e);
  LV.sig = LV.rowSig = '';
  if (isLive()) lvPoll().then(() => { if (S.pv.solo.size || S.pv.mute.size) lvMix(); }); else lvSchedule(-1);
  renderMain();
}

function renderLiveTransport(el) {
  const st = LV.st || {}, sel = secById(S.selSec), lp = LV.loop && secById(LV.loop), secs = S.song.sections;
  const playing = !!st.playing, pending = st.fired != null;
  const cur = playing && lvSongOk() && st.scene != null ? secs[st.scene] : null;
  const next = pending && lvSongOk() ? secs[st.fired] : null;
  const ld = st.loaded;
  const what = ld ? `${ld.title} · ${ld.lineup ? (ld.slug === S.slug ? S.song.lineups[ld.lineup]?.name || ld.lineup : ld.lineup) : 'unknown lineup'}`
    : 'a set that isn’t one of these songs';
  let conn;
  if (LV.err) conn = `<span class="lv-conn bad" data-tip="${esc('UI server error\n' + LV.err)}"><i></i>Server error</span>`;
  else if (!LV.st) conn = '<span class="lv-conn"><span class="spin sm"></span> Connecting…</span>';
  else if (st.busy) conn = `<span class="lv-conn bad" data-tip="${esc('OSC port busy\n' + st.error)}"><i></i>OSC port busy</span>`;
  else if (!st.connected) conn = `<span class="lv-conn bad" data-tip="${esc(LV_HELP)}"><i></i>Live not connected</span>`;
  else conn = `<span class="lv-conn ok" data-tip="${esc(`Live connected\nAbletonOSC · ${st.tempo} bpm · ${st.scenes?.length ?? 0} scenes · ${st.tracks?.length ?? 0} tracks\nOpen: ${what}`)}"><i></i>Live</span>`;
  const need = st.connected && !lvSetOk();
  const openTip = `Open in Live\nLive has ${what}; this is ${S.song.title} · ${lineup().name}\nBuilds the set if it’s out of date, then opens it. Live may ask to save the current set first.`;
  const openBtn = need || LV.opening ? `<button class="btn small primary" data-act="lv-open" ${LV.opening ? 'disabled' : ''} data-tip="${esc(openTip)}">${LV.opening ? '<span class="spin sm"></span> Opening…' : 'Open in Live'}</button>` : '';
  const status = cur ? `<span class="on-y">▶ ${esc(cur.name)}</span>${next && next !== cur ? ` → ${esc(next.name)}` : ''}`
    : next ? `<span class="spin sm"></span> launching ${esc(next.name)}…` : st.connected && lvSongOk() ? 'stopped' : '';
  const adv = (S.song.options || {}).auto_advance ? '\n⚠ Auto-advance is on: the scene’s follow action still moves on' : '';
  el.innerHTML = `
    ${ENGINE_SEG()}
    <button class="btn icon tp ${playing || pending ? 'on' : ''}" data-act="pv-toggle" data-tip="${playing || pending ? 'Stop Live' : 'Play in Live&#10;Launches the section under the playhead'}" data-tip-key="Space" aria-label="${playing || pending ? 'Stop' : 'Play'}">${playing || pending ? '■' : '▶'}</button>
    <button class="btn small" data-act="pv-from" ${sel ? '' : 'disabled'} data-tip="Launch the selected section in Live&#10;Fires its scene on the next bar" data-tip-key="P">▶ ${esc(sel ? sel.name : 'section')}</button>
    <button class="btn small ${lp ? 'act' : ''}" data-act="pv-loop" ${sel || lp ? '' : 'disabled'} data-tip="${esc(`Loop the selected section in Live\nLaunches its scene and stays there: Session clips loop until another scene is launched${adv}`)}" data-tip-key="L">⟲ ${lp ? 'Looping ' + esc(lp.name) : 'Loop'}</button>
    <button class="btn icon small" data-act="pv-zero" data-tip="Back to bar 1&#10;While playing, launches the first section" data-tip-key="Home" aria-label="Back to start">⇤</button>
    <span class="pv-clock" id="pv-clock">${fmtPos(lvPos())}</span>
    <span class="sep"></span>
    ${conn}${openBtn}
    <span class="pv-status">${status}</span>
    ${S.pv.solo.size || S.pv.mute.size ? '<button class="btn small" data-act="pv-clear" data-tip="Clear solo / mute&#10;Unmutes and unsolos the mapped Live tracks">Clear S/M</button>' : ''}
    <span class="hint pv-note" data-tip="Live engine&#10;Transport, S/M and the playhead follow Ableton Live through AbletonOSC. Space, P, L and Home work as in the browser engine.">Live engine · AbletonOSC</span>`;
}

function renderInspector() {
  const el = $('#inspector'); if (!el) return;
  const s = secById(S.selSec);
  el.hidden = !s;
  if (!s) { el.innerHTML = ''; return; }
  const bp = bpb(), cuesOn = !!(S.song.options || {}).cues;
  if (el.dataset.sid === s.id && el.contains(document.activeElement)) {   // don't clobber the field being typed in
    $('[data-act=sec-vamp]', el)?.setAttribute('aria-pressed', String(!!s.vamp)); return;
  }
  el.dataset.sid = s.id;
  el.innerHTML = `
    <span class="lbl">Section</span>
    <input class="inp" style="width:150px" data-sec="name" value="${esc(s.name)}" aria-label="Section name">
    <span class="song-meta">bar ${s.start / bp + 1}–${s.end / bp + 1} · ${Math.round((s.end - s.start) / bp)} bars</span>
    <span class="sep"></span>
    <label class="lbl" for="sec-cue">Cue text</label>
    <input class="inp" id="sec-cue" style="width:200px" data-sec="cue" value="${esc(s.cue || '')}" placeholder="${esc(s.name)} (spoken)" aria-label="Cue text override">
    <span class="hint">${cuesOn ? 'spoken one bar before the section' : 'cues are off for this song (toggle Cues above)'}</span>
    <span class="sep"></span>
    <button class="ptog" data-act="sec-vamp" aria-pressed="${!!s.vamp}" data-tip="Vamp&#10;The scene loops until you launch the next one" data-tip-key="V">∞ Vamp</button>`;
}

// ---------- rich tooltips (tip.js calls richTip(kind, el) when one is about to show)
const fmtSec = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
const tsw = (t) => ({ html: `<span class="tsw" style="--tc:var(--t-${t})"></span>${esc(TLABEL[t] || t)}` });
const H = (html) => ({ html });

function instText(p) {
  const i = p.instrument || {};
  if (i.type === 'analoglab') return `Analog Lab V · ${i.preset || '?'}`;
  if (i.type === 'drumrack') return 'Drum Rack';
  if (i.type === 'logic-builtin') return `Logic ${i.name || ''}${i.preset ? ' · ' + String(i.preset).replace(/\.\w+$/, '') : ''} (Logic-only)`;
  if (i.type === 'audio' || p.kind === 'audio') return 'Audio track';
  return i.name || i.type || p.kind || '?';
}
function stemText(p) {
  if (p.stem) return H(`<span class="on-y">exported</span> <span class="mono">${esc(p.stem.split('/').pop())}</span>`);
  if (p.raw_stem) return H(`<span class="warn">raw</span> (no FX) <span class="mono">${esc(p.raw_stem.split('/').pop())}</span>`);
  return H(`<span class="bad">none</span>${p.midi ? ' · MIDI only' : ''}`);
}
function whoPlays(p, t) {
  const a = asg(p.id);
  if (LIVE.has(t)) {
    const perf = a?.performer && bandPerf(a.performer);
    return perf ? `${perf.name} · ${a.rig || '?'}` : H('<span class="bad">nobody yet (needs a player)</span>');
  }
  return LAPTOP.has(t) ? 'the laptop' : t === 'click' ? 'click track' : '—';
}
function previewText(p) {
  if (!pvAudible(p)) return H(`<span class="on-n">${esc(pvWhyOff(p))}</span>`);
  const sm = S.pv.mute.has(p.id) ? ' · muted' : S.pv.solo.size && !S.pv.solo.has(p.id) ? ' · not soloed' : S.pv.solo.has(p.id) ? ' · solo' : '';
  return synthOf(p) ? H(`<span class="syn">synth preview</span> (MIDI notes on a browser synth)${sm}`) : 'stem' + sm;
}

function richTip(kind, el) {
  if (!S.song && kind !== 'chip') return '';
  const bp = bpb(), k = spb();
  if (kind === 'sec') {
    const s = secById(el.dataset.sid); if (!s) return '';
    const i = S.song.sections.indexOf(s), bars = Math.round((s.end - s.start) / bp), ts = (S.song.time_signature || [4, 4]).join('/');
    const cuesOn = !!(S.song.options || {}).cues;
    const cue = i === 0 ? H('<span class="on-n">none · first section (the count-in covers it)</span>')
      : H(`“${esc(s.cue || s.name)}”${s.cue ? '' : ' <span class="on-n">(section name)</span>'}${cuesOn ? '' : ' <span class="on-n">· cues off</span>'}`);
    return tipHtml({
      title: s.name, sub: `bars ${s.start / bp + 1}–${s.end / bp + 1} · ${fmtSec(s.start * k)}–${fmtSec(s.end * k)}`,
      rows: [['Length', `${bars} bar${bars === 1 ? '' : 's'} · ${((s.end - s.start) * k).toFixed(1)} s`], ['Tempo', `${S.song.tempo} bpm · ${ts}`],
        ['Cue', cue], ['Vamp', s.vamp ? H('<span class="on-y">on</span> · the scene loops until you launch the next') : 'off'],
        curLoop() === s.id ? [isLive() ? 'Live' : 'Preview', H('<span class="on-y">looping this section</span>')] : null],
      keys: ['double-click to rename', 'drag edges', '⌥-click split', 'P play · L loop'],
    });
  }
  if (kind === 'part') {
    const p = partById(el.dataset.part); if (!p) return '';
    const t = lineupT(p), ov = Object.entries(asg(p.id)?.sections || {});
    const iss = S.issues.filter((x) => x.part === p.id);
    const uniq = [...new Map(iss.map((x) => [x.message, x])).values()];
    return tipHtml({
      title: p.name, sub: `${p.id} · ${p.kind || '?'}${p.role_hint ? ' · ' + p.role_hint : ''}`,
      rows: [['Instrument', instText(p)], [`In ${lineup().name}`, tsw(t)],
        ov.length ? ['Overrides', ov.map(([sid, st]) => `${secById(sid)?.name || sid} → ${TLABEL[st] || st}`).join(', ')] : null,
        ['Played by', whoPlays(p, t)], ['Stem', stemText(p)], ['Preview', previewText(p)]],
      lines: uniq.slice(0, 3).map((x) => H(`<span class="${x.level === 'error' ? 'bad' : 'warn'}">${x.level === 'error' ? '●' : '▲'}</span> ${esc(x.message)}`)),
      keys: el.classList.contains('card') ? ['drag onto a rig', '1–9 assign to zone', '0 playback'] : ['click highlight', 'right-click treatment', 'drop a chip: whole song'],
    });
  }
  if (kind === 'cell') {
    const p = partById(el.dataset.part), s = secById(el.dataset.sid); if (!p || !s) return '';
    const t = effT(p, s.id), ovr = !!asg(p.id)?.sections?.[s.id], act = secActivity(p.id, s);
    const a = S.activity?.parts?.[p.id];
    let active;
    if (act === null) active = H('<span class="on-n">unknown (no stem or MIDI to measure)</span>');
    else {
      const lo = Math.floor(s.start / bp), hi = Math.ceil(s.end / bp);
      let n = 0; for (let b = lo; b < hi; b++) if (a.bars[b] > 0) n++;
      active = act ? H(`<span class="on-y">yes</span> · sounds in ${n} of ${hi - lo} bars (${a.source === 'midi' ? 'MIDI notes' : 'stem level'})`) : H('<span class="on-n">no · silent here</span>');
    }
    const plays = S.pv.mode === 'full' || LAPTOP.has(t);
    return tipHtml({
      title: `${p.name} · ${s.name}`, sub: `bars ${s.start / bp + 1}–${s.end / bp + 1}`,
      rows: [['Treatment', H(`${tsw(t).html}${ovr ? ' <span class="warn">override</span>' : ` <span class="on-n">from the lineup</span>`}`)],
        ['Active', active], ['Preview', plays && pvAudible(p) ? (synthOf(p) ? H('<span class="syn">synth</span>') : 'stem') : H('<span class="on-n">not played by the laptop</span>')]],
      keys: el.classList.contains('cell') ? ['drop a chip: this section', '⌥-click clear', 'right-click menu'] : [],
    });
  }
  if (kind === 'chip') {
    const t = el.dataset.t, p = el.dataset.part && S.song && partById(el.dataset.part);
    const keys = el.dataset.palette ? ['drag onto a part: whole song', 'onto a cell: one section']
      : p ? ['click to change', 'drag onto a cell: override one section'] : [];
    return tipHtml({ title: TLABEL[t] || t, sub: p ? `${p.name} in “${lineup().name}”` : LIVE.has(t) ? 'live' : LAPTOP.has(t) ? 'from the laptop' : '',
      lines: [TDESC[t]], keys });
  }
  return '';
}

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
    return `<i class="${act ? 'on' : ''}" style="--tc:var(--t-${t})" data-tip-rich="cell" data-part="${esc(p.id)}" data-sid="${esc(s.id)}"></i>`;
  }).join('');
  const t = lineupT(p), hl = S.highlight?.part === p.id;
  return `<div class="card ${hl ? 'hl' : ''}" draggable="true" tabindex="0" data-part="${esc(p.id)}" data-dragpart="${esc(p.id)}"
     data-tip-rich="part" data-tip-pos="right">
    <div class="c1"><span class="issue-dot"></span><span class="cname">${esc(p.name)}</span>
    ${chip(t, `data-act="tmenu" data-part="${esc(p.id)}"`, 'mini')}</div>
    <div class="c2">${instBadge(p)}<div class="strip">${strip}</div></div></div>`;
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
    let html = `<section class="col playback"><div class="col-h"><b>Playback</b><span class="roles">from the laptop</span><kbd class="x" data-tip="Focus a card and press 0">0</kbd></div><div class="col-b">${pb}</div></section>`;
    for (const pid of pl.cols) {
      const p = bandPerf(pid);
      const rigs = (p.rigs || []).map((r) => {
        const list = pl.rigs[pid + '/' + r.id] || [];
        const io = r.kind === 'vocal' || r.kind === 'guitar' || r.kind === 'bass'
          ? `in ${Array.isArray(r.audio_input) ? r.audio_input.join('/') : r.audio_input ?? '?'}`
          : `${r.midi_port || 'all ins'} · ch ${r.midi_channel ?? 'all'}`;
        return `<div class="zone" data-drop="rig" data-perf="${esc(pid)}" data-rig="${esc(r.id)}" data-zone="${n + 1}">
          <div class="zone-h">${zoneNo()}<span class="rig-ic">${RIG_ICON[r.kind] || '?'}</span><span class="k">${esc(r.kind)}</span><span class="ctl" data-tip="${esc(r.controller || r.id)}">${esc(r.controller || r.id)}</span><span class="io">${esc(io)}</span></div>
          ${list.map(card).join('') || `<div class="empty">drop to play live (${esc(TLABEL[rigTreatment(r.kind)])})</div>`}</div>`;
      }).join('');
      html += `<section class="col"><div class="col-h"><b>${esc(p.name)}</b><span class="roles">${esc((p.roles || []).join(' · '))}</span>
        <button class="btn icon x" data-act="ptoggle" data-perf="${esc(pid)}" data-tip="Remove from this lineup&#10;Their parts go back to playback" aria-label="Remove ${esc(p.name)} from this lineup">×</button></div>
        <div class="col-b">${rigs || '<div class="empty">No rigs — add one on the Band tab</div>'}</div></section>`;
    }
    const absent = (S.band?.performers || []).filter((p) => !pl.cols.includes(p.id));
    if (absent.length) html += `<div class="add-col"><span class="lbl">Add to lineup</span>${absent.map((p) => `<button class="btn small" data-act="ptoggle" data-perf="${esc(p.id)}">+ ${esc(p.name)}</button>`).join('')}</div>`;
    $('#board').innerHTML = html;
    void lu;
  },
};

// ---------- Import view (new song / re-import + link stems)
const baseName = (p) => String(p || '').replace(/\/+$/, '').split('/').pop().replace(/\.logicx$/i, '');

function openImport(mode) {
  if (S.imp?.job && S.imp.state === 'running') { setView('import'); return; }
  const src = (mode === 're' && S.song?.source) || {};
  S.imp = {
    mode, slug: mode === 're' ? S.slug : '', slugTouched: mode === 're',
    logicx: mode === 're' ? (src.original || src.logicx || '') : '', exportDir: mode === 're' ? (src.export_dir || '') : '',
    job: null, lines: [], state: 'idle', step: '', result: null, error: '', checks: pref.get('exportChecks', {}),
  };
  setView('import');
}

VIEWS.import = {
  shell: () => `
    <div class="toolbar"><b id="imp-title"></b><span class="hint" id="imp-sub"></span>
      <span class="spacer" style="flex:1"></span>
      ${S.song ? '<button class="btn small" data-act="imp-back">← Back to song</button>' : ''}</div>
    <div class="imp" id="imp"></div>`,
  fill() {
    const I = S.imp;
    if (!I) { $('#imp').innerHTML = '<div class="empty-state">Choose “+ New song” or “Re-import” from the song picker.</div>'; return; }
    const re = I.mode === 're', running = I.state === 'running';
    $('#imp-title').textContent = re ? `Re-import · ${S.song?.title || I.slug}` : 'New song';
    $('#imp-sub').textContent = re ? 'Re-ingest keeps your section names, treatments and lineups' : 'Logic project → song model → stems';
    const btnLabel = re ? (I.logicx && I.exportDir ? 'Re-import + link stems' : I.logicx ? 'Re-import' : 'Link stems') : (I.exportDir ? 'Import + link stems' : 'Import');
    const form = `
      <section class="fcard imp-form">
        <h3><span class="stepn">1</span> Logic project</h3>
        <label class="field">Project (.logicx)
          <div class="frow nowrap"><input class="inp mono grow" data-imp="logicx" value="${esc(I.logicx)}" placeholder="/Users/you/Music/Logic/Song.logicx" ${running ? 'disabled' : ''} spellcheck="false">
          <button class="btn" data-act="pick" data-kind="logicx" ${running ? 'disabled' : ''}>Browse…</button></div></label>
        <div class="hint">The importer makes an APFS clone into <code>samples/${esc(I.slug || '&lt;slug&gt;')}.logicx</code> and reads only that copy. Your original is never opened by Logic or modified.${re ? ' Leave empty to only link stems.' : ''}</div>
        <label class="field">Song slug
          <div class="frow nowrap"><input class="inp mono" style="width:240px" data-imp="slug" value="${esc(I.slug)}" placeholder="auto from the project name" ${re || running ? 'disabled' : ''} spellcheck="false">
          <span class="hint">→ songs/${esc(I.slug || '…')}/</span></div></label>
        <h3><span class="stepn">2</span> Exported stems <span class="hint">optional now, needed for backing tracks</span></h3>
        <label class="field">Logic export folder
          <div class="frow nowrap"><input class="inp mono grow" data-imp="exportDir" value="${esc(I.exportDir)}" placeholder="/Users/you/Desktop/Song exports" ${running ? 'disabled' : ''} spellcheck="false">
          <button class="btn" data-act="pick" data-kind="folder" ${running ? 'disabled' : ''}>Browse…</button></div></label>
        <div class="frow"><button class="btn primary" data-act="imp-go" ${running ? 'disabled' : ''}>${esc(btnLabel)}</button>
          ${I.error && I.state !== 'error' ? `<div class="err-box">${esc(I.error)}</div>` : ''}</div>
      </section>`;
    const done = Object.values(I.checks).filter(Boolean).length;
    const check = `
      <section class="fcard imp-check">
        <h3>Export from Logic first <span class="count ${done === EXPORT_STEPS.length ? 'ok' : ''}">${done}/${EXPORT_STEPS.length}</span></h3>
        <ol class="checklist">${EXPORT_STEPS.map(([k, t]) => `<li><label><input type="checkbox" data-check="${k}" ${I.checks[k] ? 'checked' : ''}><span>${esc(t)}</span></label></li>`).join('')}</ol>
        <div class="hint">Stems are matched to Logic tracks by name; anything that doesn't match becomes a new backing part, and the bounce becomes the song's full mix.</div>
      </section>`;
    $('#imp').innerHTML = `<div class="imp-top">${form}${check}</div>${jobHtml(I, 'Import')}${I.result ? importResultHtml(I.result) : ''}`;
    const log = $('#imp .joblog'); if (log) log.scrollTop = log.scrollHeight;
  },
};

function jobHtml(J, what) {
  if (!J.job && J.state !== 'error') return '';
  const steps = J.lines.filter((l) => l.startsWith('▸ ')).map((l) => l.slice(2));
  const last = steps.length - 1;
  return `<section class="fcard jobcard">
    <h3>${J.state === 'running' ? '<span class="spin"></span>' : J.state === 'done' ? '<span class="ok-mark">✓</span>' : '<span class="bad-mark">✕</span>'}
      ${esc(what)} ${J.state === 'running' ? 'running…' : J.state === 'done' ? 'finished' : 'failed'}<span class="hint">${J.elapsed != null ? J.elapsed + ' s' : ''}</span></h3>
    <div class="steps">${steps.map((s, i) => `<span class="stp ${i < last || J.state === 'done' ? 'done' : J.state === 'error' ? 'bad' : 'cur'}">${esc(s)}</span>`).join('<span class="arrow">→</span>')}</div>
    ${J.error && J.state === 'error' ? `<div class="err-box">${esc(J.error)}</div>` : ''}
    <details ${J.state !== 'done' ? 'open' : ''}><summary class="lbl">Log · ${J.lines.length} lines</summary><pre class="md joblog">${esc(J.lines.join('\n'))}</pre></details>
  </section>`;
}

function pollJob(J, onDone) {
  const tick = async () => {
    try {
      const j = await api('GET', `/jobs/${J.job}?since=${J.lines.length}`);
      J.lines.push(...j.lines); J.state = j.state; J.step = j.step; J.elapsed = j.elapsed;
      if (j.state === 'running') { J.timer = setTimeout(tick, 400); }
      else { J.error = j.error || ''; await onDone(j); }
    } catch (e) { J.state = 'error'; J.error = e.message; }
    if (S.view === 'import' || S.view === 'setlists') renderMain();
  };
  tick();
}

async function startImport() {
  const I = S.imp;
  I.error = '';
  if (I.mode === 're' && S.slug === I.slug && dirty()) { await save(); if (dirty()) return; }
  if (!I.slug && I.logicx) I.slug = slugify(baseName(I.logicx));
  try {
    const r = await api('POST', '/import', { logicx: I.logicx.trim() || null, slug: I.slug, export_dir: I.exportDir.trim() || null, reimport: I.mode === 're' });
    Object.assign(I, { job: r.job, lines: [], state: 'running', result: null });
  } catch (e) { I.error = e.message; renderMain(); return; }
  renderMain();
  pollJob(I, async (j) => {
    if (j.state !== 'done') return;
    I.result = j.result;
    await refreshSongs();
    if (I.slug === S.slug) await loadSong(I.slug);  // pick up re-imported changes
    toast(`${j.result.title}: imported`);
  });
}

function importResultHtml(r) {
  const bp = (r.time_signature || [4, 4])[0] * 4 / (r.time_signature || [4, 4])[1];
  const kinds = { audio: 0, midi: 0 }, al = r.parts.filter((p) => p.instrument === 'analoglab').length;
  for (const p of r.parts) kinds[p.kind] = (kinds[p.kind] || 0) + 1;
  const link = r.link || [];
  const by = (st) => link.filter((x) => x.status === st);
  const linkGroup = (st, title, cls) => {
    const xs = by(st);
    if (!xs.length) return '';
    return `<div class="lgrp ${cls}"><div class="h">${esc(title)} · ${xs.length}</div>${xs.map((x) => `<div class="lrow"><span class="n">${esc(x.file)}</span><span class="d">${esc(x.what)}</span></div>`).join('')}</div>`;
  };
  const stemTxt = (p) => p.stem ? '<span class="ok-mark">exported stem</span>' : p.raw_stem ? '<span class="hint">raw stem (no FX)</span>'
    : p.kind === 'midi' && p.instrument === 'analoglab' ? '<span class="hint">MIDI (Analog Lab plays it)</span>' : '<span class="bad">no stem</span>';
  const missing = (r.ignored_tracks || []).filter((t) => t.status === 'MISSING').length;
  return `<section class="fcard result">
    <h3><span class="ok-mark">✓</span> ${esc(r.title)} <span class="hint">songs/${esc(r.slug)}/</span>
      <span class="spacer" style="flex:1"></span><button class="btn primary" data-act="imp-open" data-slug="${esc(r.slug)}">Open song →</button></h3>
    <div class="song-meta">${r.tempo} bpm · ${(r.time_signature || [4, 4]).join('/')} · ${Math.ceil((r.length_beats || 0) / bp)} bars ·
      ${r.sections.length} sections · ${r.parts.length} parts (${kinds.audio || 0} audio, ${kinds.midi || 0} MIDI, ${al} Analog Lab) · full mix: ${r.mix ? 'yes' : 'no'}</div>
    <div class="h">Sections</div>
    <div class="secchips">${r.sections.map((s, i) => `<span class="secchip" style="--sc:${SEC_COLORS[i % SEC_COLORS.length]}">${esc(s.name)} <i>${Math.round((s.end - s.start) / bp)}b</i></span>`).join('')}</div>
    <div class="res-grid">
      <div><div class="h">Parts · ${r.parts.length}</div>
        <table class="report">${r.parts.map((p) => `<tr><td>${esc(p.name)}${p.origin ? ' <span class="pill">export-only</span>' : ''}</td>
          <td>${chip(p.default_treatment, 'tabindex="-1"', 'mini')}</td><td>${stemTxt(p)}</td></tr>`).join('')}</table></div>
      <div>
        <div class="h">Link stems ${r.link ? '' : '· not run (no export folder)'}</div>
        ${r.link ? linkGroup('matched', 'Matched', 'good') + linkGroup('mix', 'Full mix', 'good') + linkGroup('new', 'NEW parts (no Logic track matched: check them)', 'warn') + linkGroup('missing', '⚠ Missing stems', 'bad') : '<div class="panel-note">Choose the export folder and run “Link stems” to attach the exported audio.</div>'}
        <div class="h">Accounted for · not imported as parts · ${(r.ignored_tracks || []).length}${missing ? ` <span class="count err">${missing} MISSING</span>` : ''}</div>
        ${(r.ignored_tracks || []).map((t) => `<div class="fate ${t.status === 'MISSING' ? 'missing' : ''}" style="cursor:default"><span class="n">${esc(t.name)}</span><span class="pill ${esc(t.status)}">${esc(t.status)}</span><span class="d">${esc(t.reason)}</span></div>`).join('') || '<div class="panel-note">Every Logic track became a part.</div>'}
        ${(r.warnings || []).length ? `<details class="warnings"><summary>Ingest warnings (${r.warnings.length})</summary>${r.warnings.map((x) => `<p>${esc(x)}</p>`).join('')}</details>` : ''}
      </div>
    </div>
  </section>`;
}

// ---------- Song packages (.llsong: move a song to another Mac; lls/package.py)
function waitJob(id) {
  return new Promise((resolve, reject) => {
    let since = 0;
    const tick = async () => {
      try {
        const j = await api('GET', `/jobs/${id}?since=${since}`);
        since = j.next;
        if (j.state === 'running') { setTimeout(tick, 500); return; }
        if (j.state === 'done') resolve(j.result); else reject(new Error(j.error || 'job failed'));
      } catch (e) { reject(e); }
    };
    tick();
  });
}

async function exportPackage() {
  if (!S.song) return;
  if (dirty()) { await save(); if (dirty()) return; }
  try {
    const r = await api('POST', `/songs/${S.slug}/package`, { reveal: true });
    toast(`Packing “${S.song.title}”… (stems can take a minute)`);
    const res = await waitJob(r.job);
    toast(`Saved ${baseName(res.path)} (${(res.size / 1e6).toFixed(0)} MB) to Downloads`);
  } catch (e) { toast('Export failed: ' + e.message, true); }
}

async function importPackage() {
  let pick;
  try { pick = await api('POST', '/pick', { kind: 'llsong' }); } catch (e) { toast(e.message, true); return; }
  if (pick.cancelled) return;
  const start = (force) => api('POST', '/package/import', { path: pick.path, force });
  let r;
  try { r = await start(false); } catch (e) {
    if (e.status !== 409 || !/already exists/.test(e.message)) { toast(e.message, true); return; }
    const ok = await confirmDialog('Replace song?', `${e.message}. Replace it with the package? Lineups that only exist here are kept.`, 'Replace');
    if (!ok) return;
    if (S.slug && dirty()) { await save(); if (dirty()) return; }
    try { r = await start(true); } catch (e2) { toast(e2.message, true); return; }
  }
  try {
    toast('Checking and unpacking the package…');
    const res = await waitJob(r.job);
    await refreshSongs(); await loadSong(res.slug);
    toast(`${res.title}: imported${res.kept_lineups?.length ? ` (kept lineups: ${res.kept_lineups.join(', ')})` : ''}`);
  } catch (e) { toast('Import failed: ' + e.message, true); }
}

// ---------- Setup check (GET /api/doctor): banner when something critical is missing
async function checkSetup() {
  let r;
  try { r = await api('GET', '/doctor'); } catch { return; }
  document.querySelector('.doctor-banner')?.remove();
  if (r.ok) return;
  const bad = r.checks.filter((c) => c.critical && !c.ok);
  const el = document.createElement('div');
  el.className = 'doctor-banner'; el.setAttribute('role', 'alert');
  el.innerHTML = `<div class="grow"><b>Setup incomplete: building sets won't work on this Mac yet.</b>
    <ul>${bad.map((c) => `<li><b>${esc(c.label)}</b>${c.detail ? ` · <span class="hint">${esc(c.detail)}</span>` : ''}${c.fix ? `<div>Fix: ${esc(c.fix)}</div>` : ''}</li>`).join('')}</ul>
    <span class="hint">Full checklist: <code>python -m lls doctor</code></span></div>
    <button class="btn icon" aria-label="Dismiss">✕</button>`;
  el.querySelector('button').addEventListener('click', () => el.remove());
  document.querySelector('header.top').after(el);
}

async function pickPath(kind) {
  try {
    const r = await api('POST', '/pick', { kind });
    if (r.cancelled) return;
    if (kind === 'logicx') {
      S.imp.logicx = r.path;
      if (!S.imp.slugTouched) S.imp.slug = slugify(baseName(r.path));
    } else S.imp.exportDir = r.path;
    renderMain();
  } catch (e) { toast(e.message, true); }
}

// ---------- Setlists
const slDirty = () => !!S.sl && JSON.stringify(S.sl) !== S.slSaved;
const songInfo = (slug) => S.songs.find((s) => s.slug === slug);

async function loadSetlists(open = S.slSlug) {
  try { S.sls = await api('GET', '/setlists'); } catch (e) { S.slErr = e.message; S.sls = []; }
  const slug = S.sls.find((x) => x.slug === open)?.slug || S.sls[0]?.slug || null;
  if (slug && (slug !== S.slSlug || !S.sl)) await openSetlist(slug); else if (!slug) { S.sl = null; S.slSlug = null; }
  renderMain();
}
async function openSetlist(slug) {
  if (slDirty() && !(await confirmDialog('Unsaved setlist', `Discard changes to “${S.sl.name}”?`, 'Discard'))) return;
  try {
    S.sl = await api('GET', `/setlists/${slug}`);
    S.sl.songs ||= [];
    S.slSlug = slug; S.slSaved = JSON.stringify(S.sl); pref.set('setlist', slug);
    if (S.slJob?.key !== slug) { S.slJob = null; S.slResult = null; }
  } catch (e) { toast(e.message, true); }
  renderMain();
}
async function saveSetlist() {
  if (!S.sl) return false;
  try {
    const snap = JSON.stringify(S.sl);
    await api('PUT', `/setlists/${S.slSlug}`, S.sl);
    S.slSaved = snap; S.sls = await api('GET', '/setlists'); toast('Setlist saved'); renderMain();
    return true;
  } catch (e) { toast('Save failed: ' + e.message, true); return false; }
}
async function newSetlist() {
  const name = await promptDialog('New setlist', 'Name', 'Friday show'); if (!name) return;
  let slug = slugify(name), n = 2;
  while (S.sls.some((x) => x.slug === slug)) slug = `${slugify(name)}-${n++}`;
  try {
    await api('PUT', `/setlists/${slug}`, { name, band: 'band.json', songs: S.slug ? [{ song: S.slug, lineup: S.lineupId, notes: '' }] : [] });
    S.sl = null; S.slSaved = ''; await loadSetlists(slug);
  } catch (e) { toast(e.message, true); }
}
async function deleteSetlist() {
  if (!(await confirmDialog('Delete setlist', `Delete “${S.sl.name}” (setlists/${S.slSlug}.json)? Built output in out/setlists stays.`, 'Delete'))) return;
  try { await api('DELETE', `/setlists/${S.slSlug}`); S.sl = null; S.slSaved = ''; S.slSlug = null; await loadSetlists(null); } catch (e) { toast(e.message, true); }
}
function slEdit(fn) { fn(); renderMain(); }
function slMove(i, d) {
  const xs = S.sl.songs, j = i + d;
  if (j < 0 || j >= xs.length) return;
  slEdit(() => { const [x] = xs.splice(i, 1); xs.splice(j, 0, x); });
  $(`.sl-row[data-i="${j}"]`)?.focus();
}
async function buildShow() {
  if (!S.sl) return;
  if (!S.sl.songs.length) { toast('Add songs to the setlist first'); return; }
  if (dirty() || bandDirty()) { await save(); if (dirty() || bandDirty()) return; }
  if (slDirty() && !(await saveSetlist())) return;
  S.slResult = null;
  try {
    const r = await api('POST', `/setlists/${S.slSlug}/build`);
    S.slJob = { job: r.job, key: S.slSlug, lines: [], state: 'running' };
  } catch (e) { toast(e.message, true); return; }
  renderMain();
  const J = S.slJob;
  pollJob(J, async (j) => { if (j.state === 'done') { J.result = j.result; S.slResult = j.result; S.sls = await api('GET', '/setlists'); toast('Show built'); } });
}
async function reveal(path) {
  try { await api('POST', '/reveal', { path }); } catch (e) { toast(e.message, true); }
}

VIEWS.setlists = {
  shell: () => `
    <div class="toolbar"><b>Setlists</b><span class="hint">setlists/*.json · Build show = one Live set per song in show order, running order, phone fallback</span>
      <span class="spacer" style="flex:1"></span><button class="btn small" data-act="sl-new">+ New setlist</button></div>
    <div class="sl-wrap"><nav class="sl-list" id="sl-list" aria-label="Setlists"></nav><div class="sl-main" id="sl-main"></div></div>`,
  fill() {
    $('#sl-list').innerHTML = S.sls.map((x) => `<button class="sl-item ${x.slug === S.slSlug ? 'cur' : ''}" data-act="sl-open" data-slug="${esc(x.slug)}">
        <span class="n">${esc(x.name)}${x.slug === S.slSlug && slDirty() ? ' <span class="dirty-dot">●</span>' : ''}</span><span class="d">${x.songs} song${x.songs === 1 ? '' : 's'}${x.built ? ' · built' : ''}${x.error ? ' · ⚠' : ''}</span></button>`).join('')
      || `<div class="panel-note">No setlists yet.${S.slErr ? `<div class="err-box">${esc(S.slErr)}</div>` : ''}</div>`;
    const m = $('#sl-main');
    if (!S.sl) { m.innerHTML = '<div class="empty-state">Create a setlist to order songs for a show.<br><br><button class="btn primary" data-act="sl-new">+ New setlist</button></div>'; return; }
    if (m.contains(document.activeElement) && document.activeElement.matches('input[data-sl], textarea[data-sl]')) {
      $('#sl-save').disabled = !slDirty(); return;  // keep typing focus; structure didn't change
    }
    const sl = S.sl, running = S.slJob?.state === 'running' && S.slJob.key === S.slSlug;
    const rows = sl.songs.map((e, i) => {
      const info = songInfo(e.song), lus = info?.lineups || [];
      const lid = e.lineup || 'default';
      return `<div class="sl-row" draggable="true" tabindex="0" data-i="${i}" data-slrow="${i}" data-tip="Drag to reorder" data-tip-key="⌥↑ ⌥↓">
        <span class="grip" aria-hidden="true">⋮⋮</span><span class="num">${String(i + 1).padStart(2, '0')}</span>
        <select class="inp" data-sl="song" data-i="${i}" aria-label="Song">${S.songs.map((s) => `<option value="${esc(s.slug)}" ${s.slug === e.song ? 'selected' : ''}>${esc(s.title)}</option>`).join('')}${info ? '' : `<option selected>${esc(e.song)} (missing)</option>`}</select>
        <select class="inp" data-sl="lineup" data-i="${i}" aria-label="Lineup">${(lus.includes(lid) ? lus : [lid, ...lus]).map((l) => `<option value="${esc(l)}" ${l === lid ? 'selected' : ''}>${esc(info?.lineup_names?.[l] || l)}${lus.includes(l) ? '' : ' (missing)'}</option>`).join('')}</select>
        <input class="inp grow" data-sl="notes" data-i="${i}" value="${esc(e.notes || '')}" placeholder="notes for the running order" aria-label="Notes">
        <span class="sl-tempo">${info?.tempo ? info.tempo + ' bpm' : ''}</span>
        <button class="btn icon small" data-act="sl-up" data-i="${i}" data-tip="Move up" data-tip-key="⌥↑" aria-label="Move up" ${i ? '' : 'disabled'}>↑</button>
        <button class="btn icon small" data-act="sl-down" data-i="${i}" data-tip="Move down" data-tip-key="⌥↓" aria-label="Move down" ${i < sl.songs.length - 1 ? '' : 'disabled'}>↓</button>
        <button class="btn icon small danger" data-act="sl-del" data-i="${i}" data-tip="Remove from setlist" aria-label="Remove from setlist">×</button></div>`;
    }).join('');
    const avail = S.songs;
    const r = S.slResult && S.slJob?.key === S.slSlug ? S.slResult : null;
    m.innerHTML = `
      <div class="sl-head">
        <label class="field">Setlist name<input class="inp" style="width:260px;font-weight:600" data-sl="name" value="${esc(sl.name)}"></label>
        <span class="hint mono">setlists/${esc(S.slSlug)}.json</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn" id="sl-save" data-act="sl-save" ${slDirty() ? '' : 'disabled'}>Save</button>
        <button class="btn primary" data-act="sl-build" ${running || !sl.songs.length ? 'disabled' : ''} data-tip="Build every song's Live set in show order + running order + phone fallback">${running ? 'Building…' : 'Build show'}</button>
        <button class="btn danger" data-act="sl-delete" data-tip="Delete setlist">Delete</button>
      </div>
      <div class="sl-rows" id="sl-rows">${rows || '<div class="panel-note">No songs yet: add one below.</div>'}</div>
      <div class="frow"><select class="inp" id="sl-add" aria-label="Add song"><option value="">+ Add song…</option>${avail.map((s) => `<option value="${esc(s.slug)}">${esc(s.title)}</option>`).join('')}</select>
        <span class="hint">${sl.songs.length} song${sl.songs.length === 1 ? '' : 's'} · lineup per song comes from that song's lineups</span></div>
      ${S.slJob && S.slJob.key === S.slSlug ? jobHtml(S.slJob, 'Build show') : ''}
      ${r ? showResultHtml(r) : ''}`;
    const log = $('#sl-main .joblog'); if (log) log.scrollTop = log.scrollHeight;
  },
};

function showResultHtml(r) {
  const fb = r.fallback || [];
  return `<section class="fcard result">
    <h3><span class="ok-mark">✓</span> Show built <span class="spacer" style="flex:1"></span>
      <a class="btn" href="${esc(r.running_order_html)}" target="_blank" rel="noopener">Running order ↗</a>
      <button class="btn primary" data-act="reveal" data-path="${esc(r.dir)}">Reveal in Finder</button></h3>
    <div class="lbl">Output folder</div><div class="path">${esc(r.dir)}</div>
    <div class="h">Live sets in show order · ${r.projects.length}</div>
    ${r.projects.map((p) => `<div class="lrow"><span class="n mono">${esc(p.split('/').pop())}</span><button class="btn small" data-act="reveal" data-path="${esc(p)}">Reveal</button></div>`).join('')}
    <div class="h">Phone fallback</div>
    ${fb.map((f) => `<div class="fbrow ${f.missing.length ? 'has-missing' : ''}"><b>${esc(f.song)}</b>
      <span class="d">${f.included.length} part${f.included.length === 1 ? '' : 's'} included${f.silent ? ' · <span class="bad">silent!</span>' : ''}</span>
      ${f.missing.length ? `<div class="bad">Missing from the fallback: ${f.missing.map((x) => `${esc(x.name)} (${esc(x.reason)})`).join('; ')}</div>` : ''}</div>`).join('') || '<div class="panel-note">Fallback not built.</div>'}
    ${r.fallback_report ? `<details><summary class="lbl" style="cursor:pointer">fallback-report.md</summary><pre class="md">${esc(r.fallback_report)}</pre></details>` : ''}
  </section>`;
}

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
      <button class="btn icon small danger" data-act="del-io" data-io="${kind}" data-key="${esc(k)}" data-tip="Remove" aria-label="Remove">×</button></div>`).join('');
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
          <td><button class="btn icon small danger" data-act="del-rig" data-pi="${pi}" data-ri="${ri}" data-tip="Remove rig" aria-label="Remove rig">×</button></td></tr>`).join('')}
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
  TIP?.hide();
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
  TIP?.hide();
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
function selectLineup(id) { S.lineupId = id; pref.set('lineup:' + S.slug, id); S.highlight = null; render(); runValidate(); lvSchedule(0); }

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
    // song options + section inspector
    case 'opt': {
      mutate(() => { const o = (S.song.options ||= {}); o[el.dataset.opt] = !o[el.dataset.opt]; });
      if (el.dataset.opt === 'cues') pvSetCues(!!S.song.options.cues);
      break;
    }
    case 'sec-vamp': if (secById(S.selSec)) mutate(() => { const s = secById(S.selSec); s.vamp = !s.vamp; }); break;
    // preview
    case 'pv-toggle': tpToggle(); break;
    case 'pv-from': if (S.selSec) tpFrom(S.selSec); break;
    case 'pv-loop': tpLoop(curLoop() && !S.selSec ? curLoop() : S.selSec); break;
    case 'pv-zero': tpSeek(0); break;
    case 'engine': setEngine(el.dataset.eng); break;
    case 'lv-open': lvOpen(); break;
    case 'pv-mode': S.pv.mode = el.dataset.mode; pref.set('pv.mode', S.pv.mode); if (PV.playing) pvPlay(PV.pos()); renderMain(); break;
    case 'pv-click': S.pv.click = !S.pv.click; pref.set('pv.click', S.pv.click); PV.setClick(S.pv.click); renderTransport(); break;
    case 'pv-cues': pvSetCues(!S.pv.cues); break;
    case 'pv-clear': S.pv.solo.clear(); S.pv.mute.clear(); if (isLive()) lvMix(); else if (PV.playing) pvPlay(PV.pos()); renderMain(); break;
    case 'solo': pvSoloMute('solo', pid); break;
    case 'mute': pvSoloMute('mute', pid); break;
    // import
    case 'new-song': openImport('new'); break;
    case 'reimport': openImport('re'); break;
    case 'pick': pickPath(el.dataset.kind); break;
    case 'imp-go': startImport(); break;
    case 'imp-back': setView('arrange'); break;
    case 'imp-open': loadSong(el.dataset.slug).then(() => setView('arrange')); break;
    // setlists
    case 'sl-new': newSetlist(); break;
    case 'sl-open': if (el.dataset.slug !== S.slSlug) openSetlist(el.dataset.slug); break;
    case 'sl-save': saveSetlist(); break;
    case 'sl-delete': deleteSetlist(); break;
    case 'sl-build': buildShow(); break;
    case 'sl-up': slMove(+el.dataset.i, -1); break;
    case 'sl-down': slMove(+el.dataset.i, 1); break;
    case 'sl-del': slEdit(() => S.sl.songs.splice(+el.dataset.i, 1)); break;
    case 'reveal': reveal(el.dataset.path); break;
    case 'help-tab': showHelp(el.dataset.tab); break;
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
    '-',
    { label: isLive() ? 'Play from here in Live' : 'Preview from here', key: 'P', action: () => tpFrom(sid) },
    { label: curLoop() === sid ? 'Stop looping' : isLive() ? 'Loop in Live' : 'Loop in preview', key: 'L', cur: curLoop() === sid, action: () => tpLoop(sid) },
    '-',
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
  TIP = initTips(richTip);

  document.addEventListener('click', (ev) => {
    const mi = ev.target.closest('.mi');
    if (mi && menuState) { const it = menuState.items[+mi.dataset.mi]; closeMenu(); it.action?.(); return; }
    const a = ev.target.closest('[data-act]');
    if (a && a.dataset.act !== 'noop') { ev.stopPropagation(); onAct(a.dataset.act, a, ev); }
  });
  document.addEventListener('mousedown', (ev) => { if (menuState && !ev.target.closest('#menu')) closeMenu(); });

  main.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-act]')) return;
    const ru = ev.target.closest('#ruler');
    if (ru) {
      tpSeek(beatAt(ru, ev.clientX) * spb());
      return;
    }
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
    const t = ev.target;
    if (t.id === 'zoom') { S.zoom = +t.value; VIEWS.arrange.fill(); return; }
    if (t.id === 'pv-vol') { S.pv.vol = +t.value; pref.set('pv.vol', S.pv.vol); PV.setVolume(S.pv.vol); return; }
    if (t.id === 'pv-cvol') { S.pv.cuesVol = +t.value; pref.set('pv.cuesVol', S.pv.cuesVol); PV.setGains(pvGain); return; }
    if (t.dataset.imp) {
      const I = S.imp; I[t.dataset.imp] = t.value;
      if (t.dataset.imp === 'slug') { I.slugTouched = !!t.value; I.slug = t.value.trim(); }
      if (t.dataset.imp === 'logicx' && !I.slugTouched) { I.slug = slugify(baseName(t.value)); const si = $('[data-imp=slug]'); if (si) si.value = t.value ? I.slug : ''; if (!t.value) I.slug = ''; }
      return;
    }
    if (t.dataset.sl && t.tagName === 'INPUT') {
      if (t.dataset.sl === 'name') S.sl.name = t.value; else S.sl.songs[+t.dataset.i][t.dataset.sl] = t.value;
      const b = $('#sl-save'); if (b) b.disabled = !slDirty();
      return;
    }
    if (t.dataset.bp) onBandInput(t);
  });
  main.addEventListener('change', (ev) => {
    const t = ev.target;
    if (t.dataset.iokey || t.dataset.ioval) onIoChange(t);
    else if (t.dataset.bp && t.tagName === 'SELECT') onBandInput(t);
    else if (t.dataset.check) { S.imp.checks[t.dataset.check] = t.checked; pref.set('exportChecks', S.imp.checks); renderMain(); }
    else if (t.dataset.sec) {
      const s = secById(S.selSec), k = t.dataset.sec, v = t.value.trim();
      if (!s) return;
      if (k === 'name' && !v) { t.value = s.name; return; }
      mutate(() => { if (k === 'cue' && !v) delete s.cue; else s[k] = v; });
    } else if (t.dataset.sl && t.tagName === 'SELECT') {
      const e = S.sl.songs[+t.dataset.i];
      slEdit(() => {
        e[t.dataset.sl] = t.value;
        if (t.dataset.sl === 'song') { const lus = songInfo(t.value)?.lineups || []; if (!lus.includes(e.lineup)) e.lineup = lus[0] || 'default'; }
      });
    } else if (t.id === 'sl-add' && t.value) {
      const lus = songInfo(t.value)?.lineups || [];
      slEdit(() => S.sl.songs.push({ song: t.value, lineup: lus.includes('default') ? 'default' : lus[0] || 'default', notes: '' }));
    } else if (t.dataset.sl === 'name' || t.dataset.sl === 'notes') renderMain();
  });
  main.addEventListener('keydown', (ev) => {
    const t = ev.target;
    if (t.dataset?.sec) {
      if (ev.key === 'Enter') { ev.preventDefault(); t.blur(); }
      if (ev.key === 'Escape') { ev.preventDefault(); const s = secById(S.selSec); t.value = s ? (t.dataset.sec === 'cue' ? s.cue || '' : s.name) : ''; t.blur(); }
      ev.stopPropagation(); return;
    }
    if (t.classList?.contains('sl-row') && ev.altKey && (ev.key === 'ArrowUp' || ev.key === 'ArrowDown')) {
      ev.preventDefault(); slMove(+t.dataset.i, ev.key === 'ArrowUp' ? -1 : 1); return;
    }
    if (t.dataset?.imp) { if (ev.key === 'Enter' && S.imp?.state !== 'running') { ev.preventDefault(); startImport(); } return; }
    if (t.id !== 'rename') return;
    if (ev.key === 'Enter') { ev.preventDefault(); commitRename(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); commitRename(false); }
    ev.stopPropagation();
  });
  main.addEventListener('focusout', (ev) => { if (ev.target.id === 'rename') setTimeout(() => commitRename(true), 0); });

  // drag & drop
  main.addEventListener('dragstart', (ev) => {
    const t = ev.target.closest('[data-drag-t]'), card = ev.target.closest('[data-dragpart]'), row = ev.target.closest?.('[data-slrow]');
    if (row) {
      if (ev.target.closest('input, select')) { ev.preventDefault(); return; }
      ev.dataTransfer.setData('application/x-lls-slrow', row.dataset.slrow); ev.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging');
    } else if (t) { ev.dataTransfer.setData('application/x-lls-treatment', t.dataset.dragT); ev.dataTransfer.setData('text/plain', t.dataset.dragT); ev.dataTransfer.effectAllowed = 'copy'; }
    else if (card) { ev.dataTransfer.setData('application/x-lls-part', card.dataset.dragpart); ev.dataTransfer.setData('text/plain', card.dataset.dragpart); ev.dataTransfer.effectAllowed = 'move'; card.classList.add('dragging'); }
  });
  main.addEventListener('dragend', () => { $$('.dragging, .dropover').forEach((e) => e.classList.remove('dragging', 'dropover')); });
  const target = (ev) => {
    const ty = dropTypes(ev);
    if (ty.includes('application/x-lls-treatment')) return ev.target.closest('.cell, .ph[data-droppart]');
    if (ty.includes('application/x-lls-part')) return ev.target.closest('.zone[data-drop]');
    if (ty.includes('application/x-lls-slrow')) return ev.target.closest('.sl-row');
    return null;
  };
  main.addEventListener('dragover', (ev) => {
    const el = target(ev); if (!el) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = dropTypes(ev).some((x) => x === 'application/x-lls-part' || x === 'application/x-lls-slrow') ? 'move' : 'copy';
    if (!el.classList.contains('dropover')) { $$('.dropover').forEach((e) => e.classList.remove('dropover')); el.classList.add('dropover'); }
  });
  main.addEventListener('dragleave', (ev) => { const el = target(ev); if (el && !el.contains(ev.relatedTarget)) el.classList.remove('dropover'); });
  main.addEventListener('drop', (ev) => {
    const el = target(ev); if (!el) return;
    ev.preventDefault();
    el.classList.remove('dropover');
    const t = ev.dataTransfer.getData('application/x-lls-treatment'), pid = ev.dataTransfer.getData('application/x-lls-part');
    const from = ev.dataTransfer.getData('application/x-lls-slrow');
    if (from !== '' && el.classList.contains('sl-row')) {
      const a = +from, b = +el.dataset.i;
      if (a !== b) slEdit(() => { const [x] = S.sl.songs.splice(a, 1); S.sl.songs.splice(b, 0, x); });
    } else if (t && el.classList.contains('cell')) mutate(() => setOverride(el.dataset.part, el.dataset.sid, t));
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
    const v = ev.target.value;
    if (v === '__new' || v === '__reimport') { ev.target.value = S.slug || ''; openImport(v === '__new' ? 'new' : 're'); return; }
    if (v === '__pkg_export' || v === '__pkg_import') { ev.target.value = S.slug || ''; (v === '__pkg_export' ? exportPackage : importPackage)(); return; }
    if (dirty() && !(await confirmDialog('Unsaved changes', `Discard unsaved changes to “${S.song.title}”?`, 'Discard'))) { ev.target.value = S.slug; return; }
    await loadSong(v);
    if (!SONG_VIEWS.has(S.view) && S.view !== 'setlists') setView('arrange');
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
  addEventListener('beforeunload', (ev) => { if (dirty() || bandDirty() || slDirty()) { ev.preventDefault(); ev.returnValue = ''; } });
  let raf = 0;
  new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { if (S.song && S.view === 'arrange' && $('#tl')) VIEWS.arrange.fill(); }); }).observe(main);
}

function dropPart(pid, zone) {
  if (zone.dataset.drop === 'rig') mutate(() => assignToRig(pid, zone.dataset.perf, zone.dataset.rig));
  else mutate(() => toPlayback(pid, zone.dataset.t || null));
}

function setView(v) {
  if (v === 'import' && !S.imp) { openImport(S.song ? 're' : 'new'); return; }
  S.view = v; if (v !== 'import') pref.set('view', v); S.renaming = null; closeMenu();
  history.replaceState(null, '', '#' + v);
  render();
  if (v === 'setlists') loadSetlists();
}

function applyTheme(t) { document.documentElement.dataset.theme = t; pref.set('theme', t); }

const HOW_STEPS = [
  ['Export from Logic', 'On a copy of the project: export all tracks as 24-bit WAVs (effects on, automation on, to project end), bounce the full mix, export aux returns as tracks. One folder.'],
  ['Import', 'Song picker → <b>+ New song</b>. Pick the .logicx and the export folder. The tool clones the project, reads the clone, links the stems, and lists what happened to every Logic track.'],
  ['Arrange & name sections', '<b>Arrange</b> tab: drag section edges, ⌥-click to split, double-click to rename. Mark vamps (∞) and set spoken cue text. Preview with Space.'],
  ['Treatments', 'Each part plays as backing audio, an audio loop, a MIDI clip through its instrument, or live by a performer. Drag a treatment chip onto a part (whole song) or onto a cell (one section).'],
  ['Lineups', '<b>Lineup</b> tab: drag parts onto performers’ rigs (keys, drums, vocal…), or <b>Suggest for N performers</b>. Fix what the Checks panel flags. One song can have many lineups.'],
  ['Build & play', '<b>Build</b> → <b>Open in Live</b> for one song, or <b>Setlists</b> → <b>Build show</b> for the whole set: one Live set per song, a printable running order, and phone fallback mixes.'],
];

function showHelp(tab = 'how') {
  const m = isMac ? '⌘' : 'Ctrl';
  const rows = [[`${m} S`, 'Save song + band'], [`${m} Z / ⇧${m} Z`, 'Undo / redo'], [`${m} B`, 'Build the Live set'], ['1 2 3 4', 'Arrange / Lineup / Band / Setlists'],
    ['Space', 'Preview: play / stop'], ['P', 'Preview from the selected section'], ['L', 'Loop the selected section'], ['Home', 'Playhead to bar 1'],
    ['← →', 'Select section'], ['Enter', 'Rename section'], ['S', 'Split section (or ⌥-click at a bar)'], ['M', 'Merge with next'], ['V', 'Toggle vamp'],
    ['Drag chip → cell', 'Override one section'], ['⌥-click cell', 'Clear override'], ['Right-click', 'Context menus everywhere'],
    ['Card + 1–9 / 0', 'Assign focused card to a rig zone / Playback'], ['⌥↑ / ⌥↓', 'Move the focused setlist row'], ['Esc', 'Clear selection / close'], ['?', 'This help']];
  const how = `
    <ol class="how">${HOW_STEPS.map(([t, d], i) => `<li><span class="stepn">${i + 1}</span><div><b>${esc(t)}</b><p>${d}</p></div></li>`).join('')}</ol>
    <div class="split2">
      <div class="fcard"><h3>This UI (before the gig)</h3><ul>
        <li>Imports Logic projects and stems; never touches the original project.</li>
        <li>Edits the song model: sections, treatments, lineups, band rigs.</li>
        <li>Previews the backing in the browser so the band can rehearse against it.</li>
        <li>Generates the Live sets, running order and phone fallback files.</li></ul></div>
      <div class="fcard"><h3>Ableton Live (on stage)</h3><ul>
        <li>Runs the generated set on its own; this tool isn't needed at the gig.</li>
        <li>One scene per section: launch scenes to move through the song (or auto-advance).</li>
        <li>Plays backing stems + MIDI clips, routes performers’ keyboards/kit/mics.</li>
        <li>Click and spoken cues go to the in-ear outputs.</li></ul></div>
    </div>`;
  const keys = `<div class="keys">${rows.map(([k, d]) => `<kbd>${esc(k)}</kbd><span>${esc(d)}</span>`).join('')}</div>`;
  openDialog(`<div class="dlg-h"><b>How it works</b><div class="tabs" role="tablist" style="margin-left:auto">
      <button role="tab" data-act="help-tab" data-tab="how" aria-selected="${tab === 'how'}">Workflow</button>
      <button role="tab" data-act="help-tab" data-tab="keys" aria-selected="${tab === 'keys'}">Shortcuts</button></div></div>
    <div class="dlg-b">${tab === 'how' ? how : keys}</div>
    <div class="dlg-f"><button class="btn" data-act="close-dlg">Close</button></div>`, tab === 'how' ? '' : 'small');
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
  if (['1', '2', '3', '4'].includes(ev.key) && !ev.target.closest?.('.card')) { setView(['arrange', 'lineup', 'band', 'setlists'][+ev.key - 1]); return; }
  if (ev.key === '?') { showHelp(); return; }
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
  if (ev.key === 'Escape') { S.selSec = null; S.highlight = null; renderMain(); return; }
  if (S.view !== 'arrange') return;
  if (ev.key === ' ' && !ev.target.closest?.('button, a')) { ev.preventDefault(); tpToggle(); return; }
  if (ev.key === 'Home') { ev.preventDefault(); onAct('pv-zero', document.body); return; }
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
  else if (k === 'p') tpFrom(S.selSec);
  else if (k === 'l') tpLoop(S.selSec);
}

window.lls = { S, PV, get TIP() { return TIP; } };  // console debugging handle
wire();
init().then(() => lvSchedule(0));
checkSetup();
