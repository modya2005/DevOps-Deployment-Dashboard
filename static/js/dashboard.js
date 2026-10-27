(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var state = { apps: [], selected: null, win: '1h', chart: null, ghFor: null, ghData: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function api(path, opts) {
    opts = opts || {};
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) { location.href = '/login?next=/dashboard'; }
        if (!r.ok) { var e = new Error(j.error || ('HTTP ' + r.status)); e.status = r.status; throw e; }
        return j;
      });
    });
  }
  function ago(ts) {
    if (!ts) return 'never';
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 5) return 'just now';
    if (s < 60) return s + ' s ago';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' d ago';
  }
  function agoIso(iso) { return iso ? ago(Date.parse(iso) / 1000) : ''; }
  function clock(ts, withDate) {
    var d = new Date(ts * 1000), p = function (n) { return String(n).padStart(2, '0'); };
    var t = p(d.getHours()) + ':' + p(d.getMinutes());
    return withDate ? (d.getMonth() + 1) + '/' + d.getDate() + ' ' + t : t + ':' + p(d.getSeconds());
  }
  function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
  function pct(v) { return v == null ? '–' : v.toFixed(2) + '%'; }

  var PILL = {
    healthy: ['ok', 'Healthy'], slow: ['warn', 'Slow'], degraded: ['warn', 'Degraded'],
    down: ['bad', 'Down'], paused: ['mute', 'Paused'], unknown: ['mute', 'Waiting']
  };
  function pill(s) { var p = PILL[s] || PILL.unknown; return '<span class="pill ' + p[0] + '">' + p[1] + '</span>'; }

  /* ------------------------------------------------------------- rendering */
  function renderKpis(o) {
    $('kpis').innerHTML =
      kpi('Healthy apps', o.healthy + ' / ' + o.apps, 'from the latest check of each app') +
      kpi('Uptime · 24h', o.uptime_24h == null ? '–' : o.uptime_24h.toFixed(2) + '%', o.checks_24h + ' checks counted') +
      kpi('p95 response', o.p95_ms == null ? '–' : o.p95_ms + ' ms', 'successful checks, 24h') +
      kpi('Failed checks', String(o.failures_24h), 'timeouts, errors, wrong status');
  }
  function kpi(l, v, s) { return '<div class="kpi"><div class="l">' + l + '</div><div class="v">' + esc(v) + '</div><div class="s">' + s + '</div></div>'; }

  function latestText(l) {
    if (!l) return '<span class="sub">no data</span>';
    if (l.ok) return Math.round(l.response_ms) + ' ms';
    return l.status_code ? 'HTTP ' + l.status_code : (/timeout/.test(l.error || '') ? 'Timeout' : 'Unreachable');
  }
  function renderApps() {
    var tb = document.querySelector('#apps tbody');
    $('appsEmpty').hidden = state.apps.length > 0;
    tb.innerHTML = state.apps.map(function (a) {
      var strip = a.history.map(function (h) {
        return '<i class="' + (!h.ok ? 'f' : (h.ms > slowMs ? 's' : '')) + '"></i>';
      }).join('');
      return '<tr class="app' + (a.id === state.selected ? ' sel' : '') + '" data-id="' + a.id + '" tabindex="0" role="button" aria-label="Show ' + esc(a.name) + '">' +
        '<td><b>' + esc(a.name) + '</b><div class="u mono">' + esc(a.url) + '</div></td>' +
        '<td>' + pill(a.status) + '</td>' +
        '<td class="mono num">' + latestText(a.latest) + '</td>' +
        '<td class="mono num">' + pct(a.uptime['24h']) + '</td>' +
        '<td><div class="strip">' + (strip || '<span class="sub">–</span>') + '</div></td>' +
        '<td class="sub">' + (a.latest ? ago(a.latest.ts) : 'pending') + '</td></tr>';
    }).join('');
  }
  var slowMs = 500;

  function current() { return state.apps.filter(function (a) { return a.id === state.selected; })[0]; }

  function renderDetail() {
    var a = current();
    $('dName').textContent = a ? a.name : '';
    $('detail').style.opacity = a ? 1 : .6;
    if (!a) { $('uptimes').innerHTML = ''; return; }
    $('uptimes').innerHTML = ['1h', '24h', '7d'].map(function (k) {
      return '<div><b>' + pct(a.uptime[k]) + '</b><span>uptime ' + k + '</span></div>';
    }).join('');
    $('pauseBtn').textContent = a.paused ? 'Resume checks' : 'Pause checks';
  }

  /* ----------------------------------------------------------------- chart */
  function bucket(rows, max) {
    if (rows.length <= max) return rows.map(function (r) { return { ts: r.ts, ms: r.ok ? r.response_ms : null, fail: !r.ok }; });
    var size = Math.ceil(rows.length / max), out = [];
    for (var i = 0; i < rows.length; i += size) {
      var chunk = rows.slice(i, i + size), oks = chunk.filter(function (r) { return r.ok; });
      out.push({
        ts: chunk[Math.floor(chunk.length / 2)].ts,
        ms: oks.length ? oks.reduce(function (s, r) { return s + r.response_ms; }, 0) / oks.length : null,
        fail: chunk.some(function (r) { return !r.ok; })
      });
    }
    return out;
  }
  function drawChart(rows) {
    var box = $('chartBox');
    var old = box.querySelector('.chartmsg'); if (old) old.remove();
    var pts = bucket(rows, 240), long = state.win !== '1h';
    if (!pts.length) {
      destroyChart();
      box.insertAdjacentHTML('beforeend', '<div class="chartmsg">No checks in this window yet.<br>Results appear after the first check runs.</div>');
      return;
    }
    var labels = pts.map(function (p) { return clock(p.ts, long).replace(/:\d\d$/, function (m) { return long ? m : ''; }); });
    var ymax = Math.max(slowMs * 1.2, Math.max.apply(null, pts.map(function (p) { return p.ms || 0; })) * 1.15);
    if (window.Chart) { drawChartJs(pts, labels, ymax); } else { drawSvg(pts, labels, ymax); }
  }
  function destroyChart() {
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    var s = $('chartBox').querySelector('svg'); if (s) s.remove();
    var c = $('chart'); c.style.display = '';
  }
  function drawChartJs(pts, labels, ymax) {
    var accent = css('--accent'), bad = css('--bad'), warn = css('--warn'), grid = css('--grid'), tick = css('--fg-3');
    var svg = $('chartBox').querySelector('svg'); if (svg) svg.remove();
    var data = {
      labels: labels,
      datasets: [
        { label: 'Response (ms)', data: pts.map(function (p) { return p.ms; }), borderColor: accent, backgroundColor: accent + '22', fill: true, tension: .25, pointRadius: pts.length > 80 ? 0 : 2, borderWidth: 2, spanGaps: false },
        { label: 'Failed check', data: pts.map(function (p) { return p.fail ? 0 : null; }), showLine: false, pointStyle: 'triangle', pointRadius: 6, borderColor: bad, backgroundColor: bad },
        { label: 'Slow threshold', data: pts.map(function () { return slowMs; }), borderColor: warn, borderDash: [5, 5], pointRadius: 0, borderWidth: 1.2, fill: false }
      ]
    };
    if (state.chart) { state.chart.data = data; state.chart.options.scales.y.suggestedMax = ymax; state.chart.update('none'); return; }
    $('chart').style.display = '';
    state.chart = new Chart($('chart'), {
      type: 'line', data: data,
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: tick, boxWidth: 10, usePointStyle: true, font: { size: 11 } } }, tooltip: { callbacks: { label: function (c) { return c.parsed.y == null ? null : c.dataset.label + ': ' + Math.round(c.parsed.y); } } } },
        scales: {
          x: { ticks: { color: tick, maxTicksLimit: 6, maxRotation: 0 }, grid: { display: false } },
          y: { beginAtZero: true, suggestedMax: ymax, ticks: { color: tick }, grid: { color: grid } }
        }
      }
    });
  }
  function drawSvg(pts, labels, ymax) {
    // Fallback when Chart.js cannot load (offline or CDN blocked).
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    $('chart').style.display = 'none';
    var box = $('chartBox'), W = box.clientWidth || 400, H = 220, m = { l: 36, r: 8, t: 10, b: 22 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var x = function (i) { return m.l + (pts.length < 2 ? iw / 2 : i / (pts.length - 1) * iw); };
    var y = function (v) { return m.t + ih - Math.min(v, ymax) / ymax * ih; };
    var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Response time chart">';
    [0, .25, .5, .75, 1].forEach(function (f) {
      var v = ymax * f;
      s += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="var(--grid)"/><text x="' + (m.l - 5) + '" y="' + (y(v) + 3) + '" text-anchor="end">' + Math.round(v) + '</text>';
    });
    s += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(slowMs) + '" y2="' + y(slowMs) + '" stroke="var(--warn)" stroke-dasharray="4 4"/>';
    var segs = [], cur = [];
    pts.forEach(function (p, i) { if (p.ms != null) cur.push([x(i), y(p.ms)]); else { if (cur.length) segs.push(cur); cur = []; } });
    if (cur.length) segs.push(cur);
    segs.forEach(function (sg) {
      var d = 'M' + sg.map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' L');
      if (sg.length > 1) s += '<path d="' + d + ' L' + sg[sg.length - 1][0] + ' ' + (m.t + ih) + ' L' + sg[0][0] + ' ' + (m.t + ih) + ' Z" fill="var(--accent)" opacity=".13"/>';
      s += '<path d="' + d + '" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>';
      if (sg.length === 1) s += '<circle cx="' + sg[0][0] + '" cy="' + sg[0][1] + '" r="3" fill="var(--accent)"/>';
    });
    pts.forEach(function (p, i) { if (p.fail) s += '<line x1="' + x(i) + '" x2="' + x(i) + '" y1="' + m.t + '" y2="' + (m.t + ih) + '" stroke="var(--bad)" stroke-width="2" opacity=".8"/>'; });
    [0, Math.floor((pts.length - 1) / 2), pts.length - 1].forEach(function (i, j) {
      s += '<text x="' + x(i) + '" y="' + (H - 5) + '" text-anchor="' + (j === 0 ? 'start' : j === 2 ? 'end' : 'middle') + '">' + esc(labels[i]) + '</text>';
    });
    var prev = box.querySelector('svg'); if (prev) prev.remove();
    box.insertAdjacentHTML('afterbegin', s + '</svg>');
  }
  function loadChart() {
    if (state.selected == null) { destroyChart(); return Promise.resolve(); }
    return api('/api/apps/' + state.selected + '/checks?window=' + state.win).then(drawChart).catch(function () {});
  }

  /* ---------------------------------------------------------------- github */
  function loadGithub() {
    var a = current(), box = $('github');
    $('ghName').textContent = a ? a.name : '';
    if (!a) { box.innerHTML = '<div class="empty">Select an application.</div>'; return; }
    if (!a.repo) { box.innerHTML = '<div class="empty">No repository linked to <b>' + esc(a.name) + '</b>. Add one as <span class="mono">owner/name</span> when registering an application to see commits, branches and workflow runs.</div>'; return; }
    if (state.ghFor === a.id && state.ghData) { return; }
    state.ghFor = a.id; state.ghData = null;
    box.innerHTML = '<div class="empty">Loading ' + esc(a.repo) + '…</div>';
    api('/api/apps/' + a.id + '/github').then(function (g) {
      if (state.selected !== a.id) return;
      state.ghData = g;
      var runs = g.workflow_runs.map(function (r) {
        var c = r.conclusion || r.status, cls = c === 'success' ? 'ok' : (c === 'failure' ? 'bad' : 'warn');
        return '<div class="r"><span class="pill ' + cls + '">' + esc(c) + '</span><span>' + esc(r.name) + ' · ' + esc(r.branch) + '</span><span class="sha">' + esc(r.sha) + '</span></div>';
      }).join('') || '<div class="sub">No workflow runs found.</div>';
      box.innerHTML =
        '<div class="gh-head"><a href="' + esc(g.url) + '" target="_blank" rel="noopener"><b>' + esc(g.full_name) + '</b></a><span class="chip">default: ' + esc(g.default_branch) + '</span><span class="sub">pushed ' + agoIso(g.pushed_at) + '</span></div>' +
        '<div class="sec-l">Branches</div><div>' + g.branches.map(function (b) { return '<span class="chip">' + esc(b) + '</span> '; }).join('') + '</div>' +
        '<div class="sec-l">Latest commits</div>' + g.commits.map(function (c) {
          return '<div class="commit"><a class="sha" href="' + esc(c.url) + '" target="_blank" rel="noopener">' + esc(c.sha) + '</a><span class="m" title="' + esc(c.message) + '">' + esc(c.message) + '</span><span class="sub">' + agoIso(c.date) + '</span></div>';
        }).join('') +
        '<div class="sec-l">Workflow runs</div><div class="runs">' + runs + '</div>';
    }).catch(function (e) {
      if (state.selected !== a.id) return;
      state.ghFor = null;
      box.innerHTML = '<div class="empty">Could not load GitHub data: ' + esc(e.message) + '</div>';
    });
  }

  /* ------------------------------------------- deployments, logs, environment */
  function loadDeps() {
    return api('/api/deployments').then(function (rows) {
      $('depsEmpty').hidden = rows.length > 0;
      document.querySelector('#deps tbody').innerHTML = rows.map(function (d) {
        return '<tr><td class="mono">' + esc(d.version) + '</td><td>' + esc(d.app_name) + '</td><td class="sha">' + esc(d.commit_sha.slice(0, 7)) + '</td>' +
          '<td>' + (d.status === 'success' ? '<span class="pill ok">Success</span>' : '<span class="pill bad">Failed</span>') + '</td><td class="sub">' + ago(d.ts) + '</td></tr>';
      }).join('');
    });
  }
  function loadLogs() {
    var q = encodeURIComponent($('logq').value), lv = encodeURIComponent($('loglv').value);
    return api('/api/logs?q=' + q + '&level=' + lv).then(function (rows) {
      $('logs').innerHTML = rows.length ? rows.map(function (l) {
        return '<div class="log"><span class="t">' + clock(l.ts) + '</span><span class="lv ' + l.level + '">' + l.level + '</span><span class="m">' + esc(l.message) + '</span></div>';
      }).join('') : '<div class="empty" style="font-family:var(--font)">No log lines match. Clear the search or pick another level.</div>';
    });
  }
  function meter(label, v, extra) {
    return '<div><div class="h"><span>' + label + '</span><span class="mono num">' + Math.round(v) + '%' + (extra || '') + '</span></div><div class="meter"><i class="' + (v > 90 ? 'crit' : v > 75 ? 'hi' : '') + '" style="width:' + v + '%"></i></div></div>';
  }
  function loadEnv() {
    return api('/api/environment').then(function (e) {
      slowMs = e.slow_ms;
      $('env').innerHTML = [
        ['environment', e.env_name], ['app version', 'v' + e.app_version], ['python', e.python], ['host', e.host], ['os', e.os],
        ['database', e.database], ['timeout', e.check_timeout + ' s'], ['scheduler', e.scheduler ? 'running' : 'off'], ['github token', e.github_token ? 'set' : 'not set']
      ].map(function (r) { return '<dt>' + r[0] + '</dt><dd>' + esc(r[1]) + '</dd>'; }).join('');
      $('res').innerHTML = meter('CPU · ' + e.cpu_count + ' cores', e.cpu_percent) +
        meter('Memory', e.memory_percent, ' · ' + e.memory_used_mb + ' / ' + e.memory_total_mb + ' MB') + meter('Disk', e.disk_percent);
    });
  }

  /* --------------------------------------------------------------- refresh */
  function refresh(full) {
    return Promise.all([api('/api/overview'), api('/api/apps')]).then(function (res) {
      renderKpis(res[0]);
      state.apps = res[1];
      if (state.selected == null || !current()) state.selected = state.apps.length ? state.apps[0].id : null;
      renderApps(); renderDetail();
      $('liveDot').className = 'on'; $('liveTxt').textContent = 'live · updated ' + clock(Date.now() / 1000);
      return Promise.all([loadChart(), loadDeps(), loadLogs(), loadEnv()]).then(function () { if (full || !state.ghData) loadGithub(); });
    }).catch(function () {
      $('liveDot').className = 'off'; $('liveTxt').textContent = 'offline · retrying';
    });
  }

  /* ---------------------------------------------------------------- events */
  function select(id) {
    state.selected = id; state.ghData = null; state.ghFor = null;
    renderApps(); renderDetail(); loadChart(); loadGithub();
  }
  var tbody = document.querySelector('#apps tbody');
  tbody.addEventListener('click', function (e) { var r = e.target.closest('tr.app'); if (r) select(+r.dataset.id); });
  tbody.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') { var r = e.target.closest('tr.app'); if (r) { e.preventDefault(); select(+r.dataset.id); } }
  });
  $('winTabs').addEventListener('click', function (e) {
    var b = e.target.closest('.tab'); if (!b) return;
    state.win = b.dataset.w;
    Array.prototype.forEach.call($('winTabs').children, function (t) { t.setAttribute('aria-selected', t === b); });
    loadChart();
  });
  $('runAll').addEventListener('click', function () {
    var b = $('runAll'); b.disabled = true; b.textContent = 'Checking…';
    api('/api/check', { method: 'POST' }).catch(function () {}).then(function () { return refresh(); }).then(function () { b.disabled = false; b.textContent = 'Run checks now'; });
  });
  $('pauseBtn').addEventListener('click', function () {
    var a = current(); if (!a) return;
    api('/api/apps/' + a.id, { method: 'PATCH', body: { paused: !a.paused } }).then(function () { refresh(); });
  });
  var delArm = null;
  $('delBtn').addEventListener('click', function () {
    var a = current(); if (!a) return;
    var b = $('delBtn');
    if (!delArm) { b.textContent = 'Click again to confirm'; delArm = setTimeout(function () { delArm = null; b.textContent = 'Remove'; }, 3000); return; }
    clearTimeout(delArm); delArm = null; b.textContent = 'Remove';
    api('/api/apps/' + a.id, { method: 'DELETE' }).then(function () { state.selected = null; refresh(true); });
  });
  var logT; $('logq').addEventListener('input', function () { clearTimeout(logT); logT = setTimeout(loadLogs, 200); });
  $('loglv').addEventListener('change', loadLogs);

  document.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { b.closest('dialog').close(); }); });
  $('addBtn').addEventListener('click', function () { $('addErr').hidden = true; $('addDlg').showModal(); $('fName').focus(); });
  $('addForm').addEventListener('submit', function (e) {
    e.preventDefault();
    api('/api/apps', { method: 'POST', body: { name: $('fName').value, url: $('fUrl').value, interval_s: $('fInt').value, expected_status: $('fExp').value, repo: $('fRepo').value } })
      .then(function (r) { $('addDlg').close(); $('addForm').reset(); $('fInt').value = 30; $('fExp').value = 200; state.selected = r.id; setTimeout(function () { refresh(true); }, 1200); refresh(true); })
      .catch(function (err) { $('addErr').textContent = err.message; $('addErr').hidden = false; });
  });
  $('depBtn').addEventListener('click', function () {
    if (!state.apps.length) return;
    $('dApp').innerHTML = state.apps.map(function (a) { return '<option value="' + a.id + '"' + (a.id === state.selected ? ' selected' : '') + '>' + esc(a.name) + '</option>'; }).join('');
    $('depErr').hidden = true; $('depDlg').showModal(); $('dVer').focus();
  });
  $('depForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var headers = $('dTok').value ? { 'X-Deploy-Token': $('dTok').value } : {};
    api('/api/deployments', { method: 'POST', headers: headers, body: { app_id: +$('dApp').value, version: $('dVer').value, commit_sha: $('dSha').value, status: $('dStatus').value } })
      .then(function () { $('depDlg').close(); $('depForm').reset(); refresh(); })
      .catch(function (err) { $('depErr').textContent = err.message; $('depErr').hidden = false; });
  });
  $('theme').addEventListener('click', function () {
    var r = document.documentElement, cur = r.getAttribute('data-theme');
    var dark = cur ? cur === 'dark' : !matchMedia('(prefers-color-scheme: light)').matches;
    var next = dark ? 'light' : 'dark';
    r.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    loadChart();
  });
  addEventListener('resize', function () { if (!window.Chart) loadChart(); });

  refresh(true);
  setInterval(function () { if (!document.hidden) refresh(); }, 5000);
})();
