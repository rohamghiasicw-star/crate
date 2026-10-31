/* One snapshot of what a user can see and tap, plus the app state the oracles check.
   Called as page.evaluate(PROBE, {full}) after every action. "full" adds the slow checks
   (horizontal overflow scan, clipped dialogs). */
(opts) => {
  const full = !!(opts && opts.full);
  const W = innerWidth, H = innerHeight;
  const SEL = 'button, a[href], input:not([type=hidden]), textarea, select, summary, [role=button], [role=switch], ' +
              '[role=radio], [role=tab], [role=link], [onclick], [data-play], [data-ipopen], [data-fzc], label[for], [tabindex]:not([tabindex="-1"])';
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const lab = el => norm(el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || el.title || el.getAttribute('alt') || '').slice(0, 48);
  const volatile = s => s.toLowerCase().replace(/\d+/g, '#').slice(0, 24);
  const keyOf = el => {
    if (el.id) return '#' + el.id;
    let a = el.parentElement, d = 0;
    while (a && !a.id && d < 10) { a = a.parentElement; d++; }
    const base = a && a.id ? '#' + a.id + ' ' : '';
    const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(c => c && !/^(on|did|live|playing|hdone|done|sel|active|open)$/.test(c)).slice(0, 2).join('.') : '';
    const at = ['data-play', 'data-ipopen', 'data-tab', 'data-id', 'data-p', 'role'].filter(x => el.hasAttribute(x)).map(x => '[' + x + ']').join('');
    const al = el.getAttribute('aria-label');
    return base + el.tagName.toLowerCase() + (cls ? '.' + cls : '') + at + (al ? '[al=' + volatile(al) + ']' : '');
  };
  const shown = el => {
    if (el.closest('[hidden]')) return false;
    const s = getComputedStyle(el);
    return !(s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0);
  };
  const controls = [], seen = new Set();
  const big = W * H * 0.45;
  for (const el of document.querySelectorAll(SEL)) {
    if (seen.has(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom <= 1 || r.top >= H - 1 || r.right <= 1 || r.left >= W - 1) continue;
    const tag = el.tagName.toLowerCase();
    // a large container with a backdrop or delegated listener is not a control (tap_xy still taps it)
    if (r.width * r.height > big && !/^(button|a|input|textarea|select|summary)$/.test(tag)) continue;
    if (el.closest('[hidden]')) continue;
    const x0 = Math.max(0, r.left), x1 = Math.min(W, r.right), y0 = Math.max(0, r.top), y1 = Math.min(H, r.bottom);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const hit = document.elementFromPoint(cx, cy);
    if (!hit || !(hit === el || el.contains(hit))) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    seen.add(el);
    const href = tag === 'a' ? (el.getAttribute('href') || '') : '';
    let ext = false;
    try { if (href && !href.startsWith('#') && !href.startsWith('javascript:')) ext = new URL(el.href).host !== location.host; } catch (e) {}
    const inp = (tag === 'input' && !/^(button|submit|checkbox|radio|range|file|color|image|reset)$/i.test(el.type || '')) || tag === 'textarea';
    const sel = el.classList.contains('on') || el.classList.contains('sel') || el.classList.contains('active') ||
                el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-current') === 'page';
    controls.push({k: keyOf(el), l: lab(el), t: tag, x: Math.round(cx), y: Math.round(cy), w: Math.round(r.width), h: Math.round(r.height),
                   href: href.slice(0, 200), tg: el.getAttribute('target') || '', ext, inp, sel,
                   cls: (typeof el.className === 'string' ? el.className : '').slice(0, 60) + '|' + el.childElementCount + '|' + (el.innerHTML.length),
                   dis: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'), cur: el.getAttribute('aria-current') === 'page',
                   st: (el.getAttribute('aria-checked') || '') + (el.getAttribute('aria-pressed') || '') + (el.getAttribute('aria-expanded') || '')});
  }
  const app = document.getElementById('app');
  const state = app ? app.getAttribute('data-state') : null;
  const OV = ['onb', 'onb4', 'paywall', 'player', 'sheet', 'keep', 'picker', 'splash', 'ipl', 'scannote', 'p5', 'onelf'];
  const vis = el => !!el && !el.closest('[hidden]') && shown(el) && el.getBoundingClientRect().height > 0;
  const overlays = OV.filter(id => vis(document.getElementById(id)));
  const modals = [];
  for (const d of document.querySelectorAll('[aria-modal="true"], [role=dialog]')) {
    if (!vis(d)) continue;
    const root = d.closest('#onb, #onb4, #paywall, #player, #sheet, #keep, #picker, .p5, .o4, [id]') || d;
    const rid = root.id || keyOf(root);
    if (modals.some(m => m.id === rid)) continue;
    const r = d.getBoundingClientRect();
    const inside = controls.filter(c => { const e = document.elementFromPoint(c.x, c.y); return e && root.contains(e); }).length;
    let scrollable = false;
    for (let a = d; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (/(auto|scroll)/.test(s.overflowY) && a.scrollHeight > a.clientHeight + 2) { scrollable = true; break; }
    }
    modals.push({id: rid, top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height), inside, scrollable,
                 clipped: (r.bottom > H + 2 || r.top < -2) && !scrollable});
  }
  const txt = id => { const e = document.getElementById(id); return e && vis(e) ? norm(e.innerText).slice(0, 160) : ''; };
  const bodyText = norm(document.body ? document.body.innerText : '');
  const loading = {
    scanning: state === 'scanning',
    listening: state === 'listening',
    prices: /Loading prices/.test(txt('pwb')) || /Loading prices/.test(txt('p5plans')),
    restore: /Checking your Apple ID/.test(txt('pwm')),
    chart: /Loading the chart/.test(txt('trendbody')) || /Loading the chart/.test(txt('trendteaser')),
    playlists: /Loading your playlists/.test(txt('pkbody')),
    search: /Searching|Looking/i.test(txt('sbody')) && !!(window.sState && window.sState.busy),
  };
  let used = 0, counted = [], ls = {};
  try { used = +(localStorage.getItem('addify-scans-life') || 0); counted = JSON.parse(localStorage.getItem('addify-scans-life-urls') || '[]');
        ls = {onb: localStorage.getItem('addify-onboarded'), master: !!localStorage.getItem('addify-master'), dev: localStorage.getItem('addify-dev'),
              queue: localStorage.getItem('addify-queue'), newflow: localStorage.getItem('addify-newflow'), n: localStorage.length}; } catch (e) {}
  const out = {
    url: location.href, path: location.pathname, origin: location.origin, state, overlays, modals, controls,
    loading, used, counted: counted.length, countedList: counted.slice(-12), ls,
    g: {PRO: !!window.PRO, PAYWALL_ON: !!window.PAYWALL_ON, STORE: !!window.STORE, MASTER: !!window.MASTER, DEV: !!window.DEV,
        NEWFLOW: !!window.NEWFLOW, busy: !!window.busy, cur: String(window.curURL || '').slice(0, 200), engineUp: window.engineUp === true,
        isApp: !!document.getElementById('app'), secure: !!window.isSecureContext},
    pill: txt('scanpill'), err: txt('err'), ttl: txt('matchttl'), note: txt('scannote'), pwm: txt('pwm'), pwb: txt('pwb'),
    toastOver: (() => { const n = document.getElementById('scannote'); if (!n || !n.classList.contains('on') || !vis(n)) return [];
      const r = n.getBoundingClientRect(); const out = [];
      for (const el of document.querySelectorAll('#matchttl, .hd .brand, .hd .ttl, #scanpill, #back, #share, #cancel, #lcancel, #theme, .tabbar .tab')) {
        if (!vis(el)) continue; const q = el.getBoundingClientRect();
        if (q.right > r.left + 2 && q.left < r.right - 2 && q.bottom > r.top + 2 && q.top < r.bottom - 2) out.push(keyOf(el)); }
      return out; })(),
    resultText: state === 'locked' ? txt('result').slice(0, 160) : '',
    textHash: (() => { let h = 0; for (let i = 0; i < bodyText.length; i++) h = (h * 31 + bodyText.charCodeAt(i)) | 0; return h; })(),
    textLen: bodyText.length,
    devText: (bodyText.match(/\b(developer settings|client id|redirect uri|engine address|debug mode|master unlocked|preview off|localhost|127\.0\.0\.1|sslip\.io|undefined|NaN|\[object Object\])\b/gi) || []).slice(0, 8),
    rootCls: document.documentElement.className + '|' + (document.documentElement.getAttribute('data-theme') || ''),
    scrollY: Math.round(scrollY), active: document.activeElement && document.activeElement !== document.body ? keyOf(document.activeElement) : '',
    inputs: [...document.querySelectorAll('input,textarea')].map(i => (i.id || '') + '=' + String(i.value || '').length).join(','),
    fz: window.__fz ? {t0: window.__fz.t0, posts: window.__fz.posts.length, store: window.__fz.store.length, errs: window.__fz.errs.slice(-5), rej: window.__fz.rej.slice(-5),
                       nErr: window.__fz.errs.length, nRej: window.__fz.rej.length, opens: window.__fz.opens.length, mut: window.__fz.mut,
                       hist: window.__fz.hist.length, grants: window.__fz.proGrants.length, gum: window.__fz.gum} : null,
  };
  if (full) {
    const over = [];
    if (document.documentElement.scrollWidth > W + 1) over.push('page scrollWidth ' + document.documentElement.scrollWidth + ' > ' + W);
    const clippedX = el => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        const s = getComputedStyle(a); if (['hidden', 'auto', 'scroll', 'clip'].includes(s.overflowX)) return true; } return false; };
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > H) continue;
      if ((r.right > W + 2 || r.left < -2) && shown(el) && !clippedX(el) && getComputedStyle(el).position !== 'fixed') {
        over.push(keyOf(el) + ' x=' + Math.round(r.left) + '..' + Math.round(r.right));
        if (over.length > 6) break;
      }
    }
    out.overflow = over;
  }
  return out;
}
