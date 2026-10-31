/* Native bridge stand-in + instrumentation for the web-fuzz lane, injected at document start
   in the main frame only (the app injects forMainFrameOnly: true, EngineWebView.swift:35).

   Base: NATIVE_JS from ~/addify-harness/mock-review/web/walk.py (window.ADDIFY_NATIVE exactly
   as walk.py sets it: no shazamkit, no addifyKit; the addify and addifyStore handlers).
   Extended per walk config (__CFG__):
     products: normal | empty | error | slow
     buy:      sequence of success | cancel | fail | pending | unverified | reject (cycled per tap)
     restore:  sequence of none | success | error
     pro:      start as a subscriber
     delay:    StoreKit answer delay, ms
     clip:     what the clipboard read answers: a string, or null = permission denied
     mic:      granted | denied | pending | absent, micBytes = recording size
   StoreKit replies mirror StoreKitHandler (EngineWebView.swift:412-452): status {pro},
   products [{id,name,price,period,trial?}], buy {pro, error}, restore {pro}.
   The entitlement survives reloads and relaunches like StoreKit's does (a cookie, which the
   page's own localStorage.clear() cannot touch). */
(function(){ if (window.top !== window) return;
  var CFG = __CFG__;
  window.ADDIFY_NATIVE = {platform:'ios', version:'1.0', share:true, store:1};
  var F = window.__fz = {posts:[], store:[], errs:[], rej:[], opens:[], hist:[], mut:0, lastMut:0, gum:0, rec:0,
                         storeLog:[], proGrants:[], t0:Date.now()};
  function cookieGet(n){ var m = document.cookie.match(new RegExp('(?:^|; )'+n+'=([^;]*)')); return m ? m[1] : null; }
  function cookieSet(n,v){ document.cookie = n+'='+v+'; path=/; max-age=86400'; }
  if (CFG.pro && !cookieGet('__fzpro')) cookieSet('__fzpro','1');
  function isPro(){ return cookieGet('__fzpro') === '1'; }
  var P = CFG.products === 'empty' ? [] : [
    {id:'com.addify.app.unlimited.monthly', name:'Addify Unlimited', price:'$4.99', period:'month'},
    {id:'com.addify.app.unlimited.yearly', name:'Addify Unlimited', price:'$24.99', period:'year', trial:'3 days free'}];
  var nBuy = +(cookieGet('__fzbuy')||0), nRest = +(cookieGet('__fzrest')||0);
  function seqAt(list, i, dflt){ list = (list && list.length) ? list : [dflt]; return list[i % list.length]; }
  function grant(src){ cookieSet('__fzpro','1'); F.proGrants.push({src:src, t:Date.now()}); }
  function reply(m){ var op = m && m.op;
    if (op === 'status') return {pro:isPro()};
    if (op === 'products') { if (CFG.products === 'error') throw new Error('products failed'); return P; }
    if (op === 'buy') { var b = seqAt(CFG.buy, nBuy++, 'cancel'); cookieSet('__fzbuy', nBuy);
      F.storeLog.push({op:'buy', id:m.id, mode:b});
      if (!P.some(function(p){ return p.id === m.id; })) return {pro:isPro(), error:'not available'};
      if (b === 'success') { grant('buy'); return {pro:true, error:''}; }
      if (b === 'reject') throw new Error('purchase bridge failed');
      return {pro:isPro(), error: b === 'fail' ? 'failed' : b === 'pending' ? 'pending' : b === 'unverified' ? 'unverified' : 'cancelled'}; }
    if (op === 'restore') { var r = seqAt(CFG.restore, nRest++, 'none'); cookieSet('__fzrest', nRest);
      F.storeLog.push({op:'restore', mode:r});
      if (r === 'error') throw new Error('restore failed');
      if (r === 'success') { grant('restore'); return {pro:true}; }
      return {pro:isPro()}; }
    throw new Error('bad op'); }
  var H = {
    addify: {postMessage: function(m){
      var rec; try { rec = JSON.parse(JSON.stringify(m||null)); } catch(e) { rec = String(m); }
      var snap = {};
      try { snap = {cur: window.curURL||'', used: +(localStorage.getItem('addify-scans-life')||0),
                    counted: JSON.parse(localStorage.getItem('addify-scans-life-urls')||'[]'), pro: !!window.PRO}; } catch(e) {}
      F.posts.push({m:rec, t:Date.now(), snap:snap}); }},
    addifyStore: {postMessage: function(m){ F.store.push(m && m.op);
      var d = CFG.products === 'slow' && m && m.op === 'products' ? 9000 : (CFG.delay || 600);
      return new Promise(function(res, rej){ setTimeout(function(){ try { res(reply(m)); } catch(e) { rej(new Error(e.message)); } }, d); }); }}
  };
  try { Object.defineProperty(window, 'webkit', {value:{messageHandlers:H}, configurable:true, writable:true}); }
  catch(e) { try { window.webkit = {messageHandlers:H}; } catch(_) {} }

  /* clipboard: the iOS paste callout accepted (a string) or refused (null) */
  window.__fzClip = ('clip' in CFG) ? CFG.clip : '';
  try {
    var cb = navigator.clipboard || {};
    cb.readText = function(){ var v = window.__fzClip;
      return v === null ? Promise.reject(new DOMException('Read permission denied.', 'NotAllowedError')) : Promise.resolve(String(v)); };
    cb.writeText = function(t){ window.__fzClip = String(t); return Promise.resolve(); };
    if (!navigator.clipboard) Object.defineProperty(navigator, 'clipboard', {configurable:true, value:cb});
  } catch(e) {}

  /* microphone: getUserMedia + MediaRecorder stand-ins (the real app grants the engine
     origin after the OS prompt, EngineWebView.swift:270-277) */
  var mic = CFG.mic || 'granted';
  function fakeStream(){
    try { var AC = window.AudioContext || window.webkitAudioContext; var ac = new AC();
      if (ac.createMediaStreamDestination) { var o = ac.createOscillator(); var d = ac.createMediaStreamDestination(); o.connect(d); o.start(); return d.stream; } } catch(e) {}
    return {__fake:true, getTracks:function(){ return [{stop:function(){}, kind:'audio'}]; }, getAudioTracks:function(){ return this.getTracks(); }};
  }
  try {
    var AC0 = window.AudioContext || window.webkitAudioContext;
    if (AC0 && AC0.prototype) { var orig = AC0.prototype.createMediaStreamSource;
      AC0.prototype.createMediaStreamSource = function(s){
        if (s && s.__fake || !orig) return {connect:function(){}, disconnect:function(){}};
        try { return orig.call(this, s); } catch(e) { return {connect:function(){}, disconnect:function(){}}; } }; }
  } catch(e) {}
  if (mic !== 'absent') {
    var md = navigator.mediaDevices || {};
    md.getUserMedia = function(){ F.gum++;
      if (mic === 'denied') return Promise.reject(new DOMException('The request is not allowed by the user agent or the platform in the current context.', 'NotAllowedError'));
      if (mic === 'pending') return new Promise(function(){});
      return Promise.resolve(fakeStream()); };
    try { Object.defineProperty(navigator, 'mediaDevices', {configurable:true, value:md}); } catch(e) {}
    function FakeMR(stream, opts){ this.stream = stream; this.state = 'inactive'; this.mimeType = (opts && opts.mimeType) || 'audio/mp4';
      this.ondataavailable = null; this.onstop = null; }
    FakeMR.isTypeSupported = function(t){ return /audio\/(mp4|webm)/.test(t||''); };
    FakeMR.prototype.start = function(){ this.state = 'recording'; F.rec++; };
    FakeMR.prototype.stop = function(){ if (this.state !== 'recording') return; this.state = 'inactive'; var self = this;
      var n = CFG.micBytes || 24000; var blob = new Blob([new Uint8Array(n)], {type:self.mimeType});
      setTimeout(function(){ try { if (self.ondataavailable) self.ondataavailable({data:blob}); } catch(e) {}
        try { if (self.onstop) self.onstop(); } catch(e) {} }, 30); };
    window.MediaRecorder = FakeMR;
  } else {
    try { Object.defineProperty(navigator, 'mediaDevices', {configurable:true, value:undefined}); } catch(e) {}
    try { delete window.MediaRecorder; window.MediaRecorder = undefined; } catch(e) {}
  }

  /* errors the page did not catch */
  window.addEventListener('error', function(e){ if (!(e instanceof ErrorEvent)) return;
    F.errs.push({msg:String(e.message||''), src:String(e.filename||'').replace(location.origin,''), line:e.lineno, col:e.colno, t:Date.now()}); }, true);
  window.addEventListener('unhandledrejection', function(e){ var r = e.reason;
    F.rej.push({msg:String(r && (r.message || r) || 'undefined'), stack:String(r && r.stack || '').slice(0,600), t:Date.now()}); });

  /* window.open and history, for the escape oracle */
  var wo = window.open;
  window.open = function(u, n, f){ F.opens.push({url:String(u||''), target:String(n||''), t:Date.now()}); return wo.apply(window, arguments); };
  // every link activation (target=_blank anchors, openExternal's temporary anchor, mailto:), with the page clock
  document.addEventListener('click', function(e){ try { var el = e.target && e.target.closest && e.target.closest('a[href]');
    if (el) F.opens.push({url:String(el.href||''), target:String(el.target||''), t:Date.now(), kind:'anchor'}); } catch(_) {} }, true);
  var hb = history.back.bind(history);
  history.back = function(){ F.hist.push({op:'back', t:Date.now(), len:history.length}); return hb(); };

  /* which elements got a click/touch handler (the page sets many through el.onclick) */
  function mark(el, lst){ try { if (!el || el.nodeType !== 1 || !el.setAttribute) return; if (lst) el.__fzL = true;
      if (!el.hasAttribute('data-fzc')) el.setAttribute('data-fzc','1'); } catch(e) {} }
  function unmark(el){ try { if (el && el.nodeType === 1 && !el.__fzL && el.hasAttribute('data-fzc')) el.removeAttribute('data-fzc'); } catch(e) {} }
  var kinds = {click:1, touchstart:1, touchend:1, pointerdown:1, pointerup:1, mousedown:1, mouseup:1};
  var ael = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(t, f, o){ if (kinds[t] && this && this.nodeType === 1 && this !== document.documentElement && this !== document.body) mark(this, true);
    return ael.call(this, t, f, o); };
  [HTMLElement.prototype, Element.prototype].forEach(function(proto){
    ['onclick','ontouchstart','ontouchend','onpointerdown','onpointerup'].forEach(function(p){
      var d = Object.getOwnPropertyDescriptor(proto, p); if (!d || !d.set || d.__fz) return;
      Object.defineProperty(proto, p, {configurable:true, enumerable:d.enumerable, get:d.get,
        set:function(v){ if (this !== document.body) { if (v) mark(this); else unmark(this); } return d.set.call(this, v); }}); }); });

  /* DOM change counter, for the "control does nothing" oracle */
  function observe(){ try { new MutationObserver(function(l){ F.mut += l.length; F.lastMut = Date.now(); })
      .observe(document.documentElement, {subtree:true, childList:true, attributes:true, characterData:true}); } catch(e) {} }
  if (document.documentElement) observe(); else document.addEventListener('readystatechange', observe, {once:true});
})();
