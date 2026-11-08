// Browser preview playback (rehearsal/audition aid; the show itself runs in Live). Web Audio, no deps.
// A plan is {sources: [{key, url, pid, windows: [[a, b], ...] | null}], synths: [{pid, voice, notes, windows}],
//            loop: {a, b} | null, tempo, bpb, total}.
// Times are seconds from bar 1. `windows` = where the source is audible (null = whole song).
// synths: MIDI-only parts played on a tiny Web Audio synth. notes = [[start_s, dur_s, pitch, velocity], ...] sorted by start;
// voice = {drum: true, kind: 'kick'|'snare'|'hat'|..|null (GM map by pitch), size} | {drum: false, kind: 'bass'|'lead'|'pad'|'keys'}.

const RATE = 32000;      // decode rate: plenty for audition, ~1/3 less memory than 48k for a dozen full-length stems
const PARALLEL = 3;      // concurrent stem fetch+decode
const AHEAD = 0.15;      // note scheduling lookahead (s); tick runs every 25 ms
const mtof = (n) => 440 * 2 ** ((n - 69) / 12);
const GM = (n) => (n === 35 || n === 36 ? 'kick' : n === 37 ? 'rim' : n === 38 || n === 40 ? 'snare' : n === 39 ? 'clap'
  : n === 42 || n === 44 ? 'hat' : n === 46 ? 'openhat' : [49, 52, 55, 57].includes(n) ? 'crash' : [51, 53, 59].includes(n) ? 'ride'
  : n >= 41 && n <= 50 ? 'tom' : n === 70 || n === 82 ? 'shaker' : 'perc');
// melodic presets: oscillators [type, detune cents, semitones, level], filter (Hz, Q, env amount), envelope, voice gain
const SYN = {
  bass: { osc: [['sawtooth', 0, 0, 1], ['square', 0, -12, 0.5]], f: 650, q: 5, fenv: 900, a: 0.004, d: 0.18, sus: 0.7, r: 0.07, g: 0.2 },
  lead: { osc: [['square', 0, 0, 0.7], ['sawtooth', 8, 0, 0.6]], f: 2600, q: 2, fenv: 1800, a: 0.006, d: 0.2, sus: 0.75, r: 0.12, g: 0.11 },
  pad: { osc: [['sawtooth', -9, 0, 0.6], ['sawtooth', 9, 0, 0.6]], f: 1300, q: 1, fenv: 400, a: 0.12, d: 0.4, sus: 0.85, r: 0.45, g: 0.07 },
  keys: { osc: [['triangle', 0, 0, 1], ['sawtooth', 4, 12, 0.25]], f: 2200, q: 1, fenv: 1400, a: 0.003, d: 0.35, sus: 0.45, r: 0.18, g: 0.12 },
};

export class Preview {
  constructor(onState) {
    this.onState = onState || (() => {});
    this.ctx = null; this.master = null; this.bufs = new Map();
    this.playing = false; this.cursor = 0; this.t0 = 0; this.p0 = 0; this.plan = null;
    this.nodes = []; this.clickOn = false; this.vol = 0.8; this.timer = 0; this.clk = null; this.gainFn = () => 1;
    this.loadingKeys = new Set(); this.seq = 0; this.synths = []; this.sp = null; this.noiseBuf = null;
  }

  ensureCtx() {
    if (this.ctx) return this.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    try { this.ctx = new AC({ sampleRate: RATE, latencyHint: 'playback' }); } catch { this.ctx = new AC(); }
    this.master = this.ctx.createGain(); this.master.gain.value = this.vol; this.master.connect(this.ctx.destination);
    return this.ctx;
  }

  reset() { this.stop(); this.bufs.clear(); this.loadingKeys.clear(); this.cursor = 0; this.emit(); }

  forget(key) { this.bufs.delete(key); this.loadingKeys.delete(key); }

  status(key) { const b = this.bufs.get(key); return b ? b.state : null; }
  error(key) { return this.bufs.get(key)?.err; }

  emit() {
    this.onState({ playing: this.playing, preparing: !!this.preparing, loading: this.loadingKeys.size });
  }

  load(key, url) {
    const have = this.bufs.get(key);
    if (have) return have.promise;
    const ent = { state: 'loading', buf: null, err: null, promise: null };
    this.bufs.set(key, ent); this.loadingKeys.add(key); this.emit();
    ent.promise = this.slot(async () => {
      const ctx = this.ensureCtx();
      const get = async (u) => {
        const r = await fetch(u);
        if (!r.ok) { let m = `${r.status}`; try { m = (await r.json()).error || m; } catch { /* not json */ } throw new Error(m); }
        return ctx.decodeAudioData(await r.arrayBuffer());
      };
      try {
        try { ent.buf = await get(url); } catch (e) {
          if (/^\d|no stem|missing/.test(e.message)) throw e;             // server said no: don't retry
          ent.buf = await get(url + (url.includes('?') ? '&' : '?') + 'pcm16=1');  // undecodable original: 16-bit copy
        }
        ent.state = 'ok';
      } catch (e) { ent.state = 'err'; ent.err = e.message || String(e); }
      this.loadingKeys.delete(key); this.emit();
      return ent;
    });
    return ent.promise;
  }

  slot(fn) {  // tiny concurrency limiter
    this.q ||= []; this.active ||= 0;
    return new Promise((res) => {
      const run = async () => { this.active++; try { res(await fn()); } finally { this.active--; this.q.shift()?.(); } };
      if (this.active < PARALLEL) run(); else this.q.push(run);
    });
  }

  pos() {
    if (!this.playing) return this.cursor;
    const el = Math.max(0, this.ctx.currentTime - this.t0), L = this.plan.loop;
    if (L && L.b > L.a) {
      if (this.p0 + el < L.b) return Math.max(0, this.p0 + el);
      return L.a + ((this.p0 + el - L.a) % (L.b - L.a));
    }
    return this.p0 + el;
  }

  async play(from, plan) {
    const seq = ++this.seq;
    this.stopNodes();
    this.plan = plan; this.cursor = from;
    const ctx = this.ensureCtx();
    if (ctx.state === 'suspended') await ctx.resume();
    this.playing = false; this.preparing = true; this.emit();
    await Promise.all(plan.sources.map((s) => this.load(s.key, s.url)));
    if (seq !== this.seq) return;                         // stopped / restarted while loading
    this.preparing = false;
    const L = plan.loop;
    if (L && (from < L.a || from >= L.b)) from = L.a;
    if (!L && from >= plan.total) from = 0;
    const t0 = ctx.currentTime + 0.06;
    for (const s of plan.sources) {
      const ent = this.bufs.get(s.key);
      if (ent?.state !== 'ok' || from >= ent.buf.duration) continue;
      const src = ctx.createBufferSource(); src.buffer = ent.buf;
      const win = ctx.createGain(), part = ctx.createGain();
      part.gain.value = this.gainFn(s.pid);
      src.connect(win).connect(part).connect(this.master);
      if (L) {
        src.loop = true; src.loopStart = L.a; src.loopEnd = Math.min(L.b, ent.buf.duration);
        win.gain.value = !s.windows || s.windows.some(([a, b]) => a <= L.a + 1e-3 && b > L.a + 1e-3) ? 1 : 0;
      } else if (s.windows) {
        const inside = (t) => s.windows.some(([a, b]) => t >= a - 1e-6 && t < b - 1e-6);
        win.gain.setValueAtTime(inside(from) ? 1 : 0, t0);
        const edges = [...new Set(s.windows.flat())].filter((t) => t > from).sort((x, y) => x - y);
        for (const t of edges) win.gain.setTargetAtTime(inside(t) ? 1 : 0, t0 + (t - from), 0.004);
      }
      src.start(t0, from);
      this.nodes.push({ src, part, pid: s.pid });
    }
    this.t0 = t0; this.p0 = from; this.playing = true;
    const spb = 60 / plan.tempo;
    const nb = Math.ceil(from / spb - 1e-6) * spb;
    this.clk = { pos: nb, t: t0 + (nb - from) };
    this.synths = [];
    for (const sy of plan.synths || []) {
      if (!sy.notes?.length) continue;
      const bus = ctx.createGain(); bus.gain.value = this.gainFn(sy.pid); bus.connect(this.master);
      this.synths.push({ ...sy, bus });
      this.nodes.push({ src: null, part: bus, pid: sy.pid });
    }
    this.sp = { pos: from, t: t0 };     // note scheduler: song position `pos` sounds at context time `t`
    this.timer = setInterval(() => this.tick(), 25); this.tick();
    this.emit();
  }

  tick() {
    if (!this.playing) return;
    const ctx = this.ctx, plan = this.plan, L = plan.loop, spb = 60 / plan.tempo;
    if (!L && this.pos() >= plan.total) { this.cursor = 0; this.stop(); return; }
    while (this.clk.t < ctx.currentTime + 0.12) {
      if (L && this.clk.pos >= L.b - 1e-6) this.clk.pos = L.a + (this.clk.pos - L.b);
      if (this.clickOn && this.clk.t >= ctx.currentTime - 0.01) {
        const beat = Math.round(this.clk.pos / spb), acc = beat % Math.round(plan.bpb) === 0;
        this.beep(this.clk.t, acc);
      }
      this.clk.pos += spb; this.clk.t += spb;
    }
    if (this.synths.length) this.scheduleSynths(ctx.currentTime + AHEAD, L);
  }

  // ---- browser synth for MIDI-only parts
  scheduleSynths(horizon, L) {
    // walk song time from sp.pos up to the horizon, wrapping at the loop end
    for (let guard = 0; this.sp.t < horizon && guard < 64; guard++) {
      const limit = L && L.b > L.a ? L.b : Infinity;
      const end = Math.min(this.sp.pos + (horizon - this.sp.t), limit);
      if (end > this.sp.pos) this.notesIn(this.sp.pos, end, this.sp.t, L);
      this.sp.t += end - this.sp.pos; this.sp.pos = end;
      if (end >= limit - 1e-9) this.sp.pos = L.a; else break;
    }
  }

  notesIn(a, b, tA, L) {
    const now = this.ctx.currentTime;
    for (const sy of this.synths) {
      const ns = sy.notes;
      let lo = 0, hi = ns.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (ns[m][0] < a - 1e-9) lo = m + 1; else hi = m; }
      for (let i = lo; i < ns.length && ns[i][0] < b - 1e-9; i++) {
        const [st, du, pitch, vel] = ns[i];
        let stop = st + du;
        if (sy.windows) {
          const w = sy.windows.find(([x, y]) => st >= x - 1e-6 && st < y - 1e-6);
          if (!w) continue;
          stop = Math.min(stop, w[1]);
        }
        if (L && L.b > L.a) stop = Math.min(stop, L.b);
        const when = tA + (st - a);
        if (when < now - 0.005) continue;
        this.voice(sy, Math.max(when, now), Math.max(0.02, stop - st), pitch, (vel ?? 100) / 127);
      }
    }
  }

  noise() {
    if (this.noiseBuf) return this.noiseBuf;
    const ctx = this.ctx, b = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    return (this.noiseBuf = b);
  }

  env(t, peak, attack, decay, out) {   // percussive amp envelope -> GainNode connected to out
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(Math.max(peak, 0.0002), t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
    g.connect(out); return g;
  }

  hit(t, out, { type, freq, hp, bp, q = 1, peak, attack = 0.001, decay }) {   // noise burst through a filter
    const ctx = this.ctx, src = ctx.createBufferSource(); src.buffer = this.noise();
    src.playbackRate.value = 0.9 + Math.random() * 0.2;
    const f = ctx.createBiquadFilter(); f.type = type || (hp ? 'highpass' : 'bandpass'); f.frequency.value = hp || bp || freq; f.Q.value = q;
    src.connect(f).connect(this.env(t, peak, attack, decay, out));
    src.start(t, Math.random() * 0.5); src.stop(t + attack + decay + 0.02);
  }

  tone(t, out, { wave = 'sine', f0, f1, sweep = 0.1, peak, attack = 0.001, decay }) {   // pitched hit with a downward sweep
    const ctx = this.ctx, o = ctx.createOscillator(); o.type = wave;
    o.frequency.setValueAtTime(f0, t); if (f1) o.frequency.exponentialRampToValueAtTime(f1, t + sweep);
    o.connect(this.env(t, peak, attack, decay, out));
    o.start(t); o.stop(t + attack + decay + 0.02);
  }

  voice(sy, t, dur, pitch, v) {
    const out = sy.bus, vo = sy.voice || {};
    if (vo.drum) {
      const kind = vo.kind || GM(pitch);
      switch (kind) {
        case 'kick': this.tone(t, out, { f0: 160, f1: 42, sweep: 0.11, peak: 0.95 * v, decay: 0.42 }); this.hit(t, out, { bp: 3000, peak: 0.15 * v, decay: 0.008 }); break;
        case 'snare': this.hit(t, out, { bp: 1900, q: 0.7, peak: 0.55 * v, decay: 0.19 }); this.tone(t, out, { wave: 'triangle', f0: 230, f1: 170, sweep: 0.05, peak: 0.35 * v, decay: 0.09 }); break;
        case 'clap': for (const dt of [0, 0.011, 0.022]) this.hit(t + dt, out, { bp: 1150, q: 1.4, peak: 0.45 * v, decay: dt < 0.02 ? 0.012 : 0.16 }); break;
        case 'rim': this.tone(t, out, { wave: 'square', f0: 1700, peak: 0.18 * v, decay: 0.025 }); break;
        case 'hat': this.hit(t, out, { hp: 7500, peak: 0.28 * v, decay: 0.05 }); break;
        case 'openhat': this.hit(t, out, { hp: 7000, peak: 0.25 * v, decay: Math.min(0.45, Math.max(0.15, dur)) }); break;
        case 'crash': this.hit(t, out, { hp: 4500, peak: 0.3 * v, attack: 0.003, decay: 1.5 }); break;
        case 'ride': this.hit(t, out, { bp: 8500, q: 1.2, peak: 0.22 * v, decay: 0.7 }); this.tone(t, out, { wave: 'square', f0: 3200, peak: 0.03 * v, decay: 0.3 }); break;
        case 'shaker': this.hit(t, out, { bp: 6500, q: 1.6, peak: 0.25 * v, attack: 0.012, decay: 0.07 }); break;
        case 'tom': {
          const base = vo.kind === 'tom' ? { low: 95, mid: 135, high: 190 }[vo.size] || 135 : Math.min(260, Math.max(70, mtof(pitch) / 2));
          this.tone(t, out, { f0: base * 1.7, f1: base, sweep: 0.07, peak: 0.75 * v, decay: 0.38 }); break;
        }
        default: this.tone(t, out, { f0: mtof(pitch), f1: mtof(pitch) * 0.7, sweep: 0.08, peak: 0.4 * v, decay: 0.14 });
      }
      return;
    }
    const P = SYN[vo.kind] || SYN.keys, ctx = this.ctx, f = mtof(pitch), end = t + dur;
    const filt = ctx.createBiquadFilter(); filt.type = 'lowpass'; filt.Q.value = P.q;
    filt.frequency.setValueAtTime(P.f + P.fenv * v, t); filt.frequency.setTargetAtTime(P.f, t + P.a, P.d);
    const amp = ctx.createGain(), peak = P.g * (0.35 + 0.65 * v);
    amp.gain.setValueAtTime(0, t); amp.gain.linearRampToValueAtTime(peak, t + P.a);
    amp.gain.setTargetAtTime(peak * P.sus, t + P.a, P.d / 3);
    amp.gain.cancelAndHoldAtTime?.(end);
    amp.gain.setTargetAtTime(0, end, P.r / 3);
    filt.connect(amp).connect(sy.bus);
    for (const [type, det, semi, lvl] of P.osc) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = type; o.frequency.value = f * 2 ** (semi / 12); o.detune.value = det; g.gain.value = lvl;
      o.connect(g).connect(filt); o.start(t); o.stop(end + P.r * 2 + 0.05);
    }
  }

  beep(t, accent) {
    const ctx = this.ctx, o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = accent ? 1500 : 1000;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(accent ? 0.5 : 0.32, t + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    o.connect(g).connect(this.master); o.start(t); o.stop(t + 0.06);
  }

  stopNodes() {
    clearInterval(this.timer); this.timer = 0;
    for (const n of this.nodes) {
      if (n.src) { try { n.src.stop(); } catch { /* not started */ } n.src.disconnect(); }
      else n.part.disconnect();     // synth bus: cuts the voices already scheduled on it
    }
    this.nodes = []; this.synths = [];
  }

  stop() {
    if (this.playing) this.cursor = this.pos();
    this.seq++; this.stopNodes(); this.playing = false; this.preparing = false; this.emit();
  }

  seek(sec) { this.cursor = Math.max(0, sec); }

  setGains(fn) {
    this.gainFn = fn;
    if (!this.ctx) return;
    for (const n of this.nodes) n.part.gain.setTargetAtTime(fn(n.pid), this.ctx.currentTime, 0.01);
  }

  setVolume(v) { this.vol = v; if (this.master) this.master.gain.setTargetAtTime(v, this.ctx.currentTime, 0.02); }
  setClick(on) { this.clickOn = on; }
}
