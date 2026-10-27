(function () {
  'use strict';

  /* ---- theme toggle ---- */
  var tb = document.getElementById('theme');
  if (tb) tb.addEventListener('click', function () {
    var r = document.documentElement, cur = r.getAttribute('data-theme');
    var dark = cur ? cur === 'dark' : !matchMedia('(prefers-color-scheme: light)').matches;
    var next = dark ? 'light' : 'dark';
    r.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
  });

  /* ---- show/hide password ---- */
  document.querySelectorAll('[data-toggle]').forEach(function (b) {
    b.addEventListener('click', function () {
      var i = document.getElementById(b.dataset.toggle), show = i.type === 'password';
      i.type = show ? 'text' : 'password';
      b.textContent = show ? 'Hide' : 'Show';
      b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
  });

  /* ---- FAQ ---- */
  var list = document.getElementById('faqList');
  if (!list) return;
  var items = Array.prototype.slice.call(list.querySelectorAll('.faq-item'));
  var search = document.getElementById('faqSearch'), chips = document.getElementById('faqChips');
  var count = document.getElementById('faqCount'), toggleAll = document.getElementById('faqToggle');
  var empty = document.getElementById('faqEmpty'), term = document.getElementById('faqTerm');
  var cat = '';

  items.forEach(function (it) {
    it._text = (it.textContent || '').toLowerCase();
    it._btn = it.querySelector('.faq-q');
    it._panel = it.querySelector('.faq-a');
  });

  function setOpen(it, open, updateHash) {
    it._btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    it._panel.hidden = !open;
    it.classList.toggle('open', open);
    if (updateHash && open && history.replaceState) history.replaceState(null, '', '#' + it.id);
  }
  function visible() { return items.filter(function (it) { return !it.hidden; }); }
  function syncToggleLabel() {
    var v = visible();
    var allOpen = v.length > 0 && v.every(function (it) { return it._btn.getAttribute('aria-expanded') === 'true'; });
    toggleAll.textContent = allOpen ? 'Collapse all' : 'Expand all';
    toggleAll.hidden = v.length === 0;
  }
  function apply() {
    var q = search.value.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean);
    items.forEach(function (it) {
      var okCat = !cat || it.dataset.cat === cat;
      var okQ = words.every(function (w) { return it._text.indexOf(w) > -1; });
      it.hidden = !(okCat && okQ);
    });
    var n = visible().length;
    count.textContent = n === items.length ? items.length + ' questions' : n + ' of ' + items.length + ' questions';
    empty.hidden = n !== 0;
    term.textContent = search.value.trim() || (cat || '');
    syncToggleLabel();
  }

  items.forEach(function (it) {
    it._btn.addEventListener('click', function () {
      setOpen(it, it._btn.getAttribute('aria-expanded') !== 'true', true);
      syncToggleLabel();
    });
    it._btn.addEventListener('keydown', function (e) {
      var v = visible(), i = v.indexOf(it), t = null;
      if (e.key === 'ArrowDown') t = v[i + 1];
      else if (e.key === 'ArrowUp') t = v[i - 1];
      else if (e.key === 'Home') t = v[0];
      else if (e.key === 'End') t = v[v.length - 1];
      else return;
      e.preventDefault();
      if (t) t._btn.focus();
    });
  });

  search.addEventListener('input', apply);
  search.addEventListener('keydown', function (e) { if (e.key === 'Escape') { search.value = ''; apply(); } });
  chips.addEventListener('click', function (e) {
    var b = e.target.closest('.chipbtn'); if (!b) return;
    cat = b.dataset.cat;
    Array.prototype.forEach.call(chips.children, function (c) { c.setAttribute('aria-pressed', c === b ? 'true' : 'false'); });
    apply();
  });
  toggleAll.addEventListener('click', function () {
    var v = visible(), allOpen = v.every(function (it) { return it._btn.getAttribute('aria-expanded') === 'true'; });
    v.forEach(function (it) { setOpen(it, !allOpen, false); });
    syncToggleLabel();
  });
  document.getElementById('faqClear').addEventListener('click', function () {
    search.value = ''; cat = '';
    Array.prototype.forEach.call(chips.children, function (c, i) { c.setAttribute('aria-pressed', i === 0 ? 'true' : 'false'); });
    apply(); search.focus();
  });

  /* deep links: /#faq-uptime opens that answer */
  function openFromHash() {
    var id = location.hash.slice(1);
    if (!id || id.indexOf('faq-') !== 0) return;
    var it = document.getElementById(id);
    if (!it || !it.classList.contains('faq-item')) return;
    search.value = ''; cat = '';
    Array.prototype.forEach.call(chips.children, function (c, i) { c.setAttribute('aria-pressed', i === 0 ? 'true' : 'false'); });
    apply(); setOpen(it, true, false); syncToggleLabel();
    setTimeout(function () { it.scrollIntoView({ block: 'center' }); }, 30);
  }
  addEventListener('hashchange', openFromHash);
  apply(); openFromHash();
})();
