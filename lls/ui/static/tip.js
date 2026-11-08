// Tooltips: one delegated listener for every [data-tip] / [data-tip-rich] element (replaces native title=).
//   data-tip="Title\nmuted detail line\n…"   plain text; first line is the title, the rest are muted detail lines
//   data-tip-key="⌘S"                        key hint shown on the title line
//   data-tip-rich="kind"                     content built lazily by the `rich(kind, el)` callback (HTML string)
//   data-tip-pos="right" | "below"           preferred side (default above); flips at the viewport edge
// Shows after 120 ms of hover, instantly when moving between targets within 400 ms of the last one; hides on leave,
// pointer down, scroll, wheel, Escape, blur and while dragging. Keyboard focus (:focus-visible) shows it too.

const SEL = '[data-tip],[data-tip-rich]';
const DELAY = 120, WARM = 400, GAP = 8, EDGE = 6;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Rich layout helper: {title, kbd?, lines?: [text | {html}], rows?: [[label, value]], keys?: [text]} -> HTML
export function tipHtml({ title, kbd, sub, lines = [], rows = [], keys = [], tone }) {
  const line = (l) => (l == null || l === '' ? '' : typeof l === 'object' ? `<div class="tip-d">${l.html}</div>` : `<div class="tip-d">${esc(l)}</div>`);
  return `<div class="tip-t${tone ? ' ' + tone : ''}">${esc(title)}${kbd ? `<kbd>${esc(kbd)}</kbd>` : ''}</div>`
    + (sub ? `<div class="tip-s">${esc(sub)}</div>` : '')
    + (rows.length ? `<div class="tip-rows">${rows.filter(Boolean).map(([k, v]) => `<span>${esc(k)}</span><b>${typeof v === 'object' && v ? v.html : esc(v)}</b>`).join('')}</div>` : '')
    + lines.map(line).join('')
    + (keys.length ? `<div class="tip-k">${keys.map(esc).join(' · ')}</div>` : '');
}

export function initTips(rich = () => '') {
  const box = document.createElement('div');
  box.className = 'tip'; box.id = 'lls-tip'; box.setAttribute('role', 'tooltip'); box.hidden = true;
  document.body.appendChild(box);

  let anchor = null, pending = null, timer = 0, shownAt = 0, lastHide = -1e9, dragging = false, suppressed = null;
  let px = -1, py = -1, watch = 0;
  let dismissed = false, dpx = 0, dpy = 0;   // Escape / scroll / click: stay hidden until the pointer really moves (re-renders fire pointerover)

  const content = (el) => {
    if (el.dataset.tipRich) return rich(el.dataset.tipRich, el) || '';
    const txt = el.dataset.tip; if (!txt) return '';
    const [t, ...rest] = txt.split('\n');
    return tipHtml({ title: t, kbd: el.dataset.tipKey, lines: rest });
  };

  function place(el) {
    const r = el.getBoundingClientRect(), vw = innerWidth, vh = innerHeight;
    box.style.left = '0px'; box.style.top = '0px';
    const b = box.getBoundingClientRect(), pos = el.dataset.tipPos || 'above';
    let x, y, side;
    if (pos === 'right') {
      side = r.right + GAP + b.width <= vw - EDGE ? 'right' : r.left - GAP - b.width >= EDGE ? 'left' : null;
      if (side) {
        x = side === 'right' ? r.right + GAP : r.left - GAP - b.width;
        y = Math.min(Math.max(EDGE, r.top + r.height / 2 - b.height / 2), vh - b.height - EDGE);
      }
    }
    if (!side) {
      const above = r.top - GAP - b.height >= EDGE, below = r.bottom + GAP + b.height <= vh - EDGE;
      side = pos === 'below' ? (below || !above ? 'below' : 'above') : (above || !below ? 'above' : 'below');
      y = side === 'above' ? r.top - GAP - b.height : r.bottom + GAP;
      y = Math.min(Math.max(EDGE, y), vh - b.height - EDGE);
      // anchor horizontally on the pointer for wide targets (section blocks, cells), else on the element centre
      const cx = r.width > 220 && px >= r.left && px <= r.right ? px : r.left + r.width / 2;
      x = cx - b.width / 2;
    }
    x = Math.min(Math.max(EDGE, x), vw - b.width - EDGE);
    box.style.left = Math.round(x) + 'px'; box.style.top = Math.round(y) + 'px';
    box.dataset.side = side;
  }

  function describe(el, on) {
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter((x) => x && x !== box.id);
    if (on) ids.push(box.id);
    if (ids.length) el.setAttribute('aria-describedby', ids.join(' ')); else el.removeAttribute('aria-describedby');
  }

  function show(el) {
    clearTimeout(timer); pending = null;
    if (dragging || !el.isConnected) return;
    const html = content(el);
    if (!html) { hide(); return; }
    if (anchor && anchor !== el) describe(anchor, false);
    anchor = el; box.innerHTML = html; box.hidden = false; box.classList.remove('in');
    place(el); void box.offsetWidth; box.classList.add('in');
    describe(el, true); shownAt = performance.now();
    clearInterval(watch); watch = setInterval(check, 200);
  }

  function hide() {
    clearTimeout(timer); pending = null;
    if (!anchor) return;
    describe(anchor, false);
    anchor = null; box.hidden = true; box.classList.remove('in'); lastHide = performance.now();
    clearInterval(watch); watch = 0;
  }

  // the app re-renders often (transport status while stems load, timeline on edits): follow the replacement element
  function check() {
    if (!anchor) return;
    if (anchor.isConnected) return;
    const el = px >= 0 ? document.elementFromPoint(px, py)?.closest(SEL) : null;
    if (el) { anchor = el; const html = content(el); if (html) { box.innerHTML = html; place(el); describe(el, true); return; } }
    hide();
  }

  function want(el, instant) {
    if (el === anchor || el === pending) return;
    if (el === suppressed) return;
    clearTimeout(timer);
    pending = el;
    const warm = anchor || performance.now() - lastHide < WARM;
    if (instant || warm) show(el); else timer = setTimeout(() => show(el), DELAY);
  }

  document.addEventListener('pointerover', (ev) => {
    if (ev.pointerType === 'touch') return;
    px = ev.clientX; py = ev.clientY;
    const el = ev.target.closest?.(SEL);
    if (!el) { if (anchor || pending) leave(); return; }
    if (ev.buttons || dragging || dismissed) return;      // mid-drag (boundary drags, chip drags): never pop up
    want(el, false);
  }, true);
  document.addEventListener('pointermove', (ev) => {
    px = ev.clientX; py = ev.clientY;
    if (ev.buttons) { if (anchor) hide(); return; }
    if (dismissed && Math.abs(px - dpx) + Math.abs(py - dpy) > 3) {
      dismissed = false;
      const el = ev.target.closest?.(SEL);
      if (el && el !== suppressed && !dragging) want(el, false);
    }
  }, { capture: true, passive: true });
  document.addEventListener('pointerout', (ev) => {
    const from = ev.target.closest?.(SEL);
    if (!from) return;
    const to = ev.relatedTarget?.closest?.(SEL);
    if (to === from) return;
    if (suppressed === from) suppressed = null;
    if (!to) leave();
  }, true);
  function leave() {
    clearTimeout(timer); pending = null;
    if (anchor && anchor.contains(document.activeElement) && anchor.matches(':focus-visible')) return;   // keep for keyboard focus
    hide();
  }
  document.addEventListener('pointerdown', (ev) => {
    const el = ev.target.closest?.(SEL);
    suppressed = el || null;             // clicking a target dismisses its tip until the pointer leaves it
    dismiss();
  }, true);
  document.addEventListener('focusin', (ev) => {
    const el = ev.target.closest?.(SEL);
    if (!el || !ev.target.matches(':focus-visible')) return;
    want(el, true);
  });
  document.addEventListener('focusout', (ev) => { if (anchor && ev.target.closest?.(SEL) === anchor) hide(); });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && (anchor || pending)) dismiss(); }, true);
  document.addEventListener('dragstart', () => { dragging = true; hide(); }, true);
  document.addEventListener('dragend', () => { dragging = false; }, true);
  document.addEventListener('drop', () => { dragging = false; }, true);
  addEventListener('scroll', () => { if (anchor || pending) dismiss(); }, true);
  addEventListener('wheel', () => { if (anchor || pending) dismiss(); }, { capture: true, passive: true });
  function dismiss() { dismissed = true; dpx = px; dpy = py; hide(); }
  addEventListener('resize', hide);
  addEventListener('blur', hide);

  return {
    hide,
    refresh() { if (anchor?.isConnected) { const h = content(anchor); if (h) { box.innerHTML = h; place(anchor); } else hide(); } else check(); },
    get el() { return box; },
    showFor: show,     // for screenshots / tests
  };
}
