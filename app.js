'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const COLORS = { rented: '#16a34a', available: '#2563eb', temp_reserved: '#f59e0b',
  development: '#8b5cf6', cleaning: '#0891b2', maintenance: '#dc2626' };
const DEFAULT_LABELS = { rented: 'مؤجرة', available: 'متاحة', temp_reserved: 'محجوزة مؤقتاً',
  development: 'تطوير', cleaning: 'نظافة', maintenance: 'صيانة' };
const BUCKETS = [
  ['expired', 'منتهية', '#dc2626'], ['d30', 'خلال 30 يوماً', '#f59e0b'],
  ['d60', '31 – 60 يوماً', '#eab308'], ['d90', '61 – 90 يوماً', '#84cc16'],
  ['later', 'أكثر من 90 يوماً', '#16a34a'], ['no_date', 'بلا تاريخ', '#94a3b8'],
];

if (typeof Chart !== 'undefined') Chart.defaults.font.family = 'Tajawal, Segoe UI, Tahoma, sans-serif';

const S = {
  token: localStorage.getItem('abyat_token'), user: null,
  statuses: Object.keys(COLORS).map(k => ({ key: k, label: DEFAULT_LABELS[k], color: COLORS[k] })),
  view: 'dashboard', siteFilter: '', unitSite: null, unitData: null, statusFilter: '',
  charts: {}, editing: null, ws: null, pingT: null, pending: false, users: [], sites: [],
};
const hasCap = c => !!S.user && (S.user.caps || []).includes(c);
const assignedOnly = () => !!S.user && S.user.role !== 'admin' && S.user.visibility === 'assigned';
const st = k => S.statuses.find(s => s.key === k) || { key: k, label: k, color: '#64748b' };

function errText(j) {
  const d = j && j.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length) return String(d[0].msg || 'بيانات غير صالحة').replace(/^Value error, /, '');
  return 'حدث خطأ';
}
async function api(path, opts = {}) {
  const headers = {};
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  let body;
  if (opts.form) { headers['Content-Type'] = 'application/x-www-form-urlencoded'; body = new URLSearchParams(opts.form); }
  else if (opts.body) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body); }
  const res = await fetch('/api' + path, { method: opts.method || 'GET', headers, body });
  if (res.status === 401 && path !== '/auth/login') { logout(); throw new Error('انتهت الجلسة'); }
  if (!res.ok) { let j = null; try { j = await res.json(); } catch (_) {} throw new Error(errText(j)); }
  return res.status === 204 ? null : res.json();
}

/* ---------- الدخول والتهيئة ---------- */
async function loadStatuses() {
  try {
    const list = await api('/meta/statuses');
    S.statuses = list.map(s => ({ key: s.key, label: s.label, color: COLORS[s.key] || '#64748b' }));
  } catch (_) { /* تبقى القيم الافتراضية */ }
}
async function boot() {
  if (S.token) { try { S.user = await api('/auth/me'); await enterApp(); return; } catch (_) {} }
  showLogin();
}
function showLogin() { $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); }
async function enterApp() {
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#userLabel').textContent = S.user.full_name || S.user.username;
  $('#navAdmin').classList.toggle('hidden', S.user.role !== 'admin');
  $('#navDash').classList.toggle('hidden', !hasCap('view_dashboard'));
  await loadStatuses();
  connectWS();
  showView(hasCap('view_dashboard') ? 'dashboard' : 'tasks');
}
function logout() {
  S.token = null; S.user = null; localStorage.removeItem('abyat_token');
  if (S.ws) { try { S.ws.close(); } catch (_) {} }
  showLogin();
}
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#loginErr').textContent = '';
  try {
    const r = await api('/auth/login', { method: 'POST', form: { username: $('#lUser').value, password: $('#lPass').value } });
    S.token = r.access_token; localStorage.setItem('abyat_token', S.token);
    S.user = await api('/auth/me'); $('#lPass').value = ''; await enterApp();
  } catch (err) { $('#loginErr').textContent = err.message; }
});
$('#logoutBtn').addEventListener('click', logout);
$('#pwBtn').addEventListener('click', async () => {
  const o = prompt('كلمة المرور الحالية'); if (!o) return;
  const n = prompt('كلمة المرور الجديدة (8 أحرف على الأقل)'); if (!n) return;
  try { await api('/auth/change-password', { method: 'POST', body: { old_password: o, new_password: n } }); alert('تم تغيير كلمة المرور'); }
  catch (err) { alert(err.message); }
});

document.querySelectorAll('nav button').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
function showView(v) {
  S.view = v;
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  ['dashboard', 'tasks', 'units', 'reports', 'admin'].forEach(n => $('#view-' + n).classList.toggle('hidden', n !== v));
  if (v === 'dashboard') loadDashboard().catch(showErr);
  if (v === 'tasks') loadTasks().catch(showErr);
  if (v === 'units') loadUnitsInit().catch(showErr);
  if (v === 'reports') loadReportsInit().catch(showErr);
  if (v === 'admin') loadAdmin().catch(showErr);
}
function showErr(e) { console.error(e); }

/* ---------- التحديث اللحظي ---------- */
function setLive(on) {
  const el = $('#live'); el.className = 'live ' + (on ? 'on' : 'off');
  el.textContent = on ? '● مباشر' : '● غير متصل';
}
function connectWS() {
  if (S.ws || !S.token) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(S.token)}`);
  S.ws = ws;
  ws.onopen = () => { setLive(true); S.pingT = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 25000); };
  ws.onmessage = () => scheduleRefresh();
  ws.onclose = () => { clearInterval(S.pingT); S.ws = null; setLive(false); if (S.token) setTimeout(connectWS, 3000); };
}
let refreshTimer;
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshNow, 300); }
function refreshNow() {
  if (!S.user) return;
  if (S.view === 'dashboard') loadDashboard().catch(showErr);
  else if (S.view === 'tasks') loadTasks().catch(showErr);
  else if (S.view === 'units') { if ($('#unitDialog').open) S.pending = true; else loadUnits().catch(showErr); }
  else if (S.view === 'reports') { if (R.cat && !$('#authPanel').classList.contains('hidden')) { if (!$('#authDialog').open) loadAuthList().catch(showErr); } else refreshReports().catch(showErr); }
}
setInterval(() => { if (S.token && !S.ws) scheduleRefresh(); }, 20000);   // احتياطي عند انقطاع WebSocket

/* ---------- لوحة الإشغال ---------- */
$('#siteFilter').addEventListener('change', e => { S.siteFilter = e.target.value; loadDashboard().catch(showErr); });

async function loadDashboard() {
  const d = await api('/dashboard' + (S.siteFilter ? `?site_id=${S.siteFilter}` : ''));
  const sel = $('#siteFilter');
  sel.innerHTML = '<option value="">كل المواقع</option>' + d.sites.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  sel.value = S.siteFilter;
  $('#updated').textContent = 'آخر تحديث: ' + new Date().toLocaleTimeString('ar-EG');

  const t = d.totals, rented = t.counts.rented || 0;
  const pct = t.total ? Math.round(rented * 100 / t.total) : 0;
  $('#kpis').innerHTML =
    `<div class="kpi" style="--c:#1a3e5f"><b>${t.total}</b><span>إجمالي الوحدات</span></div>` +
    `<div class="kpi" style="--c:#1a3e5f"><b>${pct}%</b><span>نسبة الإشغال</span></div>` +
    S.statuses.map(s => `<div class="kpi" style="--c:${s.color}"><b>${t.counts[s.key] || 0}</b><span>${esc(s.label)}</span></div>`).join('');
  drawCharts(d);

  const rowsExp = d.expiring.map(x => {
    const cls = x.days_left < 0 ? 'red' : x.days_left <= 30 ? 'amber' : '';
    const label = x.days_left < 0 ? `منتهٍ منذ ${-x.days_left} يوماً` : `${x.days_left} يوماً`;
    return `<tr><td>${esc(x.site)} / ${esc(x.building)} / ${esc(x.unit)}</td><td>${esc(x.tenant || '—')}</td><td>${esc(x.contract_end)}</td><td><span class="pill ${cls}">${label}</span></td></tr>`;
  }).join('');
  $('#expiring').innerHTML = d.expiring.length
    ? `<table><tr><th>الوحدة</th><th>المستأجر</th><th>الانتهاء</th><th>المتبقي</th></tr>${rowsExp}</table>`
    : '<p class="muted">لا توجد عقود قريبة الانتهاء.</p>';

  const head = S.statuses.map(s => `<th>${esc(s.label)}</th>`).join('');
  const body = d.sites.map(s => {
    const p = s.total ? Math.round((s.counts.rented || 0) * 100 / s.total) : 0;
    return `<tr><td><a class="lnk" data-site="${s.id}">${esc(s.name)}</a></td><td>${s.total}</td>` +
      S.statuses.map(x => `<td>${s.counts[x.key] || 0}</td>`).join('') +
      `<td>${p}%</td><td><div class="bar"><i style="width:${p}%"></i></div></td></tr>`;
  }).join('');
  $('#sitesTable').innerHTML = d.sites.length
    ? `<table><tr><th>الموقع</th><th>الإجمالي</th>${head}<th>الإشغال</th><th></th></tr>${body}</table>`
    : '<p class="muted">لا توجد مواقع بعد. أضِف موقعاً من تبويب الإدارة.</p>';
}
$('#sitesTable').addEventListener('click', e => {
  const a = e.target.closest('[data-site]'); if (!a) return;
  S.siteFilter = a.dataset.site; loadDashboard().catch(showErr);
});


/* ---------- الدوائر التفاعلية: نسبة مئوية داخل كل لون + إجمالي في الوسط + مفتاح جانبي ----------
   أي رسم doughnut جديد يمر عبر drawDonut فيحصل تلقائياً على الشكل نفسه. */
function pctInts(vals) {                       // نسب صحيحة مجموعها 100 (طريقة أكبر باقي)
  const total = vals.reduce((a, b) => a + b, 0);
  if (!total) return vals.map(() => 0);
  const raw = vals.map(v => v * 100 / total), base = raw.map(Math.floor);
  let left = 100 - base.reduce((a, b) => a + b, 0);
  raw.map((r, i) => [r - base[i], i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (left > 0 && vals[i] > 0) { base[i]++; left--; } });
  return base;
}
const pctText = (v, p) => (v > 0 && p === 0) ? '<1%' : p + '%';
const DONUT_FONT = '"Tajawal", "Segoe UI", Tahoma, sans-serif';
const donutPlugin = {
  id: 'donutLabels',
  afterDatasetsDraw(chart) {
    if (chart.config.type !== 'doughnut') return;           // لا يُرسم على الرسم المساحي أو الأعمدة
    const o = chart.options.plugins.donutLabels; if (!o) return;
    const ds = chart.data.datasets[0], vals = ds.data.map(Number), pcts = pctInts(vals), ctx = chart.ctx;
    ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    chart.getDatasetMeta(0).data.forEach((arc, i) => {
      if (!vals[i] || !chart.getDataVisibility(i)) return;
      const p = arc.getProps(['x', 'y', 'startAngle', 'endAngle', 'innerRadius', 'outerRadius'], true);
      const r = (p.innerRadius + p.outerRadius) / 2, mid = (p.startAngle + p.endAngle) / 2;
      if ((p.endAngle - p.startAngle) * r < 30) return;            // الشريحة أصغر من أن تتسع للنص
      ctx.font = '700 13px ' + DONUT_FONT; ctx.fillStyle = '#fff';
      ctx.shadowColor = 'rgba(15,23,42,.45)'; ctx.shadowBlur = 4; ctx.shadowOffsetY = 1;
      ctx.fillText(pctText(vals[i], pcts[i]), p.x + Math.cos(mid) * r, p.y + Math.sin(mid) * r);
    });
    ctx.shadowColor = 'transparent';
    const a = chart.getDatasetMeta(0).data[0]; if (a) {
      const total = vals.reduce((x, y) => x + y, 0);
      ctx.fillStyle = '#94a3b8'; ctx.font = '500 12px ' + DONUT_FONT; ctx.fillText(o.label || '', a.x, a.y - 14);
      ctx.fillStyle = '#0f172a'; ctx.font = '700 26px ' + DONUT_FONT; ctx.fillText(String(total), a.x, a.y + 12);
    }
    ctx.restore();
  },
};
if (typeof Chart !== 'undefined') Chart.register(donutPlugin);
function drawDonut(key, canvasId, legendId, items, centerLabel) {
  const cv = document.getElementById(canvasId); if (!cv) return;
  if (S.charts[key]) S.charts[key].destroy();
  const vals = items.map(i => i.value), pcts = pctInts(vals);
  S.charts[key] = new Chart(cv, { type: 'doughnut',
    data: { labels: items.map(i => i.label),
      datasets: [{ data: vals, backgroundColor: items.map(i => i.color), borderColor: '#fff', borderWidth: 3, borderRadius: 6, hoverOffset: 6 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, cutout: '62%',
      plugins: { legend: { display: false }, donutLabels: { label: centerLabel },
        tooltip: { rtl: true, callbacks: { label: c => ` ${c.label}: ${c.parsed} (${pctText(c.parsed, pcts[c.dataIndex])})` } } } } });
  const lg = document.getElementById(legendId);
  if (lg) lg.innerHTML = items.map((it, i) =>
    `<div class="lg" style="--c:${it.color}"><span class="dot"></span><span class="nm">${esc(it.label)}</span><b>(${it.value})</b><span class="pc">${pctText(it.value, pcts[i])}</span></div>`).join('');
}

function drawCharts(d) {
  if (typeof Chart === 'undefined') {
    document.querySelectorAll('.chartbox').forEach(b => b.innerHTML = '<p class="muted">تعذر تحميل مكتبة الرسوم (يلزم اتصال بالإنترنت).</p>');
    return;
  }
  const keep = (key, id, cfg) => {
    if (S.charts[key]) S.charts[key].destroy();
    const cv = document.getElementById(id); if (!cv) return;
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, cfg.options);
    S.charts[key] = new Chart(cv, cfg);
  };
  drawDonut('donut', 'cDonut', 'donutLegend',
    S.statuses.map(s => ({ label: s.label, value: d.totals.counts[s.key] || 0, color: s.color })), 'إجمالي الوحدات');
  keep('area', 'cArea', { type: 'line',
    data: { labels: d.history.map(h => h.day.slice(5)),
      datasets: S.statuses.map(s => ({ label: s.label, data: d.history.map(h => h[s.key]),
        borderColor: s.color, backgroundColor: s.color + '99', fill: true, pointRadius: 0, tension: .25, borderWidth: 1 })) },
    options: { scales: { y: { stacked: true, beginAtZero: true }, x: { ticks: { maxTicksLimit: 8 } } },
      plugins: { legend: { position: 'bottom' } }, interaction: { mode: 'index', intersect: false } } });
  keep('contracts', 'cContracts', { type: 'bar',
    data: { labels: BUCKETS.map(b => b[1]),
      datasets: [{ data: BUCKETS.map(b => d.contracts[b[0]] || 0), backgroundColor: BUCKETS.map(b => b[2]) }] },
    options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } } });
}

/* ---------- تحديث الوحدات ---------- */
$('#unitSite').addEventListener('change', e => { S.unitSite = Number(e.target.value); S.statusFilter = ''; loadUnits().catch(showErr); });
async function loadUnitsInit() {
  S.sites = await api('/sites');
  const sel = $('#unitSite');
  sel.innerHTML = S.sites.map(s => `<option value="${s.id}">${esc(s.name)}${canUpdate(s.id) ? '' : ' (عرض فقط)'}</option>`).join('');
  if (!S.sites.some(s => s.id === S.unitSite)) S.unitSite = S.sites.length ? S.sites[0].id : null;
  sel.value = S.unitSite ?? '';
  await loadUnits();
}
function canUpdate(siteId) {
  if (!hasCap('update_units')) return false;
  if (assignedOnly()) return true;      // وحدات مهامه فقط تصله من الخادم
  return S.user.role === 'admin' || (S.user.role === 'updater' && (S.user.site_ids || []).includes(siteId));
}
const natural = (a, b) => (/^\d+$/.test(a.number) && /^\d+$/.test(b.number))
  ? Number(a.number) - Number(b.number) : a.number.localeCompare(b.number, 'ar');
async function loadUnits() {
  if (!S.unitSite) { $('#buildings').innerHTML = '<p class="muted">لا توجد مواقع بعد.</p>'; $('#chips').innerHTML = ''; return; }
  const [blds, units] = await Promise.all([
    api(`/buildings?site_id=${S.unitSite}`), api(`/units?site_id=${S.unitSite}&limit=2000`)]);
  S.unitData = { siteId: S.unitSite,
    buildings: blds.map(b => ({ id: b.id, name: b.name, units: units.filter(u => u.building_id === b.id).sort(natural) }))
      .filter(b => !assignedOnly() || b.units.length) };
  renderUnits();
}
function renderUnits() {
  const d = S.unitData; if (!d) return;
  const can = canUpdate(d.siteId);
  $('#siteInfo').textContent = can ? 'يمكنك تحديث وحدات هذا الموقع' : 'عرض فقط: لا تملك صلاحية التحديث';
  const c = {}; let total = 0;
  d.buildings.forEach(b => b.units.forEach(u => { c[u.status] = (c[u.status] || 0) + 1; total++; }));
  $('#chips').innerHTML =
    `<button class="chip ${S.statusFilter ? '' : 'on'}" data-f="">الكل (${total})</button>` +
    S.statuses.map(s => `<button class="chip ${S.statusFilter === s.key ? 'on' : ''}" style="--c:${s.color}" data-f="${s.key}">${esc(s.label)} (${c[s.key] || 0})</button>`).join('');
  $('#buildings').innerHTML = d.buildings.map(b => {
    const tiles = b.units.filter(u => !S.statusFilter || u.status === S.statusFilter).map(u => {
      const s = st(u.status);
      return `<button class="tile" data-id="${u.id}" style="--c:${s.color}" aria-label="وحدة ${esc(u.number)}: ${esc(s.label)}">` +
        `${ICONS.house(44)}<span class="num"><i class="dot"></i><b>${esc(u.number)}</b></span><small>${esc(s.label)}</small>` +
        `${u.tenant_name ? `<em>${esc(u.tenant_name)}</em>` : ''}</button>`;
    }).join('');
    return `<div class="bld"><h3>${ICONS.building(20)}<span>${esc(b.name)}</span> <span class="muted">(${b.units.length} وحدة)</span></h3><div class="tiles">${tiles || '<span class="muted">لا وحدات مطابقة</span>'}</div></div>`;
  }).join('') || '<p class="muted">لا توجد عمارات في هذا الموقع.</p>';
}
$('#chips').addEventListener('click', e => { const b = e.target.closest('.chip'); if (!b) return; S.statusFilter = b.dataset.f; renderUnits(); });
$('#buildings').addEventListener('click', e => { const t = e.target.closest('.tile'); if (t) openUnit(Number(t.dataset.id)); });

const dlg = $('#unitDialog');
function findUnit(id) { for (const b of S.unitData.buildings) for (const u of b.units) if (u.id === id) return { u, b }; return null; }
function openUnit(id) {
  const f = findUnit(id); if (!f) return;
  const can = canUpdate(S.unitData.siteId);
  S.editing = { id, status: f.u.status };
  $('#dlgTitle').innerHTML = `<span class="dlg-ico" id="dlgIco" style="--c:${st(f.u.status).color}">${ICONS.house(34)}</span> ${esc(f.b.name)} — وحدة ${esc(f.u.number)}`;
  $('#fTenant').value = f.u.tenant_name || ''; $('#fEnd').value = f.u.contract_end || ''; $('#fNote').value = '';
  ['#fTenant', '#fEnd', '#fNote'].forEach(s => $(s).disabled = !can);
  $('#dlgSave').classList.toggle('hidden', !can); $('#dlgErr').textContent = '';
  drawPick(can); dlg.showModal();
}
function drawPick(can) {
  $('#dlgStatuses').innerHTML = S.statuses.map(s =>
    `<button type="button" data-k="${s.key}" class="${S.editing.status === s.key ? 'on' : ''}" style="--c:${s.color}" ${can ? '' : 'disabled'}>${esc(s.label)}</button>`).join('');
}
$('#dlgStatuses').addEventListener('click', e => {
  const b = e.target.closest('button[data-k]'); if (!b || b.disabled) return;
  S.editing.status = b.dataset.k; drawPick(true);
  const ic = $('#dlgIco'); if (ic) ic.style.setProperty('--c', st(S.editing.status).color);   // الأيقونة تتبدّل مباشرة مع اختيار الحالة
});
$('#dlgCancel').addEventListener('click', () => dlg.close());
$('#unitForm').addEventListener('submit', e => { e.preventDefault(); if (!$('#dlgSave').classList.contains('hidden')) $('#dlgSave').click(); });
dlg.addEventListener('close', () => { if (S.pending) { S.pending = false; loadUnits().catch(showErr); } });
$('#dlgSave').addEventListener('click', async () => {
  $('#dlgErr').textContent = '';
  const body = { status: S.editing.status, note: $('#fNote').value.trim() || null };
  if (S.editing.status !== 'available') {      // «متاحة» تمسح المستأجر والتاريخ في الخادم
    body.tenant_name = $('#fTenant').value.trim() || null;
    body.contract_end = $('#fEnd').value || null;
  }
  try {
    await api(`/units/${S.editing.id}/status`, { method: 'PATCH', body });
    S.pending = false; dlg.close(); await loadUnits();
  } catch (err) { $('#dlgErr').textContent = err.message; }
});

/* ---------- الإدارة ---------- */
async function loadAdmin() {
  const [sites, users] = await Promise.all([api('/sites?include_inactive=true'), api('/users')]);
  S.sites = sites; S.users = users;
  $('#siteList').innerHTML = sites.map(s =>
    `<div><span>${esc(s.name)}${s.is_active ? '' : ' <span class="muted">— معطّل</span>'}</span>` +
    `<a class="lnk" data-toggle="${s.id}">${s.is_active ? 'تعطيل' : 'تفعيل'}</a></div>`).join('') || '<p class="muted">لا توجد مواقع.</p>';
  const opts = sites.filter(s => s.is_active).map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('#bSite').innerHTML = opts; $('#uSites').innerHTML = opts;
  loadPerms().catch(showErr);
  loadMediaAdmin().catch(showErr);
  await loadLevels();
  const visL = Object.fromEntries(LV.visibilities.map(v => [v.key, v.label]));
  $('#userList').innerHTML = `<table><tr><th>المستخدم</th><th>الاسم</th><th>المستوى</th><th>النطاق</th><th>المواقع</th><th>الحالة</th><th></th></tr>` +
    users.map(u => `<tr><td>${esc(u.username)}</td><td>${esc(u.full_name)}</td><td>${esc(u.level_label)}</td>` +
      `<td>${esc(visL[u.visibility] || u.visibility)}${u.visibility_custom ? ' ✎' : ''}</td>` +
      `<td>${u.role === 'updater' ? u.site_ids.length : '—'}</td><td>${u.is_active ? 'فعّال' : 'معطّل'}</td>` +
      `<td><a class="lnk" data-edit="${u.id}">تعديل</a> · <a class="lnk" data-active="${u.id}">${u.is_active ? 'تعطيل' : 'تفعيل'}</a></td></tr>`).join('') + '</table>';
  resetUserForm();
}
$('#siteForm').addEventListener('submit', async e => {
  e.preventDefault();
  try { await api('/sites', { method: 'POST', body: { name: $('#siteName').value.trim() } }); $('#siteName').value = ''; await loadAdmin(); }
  catch (err) { alert(err.message); }
});
$('#siteList').addEventListener('click', async e => {
  const a = e.target.closest('[data-toggle]'); if (!a) return;
  const s = S.sites.find(x => x.id === Number(a.dataset.toggle));
  try { await api(`/sites/${s.id}`, { method: 'PATCH', body: { is_active: !s.is_active } }); await loadAdmin(); }
  catch (err) { alert(err.message); }
});
$('#bldForm').addEventListener('submit', async e => {
  e.preventDefault();
  const count = Number($('#bCount').value || 0), first = Number($('#bFirst').value || 1), prefix = $('#bPrefix').value.trim();
  const numbers = []; for (let i = 0; i < count; i++) numbers.push(`${prefix}${first + i}`);
  try {
    const b = await api('/buildings', { method: 'POST', body: { site_id: Number($('#bSite').value), name: $('#bName').value.trim() } });
    let msg = `تم إنشاء ${b.name}`;
    if (numbers.length) {
      const r = await api('/units/bulk', { method: 'POST', body: { building_id: b.id, numbers } });
      msg += ` بعدد ${r.created} وحدة`;
    }
    alert(msg); $('#bName').value = ''; await loadAdmin();
  } catch (err) { alert(err.message); }
});

function resetUserForm() {
  S.editingUser = null;
  $('#uId').value = ''; $('#uName').value = ''; $('#uName').disabled = false; $('#uFull').value = '';
  $('#uPass').value = ''; $('#uPass').required = true; $('#uLevel').value = 'worker'; $('#uVis').value = '';
  Array.from($('#uSites').options).forEach(o => o.selected = false);
  $('#userErr').textContent = ''; $('#userFormTitle').textContent = 'المستخدمون';
}
$('#uCancel').addEventListener('click', resetUserForm);
$('#userList').addEventListener('click', async e => {
  const ed = e.target.closest('[data-edit]'), ac = e.target.closest('[data-active]');
  if (ed) {
    const u = S.users.find(x => x.id === Number(ed.dataset.edit));
    $('#uId').value = u.id; $('#uName').value = u.username; $('#uName').disabled = true;
    $('#uFull').value = u.full_name; $('#uPass').value = ''; $('#uPass').required = false; $('#uLevel').value = u.level; $('#uVis').value = u.visibility_custom ? u.visibility : '';
    S.editingUser = u;
    Array.from($('#uSites').options).forEach(o => o.selected = u.site_ids.includes(Number(o.value)));
    $('#userFormTitle').textContent = 'تعديل المستخدم: ' + u.username;
    $('#userForm').scrollIntoView({ behavior: 'smooth' });
  }
  if (ac) {
    const u = S.users.find(x => x.id === Number(ac.dataset.active));
    try { await api(`/users/${u.id}`, { method: 'PATCH', body: { is_active: !u.is_active } }); await loadAdmin(); }
    catch (err) { alert(err.message); }
  }
});
$('#userForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#userErr').textContent = '';
  const ids = Array.from($('#uSites').selectedOptions).map(o => Number(o.value));
  const id = $('#uId').value;
  try {
    if (id) {
      const body = { full_name: $('#uFull').value.trim(), visibility: $('#uVis').value || null };
      if (!S.editingUser || $('#uLevel').value !== S.editingUser.level) body.job_level = $('#uLevel').value;
      if ($('#uPass').value) body.password = $('#uPass').value;
      await api(`/users/${id}`, { method: 'PATCH', body });
      await api(`/users/${id}/sites`, { method: 'PUT', body: { site_ids: ids } });
    } else {
      await api('/users', { method: 'POST', body: { username: $('#uName').value.trim(), full_name: $('#uFull').value.trim() || $('#uName').value.trim(),
        password: $('#uPass').value, job_level: $('#uLevel').value, visibility: $('#uVis').value || null, site_ids: ids } });
    }
    await loadAdmin();
  } catch (err) { $('#userErr').textContent = err.message; }
});

/* ---------- التقارير والجدولة (المرحلة 3) ---------- */
const R = { types: [], key: null, tzOffset: 3, schedules: [], smtp: false };
const WEEKDAYS = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];
const FREQ = { daily: 'يومياً', weekly: 'أسبوعياً', monthly: 'شهرياً' };
const isAdmin = () => S.user && S.user.role === 'admin';
const utc = v => v ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : v + 'Z') : null;
function localStr(v) {
  const d = utc(v); if (!d || isNaN(d)) return '—';
  return new Date(d.getTime() + R.tzOffset * 3600e3).toISOString().slice(0, 16).replace('T', ' ');
}

async function loadReportsInit() {
  const [types, sites] = await Promise.all([api('/reports/types'), api('/sites')]);
  R.types = types; S.sites = sites;
  if (isAdmin()) { try { const c = await api('/report-schedules/config'); R.tzOffset = c.tz_offset_hours; R.smtp = c.smtp_configured; } catch (_) {} }
  const avail = types.filter(t => t.available);
  if (!avail.some(t => t.key === R.key)) R.key = avail.length ? avail[0].key : null;
  const siteOpts = '<option value="">كل المواقع</option>' + sites.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  const keepSite = $('#rSite').value; $('#rSite').innerHTML = siteOpts; $('#rSite').value = keepSite;
  const stOpts = '<option value="">كل الحالات</option>' + S.statuses.map(s => `<option value="${s.key}">${esc(s.label)}</option>`).join('');
  $('#rStatus').innerHTML = stOpts; $('#schStatus').innerHTML = stOpts;
  $('#reportTypes').innerHTML = types.map(t => t.available
    ? `<button class="chip" data-rt="${t.key}">${esc(t.label)}</button>`
    : `<button class="chip" disabled title="${esc(t.note)}">${esc(t.label)} (قريباً)</button>`).join('');
  $('#rUpcoming').textContent = types.filter(t => !t.available).map(t => `${t.label}: ${t.note}`).join(' · ');
  $('#schedBox').classList.toggle('hidden', !isAdmin());
  if (isAdmin()) initSchedForm(avail, sites);
  await loadCards();
}

async function refreshReports() {
  await loadReport();
  if (isAdmin()) await loadScheduler();
}

function reportParams(extra = {}) {
  const q = new URLSearchParams(extra);
  const def = R.types.find(t => t.key === R.key) || { filters: [] };
  if ($('#rSite').value) q.set('site_id', $('#rSite').value);
  if (def.filters.includes('status') && $('#rStatus').value) q.set('status', $('#rStatus').value);
  if (def.filters.includes('days') && $('#rDays').value !== '') q.set('days', $('#rDays').value);
  return q.toString();
}

async function loadReport() {
  const def = R.types.find(t => t.key === R.key);
  document.querySelectorAll('#reportTypes .chip[data-rt]').forEach(b => b.classList.toggle('on', b.dataset.rt === R.key));
  if (!def) { $('#rTable').innerHTML = '<p class="muted">لا توجد تقارير متاحة.</p>'; return; }
  $('#rDesc').textContent = def.description;
  $('#rSite').classList.toggle('hidden', !def.filters.includes('site'));
  $('#rStatus').classList.toggle('hidden', !def.filters.includes('status'));
  $('#rDays').classList.toggle('hidden', !def.filters.includes('days'));
  const d = await api(`/reports/${R.key}?${reportParams({ limit: 300 })}`);
  $('#rInfo').textContent = `${d.total_rows} صف` + (d.truncated ? ` (المعروض أول ${d.rows.length}؛ التصدير يشمل الكل)` : '');
  $('#rTable').innerHTML = d.rows.length
    ? `<table><tr>${d.columns.map(c => `<th>${esc(c.label)}</th>`).join('')}</tr>` +
      d.rows.map(r => `<tr>${d.columns.map(c => `<td>${esc(r[c.key])}</td>`).join('')}</tr>`).join('') + '</table>'
    : '<p class="muted">لا توجد بيانات مطابقة.</p>';
}
$('#reportTypes').addEventListener('click', e => {
  const b = e.target.closest('[data-rt]'); if (!b) return;
  R.key = b.dataset.rt; loadReport().catch(showErr);
});
['#rSite', '#rStatus', '#rDays'].forEach(id => $(id).addEventListener('change', () => loadReport().catch(showErr)));

async function downloadFile(path, fallback) {
  const res = await fetch('/api' + path, { headers: { Authorization: 'Bearer ' + S.token } });
  if (!res.ok) { let j = null; try { j = await res.json(); } catch (_) {} alert(errText(j)); return; }
  let name = fallback;
  const m = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('Content-Disposition') || '');
  if (m) { try { name = decodeURIComponent(m[1]); } catch (_) {} }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a'); a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
$('#rXlsx').addEventListener('click', () => downloadFile(`/reports/${R.key}?${reportParams({ format: 'xlsx' })}`, 'report.xlsx'));
$('#rCsv').addEventListener('click', () => downloadFile(`/reports/${R.key}?${reportParams({ format: 'csv' })}`, 'report.csv'));
$('#rPrint').addEventListener('click', () => window.print());

/* ---- جدولة التقارير (للمدير) ---- */
function initSchedForm(avail, sites) {
  $('#schType').innerHTML = avail.map(t => `<option value="${t.key}">${esc(t.label)}</option>`).join('');
  $('#schSite').innerHTML = '<option value="">كل المواقع</option>' + sites.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('#schWeekday').innerHTML = WEEKDAYS.map((w, i) => `<option value="${i}">${w}</option>`).join('');
  $('#schMail').textContent = R.smtp
    ? 'البريد مُفعّل: تُرسل التقارير المجدولة إلى المستلمين مرفقة.'
    : 'البريد غير مضبوط في الخادم (إعدادات SMTP في ملف .env)؛ تُحفظ التقارير هنا للتنزيل ولا تُرسل بالبريد.';
  syncSchedForm();
}
function syncSchedForm() {
  const def = R.types.find(t => t.key === $('#schType').value) || { filters: [] };
  $('#schStatus').classList.toggle('hidden', !def.filters.includes('status'));
  $('#schDays').classList.toggle('hidden', !def.filters.includes('days'));
  $('#schSite').classList.toggle('hidden', !def.filters.includes('site'));
  const f = $('#schFreq').value;
  $('#schWeekday').classList.toggle('hidden', f !== 'weekly');
  $('#schDom').classList.toggle('hidden', f !== 'monthly');
}
['#schType', '#schFreq'].forEach(id => $(id).addEventListener('change', syncSchedForm));

function resetSchedForm() {
  $('#schId').value = ''; $('#schName').value = ''; $('#schRecipients').value = ''; $('#schErr').textContent = '';
  $('#schFreq').value = 'daily'; $('#schHour').value = 8; $('#schDom').value = 1; $('#schWeekday').value = 0;
  $('#schStatus').value = ''; $('#schDays').value = ''; $('#schSite').value = ''; $('#schFmt').value = 'xlsx';
  if ($('#schType').options.length) $('#schType').selectedIndex = 0;
  $('#schTitle').textContent = 'جدولة التقارير'; syncSchedForm();
}
$('#schCancel').addEventListener('click', resetSchedForm);

function schedWhen(s) {
  const h = String(s.hour).padStart(2, '0') + ':00';
  if (s.frequency === 'weekly') return `كل ${WEEKDAYS[s.weekday]} ${h}`;
  if (s.frequency === 'monthly') return `يوم ${s.day_of_month} من كل شهر ${h}`;
  return `يومياً ${h}`;
}
async function loadScheduler() {
  const [list, runs] = await Promise.all([api('/report-schedules'), api('/report-runs?limit=30')]);
  R.schedules = list;
  const label = k => (R.types.find(t => t.key === k) || { label: k }).label;
  const site = id => id ? ((S.sites.find(s => s.id === id) || {}).name || '#' + id) : 'كل المواقع';
  $('#schList').innerHTML = list.length
    ? `<table><tr><th>الاسم</th><th>التقرير</th><th>الموقع</th><th>الموعد</th><th>التشغيل التالي</th><th>آخر تشغيل</th><th>الحالة</th><th></th></tr>` +
      list.map(s => `<tr><td>${esc(s.name)}</td><td>${esc(label(s.report_type))} (${s.fmt === 'xlsx' ? 'Excel' : 'CSV'})</td><td>${esc(site(s.site_id))}</td>` +
        `<td>${esc(schedWhen(s))}</td><td>${s.is_active ? localStr(s.next_run_at) : '—'}</td>` +
        `<td>${localStr(s.last_run_at)}${s.last_status === 'failed' ? ` <span class="pill red" title="${esc(s.last_error)}">فشل</span>` : ''}</td>` +
        `<td>${s.is_active ? 'فعّالة' : 'متوقفة'}</td>` +
        `<td><a class="lnk" data-sch-run="${s.id}">تشغيل الآن</a> · <a class="lnk" data-sch-edit="${s.id}">تعديل</a> · ` +
        `<a class="lnk" data-sch-toggle="${s.id}">${s.is_active ? 'إيقاف' : 'تفعيل'}</a> · <a class="lnk" data-sch-del="${s.id}">حذف</a></td></tr>`).join('') + '</table>'
    : '<p class="muted">لا توجد جداول بعد.</p>';
  $('#runList').innerHTML = runs.length
    ? `<table><tr><th>الوقت</th><th>التقرير</th><th>الجدولة</th><th>الصفوف</th><th>النوع</th><th>البريد</th><th></th></tr>` +
      runs.map(r => `<tr><td>${localStr(r.created_at)}</td><td>${esc(r.label)}</td><td>${esc(r.schedule_name || '—')}</td><td>${r.row_count}</td>` +
        `<td>${r.trigger === 'schedule' ? 'مجدول' : 'يدوي'}</td><td>${esc(r.email_note || '—')}</td>` +
        `<td>${r.status === 'ok' ? `<a class="lnk" data-run-dl="${r.id}" data-name="${esc(r.download_name)}">تنزيل</a>` : `<span class="pill red" title="${esc(r.error)}">فشل</span>`}</td></tr>`).join('') + '</table>'
    : '<p class="muted">لم يُولَّد أي تقرير بعد.</p>';
}
$('#runList').addEventListener('click', e => {
  const a = e.target.closest('[data-run-dl]'); if (!a) return;
  downloadFile(`/report-runs/${a.dataset.runDl}/download`, a.dataset.name || 'report');
});
$('#schList').addEventListener('click', async e => {
  const run = e.target.closest('[data-sch-run]'), ed = e.target.closest('[data-sch-edit]'),
    tg = e.target.closest('[data-sch-toggle]'), del = e.target.closest('[data-sch-del]');
  try {
    if (run) {
      const r = await api(`/report-schedules/${run.dataset.schRun}/run`, { method: 'POST' });
      alert(r.status === 'ok' ? `تم توليد التقرير (${r.row_count} صف). ${r.email_note || ''}` : 'فشل التوليد: ' + (r.error || ''));
      await loadScheduler();
    } else if (ed) {
      const s = R.schedules.find(x => x.id === Number(ed.dataset.schEdit));
      $('#schId').value = s.id; $('#schName').value = s.name; $('#schType').value = s.report_type;
      $('#schSite').value = s.site_id ?? ''; $('#schStatus').value = s.status || ''; $('#schDays').value = s.days ?? '';
      $('#schFmt').value = s.fmt; $('#schFreq').value = s.frequency; $('#schHour').value = s.hour;
      $('#schWeekday').value = s.weekday ?? 0; $('#schDom').value = s.day_of_month ?? 1;
      $('#schRecipients').value = s.recipients.join(', '); $('#schTitle').textContent = 'تعديل الجدولة: ' + s.name;
      syncSchedForm(); $('#schForm').scrollIntoView({ behavior: 'smooth' });
    } else if (tg) {
      const s = R.schedules.find(x => x.id === Number(tg.dataset.schToggle));
      await api(`/report-schedules/${s.id}`, { method: 'PATCH', body: { is_active: !s.is_active } }); await loadScheduler();
    } else if (del) {
      const s = R.schedules.find(x => x.id === Number(del.dataset.schDel));
      if (confirm(`حذف الجدولة «${s.name}»؟ تبقى التقارير المولّدة سابقاً متاحة للتنزيل.`)) {
        await api(`/report-schedules/${s.id}`, { method: 'DELETE' }); await loadScheduler();
      }
    }
  } catch (err) { alert(err.message); }
});
$('#schForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#schErr').textContent = '';
  const f = $('#schFreq').value, def = R.types.find(t => t.key === $('#schType').value) || { filters: [] };
  const body = {
    name: $('#schName').value.trim(), report_type: $('#schType').value,
    site_id: def.filters.includes('site') && $('#schSite').value ? Number($('#schSite').value) : null,
    status: def.filters.includes('status') && $('#schStatus').value ? $('#schStatus').value : null,
    days: def.filters.includes('days') && $('#schDays').value !== '' ? Number($('#schDays').value) : null,
    fmt: $('#schFmt').value, frequency: f, hour: Number($('#schHour').value || 0),
    weekday: f === 'weekly' ? Number($('#schWeekday').value) : null,
    day_of_month: f === 'monthly' ? Number($('#schDom').value || 1) : null,
    recipients: $('#schRecipients').value.split(/[,؛;\s]+/).map(x => x.trim()).filter(Boolean),
  };
  const id = $('#schId').value;
  try {
    if (id) await api(`/report-schedules/${id}`, { method: 'PATCH', body });
    else await api('/report-schedules', { method: 'POST', body });
    resetSchedForm(); await loadScheduler();
  } catch (err) { $('#schErr').textContent = err.message; }
});

/* ---------- بطاقات التقارير وكتابتها (المرحلة 3ب) ---------- */
const PRIO = { low: 'منخفضة', normal: 'عادية', high: 'عالية', urgent: 'عاجلة' };
const STAT = { draft: ['مسودة', 'gray'], submitted: ['مرسل', 'blue'], reviewed: ['مُطّلع عليه', 'green'] };
Object.assign(R, { cats: [], depts: [], cat: null, cardKey: null, cur: null });
const localDate = () => new Date(Date.now() + R.tzOffset * 3600e3).toISOString().slice(0, 10);

async function loadCards() {
  const [cats, depts, ms, mcats, fac] = await Promise.all([api('/report-categories'), api('/departments'),
    api('/media-settings'), api('/media-categories'), api('/authored-reports/facets')]);
  R.cats = cats; R.depts = depts; L.libCards = ms.cards; M.cats = mcats; L.authors = fac.authors;
  if (!R.cats.some(c => c.key === R.cardKey)) {
    const first = cats.find(c => c.kind === 'data' && R.types.some(t => t.available && t.key === c.key)) || cats[0];
    R.cardKey = first ? first.key : null;
  }
  renderCards();
  await selectCard(R.cardKey);
}
function renderCards() {
  $('#cardGrid').innerHTML = R.cats.map(c => {
    const badges = c.kind === 'data' ? '<span class="pill gray">بيانات تلقائية</span>'
      : (c.my_drafts ? `<span class="pill amber">مسوداتي ${c.my_drafts}</span>` : '') +
        (c.can_read ? `<span class="pill blue">مرسلة ${c.submitted}</span>` : '') +
        (!c.can_write ? '<span class="pill gray">قراءة فقط</span>' : '');
    return `<button class="rcard ${c.key === R.cardKey ? 'on' : ''}" data-card="${esc(c.key)}"><span class="ic">${esc(c.icon || '📄')}</span>` +
      `<b>${esc(c.title)}</b><small>${esc(c.description || '')}</small><span class="badges">${badges}</span></button>`;
  }).join('') || '<p class="muted">لا توجد بطاقات متاحة لك.</p>';
}
$('#cardGrid').addEventListener('click', e => {
  const b = e.target.closest('[data-card]'); if (!b) return;
  R.cardKey = b.dataset.card; renderCards(); selectCard(R.cardKey).catch(showErr);
});
async function selectCard(key) {
  const c = R.cats.find(x => x.key === key);
  if (!c) { await refreshReports(); return; }
  if (c.kind === 'data') {
    R.cat = null; R.key = c.key;
    $('#authPanel').classList.add('hidden'); $('#dataPanel').classList.remove('hidden');
    await refreshReports();
  } else {
    R.cat = c;
    $('#dataPanel').classList.add('hidden'); $('#authPanel').classList.remove('hidden');
    $('#apTitle').textContent = c.title; $('#apDesc').textContent = c.description || '';
    $('#apNew').classList.toggle('hidden', !c.can_write);
    setupPanel(c);
    await loadAuthList();
  }
}
/* ================= 3ج: سجلات التقارير + PDF القياسي ================= */
const L = { sort: 'created_at', order: 'desc', offset: 0, limit: 25, total: 0, libCards: [], authors: [], seq: 0 };
const M = { cats: [], cat: '', offset: 0, limit: 24, total: 0, seq: 0, byId: new Map() };
const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const COLS = [['serial', 'الرقم'], ['title', 'العنوان'], ['category', 'النوع'], ['created_at', 'الوقت'], ['report_date', 'التاريخ'],
  ['author', 'الكاتب'], ['site', 'الموقع'], ['priority', 'الأولوية'], ['department', 'القطاع / الإدارة'], ['status', 'الحالة'], [null, '📎']];

function setupPanel(c) {
  $('#apTabs').classList.toggle('hidden', !L.libCards.includes(c.key));
  setTab('log');
  const readable = R.cats.filter(x => x.kind === 'authored' && (x.can_read || x.can_write));
  fillSel($('#fCat'), readable.map(x => [x.id, x.title]), 'كل الأنواع');
  fillSel($('#fAuthor'), L.authors.map(a => [a.id, a.name]), 'كل الكتّاب');
  fillSel($('#fSite'), S.sites.filter(s => s.is_active !== false).map(s => [s.id, s.name]), 'كل المواقع');
  fillSel($('#fDept'), R.depts.map(d => [d.id, d.name]), 'كل الإدارات');
  fillSel($('#fPrio'), Object.entries(PRIO), 'كل الأولويات');
  resetFilters(c.id);
}
function resetFilters(catId) {
  ['#fQ', '#fSerial', '#fFrom', '#fTo', '#fAuthor', '#fSite', '#fDept', '#fPrio', '#apStatus'].forEach(s => { $(s).value = ''; });
  $('#apMine').checked = false; $('#fCat').value = String(catId); L.offset = 0;
}
function setTab(t) {
  document.querySelectorAll('#apTabs [data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  $('#apLog').classList.toggle('hidden', t !== 'log'); $('#apLib').classList.toggle('hidden', t !== 'lib');
  if (t === 'lib') { M.offset = 0; loadLib().catch(showErr); }
}
$('#apTabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (b) setTab(b.dataset.tab); });

function listQuery() {
  const q = new URLSearchParams({ sort: L.sort, order: L.order, limit: L.limit, offset: L.offset });
  const m = { category_id: '#fCat', q: '#fQ', serial: '#fSerial', date_from: '#fFrom', date_to: '#fTo', author_id: '#fAuthor',
    site_id: '#fSite', department_id: '#fDept', priority: '#fPrio', status: '#apStatus' };
  for (const [k, sel] of Object.entries(m)) { const v = $(sel).value.trim(); if (v) q.set(k, v); }
  if ($('#apMine').checked) q.set('mine', 'true');
  return q;
}
async function loadAuthList() {
  if (!R.cat) return;
  const seq = ++L.seq;
  const d = await api('/authored-reports?' + listQuery());
  if (seq !== L.seq) return;                       // وصل رد أحدث؛ تجاهل هذا
  L.total = d.total; R.list = d.items;
  const arrow = k => (L.sort === k ? (L.order === 'asc' ? ' ▲' : ' ▼') : '');
  $('#apList').innerHTML = d.items.length
    ? '<table><tr>' + COLS.map(([k, l]) => k ? `<th class="sortable ${L.sort === k ? 'on' : ''}" data-sort="${k}">${l}${arrow(k)}</th>` : `<th>${l}</th>`).join('') + '<th></th></tr>' +
      d.items.map(r => { const s = STAT[r.status] || [r.status, 'gray'];
        return `<tr class="clickable" data-rid="${r.id}" data-st="${r.status}"><td>${esc(r.serial || '—')}</td><td>${esc(r.title)}</td><td>${esc(r.category || '—')}</td>` +
          `<td>${esc(r.time || '—')}</td><td>${esc(r.report_date)}</td><td>${esc(r.author || '—')}</td><td>${esc(r.site || '—')}</td>` +
          `<td>${esc(PRIO[r.priority] || r.priority)}</td><td>${esc(r.department || '—')}</td><td><span class="pill ${s[1]}">${s[0]}</span></td>` +
          `<td>${r.media_count ? '📎 ' + r.media_count : ''}</td><td><a class="lnk" data-open="${r.id}">تفاصيل</a></td></tr>`; }).join('') + '</table>'
    : '<p class="muted">لا توجد تقارير مطابقة.</p>';
  const pages = Math.max(1, Math.ceil(L.total / L.limit)), page = Math.floor(L.offset / L.limit) + 1;
  $('#apInfo').textContent = `${L.total} تقرير`; $('#apPage').textContent = `صفحة ${page} من ${pages}`;
  $('#apPrev').disabled = L.offset <= 0; $('#apNext').disabled = L.offset + L.limit >= L.total;
  const mine = await api('/report-categories'); R.cats = mine; renderCards();   // تحديث عدّادات البطاقات
}
const reload = () => { L.offset = 0; loadAuthList().catch(showErr); };
['#fCat', '#fAuthor', '#fSite', '#fDept', '#fPrio', '#apStatus', '#fFrom', '#fTo', '#apMine'].forEach(id => $(id).addEventListener('change', reload));
['#fQ', '#fSerial'].forEach(id => $(id).addEventListener('input', debounce(reload)));
$('#fReset').addEventListener('click', () => { resetFilters(R.cat.id); L.sort = 'created_at'; L.order = 'desc'; loadAuthList().catch(showErr); });
$('#apPrev').addEventListener('click', () => { L.offset = Math.max(0, L.offset - L.limit); loadAuthList().catch(showErr); });
$('#apNext').addEventListener('click', () => { L.offset += L.limit; loadAuthList().catch(showErr); });
$('#apList').addEventListener('click', e => {
  const th = e.target.closest('[data-sort]');
  if (th) { const k = th.dataset.sort; L.order = (L.sort === k && L.order === 'asc') ? 'desc' : 'asc'; L.sort = k; L.offset = 0; loadAuthList().catch(showErr); return; }
  const det = e.target.closest('[data-open]');
  if (det) { openAuth(Number(det.dataset.open)).catch(err => alert(err.message)); return; }
  const tr = e.target.closest('[data-rid]'); if (!tr) return;
  const id = Number(tr.dataset.rid);
  if (tr.dataset.st === 'draft') openAuth(id).catch(err => alert(err.message));   // المسودة تُعدَّل، والمرسل يُفتح PDF
  else openReport(id, 'pdf');
});
$('#apNew').addEventListener('click', () => openAuth(null).catch(err => alert(err.message)));

async function fetchBlob(path) {
  const res = await fetch('/api' + path, { headers: { Authorization: 'Bearer ' + S.token } });
  if (res.status === 401) { logout(); throw new Error('انتهت الجلسة'); }
  if (!res.ok) { let j = null; try { j = await res.json(); } catch (_) {} const e = new Error(errText(j)); e.status = res.status; throw e; }
  return res.blob();
}
/* يفتح PDF القياسي في تبويب جديد؛ وإن تعذر توليده على الخادم تُفتح صفحة الطباعة القياسية نفسها لحفظها PDF */
async function openReport(id, mode) {
  const w = window.open('', '_blank');
  if (!w) { alert('اسمح بالنوافذ المنبثقة لهذه الصفحة ثم أعد المحاولة'); return; }
  w.document.write('<p style="font-family:sans-serif;direction:rtl;padding:20px">جارٍ تجهيز التقرير…</p>');
  try {
    if (mode === 'pdf') {
      try { w.location.href = URL.createObjectURL(await fetchBlob(`/authored-reports/${id}/pdf`)); return; }
      catch (e) { if (e.status !== 501) throw e; }
    }
    const html = await (await fetchBlob(`/authored-reports/${id}/print`)).text();
    w.document.open(); w.document.write(html); w.document.close();
    const go = () => setTimeout(() => { try { w.focus(); w.print(); } catch (_) {} }, 400);
    if (w.document.fonts && w.document.fonts.ready) w.document.fonts.ready.then(go); else go();
  } catch (err) { w.close(); alert(err.message); }
}

/* ================= 3ج: مكتبة الوسائط ================= */
function mediaTile(m) {
  MEDIA.set(m.id, m);
  const thumb = m.kind === 'image' ? `<img loading="lazy" src="${esc(m.url)}" alt="">`
    : `<video preload="metadata" muted playsinline src="${esc(m.url)}#t=0.5"></video><span class="play">▶</span>`;
  return `<button type="button" class="mtile" data-mid="${m.id}"><span class="thumb">${thumb}</span>` +
    `<span class="mtag" style="background:${esc(m.color)}">${esc(m.category || '')}</span>` +
    `<small>${esc(m.caption || m.original_name || '')}</small>${m.report_serial ? `<em>🔗 ${esc(m.report_serial)}</em>` : (m.report_id ? '<em>🔗 مسودة</em>' : '')}</button>`;
}
const MEDIA = M.byId;
function renderLibChips() {
  const total = M.cats.reduce((a, c) => a + c.count, 0);
  $('#libChips').innerHTML = `<button class="chip ${M.cat === '' ? 'on' : ''}" data-mc="" style="--c:#334155">الكل (${total})</button>` +
    M.cats.map(c => `<button class="chip ${String(c.id) === String(M.cat) ? 'on' : ''}" data-mc="${c.id}" style="--c:${esc(c.color)}">${esc(c.name)} (${c.count})</button>`).join('');
}
async function loadLib() {
  const seq = ++M.seq;
  M.cats = await api('/media-categories');
  const q = new URLSearchParams({ limit: M.limit, offset: M.offset });
  if (M.cat) q.set('category_id', M.cat);
  if ($('#libKind').value) q.set('kind', $('#libKind').value);
  if ($('#libQ').value.trim()) q.set('q', $('#libQ').value.trim());
  if ($('#libMine').checked) q.set('mine', 'true');
  const d = await api('/media?' + q);
  if (seq !== M.seq) return;
  M.total = d.total; renderLibChips();
  $('#libGrid').innerHTML = d.items.map(mediaTile).join('') || '<p class="muted">لا توجد وسائط مطابقة.</p>';
  const pages = Math.max(1, Math.ceil(M.total / M.limit));
  $('#libInfo').textContent = `${M.total} ملف`; $('#libPage').textContent = `صفحة ${Math.floor(M.offset / M.limit) + 1} من ${pages}`;
  $('#libPrev').disabled = M.offset <= 0; $('#libNext').disabled = M.offset + M.limit >= M.total;
  $('#libUp').classList.toggle('hidden', S.user.role === 'viewer');
}
const libReload = () => { M.offset = 0; loadLib().catch(showErr); };
$('#libChips').addEventListener('click', e => { const b = e.target.closest('[data-mc]'); if (b) { M.cat = b.dataset.mc; libReload(); } });
['#libKind', '#libMine'].forEach(id => $(id).addEventListener('change', libReload));
$('#libQ').addEventListener('input', debounce(libReload));
$('#libPrev').addEventListener('click', () => { M.offset = Math.max(0, M.offset - M.limit); loadLib().catch(showErr); });
$('#libNext').addEventListener('click', () => { M.offset += M.limit; loadLib().catch(showErr); });
$('#libGrid').addEventListener('click', e => { const t = e.target.closest('[data-mid]'); if (t) openMedia(Number(t.dataset.mid)).catch(err => alert(err.message)); });
$('#adMediaGrid').addEventListener('click', e => { const t = e.target.closest('[data-mid]'); if (t) openMedia(Number(t.dataset.mid)).catch(err => alert(err.message)); });
$('#libUp').addEventListener('click', () => openUpload(null));

/* --- رفع الملفات مع شريط تقدم --- */
function xhrUpload(fd, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/media'); x.setRequestHeader('Authorization', 'Bearer ' + S.token);
    x.upload.onprogress = e => { if (e.lengthComputable) onProgress(Math.round(e.loaded * 100 / e.total)); };
    x.onload = () => {
      let j = null; try { j = JSON.parse(x.responseText); } catch (_) {}
      if (x.status === 401) { logout(); reject(new Error('انتهت الجلسة')); }
      else if (x.status >= 200 && x.status < 300) resolve(j); else reject(new Error(errText(j)));
    };
    x.onerror = () => reject(new Error('انقطع الاتصال أثناء الرفع'));
    x.send(fd);
  });
}
const upDlg = $('#upDialog'); let upReport = null, upBusy = false;
async function openUpload(reportId, siteId) {
  upReport = reportId;
  M.cats = await api('/media-categories');
  if (!M.cats.length) { alert('لا توجد تصنيفات في المكتبة. أضف تصنيفاً من تبويب الإدارة أولاً.'); return; }
  fillSel($('#upCat'), M.cats.map(c => [c.id, c.name]));
  fillSel($('#upSite'), S.sites.filter(s => s.is_active !== false).map(s => [s.id, s.name]), '— بلا موقع —');
  $('#upSite').value = siteId ? String(siteId) : '';
  $('#upFiles').value = ''; $('#upCap').value = ''; $('#upProg').innerHTML = ''; $('#upErr').textContent = '';
  $('#upInfo').textContent = reportId ? 'ستُرفق الملفات بالتقرير الحالي.' : 'الصور حتى 15 MB والفيديو حتى 150 MB (JPG، PNG، WEBP، GIF، MP4، WEBM، MOV).';
  upDlg.showModal();
}
$('#upGo').addEventListener('click', async () => {
  if (upBusy) return;
  const files = [...$('#upFiles').files]; $('#upErr').textContent = '';
  if (!files.length) { $('#upErr').textContent = 'اختر ملفاً واحداً على الأقل'; return; }
  upBusy = true; $('#upGo').disabled = true;
  const prog = $('#upProg'); prog.innerHTML = files.map((f, i) => `<div id="up${i}">${esc(f.name)} — بانتظار الرفع</div>`).join('');
  let bad = 0;
  for (let i = 0; i < files.length; i++) {
    const row = $('#up' + i), fd = new FormData();
    fd.append('file', files[i]); fd.append('category_id', $('#upCat').value);
    if ($('#upCap').value.trim()) fd.append('caption', $('#upCap').value.trim());
    if ($('#upSite').value) fd.append('site_id', $('#upSite').value);
    if (upReport) fd.append('report_id', upReport);
    try {
      await xhrUpload(fd, p => { row.textContent = `${files[i].name} — ${p}%`; });
      row.className = 'ok'; row.textContent = `✓ ${files[i].name}`;
    } catch (err) { bad++; row.className = 'bad'; row.textContent = `✗ ${files[i].name}: ${err.message}`; }
  }
  upBusy = false; $('#upGo').disabled = false;
  await refreshMediaViews();
  if (!bad) upDlg.close(); else $('#upErr').textContent = `تعذر رفع ${bad} من ${files.length} ملف`;
});
$('#upClose').addEventListener('click', () => { if (!upBusy) upDlg.close(); });

/* --- عرض ملف وسائط وتعديله --- */
const mvDlg = $('#mvDialog'); let mvCur = null;
async function reportOptions(m) {
  const q = new URLSearchParams({ limit: 200 });
  if (!isAdmin()) { q.set('mine', 'true'); q.set('status', 'draft'); }
  const d = await api('/authored-reports?' + q);
  const opts = d.items.map(r => [r.id, `${r.serial || 'مسودة'} — ${r.title}`]);
  if (m.report_id && !opts.some(o => o[0] === m.report_id)) opts.unshift([m.report_id, `${m.report_serial || 'مسودة'} — ${m.report_title || ''}`]);
  return opts;
}
async function openMedia(id) {
  const m = MEDIA.get(id); if (!m) return; mvCur = m;
  $('#mvPreview').innerHTML = m.kind === 'image' ? `<img src="${esc(m.url)}" alt="">` : `<video controls playsinline preload="metadata" src="${esc(m.url)}"></video>`;
  fillSel($('#mvCat'), M.cats.map(c => [c.id, c.name]));
  fillSel($('#mvSite'), S.sites.filter(s => s.is_active !== false || s.id === m.site_id).map(s => [s.id, s.name]), '— بلا موقع —');
  fillSel($('#mvRep'), [], '— غير مرتبط —');
  $('#mvCat').value = String(m.category_id); $('#mvSite').value = m.site_id ? String(m.site_id) : ''; $('#mvCap').value = m.caption || '';
  const ed = m.can_edit;
  ['#mvCat', '#mvSite', '#mvRep', '#mvCap'].forEach(s => { $(s).disabled = !ed; });
  ['#mvSave', '#mvDel'].forEach(s => $(s).classList.toggle('hidden', !ed));
  $('#mvRepOpen').classList.toggle('hidden', !(m.report_id && m.report_serial));
  $('#mvDl').href = m.url + '&download=true';
  $('#mvInfo').textContent = `${m.kind === 'image' ? 'صورة' : 'فيديو'} · ${(m.size / 1048576).toFixed(m.size < 1048576 ? 2 : 1)} MB · رفعه ${m.uploader || '—'} · ${localStr(m.created_at)}`;
  $('#mvErr').textContent = '';
  mvDlg.showModal();
  if (ed) { try { const o = await reportOptions(m); fillSel($('#mvRep'), o, '— غير مرتبط —'); $('#mvRep').value = m.report_id ? String(m.report_id) : ''; } catch (_) {} }
}
$('#mvSave').addEventListener('click', async () => {
  try {
    await api('/media/' + mvCur.id, { method: 'PATCH', body: { caption: $('#mvCap').value.trim() || null, category_id: Number($('#mvCat').value),
      site_id: $('#mvSite').value ? Number($('#mvSite').value) : null, report_id: $('#mvRep').value ? Number($('#mvRep').value) : null } });
    mvDlg.close(); await refreshMediaViews();
  } catch (err) { $('#mvErr').textContent = err.message; }
});
$('#mvDel').addEventListener('click', async () => {
  if (!confirm('حذف هذا الملف نهائياً؟')) return;
  try { await api('/media/' + mvCur.id, { method: 'DELETE' }); mvDlg.close(); await refreshMediaViews(); }
  catch (err) { $('#mvErr').textContent = err.message; }
});
$('#mvRepOpen').addEventListener('click', () => openReport(mvCur.report_id, 'pdf'));
$('#mvClose').addEventListener('click', () => mvDlg.close());
mvDlg.addEventListener('close', () => { $('#mvPreview').innerHTML = ''; });          // يوقف تشغيل الفيديو
$('#mvForm').addEventListener('submit', e => e.preventDefault());
async function refreshMediaViews() {
  if (!$('#apLib').classList.contains('hidden')) await loadLib();
  if (adlg.open && R.cur && R.cur.id) await loadAdMedia();
}

const adlg = $('#authDialog');
const fillSel = (el, items, none) => { el.innerHTML = (none ? `<option value="">${none}</option>` : '') + items.map(i => `<option value="${i[0]}">${esc(i[1])}</option>`).join(''); };

function fieldHtml(f, val, ro) {
  const req = f.required ? ' <span class="req">*</span>' : '', help = f.help ? `<span class="help">${esc(f.help)}</span>` : '';
  if (ro) {
    let v = val;
    if (f.type === 'checkbox') v = val ? 'نعم' : 'لا';
    if (f.type === 'table') {
      const rows = Array.isArray(val) ? val : [];
      v = rows.length ? `<div class="subtbl"><table><tr>${f.columns.map(c => `<th>${esc(c.label)}</th>`).join('')}</tr>` +
        rows.map(r => `<tr>${f.columns.map(c => `<td>${esc(r[c.key] ?? '')}</td>`).join('')}</tr>`).join('') + '</table></div>' : '—';
      return `<div class="fld"><span>${esc(f.label)}</span>${v}</div>`;
    }
    return `<div class="fld"><span>${esc(f.label)}</span><div class="ro-val">${esc(v === undefined || v === null || v === '' ? '—' : v)}</div></div>`;
  }
  const k = `data-fk="${f.key}"`;
  if (f.type === 'textarea') return `<label class="fld"><span>${esc(f.label)}${req}</span><textarea ${k}>${esc(val ?? '')}</textarea>${help}</label>`;
  if (f.type === 'number') return `<label class="fld"><span>${esc(f.label)}${req}</span><input ${k} type="number" step="any" value="${esc(val ?? '')}">${help}</label>`;
  if (f.type === 'date') return `<label class="fld"><span>${esc(f.label)}${req}</span><input ${k} type="date" value="${esc(val ?? '')}">${help}</label>`;
  if (f.type === 'select') return `<label class="fld"><span>${esc(f.label)}${req}</span><select ${k}><option value="">—</option>` +
    f.options.map(o => `<option ${o === val ? 'selected' : ''}>${esc(o)}</option>`).join('') + `</select>${help}</label>`;
  if (f.type === 'checkbox') return `<label class="fld chk"><input ${k} type="checkbox" ${val ? 'checked' : ''}><span>${esc(f.label)}${req}</span></label>`;
  if (f.type === 'table') {
    const rows = Array.isArray(val) && val.length ? val : [{}];
    return `<div class="fld"><span>${esc(f.label)}${req}</span><div class="subtbl" data-tk="${f.key}"><table><tr>${f.columns.map(c => `<th>${esc(c.label)}</th>`).join('')}<th></th></tr>` +
      rows.map(r => tableRow(f, r)).join('') + `</table><button type="button" class="btn small" data-addrow="${f.key}">+ صف</button></div>${help}</div>`;
  }
  return `<label class="fld"><span>${esc(f.label)}${req}</span><input ${k} maxlength="300" value="${esc(val ?? '')}">${help}</label>`;
}
function tableRow(f, r) {
  return '<tr data-row>' + f.columns.map(c => `<td><input data-ck="${c.key}" type="${c.type === 'number' ? 'number' : c.type === 'date' ? 'date' : 'text'}" ${c.type === 'number' ? 'step="any"' : ''} value="${esc(r[c.key] ?? '')}"></td>`).join('') +
    '<td><a class="lnk" data-delrow>حذف</a></td></tr>';
}
$('#adFields').addEventListener('click', e => {
  const add = e.target.closest('[data-addrow]'), del = e.target.closest('[data-delrow]');
  if (add && R.cur) { const f = R.cur.schema.fields.find(x => x.key === add.dataset.addrow); add.previousElementSibling.insertAdjacentHTML('beforeend', tableRow(f, {})); }
  if (del) del.closest('tr').remove();
});
function collectBody(schema) {
  const body = {};
  for (const f of schema.fields) {
    if (f.type === 'table') {
      const box = $(`[data-tk="${f.key}"]`, $('#adFields')); if (!box) continue;
      body[f.key] = [...box.querySelectorAll('tr[data-row]')].map(tr => {
        const row = {}; tr.querySelectorAll('[data-ck]').forEach(i => { row[i.dataset.ck] = i.value; }); return row; });
      continue;
    }
    const el = $(`[data-fk="${f.key}"]`, $('#adFields')); if (!el) continue;
    body[f.key] = f.type === 'checkbox' ? el.checked : el.value;
  }
  return body;
}
async function openAuth(id) {
  const cat = R.cat; let rep = null, schema;
  if (id) { rep = await api('/authored-reports/' + id); schema = rep.schema; }
  else { const t = await api(`/report-categories/${cat.id}/template`); schema = t.schema; }
  const editable = !rep || (rep.status === 'draft' && (isAdmin() || rep.author_id === S.user.id));
  R.cur = { id, schema, editable, rep };
  const st = rep ? (STAT[rep.status] || [rep.status])[0] : 'جديد';
  $('#adTitle').textContent = `${cat.title} — ${rep && rep.serial ? rep.serial + ' · ' : ''}${st}`;
  fillSel($('#adPriority'), Object.entries(PRIO));
  fillSel($('#adSite'), S.sites.filter(s => s.is_active !== false).map(s => [s.id, s.name]), '— بلا موقع —');
  fillSel($('#adDept'), R.depts.map(d => [d.id, d.name]), '— بلا إدارة —');
  $('#adTitleIn').value = rep ? rep.title : ''; $('#adPriority').value = rep ? rep.priority : 'normal';
  $('#adSite').value = rep && rep.site_id ? rep.site_id : ''; $('#adDept').value = rep && rep.department_id ? rep.department_id : '';
  $('#adDate').value = rep ? rep.report_date : localDate();
  ['#adTitleIn', '#adPriority', '#adSite', '#adDept', '#adDate'].forEach(s => { $(s).disabled = !editable; });
  $('#adFields').innerHTML = schema.fields.map(f => fieldHtml(f, rep ? rep.body[f.key] : undefined, !editable)).join('');
  ['#adSave', '#adSubmit'].forEach(s => $(s).classList.toggle('hidden', !editable));
  $('#adDelete').classList.toggle('hidden', !(editable && rep));
  $('#adErr').textContent = '';
  const saved = !!(rep && rep.id);
  ['#adPdf', '#adPrint'].forEach(x => $(x).classList.toggle('hidden', !saved));
  $('#adMediaBox').classList.toggle('hidden', !(editable || (saved && isAdmin())));
  $('#adMediaGrid').innerHTML = '';
  adlg.showModal();
  if (saved) loadAdMedia().catch(showErr);
}
async function loadAdMedia() {
  const d = await api(`/media?report_id=${R.cur.id}&limit=200`);
  $('#adMediaGrid').innerHTML = d.items.map(mediaTile).join('') || '<p class="muted">لا توجد مرفقات بعد.</p>';
}
/* ينشئ مسودة من النموذج الحالي إن لم تُحفظ بعد، ليمكن ربط الوسائط بها */
async function ensureDraft() {
  const c = R.cur; if (c.id) return c.id;
  const title = $('#adTitleIn').value.trim(); if (!title) throw new Error('اكتب عنوان التقرير أولاً ثم أرفق الملفات');
  const rep = await api('/authored-reports', { method: 'POST', body: { ...formBase(c), category_id: R.cat.id, submit: false } });
  c.id = rep.id; c.rep = rep;
  $('#adDelete').classList.remove('hidden'); ['#adPdf', '#adPrint'].forEach(x => $(x).classList.remove('hidden'));
  return rep.id;
}
function formBase(c) {
  return { title: $('#adTitleIn').value.trim(), priority: $('#adPriority').value, report_date: $('#adDate').value || null,
    site_id: $('#adSite').value ? Number($('#adSite').value) : null,
    department_id: $('#adDept').value ? Number($('#adDept').value) : null, body: collectBody(c.schema) };
}
async function saveAuth(submit) {
  const c = R.cur; $('#adErr').textContent = '';
  const title = $('#adTitleIn').value.trim(); if (!title) { $('#adErr').textContent = 'العنوان مطلوب'; return; }
  const base = formBase(c);
  try {
    let rep;
    if (!c.id) rep = await api('/authored-reports', { method: 'POST', body: { ...base, category_id: R.cat.id, submit } });
    else {
      rep = await api('/authored-reports/' + c.id, { method: 'PATCH', body: base });
      if (submit) rep = await api(`/authored-reports/${c.id}/submit`, { method: 'POST' });
    }
    adlg.close(); await loadAuthList();
    if (submit && rep.serial) alert('تم إرسال التقرير برقم ' + rep.serial);
  } catch (err) { $('#adErr').textContent = err.message; }
}
$('#adSave').addEventListener('click', () => saveAuth(false));
$('#adSubmit').addEventListener('click', () => { if (confirm('بعد الإرسال لا يمكن تعديل التقرير. متابعة؟')) saveAuth(true); });
$('#adDelete').addEventListener('click', async () => {
  if (!R.cur || !R.cur.id || !confirm('حذف هذه المسودة نهائياً؟')) return;
  try { await api('/authored-reports/' + R.cur.id, { method: 'DELETE' }); adlg.close(); await loadAuthList(); } catch (err) { $('#adErr').textContent = err.message; }
});
$('#adClose').addEventListener('click', () => adlg.close());
adlg.addEventListener('close', () => { if (R.cat) loadAuthList().catch(showErr); });
$('#adAttach').addEventListener('click', async () => {
  try { $('#adErr').textContent = ''; const id = await ensureDraft(); await openUpload(id, $('#adSite').value || null); }
  catch (err) { $('#adErr').textContent = err.message; }
});
$('#adPdf').addEventListener('click', () => { if (R.cur && R.cur.id) openReport(R.cur.id, 'pdf'); });
$('#adPrint').addEventListener('click', () => { if (R.cur && R.cur.id) openReport(R.cur.id, 'print'); });
$('#authForm').addEventListener('submit', e => e.preventDefault());

/* ---- صلاحيات البطاقات (الإدارة) ---- */
let PM_ROLES = [['worker', 'عامل'], ['technician', 'فني صيانة'], ['receptionist', 'موظف استقبال'], ['supervisor', 'مشرف موقع']];
const PM = { items: [], catId: null };
async function loadPerms() {
  const cats = (await api('/report-categories?include_inactive=true')).filter(c => c.kind === 'authored');
  const keep = $('#pmCat').value;
  $('#pmCat').innerHTML = cats.map(c => `<option value="${c.id}">${esc(c.title)}</option>`).join('');
  if (cats.some(c => String(c.id) === keep)) $('#pmCat').value = keep;
  await loadPermRows();
}
async function loadPermRows() {
  PM.catId = Number($('#pmCat').value); if (!PM.catId) { $('#pmTable').innerHTML = ''; return; }
  PM.items = (await api(`/report-categories/${PM.catId}/permissions`)).items;
  const get = role => PM.items.find(i => i.role === role) || PM.items.find(i => i.role === 'updater') || {};
  $('#pmTable').innerHTML = '<table><tr><th>الدور</th><th>يكتب</th><th>يقرأ</th></tr>' +
    PM_ROLES.map(([r, l]) => { const p = get(r);
      return `<tr><td>${l}</td><td><input type="checkbox" data-pm="${r}:can_write" ${p.can_write ? 'checked' : ''}></td>` +
        `<td><input type="checkbox" data-pm="${r}:can_read" ${p.can_read ? 'checked' : ''}></td></tr>`; }).join('') + '</table>' +
    (PM.items.some(i => i.user_id) ? `<p class="muted">يوجد ${PM.items.filter(i => i.user_id).length} صف صلاحيات لمستخدمين محددين، وتبقى كما هي عند الحفظ.</p>` : '');
  $('#pmErr').textContent = '';
}
$('#pmCat').addEventListener('change', () => loadPermRows().catch(showErr));
$('#pmTable').addEventListener('change', e => {   // الكتابة تتضمن القراءة
  const t = e.target.closest('[data-pm]'); if (!t) return; const [r, f] = t.dataset.pm.split(':');
  if (f === 'can_write' && t.checked) $(`[data-pm="${r}:can_read"]`, $('#pmTable')).checked = true;
  if (f === 'can_read' && !t.checked) $(`[data-pm="${r}:can_write"]`, $('#pmTable')).checked = false;
});
$('#pmSave').addEventListener('click', async () => {
  const items = PM.items.filter(i => i.user_id).map(i => ({ user_id: i.user_id, can_write: i.can_write, can_read: i.can_read, can_review: i.can_review }));
  PM_ROLES.forEach(([r]) => {
    const w = $(`[data-pm="${r}:can_write"]`, $('#pmTable')).checked, rd = $(`[data-pm="${r}:can_read"]`, $('#pmTable')).checked;
    const old = PM.items.find(i => i.role === r) || {};
    if (w || rd || old.can_review) items.push({ role: r, can_write: w, can_read: rd || w, can_review: !!old.can_review });
  });
  try { await api(`/report-categories/${PM.catId}/permissions`, { method: 'PUT', body: { items } }); $('#pmErr').textContent = ''; await loadPermRows(); alert('تم حفظ الصلاحيات'); }
  catch (err) { $('#pmErr').textContent = err.message; }
});

/* ---- تصنيفات مكتبة الوسائط (الإدارة) ---- */
async function loadMediaAdmin() {
  const [cats, ms, rc] = await Promise.all([api('/media-categories'), api('/media-settings'), api('/report-categories?include_inactive=true')]);
  M.cats = cats;
  $('#mcList').innerHTML = cats.length ? '<table><tr><th>اللون</th><th>الاسم</th><th>الملفات</th><th></th></tr>' + cats.map(c =>
    `<tr data-mc="${c.id}"><td><input type="color" data-mccolor value="${esc(c.color)}"></td><td><input data-mcname value="${esc(c.name)}" maxlength="80"></td>` +
    `<td>${c.count}</td><td><a class="lnk" data-mcsave>حفظ</a> · <a class="lnk" data-mcdel>حذف</a></td></tr>`).join('') + '</table>'
    : '<p class="muted">لا توجد تصنيفات؛ أضف تصنيفاً ليتمكن الموظفون من الرفع.</p>';
  $('#mcCards').innerHTML = rc.filter(c => c.kind === 'authored').map(c =>
    `<label class="inline"><input type="checkbox" data-mcc="${esc(c.key)}" ${ms.cards.includes(c.key) ? 'checked' : ''}> ${esc(c.icon || '')} ${esc(c.title)}</label>`).join('');
  $('#mcErr').textContent = '';
}
$('#mcForm').addEventListener('submit', async e => {
  e.preventDefault();
  try { await api('/media-categories', { method: 'POST', body: { name: $('#mcName').value.trim(), color: $('#mcColor').value } }); $('#mcName').value = ''; await loadMediaAdmin(); }
  catch (err) { $('#mcErr').textContent = err.message; }
});
const mcMove = $('#mcMove'); let mcDelId = null;
$('#mcList').addEventListener('click', async e => {
  const tr = e.target.closest('[data-mc]'); if (!tr) return; const id = Number(tr.dataset.mc);
  const c = M.cats.find(x => x.id === id);
  try {
    if (e.target.closest('[data-mcsave]')) {
      await api('/media-categories/' + id, { method: 'PATCH', body: { name: $('[data-mcname]', tr).value.trim(), color: $('[data-mccolor]', tr).value } });
      await loadMediaAdmin();
    } else if (e.target.closest('[data-mcdel]')) {
      if (!c.count) { if (confirm(`حذف التصنيف «${c.name}»؟`)) { await api('/media-categories/' + id, { method: 'DELETE' }); await loadMediaAdmin(); } return; }
      const others = M.cats.filter(x => x.id !== id);
      if (!others.length) { $('#mcErr').textContent = 'أضف تصنيفاً آخر لتُنقل إليه الملفات قبل الحذف'; return; }
      mcDelId = id; $('#mcMoveMsg').textContent = `التصنيف «${c.name}» فيه ${c.count} ملف.`;
      fillSel($('#mcMoveTo'), others.map(x => [x.id, x.name])); mcMove.showModal();
    }
  } catch (err) { $('#mcErr').textContent = err.message; }
});
$('#mcMoveGo').addEventListener('click', async () => {
  try { await api(`/media-categories/${mcDelId}?move_to=${$('#mcMoveTo').value}`, { method: 'DELETE' }); mcMove.close(); await loadMediaAdmin(); }
  catch (err) { mcMove.close(); $('#mcErr').textContent = err.message; }
});
$('#mcMoveCancel').addEventListener('click', () => mcMove.close());
$('#mcCardsSave').addEventListener('click', async () => {
  const cards = [...document.querySelectorAll('[data-mcc]')].filter(i => i.checked).map(i => i.dataset.mcc);
  try { await api('/media-settings', { method: 'PUT', body: { cards } }); $('#mcErr').textContent = ''; alert('تم الحفظ'); }
  catch (err) { $('#mcErr').textContent = err.message; }
});

boot();

/* ---------- المستويات الوظيفية والمصفوفة والمهام (3هـ) ---------- */
const LV = { levels: [], capabilities: [], visibilities: [], task_kinds: [], matrix: null, canEdit: false };
async function loadLevels() {
  const d = await api('/levels');
  Object.assign(LV, { levels: d.levels, capabilities: d.capabilities, visibilities: d.visibilities, task_kinds: d.task_kinds,
    matrix: d.matrix || null, canEdit: !!d.can_edit_matrix });
  const mine = S.user ? S.user.level : 'general_manager', rank = k => (LV.levels.find(l => l.key === k) || {}).rank || 0;
  $('#uLevel').innerHTML = LV.levels.filter(l => S.user.level === 'general_manager' || rank(l.key) < rank(mine))
    .map(l => `<option value="${l.key}">${esc(l.label)}</option>`).join('');
  $('#uVis').innerHTML = '<option value="">النطاق الافتراضي للمستوى</option>' + LV.visibilities.map(v => `<option value="${v.key}">${esc(v.label)}</option>`).join('');
  if (LV.matrix) {
    $('#lvMatrix').innerHTML = '<table><tr><th>الصلاحية</th>' + LV.levels.map(l => `<th>${esc(l.label)}</th>`).join('') + '</tr>' +
      LV.capabilities.map(c => `<tr><td>${esc(c.label)}</td>` + LV.levels.map(l =>
        `<td><input type="checkbox" data-lv="${l.key}:${c.key}" ${LV.matrix[l.key][c.key] ? 'checked' : ''} ${(l.locked || !LV.canEdit) ? 'disabled' : ''}></td>`).join('') + '</tr>').join('') + '</table>';
    $('#lvSave').classList.toggle('hidden', !LV.canEdit);
  }
}
$('#lvSave').addEventListener('click', async () => {
  const matrix = {};
  document.querySelectorAll('#lvMatrix [data-lv]:not(:disabled)').forEach(i => { const [l, c] = i.dataset.lv.split(':'); (matrix[l] = matrix[l] || {})[c] = i.checked; });
  try { await api('/levels/matrix', { method: 'PUT', body: { matrix } }); $('#lvErr').textContent = ''; alert('تم حفظ المصفوفة'); await loadLevels(); }
  catch (err) { $('#lvErr').textContent = err.message; }
});

const TK = { items: [], canAssign: false };
const TK_COLOR = { cleaning: '#0891b2', maintenance: '#dc2626', inspection: '#8b5cf6', other: '#64748b' };
async function loadTasks() {
  const d = await api('/tasks?status=' + $('#tkStatus').value);
  TK.items = d.items; TK.canAssign = d.can_assign;
  $('#tkNew').classList.toggle('hidden', !d.can_assign);
  $('#tkInfo').textContent = assignedOnly() ? 'تظهر لك المهام المسندة إليك فقط' : `${d.items.length} مهمة`;
  $('#tkList').innerHTML = d.items.map(t => `<div class="task ${t.status === 'done' ? 'done' : ''}" style="--c:${TK_COLOR[t.kind] || '#64748b'}">` +
    `<span class="tk-ico">${ICONS.house(30)}</span><div class="tk-body"><b>${esc(t.title)}</b> <span class="muted">— ${esc(t.kind_label)}</span>` +
    `<div class="muted">${esc(t.site)} · ${esc(t.building)} · وحدة ${esc(t.unit_number)}${t.assignee ? ' · إلى: ' + esc(t.assignee) : ''}` +
    `${t.due_date ? ' · الموعد ' + esc(t.due_date) : ''}${t.overdue ? ' <span class="err">متأخرة</span>' : ''}</div>${t.note ? `<div>${esc(t.note)}</div>` : ''}</div>` +
    `<div class="tk-act">${t.status === 'open' ? `<button class="btn small primary" data-done="${t.id}">تم</button>` : `<button class="btn small" data-reopen="${t.id}">إعادة فتح</button>`}</div></div>`).join('')
    || '<p class="muted">لا توجد مهام.</p>';
}
$('#tkStatus').addEventListener('change', () => loadTasks().catch(showErr));
$('#tkList').addEventListener('click', async e => {
  const d = e.target.closest('[data-done]'), r = e.target.closest('[data-reopen]');
  if (!d && !r) return;
  try { await api(`/tasks/${(d || r).dataset.done || (d || r).dataset.reopen}/${d ? 'done' : 'reopen'}`, { method: 'POST' }); await loadTasks(); }
  catch (err) { alert(err.message); }
});
const tdlg = $('#taskDialog');
$('#tkNew').addEventListener('click', async () => {
  $('#tErr').textContent = '';
  const [sites, who] = await Promise.all([api('/sites'), api('/tasks/assignees')]);
  $('#tSite').innerHTML = sites.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('#tWho').innerHTML = who.map(w => `<option value="${w.id}">${esc(w.name)} (${esc(w.level_label)})</option>`).join('');
  if (!LV.task_kinds.length) await loadLevels();
  $('#tKind').innerHTML = LV.task_kinds.map(k => `<option value="${k.key}">${esc(k.label)}</option>`).join('');
  await fillTaskUnits(); tdlg.showModal();
});
async function fillTaskUnits() {
  const us = await api(`/units?site_id=${$('#tSite').value}&limit=2000`);
  $('#tUnit').innerHTML = us.sort(natural).map(u => `<option value="${u.id}">${esc(u.number)}</option>`).join('');
}
$('#tSite').addEventListener('change', () => fillTaskUnits().catch(showErr));
$('#tCancel').addEventListener('click', () => tdlg.close());
$('#taskForm').addEventListener('submit', e => { e.preventDefault(); $('#tSave').click(); });
$('#tSave').addEventListener('click', async () => {
  $('#tErr').textContent = '';
  try {
    await api('/tasks', { method: 'POST', body: { unit_id: Number($('#tUnit').value), assignee_id: Number($('#tWho').value), kind: $('#tKind').value,
      title: $('#tTitle').value.trim() || null, note: $('#tNote').value.trim() || null, due_date: $('#tDue').value || null } });
    tdlg.close(); $('#tTitle').value = ''; $('#tNote').value = ''; $('#tDue').value = ''; await loadTasks();
  } catch (err) { $('#tErr').textContent = err.message; }
});
