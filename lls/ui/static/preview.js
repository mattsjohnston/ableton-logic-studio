// Browser preview playback (rehearsal/audition aid; the show itself runs in Live). Web Audio, no deps.
// A plan is {sources: [{key, url, pid, windows: [[a, b], ...] | null}], loop: {a, b} | null, tempo, bpb, total, click}.
// Times are seconds from bar 1. `windows` = where the source is audible (null = whole song).

const RATE = 32000;      // decode rate: plenty for audition, ~1/3 less memory than 48k for a dozen full-length stems
const PARALLEL = 3;      // concurrent stem fetch+decode

export class Preview {
  constructor(onState) {
    this.onState = onState || (() => {});
    this.ctx = null; this.master = null; this.bufs = new Map();
    this.playing = false; this.cursor = 0; this.t0 = 0; this.p0 = 0; this.plan = null;
    this.nodes = []; this.clickOn = false; this.vol = 0.8; this.timer = 0; this.clk = null; this.gainFn = () => 1;
    this.loadingKeys = new Set(); this.seq = 0;
  }

  ensureCtx() {
    if (this.ctx) return this.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    try { this.ctx = new AC({ sampleRate: RATE, latencyHint: 'playback' }); } catch { this.ctx = new AC(); }
    this.master = this.ctx.createGain(); this.master.gain.value = this.vol; this.master.connect(this.ctx.destination);
    return this.ctx;
  }

  reset() { this.stop(); this.bufs.clear(); this.loadingKeys.clear(); this.cursor = 0; this.emit(); }

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
    for (const n of this.nodes) { try { n.src.stop(); } catch { /* not started */ } n.src.disconnect(); }
    this.nodes = [];
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
