// Comum a estúdio e admin: sessão, API, toast, helpers.

const state = { me: null, catalog: null, models: { image: [], video: [] }, jobs: [], files: [] };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const lensLabel = (k) => (k.endsWith('m') ? `${k.slice(0, -1)}mm macro` : `${k}mm`);
const fmtBytes = (b) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(2)} GB` : b >= 2 ** 20 ? `${(b / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
// Estúdio mostra custo em tokens (o servidor já manda convertido); dinheiro só no painel admin.
const fmtTok = (v) => { const n = +v || 0; return `${n > 0 && n < 1 ? '<1' : n < 10 ? n.toFixed(n % 1 ? 1 : 0) : Math.round(n).toLocaleString(LOC)} tokens`; };
const fmtUsd = (v) => `US$ ${(+v || 0).toFixed((+v || 0) < 1 ? 3 : 2)}`;
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString(LOC, { dateStyle: 'short', timeStyle: 'short' }) : '—');

async function api(path, body, method) {
  const opts = body === undefined && !method ? { headers: {} } : {
    method: method || 'POST', headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
  opts.headers['X-Lang'] = window.I18N?.lang || 'en'; // a IA escreve no idioma da interface
  const res = await fetch(path, opts);
  if (res.status === 401 && !path.startsWith('/api/auth/')) { location.href = '/login'; throw new Error('Sessão expirada'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail?.[0]?.msg || `HTTP ${res.status}`));
  return data;
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/upload', { method: 'POST', body: fd, headers: { 'X-Lang': window.I18N?.lang || 'en' } });
  if (res.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  const m = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(m.detail || `HTTP ${res.status}`);
  return m;
}

function toast(msg) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 3600);
}

async function logout() {
  await api('/api/auth/logout', {}).catch(() => {});
  location.href = '/login';
}

// Cabeçalho do usuário: nome, uso do mês contra os limites, admin, sair.
function renderUserMenu() {
  const me = state.me;
  const box = $('#userMenu');
  if (!box || !me) return;
  const lim = me.limits || {};
  const tok = me.usage.tokens || 0;
  const tmax = lim.tokens_month;
  const pct = tmax == null ? 0 : Math.min(100, (tok / Math.max(1, tmax)) * 100);
  const lvl = pct >= 90 ? 'hot' : pct >= 70 ? 'warm' : '';
  const n = (v) => Math.round(v).toLocaleString(LOC);
  // velocímetro: arco de 180° preenchido conforme o uso
  const arc = 37.7; // comprimento do semicírculo r=12
  const gauge = `<svg class="ug-gauge" viewBox="0 0 30 17" aria-hidden="true"><path d="M3 15a12 12 0 0 1 24 0" class="t"/><path d="M3 15a12 12 0 0 1 24 0" class="v" style="stroke-dasharray:${(pct / 100) * arc} ${arc}"/><line x1="15" y1="15" x2="${15 + 9 * Math.cos(Math.PI * (1 - pct / 100))}" y2="${15 - 9 * Math.sin(Math.PI * (1 - pct / 100))}" class="n"/></svg>`;
  box.innerHTML = `
    <button class="hud-pill um-usage ${lvl}" id="umUsage" title="${tmax == null ? `${n(tok)} tokens usados este mês (sem limite)` : `${n(tok)} de ${n(tmax)} tokens usados este mês · restam ${n(Math.max(0, tmax - tok))}`}">
      ${gauge}
      <span class="ug-num">${n(tok)}${tmax == null ? ' tokens' : ` / ${n(tmax)}`}</span>
    </button>
    <button class="hud-pill um-btn" id="umToggle" title="${esc(me.name || me.email)}" aria-label="${esc(me.name || me.email)}">${ic('user')} ${esc(me.name || me.email)} ${ic('chevron-down', 'caret')}</button>
    <div class="um-pop" id="umPop" hidden>
      <div class="um-head"><b>${esc(me.name || me.email)}</b><small>${esc(me.email)}</small></div>
      <div class="section-label"><span>Uso deste mês</span></div>
      ${lim.tokens_month == null ? `<div class="um-row"><span>Tokens usados</span><b>${fmtTok(tok)}</b></div>`
        : `<div class="um-row"><span>Tokens</span><b>${Math.round(tok).toLocaleString(LOC)} / ${fmtTok(lim.tokens_month)}</b></div><div class="um-bar"><i style="width:${Math.min(100, (tok / Math.max(1, lim.tokens_month)) * 100)}%"></i></div>`}
      <div class="um-row"><span>Espaço</span><b>${fmtBytes(me.storage_bytes)}${lim.storage_mb != null ? ` / ${lim.storage_mb} MB` : ''}</b></div>
      <label class="um-lang"><span>Idioma</span><select id="umLang" data-noi18n>${Object.entries(I18N.LANGS).map(([k, v]) => `<option value="${k}" ${k === I18N.lang ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <div class="um-actions">
        ${me.is_admin ? (location.pathname.startsWith('/admin') ? `<a class="btn small ghost" href="/">${ic('film')} Estúdio</a>` : `<a class="btn small ghost" href="/admin">${ic('settings')} Painel admin</a>`) : ''}
        ${me.auth_provider === 'microsoft' ? '' : `<button class="btn small ghost" id="umPass">${ic('lock')} Trocar senha</button>`}
        <button class="btn small" id="umLogout">${ic('logout')} Sair</button>
      </div>
    </div>`;
  const keepOpen = renderUserMenu.open;
  if (keepOpen) $('#umPop').hidden = false;
  const toggle = (e) => { e.stopPropagation(); $('#umPop').hidden = !$('#umPop').hidden; renderUserMenu.open = !$('#umPop').hidden; };
  $('#umToggle').onclick = toggle;
  $('#umUsage').onclick = toggle;
  $('#umLogout').onclick = logout;
  $('#umLang').onchange = (e) => I18N.setLang(e.target.value);
  $('#umPass')?.addEventListener('click', changePassword);
  if (!renderUserMenu.bound) {
    renderUserMenu.bound = true;
    document.addEventListener('click', (e) => { const b = $('#userMenu'); if (b && !b.contains(e.target) && $('#umPop')) { $('#umPop').hidden = true; renderUserMenu.open = false; } });
  }
}

async function changePassword() {
  const current = prompt('Senha atual');
  if (!current) return;
  const next = prompt('Nova senha (8+ caracteres)');
  if (!next) return;
  try {
    await api('/api/me/password', { current, new: next });
    toast('Senha alterada');
  } catch (e) { toast(e.message); }
}

// ---------------- Branding (tema, logo, estilo) ----------------

function hexRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function mixHex(h, w) { const [r, g, b] = hexRgb(h); const m = (c) => Math.round(c + (255 - c) * w); return `rgb(${m(r)}, ${m(g)}, ${m(b)})`; }

function applyBranding(b) {
  if (!b) return;
  const c = b.colors || {};
  const root = document.documentElement.style;
  if (c.accent) {
    root.setProperty('--emerald', c.accent);
    root.setProperty('--accent-rgb', hexRgb(c.accent).join(', '));
    root.setProperty('--emerald-soft', `rgba(${hexRgb(c.accent).join(', ')}, .14)`);
  }
  if (c.accent2) root.setProperty('--cyan', c.accent2);
  if (c.bg) {
    root.setProperty('--bg-deep', c.bg);
    root.setProperty('--bg-surface', mixHex(c.bg, 0.035));
    root.setProperty('--bg-card', mixHex(c.bg, 0.06));
    root.setProperty('--bg-elevated', mixHex(c.bg, 0.1));
  }
  document.body.classList.toggle('style-pixel', b.style !== 'smooth');
  document.body.classList.toggle('style-smooth', b.style === 'smooth');
  document.querySelectorAll('[data-app-logo]').forEach((el) => {
    el.innerHTML = b.logo_url ? `<img src="${b.logo_url}" alt="" class="brand-img">` : ic('logo');
  });
  if (b.logo_url) {
    let link = document.querySelector('link[rel=icon]');
    if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
    link.href = b.logo_url;
  }
}

async function loadMe() {
  state.me = await api('/api/me');
  if (state.me.default_lang) I18N.setDefault(state.me.default_lang);
  applyBranding(state.me.branding);
  document.title = `${state.me.app_name}${location.pathname.startsWith('/admin') ? ' · Admin' : ''}`;
  document.querySelectorAll('[data-app-name]').forEach((el) => { el.textContent = state.me.app_name; });
  renderUserMenu();
  return state.me;
}
