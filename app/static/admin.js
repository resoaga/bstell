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
  document.body.addEventListener('htmx:responseError', function () {
    window.toast('Das hat nicht geklappt. Bitte Seite neu laden und nochmal versuchen.', 'error');
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
    if (p) { p.form.dataset.confirmed = '1'; p.form.requestSubmit(p.submitter || undefined); }
  });
  function ask(form, submitter, el) {
    pending = { form: form, submitter: submitter };
    dlg.querySelector('p').textContent = el.dataset.confirm;
    dlg.querySelector('[data-yes]').textContent = el.dataset.confirmLabel || 'Löschen';
    dlg.showModal();
  }
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f.dataset.confirmed) { delete f.dataset.confirmed; return; }
    var el = (e.submitter && e.submitter.dataset.confirm) ? e.submitter : (f.dataset.confirm ? f : null);
    if (!el) return;
    e.preventDefault();
    ask(f, e.submitter, el);
  }, true);
})();
