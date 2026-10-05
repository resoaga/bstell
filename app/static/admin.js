/* Admin: soft toasts, confirm dialog instead of the browser pop-up, flash from the server */
(function () {
  var box = document.createElement('div');
  box.className = 'toasts';
  box.setAttribute('aria-live', 'polite');
  document.body.appendChild(box);

  window.toast = function (text, kind) {
    var el = document.createElement('div');
    el.className = 'toast' + (kind === 'error' ? ' error' : '');
    el.innerHTML = '<span class="toast-dot">' + (kind === 'error' ? '!' : '✓') + '</span><span></span>';
    el.lastChild.textContent = text;
    box.appendChild(el);
    var close = function () { el.classList.add('out'); setTimeout(function () { el.remove(); }, 220); };
    el.addEventListener('click', close);
    setTimeout(close, kind === 'error' ? 6000 : 3000);
  };

  // The server leaves a short message in a cookie after every successful change
  function flash() {
    var m = document.cookie.match(/(?:^|; )admin_toast=([^;]*)/);
    if (!m) return;
    document.cookie = 'admin_toast=; Max-Age=0; path=/admin';
    try { window.toast(decodeURIComponent(m[1])); } catch (e) {}
  }
  flash();
  document.body.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.successful) flash();
  });
  document.body.addEventListener('htmx:responseError', function (e) {
    var msg = 'Das hat nicht geklappt. Bitte Seite neu laden und nochmal versuchen.';
    try { var d = JSON.parse(e.detail.xhr.responseText).detail; if (typeof d === 'string') msg = d; } catch (err) {}
    window.toast(msg, 'error');
  });
  document.body.addEventListener('htmx:sendError', function () {
    window.toast('Keine Verbindung zum Server.', 'error');
  });

  // data-confirm="Frage" on a form or a submit button
  var dlg = document.createElement('dialog');
  dlg.className = 'confirm';
  dlg.innerHTML = '<p></p><div class="row"><button type="button" class="ghost" data-no>Abbrechen</button><button type="button" class="danger" data-yes>Löschen</button></div>';
  document.body.appendChild(dlg);
  var pending = null;
  dlg.querySelector('[data-no]').addEventListener('click', function () { dlg.close(); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
  dlg.querySelector('[data-yes]').addEventListener('click', function () {
    var p = pending; pending = null; dlg.close();
    if (!p) return;
    if (p.run) { p.run(); return; }
    p.form.dataset.confirmed = '1'; p.form.requestSubmit(p.submitter || undefined);
  });
  function ask(form, submitter, el, run) {
    pending = { form: form, submitter: submitter, run: run };
    dlg.querySelector('[data-yes]').className = el.dataset.confirmStyle === 'plain' ? '' : 'danger';
    dlg.querySelector('p').textContent = el.dataset.confirm;
    dlg.querySelector('[data-yes]').textContent = el.dataset.confirmLabel || 'Löschen';
    dlg.showModal();
  }
  // htmx buttons outside a form (e.g. resend receipt)
  document.body.addEventListener('htmx:confirm', function (e) {
    var el = e.detail.elt;
    if (!el || !el.dataset || !el.dataset.confirm) return;
    e.preventDefault();
    ask(null, null, el, function () { e.detail.issueRequest(true); });
  });
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f.dataset.confirmed) { delete f.dataset.confirmed; return; }
    var el = (e.submitter && e.submitter.dataset.confirm) ? e.submitter : (f.dataset.confirm ? f : null);
    if (!el) return;
    e.preventDefault();
    ask(f, e.submitter, el);
  }, true);
})();

/* Klingelton für neue Bestellungen (Bestellseite + Probe in den Einstellungen) */
window.BstellSound = (function () {
  var ctx = null, audioEl = null, stopTimer = null;
  function audioCtx() {
    if (!ctx) { var C = window.AudioContext || window.webkitAudioContext; if (C) ctx = new C(); }
    if (ctx && ctx.state === 'suspended') ctx.resume();
    return ctx;
  }
  function tone(t, freq, len, vol, type) {
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || 'sine'; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(Math.max(vol, 0.0002), t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + len + 0.02);
  }
  // Each pattern schedules one round at time t and returns its length in seconds
  var PATTERNS = {
    klingel: function (t, v) { [880, 660, 880].forEach(function (f, i) { tone(t + i * 0.28, f, 0.24, 0.35 * v); }); return 0.9; },
    glocke: function (t, v) { [0, 0.7].forEach(function (d) { tone(t + d, 1046, 0.9, 0.3 * v); tone(t + d, 1568, 0.6, 0.15 * v); }); return 1.5; },
    pling: function (t, v) { tone(t, 1320, 0.18, 0.35 * v); tone(t + 0.22, 1760, 0.25, 0.35 * v); return 0.6; },
    sirene: function (t, v) { for (var i = 0; i < 4; i++) tone(t + i * 0.3, i % 2 ? 620 : 880, 0.28, 0.3 * v, 'triangle'); return 1.3; },
    fanfare: function (t, v) { [523, 659, 784, 1046].forEach(function (f, i) { tone(t + i * 0.17, f, 0.2, 0.3 * v, 'square'); }); tone(t + 0.8, 1046, 0.5, 0.3 * v, 'square'); return 1.4; }
  };
  var roundTimer = null;
  function stop() {
    clearTimeout(roundTimer); clearTimeout(stopTimer);
    if (audioEl) { audioEl.pause(); audioEl = null; }
  }
  // Plays "b:name" / "f:/url" for `seconds` (repeating), then stops
  function play(spec, seconds, volume) {
    stop();
    var vol = Math.max(0, Math.min(100, +volume || 0)) / 100;
    if (!spec || spec === 'n' || vol === 0) return;
    if (spec.indexOf('f:') === 0) {
      audioEl = new Audio(spec.slice(2)); audioEl.loop = true; audioEl.volume = vol;
      audioEl.play().catch(function () {});
      stopTimer = setTimeout(stop, seconds * 1000);
      return;
    }
    if (!audioCtx()) return;
    var pattern = PATTERNS[spec.slice(2)] || PATTERNS.klingel;
    var end = ctx.currentTime + seconds, next = ctx.currentTime;
    (function schedule() {
      if (next >= end) return;
      next += pattern(next, vol) + 0.2;
      roundTimer = setTimeout(schedule, Math.max(0, (next - ctx.currentTime - 0.5) * 1000));
    })();
    stopTimer = setTimeout(stop, seconds * 1000 + 300);
  }
  function preview(spec, volume) { play(spec, 3, volume); }

  // Ringer: ring `ring` s, silence `pause` s, again - as long as getSpec() returns something
  function Ringer(cfg, getSpecs) {
    var state = 'idle', timer = null, turn = 0;
    function cycle() {
      var specs = getSpecs();
      if (!specs.length) { state = 'idle'; stop(); return; }
      state = 'ring';
      play(specs[turn++ % specs.length], cfg.ring, cfg.volume);
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      timer = setTimeout(function () {
        stop(); state = 'pause';
        timer = setTimeout(cycle, cfg.pause * 1000);
      }, cfg.ring * 1000);
    }
    return {
      check: function () {
        var has = getSpecs().length > 0;
        if (state === 'idle' && has) cycle();
        else if (state !== 'idle' && !has) { clearTimeout(timer); state = 'idle'; stop(); }
      },
      off: function () { clearTimeout(timer); state = 'idle'; stop(); },
      idle: function () { return state === 'idle'; }
    };
  }
  return { unlock: audioCtx, preview: preview, Ringer: Ringer, stop: stop, ready: function () { return !ctx || ctx.state === 'running'; } };
})();
